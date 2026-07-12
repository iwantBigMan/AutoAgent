"""decompose 병렬 실행기 본체.

승인된 task_graph.json을 의존성 wavefront로 실행한다: baseline 확인 → 위상정렬 →
파도별 worktree 격리 병렬 실행(구현→반대모델 리뷰→수정) → 통합 브랜치 병합 →
통합 트리 최종리뷰/평가/리포트 → 정리. max_parallel_lanes=1이면 순차 실행과 동치다.
현재 실행기는 backend/frontend(및 backend로 정규화되는 db) 노드만 구현하고,
docs/review/test/infra 노드는 skip하며 리포트에 미실행으로 명시한다.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from autoagent.artifacts import read_text, write_json, write_text
from autoagent.config import Config
from autoagent.routing import route_task
from autoagent.runner import AgentCallBudget, AgentCallBudgetStopped, require_command, run_process, write_command_artifact
from autoagent.safety import git_baseline_status
from autoagent.workflows.routed_common import block_implementation, run_evaluation, run_final_report
from autoagent.workflows.routed_impl import command_for_agent, run_impl_review_fix


# 프롬프트 파일(PROMPT_ALIASES)에 존재하는, 레인으로 구현 가능한 타입.
CODE_NODE_TYPES = {"backend", "frontend"}


def topological_waves(tasks: list[dict[str, Any]]) -> list[list[str]]:
    """의존성 기반 파도 리스트를 만든다. 이미 done인 노드는 만족된 것으로 보고 배제한다.

    한 파도 = 아직 미완이며 모든 의존성이 done이거나 이전 파도에서 처리된 노드들.
    파도 내부 순서는 입력 tasks 순서를 보존해 결정론적이다. 순환 의존이면 SystemExit.
    """
    by_id = {t.get("id"): t for t in tasks}
    done: set[str] = {t.get("id") for t in tasks if t.get("status") == "done"}
    remaining = [t.get("id") for t in tasks if t.get("id") not in done]
    waves: list[list[str]] = []
    satisfied = set(done)
    while remaining:
        wave = [
            nid for nid in remaining
            if all(dep in satisfied for dep in (by_id[nid].get("dependencies") or []))
        ]
        if not wave:
            raise SystemExit(f"task_graph에 순환 의존이 있습니다(진행 불가): {remaining}")
        waves.append(wave)
        satisfied |= set(wave)
        remaining = [nid for nid in remaining if nid not in satisfied]
    return waves


def load_task_graph(run_dir: Path) -> dict[str, Any]:
    # 승인 게이트에서 저장한 task_graph.json을 읽는다. 없으면 재개 불가로 종료.
    path = run_dir / "task_graph.json"
    if not path.exists():
        raise SystemExit(f"No task_graph.json in {run_dir}; cannot execute.")
    return json.loads(read_text(path))


def persist_status(run_dir: Path, task_graph: dict[str, Any]) -> None:
    # status 전이마다 task_graph.json을 다시 써 재개 시 done 노드를 건너뛸 수 있게 한다.
    write_json(run_dir / "task_graph.json", task_graph)


def set_status(run_dir: Path, task_graph: dict[str, Any], node_id: str, status: str) -> None:
    # 단일 노드 status를 갱신하고 즉시 영속한다.
    for t in task_graph.get("tasks", []):
        if t.get("id") == node_id:
            t["status"] = status
            break
    persist_status(run_dir, task_graph)


def _node_route(node: dict[str, Any]) -> dict[str, Any]:
    # 노드 type/description으로 route를 파생하되, 그래프가 선언한 risk_level/subtype이 있으면 덮는다.
    # db 타입은 backend로 정규화(프롬프트 파일이 backend/frontend만 존재; route_task가 db subtype 도출).
    node_type = node.get("type", "backend")
    route_type = "backend" if node_type == "db" else node_type
    route = route_task(route_type, node.get("description", ""), "auto")
    if node.get("risk_level"):
        route["risk_level"] = node["risk_level"]
    if node.get("subtype"):
        route["subtype"] = node["subtype"]
    return route


def _node_common(config: Config, node: dict[str, Any], route: dict[str, Any],
                 request: str, max_review_rounds: int) -> dict[str, Any]:
    # run_impl_review_fix가 프롬프트 렌더에 쓰는 공용 값. routed의 base_values/common 규약과 동일.
    # codex_final.md 등이 요구하는 CLAUDE_CONTEXT/CLAUDE_ARCHITECTURE/CODEX_VALIDATION도 채운다.
    return {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
        "TASK_TYPE": route["task_type"],
        "ROUTE_JSON": json.dumps(route, ensure_ascii=False, indent=2),
        "MAX_REVIEW_ROUNDS": str(max(max_review_rounds, 0)),
        "CLAUDE_CONTEXT": node.get("description", ""),
        "CLAUDE_ARCHITECTURE": node.get("rationale", ""),
        "CODEX_VALIDATION": "\n".join(node.get("validation_commands") or []),
    }


def run_node(
    *, args: Namespace, config: Config, task_graph: dict[str, Any],
    node: dict[str, Any], budget: AgentCallBudget, run_dir: Path, stamp: str, baseline: str,
) -> str:
    """노드 하나를 격리 worktree에서 구현→리뷰→수정 코어로 돌리고 status 문자열을 반환한다."""
    # worktree 헬퍼는 함수 내부에서 지연 import한다(모듈 로드 순서/순환 회피).
    from autoagent import worktree as wt

    node_id = node.get("id")
    node_type = node.get("type", "")
    node_out = run_dir / "nodes" / str(node_id)

    # backend/frontend(및 backend로 정규화되는 db)만 레인. 그 외는 skip하고 미실행으로 기록.
    route_type = "backend" if node_type == "db" else node_type
    if route_type not in CODE_NODE_TYPES:
        write_text(node_out / "skipped.md",
                   f"타입 {node_type} 노드({node_id})는 현재 실행기가 구현하지 않습니다(미실행).\n")
        return "skipped"

    route = _node_route(node)
    goal = task_graph.get("goal", "")
    # dry/non-dry 모두 worktree 경로를 workspace로 삼은 Config 사본을 만든다(스펙 #6).
    worktree_path = run_dir / "worktrees" / str(node_id)
    node_config = dataclasses.replace(config, workspace=worktree_path)
    common = _node_common(node_config, node, route, goal, args.max_review_rounds)

    if not args.dry_run:
        branch = f"aa/{stamp}/{node_id}"
        wt.add_worktree(config.workspace, worktree_path, branch, baseline)

    try:
        implementation, review, fix, resolved, stopped = run_impl_review_fix(
            args=args, config=node_config, common=common, route=route,
            request=goal, budget=budget, run_dir=node_out,
        )
    except AgentCallBudgetStopped:
        # 예산 소진: 실제 실패와 구분해 budget_stopped로 표시(스펙 §113: pending으로 남겨 재개 대상).
        write_text(node_out / "node_budget_stopped.md", f"노드 {node_id}는 예산 소진으로 정지했습니다.\n")
        return "budget_stopped"
    except SystemExit as exc:
        write_text(node_out / "node_failed.md", f"노드 {node_id} 실행 실패: {exc}\n")
        return "failed"

    if not args.dry_run:
        # soft scope 가드: allowed_paths 밖/blocked_paths 안 변경을 플래그(차단 아님).
        violations = wt.scope_violations(
            config.workspace, worktree_path,
            node.get("allowed_paths") or [], node.get("blocked_paths") or [],
        )
        if violations:
            write_text(node_out / "scope_violations.md",
                       "# scope 위반\n\n" + "\n".join(f"- {v}" for v in violations) + "\n")
        # 레인 브랜치에 커밋(구현 산출을 병합 대상으로 고정). 변경 없으면 commit이 실패하나 무해.
        subprocess.run(["git", "-C", str(worktree_path), "add", "-A"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        subprocess.run(["git", "-C", str(worktree_path), "commit", "-m", f"aa: node {node_id}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "done"


def _mark_blocked_descendants(run_dir: Path, task_graph: dict[str, Any]) -> None:
    # 실제 failed/미완 노드에 (직간접) 의존하는 미완 노드를 blocked로 표시한다.
    tasks = task_graph.get("tasks", [])
    bad = {t.get("id") for t in tasks if t.get("status") in {"failed", "in_progress"}}
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if t.get("status") in {"done", "skipped", "blocked", "failed"}:
                continue
            if any(dep in bad for dep in (t.get("dependencies") or [])):
                t["status"] = "blocked"
                bad.add(t.get("id"))
                changed = True
    persist_status(run_dir, task_graph)


def _write_skipped_report(run_dir: Path, tasks: list[dict[str, Any]]) -> None:
    # 승인했으나 미실행(skip)된 노드를 사람이 인지하도록 명시(Global Constraint 7).
    skipped = [t for t in tasks
               if ("backend" if t.get("type") == "db" else t.get("type")) not in CODE_NODE_TYPES]
    if not skipped:
        return
    lines = ["# 미실행 노드 (승인했으나 실행기가 구현하지 않음)\n"]
    for t in skipped:
        lines.append(f"- **{t.get('id')}** (type={t.get('type')}): {t.get('title', '')}")
    lines.append("\n현재 실행기는 backend/frontend(및 db) 노드만 구현합니다.\n")
    write_text(run_dir / "skipped_nodes.md", "\n".join(lines))


def _integrate_and_cleanup(
    args: Namespace, config: Config, run_dir: Path, tasks: list[dict[str, Any]],
    stamp: str, baseline: str, failed: bool, budget_stopped: bool,
) -> tuple[bool, str]:
    """완료 레인을 통합 브랜치로 순차 병합하고 성공 시 정리한다. (통합성공여부, 통합브랜치명) 반환."""
    # worktree 헬퍼는 함수 내부에서 지연 import한다(모듈 로드 순서/순환 회피).
    from autoagent import worktree as wt

    # 레인 브랜치는 aa/<stamp>/<id> 네임스페이스(=refs/heads/aa/<stamp>/ 디렉터리)를 쓰므로,
    # 통합 브랜치를 aa/<stamp>(=같은 경로의 파일)로 두면 git ref D/F 충돌로 branch 생성이 실패한다.
    # 별도 최상위 네임스페이스로 분리해 충돌을 피한다.
    integration_branch = f"aa-integration/{stamp}"
    if failed or budget_stopped:
        # 안전편향: 실패/예산소진/블록이 있으면 통합하지 않고 전체 보존.
        reason = "실패 노드" if failed else "예산 소진"
        write_text(run_dir / "integration_report.md",
                   f"# 통합 생략(안전편향 정지: {reason})\n\n"
                   "통합 병합을 하지 않고 worktree와 레인 브랜치를 모두 보존합니다.\n")
        return False, integration_branch

    # 통합 worktree를 baseline에서 만들고 그 안에서 순차 병합(레인 브랜치를 위상 순으로).
    wt.create_integration_branch(config.workspace, integration_branch, baseline)
    integ_wt = run_dir / "worktrees" / "_integration"
    proc = subprocess.run(
        ["git", "-C", str(config.workspace), "worktree", "add", str(integ_wt), integration_branch],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        write_text(run_dir / "integration_report.md",
                   f"# 통합 worktree 생성 실패\n\n{proc.stderr.strip() or proc.stdout.strip()}\n")
        return False, integration_branch

    merged: list[str] = []
    done_ids = [t.get("id") for t in tasks
                if t.get("status") == "done"
                and ("backend" if t.get("type") == "db" else t.get("type")) in CODE_NODE_TYPES]
    for node_id in done_ids:
        result = wt.merge_branch(integ_wt, f"aa/{stamp}/{node_id}")
        if not result.ok:
            write_text(run_dir / "integration_report.md",
                       "# 통합 병합 충돌(수동 병합 필요)\n\n"
                       f"- 충돌 브랜치: aa/{stamp}/{node_id}\n"
                       f"- 충돌 파일: {result.conflicts}\n"
                       f"- 이미 병합된 노드: {merged}\n\n"
                       "worktree/레인 브랜치를 보존합니다. 수동 병합 후 다시 진행하세요.\n")
            return False, integration_branch
        merged.append(node_id)

    write_text(run_dir / "integration_report.md",
               f"# 통합 성공\n\n- 통합 브랜치: {integration_branch}\n- 병합 노드: {merged}\n")
    return True, integration_branch


def _cleanup_lanes(config: Config, run_dir: Path, tasks: list[dict[str, Any]], stamp: str) -> None:
    # 성공 정리: 레인 worktree 제거 + 레인 브랜치 삭제. 통합 브랜치는 남긴다(사람 리뷰 대상).
    # worktree 헬퍼는 함수 내부에서 지연 import한다(모듈 로드 순서/순환 회피).
    from autoagent import worktree as wt

    for t in tasks:
        if ("backend" if t.get("type") == "db" else t.get("type")) not in CODE_NODE_TYPES:
            continue
        node_id = t.get("id")
        wt.remove_worktree(config.workspace, run_dir / "worktrees" / str(node_id))
        wt.delete_branch(config.workspace, f"aa/{stamp}/{node_id}")
    wt.remove_worktree(config.workspace, run_dir / "worktrees" / "_integration")


def run_task_graph_execution(args: Namespace, config: Config, run_dir: Path) -> int:
    """승인된 task_graph를 wavefront 병렬로 실행한다(재개 진입점)."""
    # worktree 헬퍼는 함수 내부에서 지연 import한다(모듈 로드 순서/순환 회피).
    from autoagent import worktree as wt

    try:
        task_graph = load_task_graph(run_dir)
        tasks = task_graph.get("tasks", []) or []

        # baseline 안전 확인: 타깃 워킹트리가 커밋된 HEAD를 가져야 격리 worktree가 깨끗하다.
        if not args.dry_run:
            ok, git_message = git_baseline_status(config.workspace)
            write_text(run_dir / "git_baseline_status.txt", git_message)
            if not ok:
                return block_implementation(run_dir, git_message)

        waves = topological_waves(tasks)  # 순환이면 여기서 SystemExit
        write_text(run_dir / "waves.txt", "\n".join(" ".join(w) for w in waves) + "\n")

        overlaps = wt.warn_path_overlap(tasks)
        if overlaps:
            write_text(run_dir / "path_overlap_warnings.md",
                       "# allowed_paths 겹침 경고\n\n" + "\n".join(f"- {w}" for w in overlaps) + "\n")

        budget = AgentCallBudget(args.max_agent_calls)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        baseline = "HEAD"
        by_id = {t.get("id"): t for t in tasks}
        failed = False
        budget_stopped = False
        for wave in waves:
            if budget_stopped:
                break  # 예산 소진 후 새 파도 시작 안 함(스펙 §333).
            results: dict[str, str] = {}
            with ThreadPoolExecutor(max_workers=max(config.max_parallel_lanes, 1)) as pool:
                futures = {}
                for node_id in wave:
                    node = by_id[node_id]
                    set_status(run_dir, task_graph, node_id, "in_progress")
                    futures[pool.submit(
                        run_node, args=args, config=config, task_graph=task_graph,
                        node=node, budget=budget, run_dir=run_dir, stamp=stamp, baseline=baseline,
                    )] = node_id
                for fut, node_id in futures.items():
                    results[node_id] = fut.result()  # run_node가 예외를 삼켜 문자열로 반환
            for node_id, status in results.items():
                if status in {"done", "skipped"}:
                    set_status(run_dir, task_graph, node_id, status)
                elif status == "budget_stopped":
                    # 예산 소진 노드는 pending으로 되돌려 재개 대상으로 남긴다(스펙 §113).
                    set_status(run_dir, task_graph, node_id, "pending")
                    budget_stopped = True
                else:  # "failed"
                    set_status(run_dir, task_graph, node_id, "failed")
                    failed = True
            if failed:
                break  # 실제 실패면 안전편향 정지: 다음 파도 시작 안 함(barrier에서 멈춤).

        _write_skipped_report(run_dir, tasks)

        if args.dry_run:
            write_text(run_dir / "final_report.md", "# Task Graph dry-run 완료\n\n노드 프롬프트만 렌더했습니다.\n")
            print(f"Task graph dry-run complete: {run_dir}")
            return 0

        if failed:
            _mark_blocked_descendants(run_dir, task_graph)

        integrated, integration_branch = _integrate_and_cleanup(
            args, config, run_dir, tasks, stamp, baseline, failed, budget_stopped,
        )
        if not integrated:
            write_text(run_dir / "final_report.md",
                       "# Task Graph 실행 정지\n\n통합하지 못했습니다. integration_report.md를 보세요.\n"
                       f"worktree/브랜치를 보존합니다: {run_dir / 'worktrees'}\n")
            print(f"Task graph run stopped without integration: {run_dir}")
            return 0

        # 통합 트리에 대해 run 레벨 1회: 최종리뷰(codex 07) → 평가(codex 08) → 최종보고(claude 09).
        integ_wt = run_dir / "worktrees" / "_integration"
        integ_config = dataclasses.replace(config, workspace=integ_wt)
        run_route = route_task("backend", task_graph.get("goal", ""), "auto")
        common = {
            "REQUEST": task_graph.get("goal", ""),
            "WORKSPACE": str(integ_wt),
            "TASK_TYPE": run_route["task_type"],
            "ROUTE_JSON": json.dumps(run_route, ensure_ascii=False, indent=2),
            "MAX_REVIEW_ROUNDS": str(max(args.max_review_rounds, 0)),
            "CLAUDE_CONTEXT": task_graph.get("goal", ""),
            "CLAUDE_ARCHITECTURE": "\n".join(t.get("title", "") for t in tasks),
            "CODEX_VALIDATION": "\n".join(
                cmd for t in tasks for cmd in (t.get("validation_commands") or [])
            ),
        }

        from autoagent.workflows.routed_impl import run_final_review  # 지연 import(순환 회피)
        final_review = run_final_review(
            args=args, config=integ_config, common=common, route=run_route,
            request=common["REQUEST"], budget=budget, run_dir=run_dir,
            implementation="통합 브랜치 트리", review="-", fix="-",
        )
        evaluation = run_evaluation(
            args, integ_config, common, budget, run_dir,
            name="08_codex_evaluation",
            implementation="통합 브랜치 트리", review="-", fix="-", final_review=final_review,
        )
        final = run_final_report(
            args, integ_config, common, budget, run_dir,
            name="09_claude_final_report",
            implementation="통합 브랜치 트리", review="-", fix="-",
            final_review=final_review, evaluation=evaluation,
        )
        write_text(run_dir / "final_report.md", final)

        _cleanup_lanes(config, run_dir, tasks, stamp)
        print(f"Task graph execution complete: {run_dir} (통합 브랜치 {integration_branch})")
        return 0
    except AgentCallBudgetStopped as stopped:
        write_text(run_dir / "final_report.md",
                   f"# 예산 소진 정지\n\nbefore {stopped.next_step}에서 예산이 소진돼 정지했습니다. "
                   "--resume로 이어갈 수 있습니다.\n")
        print(f"Task graph run stopped by budget before {stopped.next_step}: {run_dir}")
        return 0

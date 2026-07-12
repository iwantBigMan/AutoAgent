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


def run_task_graph_execution(args: Namespace, config: Config, run_dir: Path) -> int:
    """승인된 task_graph를 wavefront 병렬로 실행한다(재개 진입점)."""
    # worktree 헬퍼는 함수 내부에서 지연 import한다(모듈 로드 순서/순환 회피).
    from autoagent import worktree as wt

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
    # 7b/7c/7d에서 파도 실행·병합·평가/리포트를 채운다.
    return 0

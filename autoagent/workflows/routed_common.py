"""routed 워크플로우 공용 헬퍼.

- 승인 게이트: approval_required(판정) / block_for_human_approval / write_checkpoint.
- 재개용 상태 저장(checkpoint.json), 구현 차단(block_implementation).
- 종료 제어: stop_after. 평가·보고: run_evaluation / run_final_report
  (각각 evaluation/report 역할을 resolve_role로 해석해 조립한다).
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import DEFAULT_CONFIG, render_template, write_json, write_text
from autoagent.config import Config
from autoagent.roles import load_roles, resolve_role
from autoagent.runner import AgentCallBudget, require_command, run_process, write_command_artifact


HIGH_RISK_REQUEST_TERMS = ["migration", "auth", "payment", "production", "backfill", "rollback"]


def block_implementation(run_dir: Path, git_message: str) -> int:
    blocked = (
        "# Implementation blocked\n\n"
        "Git baseline is not safe for implementation work.\n\n"
        f"Details:\n\n```text\n{git_message}\n```\n\n"
        "Create or repair the repository baseline before allowing implementation steps.\n"
    )
    write_text(run_dir / "implementation_blocked.md", blocked)
    write_text(run_dir / "final_report.md", blocked)
    print(f"Routed run blocked before implementation: {run_dir}")
    return 0


def resume_command_for(run_dir: Path) -> str:
    """게이트에서 정지한 run을 구현 단계로 이어가는 표준 재개 명령 문자열.

    run_dir은 프로젝트 유무에 따라 ROOT/runs/<stamp> 또는
    ROOT/projects/<name>/runs/<stamp>일 수 있어 run_dir 기준 상대 위치로는 run.py를
    찾을 수 없다. DEFAULT_CONFIG.parent(=ROOT, cli.py의 roles 로딩과 같은 결합)로 고정한다.
    어느 cwd에서든 그대로 붙여넣어 실행할 수 있게 절대경로를 쌍따옴표로 감싼다.
    --resume는 checkpoint.json에서 workspace를 복원하므로 --workspace가 필요 없다.
    """
    run_py = DEFAULT_CONFIG.parent / "run.py"
    return f'python "{run_py}" --resume "{run_dir}"'


def block_for_human_approval(run_dir: Path, route: dict[str, Any]) -> int:
    reason = "High-risk implementation requires human approval before code changes."
    resume_command = resume_command_for(run_dir)
    status = {
        "status": "waiting_for_human_approval",
        "approved": False,
        "required": True,
        "reason": reason,
        "run_dir": str(run_dir),
        "resume_command": resume_command,
    }
    write_json(run_dir / "approval_status.json", status)
    write_text(
        run_dir / "approval_required.md",
        "# Human Approval Required\n\n"
        f"{reason}\n\n"
        "Review these artifacts before approving:\n\n"
        "- 01_claude_context.md\n"
        "- 02_claude_architecture.md\n"
        "- 03_codex_validation.md\n"
        "- route.json\n\n"
        "To approve and continue into implementation, run:\n\n"
        f"```powershell\n{resume_command}\n```\n\n"
        "Running that resume command IS the act of approval.\n\n"
        "Route:\n\n"
        f"```json\n{json.dumps(route, ensure_ascii=False, indent=2)}\n```\n",
    )
    write_text(
        run_dir / "final_report.md",
        "# Waiting for Human Approval\n\n"
        f"{reason}\n\n"
        "No implementation step was run.\n\n"
        f"Resume with:\n\n```powershell\n{resume_command}\n```\n",
    )
    # 파싱 가능한 재개 핸드오프: 구동 측(사람 또는 Claude CLI 커맨드)이 어떤 라우트든
    # 동일한 형식으로 run_dir/재개 명령을 안정적으로 집도록 stdout에 고정 라인을 찍는다.
    print("ROUTED_STATUS: waiting_for_human_approval")
    print(f"RUN_DIR: {run_dir}")
    print(f"RESUME_COMMAND: {resume_command}")
    print(f"Routed run waiting for human approval: {run_dir}")
    return 0


def _route_request_from_common(common: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """common에 담긴 ROUTE_JSON/REQUEST에서 route/request를 복원한다.

    evaluation/report 역할은 high_risk_condition="none"이라 route/request 값 자체는
    판정에 영향을 주지 않지만, resolve_role 시그니처를 맞추기 위해 필요하다.
    """
    route = json.loads(common["ROUTE_JSON"])
    request = common["REQUEST"]
    return route, request


def run_evaluation(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
    *,
    name: str,
    implementation: str,
    review: str,
    fix: str,
    final_review: str,
) -> str:
    # 지연 import: routed_impl.command_for_agent와의 순환 import 방지(roles.py의 관례와 동일).
    from autoagent.workflows.routed_impl import command_for_agent

    evaluation_prompt = render_template(
        "codex_evaluator.md",
        {
            **common,
            "IMPLEMENTATION_RESULT": implementation,
            "REVIEW_RESULT": review,
            "FIX_RESULT": fix,
            "FINAL_REVIEW_RESULT": final_review,
        },
    )
    route, request = _route_request_from_common(common)
    roles = load_roles(DEFAULT_CONFIG.parent)
    resolved = resolve_role(
        roles["evaluation"], config=config, route=route, request=request, agent="codex", read_only=args.read_only
    )
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", evaluation_prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, resolved))
        evaluation = "[dry-run: Codex evaluation output]"
    else:
        codex = require_command(config.codex_command)
        budget.before_call(next_step="evaluation", out_dir=run_dir, dry_run=args.dry_run)
        evaluation = run_process(
            name=name,
            command=command_for_agent(config, resolved, resolved_command=codex),
            prompt=evaluation_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / f"{name}.md", evaluation)
    write_text(run_dir / "final_evaluation.md", evaluation)
    return evaluation


def run_final_report(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
    *,
    name: str,
    implementation: str,
    review: str,
    fix: str,
    final_review: str,
    evaluation: str,
) -> str:
    # 지연 import: routed_impl.command_for_agent와의 순환 import 방지(roles.py의 관례와 동일).
    from autoagent.workflows.routed_impl import command_for_agent

    final_prompt = render_template(
        "claude_final.md",
        {
            **common,
            "IMPLEMENTATION_RESULT": implementation,
            "REVIEW_RESULT": review,
            "FIX_RESULT": fix,
            "FINAL_REVIEW_RESULT": final_review,
            "FINAL_EVALUATION": evaluation,
        },
    )
    route, request = _route_request_from_common(common)
    roles = load_roles(DEFAULT_CONFIG.parent)
    resolved = resolve_role(
        roles["report"], config=config, route=route, request=request, agent="claude", read_only=args.read_only
    )
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", final_prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, resolved))
        return "[dry-run: final report output]"

    claude = require_command(config.claude_command)
    budget.before_call(next_step="report", out_dir=run_dir, dry_run=args.dry_run)
    return run_process(
        name=name,
        command=command_for_agent(config, resolved, resolved_command=claude),
        prompt=final_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )


def write_checkpoint(run_dir, *, request, config, route, args) -> None:
    """게이트에서 정지하기 전에 재개(--resume)에 필요한 상태를 저장한다."""
    checkpoint = {
        "version": 1,
        "stage": "awaiting_approval",
        "request": request,
        "workspace": str(config.workspace),
        "config_path": args.config,
        "route": route,
        "artifacts": {
            "context": "01_claude_context.md",
            "architecture": "02_claude_architecture.md",
            "validation": "03_codex_validation.md",
        },
        "max_review_rounds": args.max_review_rounds,
        "max_agent_calls": args.max_agent_calls,
    }
    write_json(run_dir / "checkpoint.json", checkpoint)


def approval_required(args: Namespace, route: dict[str, Any], request: str) -> bool:
    if route.get("task_type") not in {"backend", "frontend"}:
        return False
    return args.require_human_approval or is_high_risk(route, request)


def is_high_risk(route: dict[str, Any], request: str) -> bool:
    lowered = request.lower()
    return (
        route.get("risk_level") == "high"
        or route.get("subtype") == "db"
        or any(term in lowered for term in HIGH_RISK_REQUEST_TERMS)
    )


def stop_after(args: Namespace, run_dir: Path, stage: str) -> bool:
    if args.stop_after != stage:
        return False
    write_text(
        run_dir / "stopped_after.md",
        "# Stopped After Stage\n\n"
        f"The run stopped after `{stage}` as requested by --stop-after.\n",
    )
    print(f"Routed run stopped after {stage}: {run_dir}")
    return True


def missing_layers(route: dict[str, Any], implemented: list[str]) -> list[str]:
    """route.layers가 요구한 task_type 중 implemented에 없는 것들(요구 순서 보존)."""
    expected = [layer["task_type"] for layer in (route.get("layers") or [])]
    done = set(implemented)
    return [task_type for task_type in expected if task_type not in done]


def coverage_banner_md(route: dict[str, Any], implemented: list[str]) -> str:
    """final_report.md 상단에 prepend할 레이어 커버리지 배너(markdown). layers 없으면 빈 문자열."""
    expected = [layer["task_type"] for layer in (route.get("layers") or [])]
    if not expected:
        return ""
    missing = missing_layers(route, implemented)
    pct = round(len(implemented) / len(expected) * 100)
    status = "✅ 전 레이어 구현됨" if not missing else f"⚠ 미구현: {', '.join(missing)}"
    return (
        f"> **레이어 커버리지 {pct}%** — 요구: {', '.join(expected)} / "
        f"구현: {', '.join(implemented) or '없음'}. {status}\n\n"
    )


def coverage_gate(run_dir: Path, route: dict[str, Any], missing: list[str]) -> int:
    """미구현 레이어가 있을 때 forced 정지. coverage_status.json 기록 + stdout 핸드오프.

    승인/재개로 우회 불가한 forced 게이트 — 라우팅이 요구한 레이어가 실제로 안 돌았다는 건
    진짜 버그 신호이므로 사람이 입력/요청을 손봐야 한다(resume_command을 제공하지 않는다).
    """
    expected = [layer["task_type"] for layer in (route.get("layers") or [])]
    missing_set = set(missing)
    implemented = [task_type for task_type in expected if task_type not in missing_set]
    reason = f"라우팅이 요구한 레이어 중 미구현: {', '.join(missing)}. 조용한 누락 대신 게이트에서 정지."
    write_json(
        run_dir / "coverage_status.json",
        {
            "status": "blocked",
            "kind": "layer_coverage",
            "expected": expected,
            "implemented": implemented,
            "missing": missing,
            "reason": reason,
            "run_dir": str(run_dir),
        },
    )
    write_text(
        run_dir / "final_report.md",
        "# Layer Coverage Blocked\n\n"
        f"{reason}\n\n"
        f"- 요구 레이어: {', '.join(expected)}\n"
        f"- 구현됨: {', '.join(implemented) or '없음'}\n"
        f"- 미구현: {', '.join(missing)}\n",
    )
    print("ROUTED_STATUS: blocked_layer_coverage")
    print(f"RUN_DIR: {run_dir}")
    print(f"Routed run blocked on layer coverage: {run_dir}")
    return 0

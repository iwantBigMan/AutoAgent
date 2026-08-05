"""routed 워크플로우 오케스트레이션.

preamble(context/architecture/validation) -> 승인 게이트 -> 구현 라우트 순으로 진행한다.
게이트에서 checkpoint.json을 남기고 정지하며, resume_routed_workflow가 --resume로
preamble을 재실행하지 않고 구현 단계부터 이어받는다(사람의 승인 = --resume 실행).
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from autoagent.artifacts import read_text, write_json, write_text
from autoagent.config import Config
from autoagent.routing import route_task
from autoagent.runner import AgentCallBudget, AgentCallBudgetStopped
from autoagent.safety import git_baseline_status
from autoagent.workflows.routed_common import (
    approval_required,
    block_for_human_approval,
    block_implementation,
    write_checkpoint,
)
from autoagent.workflows.routed_docs import run_docs_route
from autoagent.workflows.routed_impl import run_implementation_route
from autoagent.workflows.routed_preamble import run_preamble


def run_routed_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    """라우팅 -> preamble -> (docs/read-only면 문서 라우트) -> 승인 게이트 -> 구현 라우트."""
    route = route_task(args.task_type, request, args.implementer)
    write_json(run_dir / "route.json", route)
    budget = AgentCallBudget(args.max_agent_calls)

    base_values = {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
        "TASK_TYPE": route["task_type"],
        "ROUTE_JSON": json.dumps(route, ensure_ascii=False, indent=2),
        "MAX_REVIEW_ROUNDS": str(max(args.max_review_rounds, 0)),
    }

    try:
        context, architecture, validation, verification_summary, stopped = run_preamble(
            args, config, base_values, route, budget, run_dir
        )
        if stopped:
            return 0

        common = {
            **base_values,
            "CLAUDE_CONTEXT": context,
            "CLAUDE_ARCHITECTURE": architecture,
            "CODEX_VALIDATION": validation,
            "VERIFICATION_SUMMARY": verification_summary,
        }

        if args.read_only or route["task_type"] in {"docs", "review"}:
            return run_docs_route(args, config, common, budget, run_dir)

        if approval_required(args, route, request):
            write_checkpoint(run_dir, request=request, config=config, route=route, args=args)
            return block_for_human_approval(run_dir, route)

        if not args.dry_run:
            ok, git_message = git_baseline_status(config.workspace)
            write_text(run_dir / "git_baseline_status.txt", git_message)
            if not ok:
                return block_implementation(run_dir, git_message)

        if route["layers"]:
            return run_implementation_route(args, config, common, route, request, budget, run_dir)
    except AgentCallBudgetStopped as stopped:
        print(f"Routed run stopped by budget before {stopped.next_step}: {run_dir}")
        return 0

    raise SystemExit(f"Unsupported routed task type: {route['task_type']}")


def resume_routed_workflow(args: Namespace, config: Config) -> int:
    """게이트에서 정지했던 run을 사람이 검토·승인한 뒤 구현 단계부터 이어간다.

    preamble을 다시 실행하지 않고 checkpoint.json + 저장된 계획 산출물을 읽어
    run_implementation_route로 진입한다. --resume 실행 자체가 사람의 승인 행위다.
    """
    run_dir = Path(args.resume)
    checkpoint_path = run_dir / "checkpoint.json"
    if not checkpoint_path.exists():
        raise SystemExit(f"No checkpoint.json in {run_dir}; cannot resume.")

    checkpoint = json.loads(read_text(checkpoint_path))
    if not args.workspace:
        config.workspace = Path(checkpoint["workspace"])
    if not config.workspace.exists():
        raise SystemExit(f"Workspace does not exist: {config.workspace}")

    route = checkpoint["route"]
    request = checkpoint["request"]
    artifacts = checkpoint.get("artifacts", {})
    context = read_text(run_dir / artifacts.get("context", "01_claude_context.md"))
    architecture = read_text(run_dir / artifacts.get("architecture", "02_claude_architecture.md"))
    validation = read_text(run_dir / artifacts.get("validation", "03_codex_validation.md"))

    base_values = {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
        "TASK_TYPE": route["task_type"],
        "ROUTE_JSON": json.dumps(route, ensure_ascii=False, indent=2),
        "MAX_REVIEW_ROUNDS": str(max(args.max_review_rounds, 0)),
    }
    common = {
        **base_values,
        "CLAUDE_CONTEXT": context,
        "CLAUDE_ARCHITECTURE": architecture,
        "CODEX_VALIDATION": validation,
    }
    budget = AgentCallBudget(args.max_agent_calls)

    if not args.dry_run:
        ok, git_message = git_baseline_status(config.workspace)
        write_text(run_dir / "git_baseline_status.txt", git_message)
        if not ok:
            return block_implementation(run_dir, git_message)

    write_json(
        run_dir / "approval_status.json",
        {
            "status": "approved",
            "approved": True,
            "required": True,
            "reason": "Resumed by human after reviewing plan artifacts.",
        },
    )
    print(f"Resuming routed run into implementation: {run_dir}")
    try:
        return run_implementation_route(args, config, common, route, request, budget, run_dir)
    except AgentCallBudgetStopped as stopped:
        print(f"Resumed run stopped by budget before {stopped.next_step}: {run_dir}")
        return 0

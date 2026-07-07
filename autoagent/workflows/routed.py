from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from autoagent.artifacts import write_json, write_text
from autoagent.config import Config
from autoagent.routing import route_task
from autoagent.runner import AgentCallBudget, AgentCallBudgetStopped
from autoagent.safety import git_baseline_status
from autoagent.workflows.routed_common import (
    approval_required,
    block_for_human_approval,
    block_implementation,
)
from autoagent.workflows.routed_docs import run_docs_route
from autoagent.workflows.routed_impl import run_implementation_route
from autoagent.workflows.routed_preamble import run_preamble


def run_routed_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
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
        context, architecture, validation, stopped = run_preamble(args, config, base_values, route, budget, run_dir)
        if stopped:
            return 0

        common = {
            **base_values,
            "CLAUDE_CONTEXT": context,
            "CLAUDE_ARCHITECTURE": architecture,
            "CODEX_VALIDATION": validation,
        }

        if args.read_only or route["task_type"] in {"docs", "review"}:
            return run_docs_route(args, config, common, budget, run_dir)

        if approval_required(args, route, request):
            return block_for_human_approval(run_dir, route)

        if not args.dry_run:
            ok, git_message = git_baseline_status(config.workspace)
            write_text(run_dir / "git_baseline_status.txt", git_message)
            if not ok:
                return block_implementation(run_dir, git_message)

        if route["task_type"] in {"backend", "frontend"}:
            return run_implementation_route(args, config, common, route, request, budget, run_dir)
    except AgentCallBudgetStopped as stopped:
        print(f"Routed run stopped by budget before {stopped.next_step}: {run_dir}")
        return 0

    raise SystemExit(f"Unsupported routed task type: {route['task_type']}")

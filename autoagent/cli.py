"""CLI 진입점(argparse) + 워크플로우 분기.

인자를 파싱하고 config를 로드한 뒤, --resume면 재개 경로로, 아니면 run 폴더를
만들고 simple/routed/decompose 워크플로우 중 하나로 분기한다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from autoagent.artifacts import DEFAULT_CONFIG, make_run_dir, read_text, write_metadata, write_text
from autoagent.config import load_config
from autoagent.workflows.decompose import run_decompose_workflow
from autoagent.workflows.routed import resume_routed_workflow, run_routed_workflow
from autoagent.workflows.simple import run_simple_workflow


def load_request(args: argparse.Namespace) -> str:
    """요청 텍스트를 --request-file > --request > stdin 순으로 읽는다. 없으면 종료."""
    if args.request_file:
        return read_text(Path(args.request_file))
    if args.request:
        return args.request
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide a request with --request, --request-file, or stdin.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude Code + Codex CLI local harness MVP")
    parser.add_argument("--request", help="Task request text")
    parser.add_argument("--request-file", help="Path to a markdown request file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config JSON")
    parser.add_argument("--project", help="Project registry name under projects/<name>/ (config + runs)")
    parser.add_argument("--workspace", help="Override target workspace path")
    parser.add_argument("--workflow", choices=["simple", "routed", "decompose"], default="simple", help="Workflow to run")
    parser.add_argument(
        "--task-type",
        choices=["auto", "backend", "frontend", "docs", "review"],
        default="auto",
        help="Task route for routed workflow",
    )
    parser.add_argument(
        "--implementer",
        choices=["auto", "claude", "codex"],
        default="auto",
        help="Implementation agent for routed implementation workflows",
    )
    parser.add_argument("--read-only", action="store_true", help="Run routed workflow without implementation steps")
    parser.add_argument("--max-review-rounds", type=int, default=1, help="Maximum fix rounds after a review")
    parser.add_argument("--max-agent-calls", type=int, default=0, help="Maximum Claude/Codex subprocess calls")
    parser.add_argument(
        "--stop-after",
        choices=[
            "none",
            "context",
            "architecture",
            "validation",
            "implementation",
            "review",
            "final-review",
            "evaluation",
            "report",
        ],
        default="none",
        help="Stop after a routed workflow stage completes",
    )
    parser.add_argument(
        "--require-human-approval",
        action="store_true",
        help="Stop before implementation until a human approves the run",
    )
    parser.add_argument("--plan-only", action="store_true", help="Run Claude planning only")
    parser.add_argument("--skip-review", action="store_true", help="Skip final Claude review")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts without calling CLIs")
    parser.add_argument(
        "--resume",
        help="Resume a gated routed run from its run directory; loads checkpoint.json and continues into implementation",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(Path(args.config), project=args.project)
    from autoagent.roles import load_roles, validate_roles
    validate_roles(load_roles(DEFAULT_CONFIG.parent), DEFAULT_CONFIG.parent)
    if args.workspace:
        config.workspace = Path(args.workspace)

    # --resume는 게이트에서 정지했던 run을 이어받아 구현 단계로 재개한다.
    # 새 요청을 받는 게 아니므로 --request/--request-file과 함께 쓸 수 없다.
    if args.resume:
        if args.request or args.request_file:
            raise SystemExit("--resume cannot be combined with --request/--request-file.")
        return resume_routed_workflow(args, config)

    if not config.workspace.exists():
        raise SystemExit(f"Workspace does not exist: {config.workspace}")

    request = load_request(args).strip()
    if not request:
        raise SystemExit("Request is empty.")

    run_dir = make_run_dir(project=args.project)
    write_text(run_dir / "00_request.md", request)
    write_metadata(
        run_dir,
        {
            "project": args.project,
            "workspace": str(config.workspace),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "workflow": args.workflow,
            "task_type": args.task_type,
            "implementer": args.implementer,
            "read_only": args.read_only,
            "max_review_rounds": args.max_review_rounds,
            "max_agent_calls": args.max_agent_calls,
            "stop_after": args.stop_after,
            "require_human_approval": args.require_human_approval,
            "plan_only": args.plan_only,
            "skip_review": args.skip_review,
            "dry_run": args.dry_run,
            "claude_model": config.claude_model,
            "claude_high_risk_model": config.claude_high_risk_model,
            "claude_effort": config.claude_effort,
            "claude_high_risk_effort": config.claude_high_risk_effort,
            "claude_impl_permission": config.claude_impl_permission,
            "codex_model": config.codex_model,
            "codex_reasoning_effort": config.codex_reasoning_effort,
        },
    )

    if args.workflow == "routed":
        return run_routed_workflow(args, config, request, run_dir)
    if args.workflow == "decompose":
        return run_decompose_workflow(args, config, request, run_dir)
    return run_simple_workflow(args, config, request, run_dir)

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from autoagent.artifacts import DEFAULT_CONFIG, make_run_dir, read_text, write_metadata, write_text
from autoagent.config import load_config
from autoagent.workflows.routed import run_routed_workflow
from autoagent.workflows.simple import run_simple_workflow


def load_request(args: argparse.Namespace) -> str:
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
    parser.add_argument("--workspace", help="Override target workspace path")
    parser.add_argument("--workflow", choices=["simple", "routed"], default="simple", help="Workflow to run")
    parser.add_argument(
        "--task-type",
        choices=["auto", "backend", "frontend", "docs", "review"],
        default="auto",
        help="Task route for routed workflow",
    )
    parser.add_argument("--read-only", action="store_true", help="Run routed workflow without implementation steps")
    parser.add_argument("--max-review-rounds", type=int, default=1, help="Maximum fix rounds after a review")
    parser.add_argument("--plan-only", action="store_true", help="Run Claude planning only")
    parser.add_argument("--skip-review", action="store_true", help="Skip final Claude review")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts without calling CLIs")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(Path(args.config))
    if args.workspace:
        config.workspace = Path(args.workspace)

    if not config.workspace.exists():
        raise SystemExit(f"Workspace does not exist: {config.workspace}")

    request = load_request(args).strip()
    if not request:
        raise SystemExit("Request is empty.")

    run_dir = make_run_dir()
    write_text(run_dir / "00_request.md", request)
    write_metadata(
        run_dir,
        {
            "workspace": str(config.workspace),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "workflow": args.workflow,
            "task_type": args.task_type,
            "read_only": args.read_only,
            "max_review_rounds": args.max_review_rounds,
            "plan_only": args.plan_only,
            "skip_review": args.skip_review,
            "dry_run": args.dry_run,
        },
    )

    if args.workflow == "routed":
        return run_routed_workflow(args, config, request, run_dir)
    return run_simple_workflow(args, config, request, run_dir)

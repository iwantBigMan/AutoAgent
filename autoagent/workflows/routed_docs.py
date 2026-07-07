from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import write_text
from autoagent.config import Config
from autoagent.runner import AgentCallBudget
from autoagent.workflows.routed_common import run_evaluation, run_final_report, stop_after


def run_docs_route(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
) -> int:
    evaluation = run_evaluation(
        args,
        config,
        common,
        budget,
        run_dir,
        name="04_codex_evaluation",
        implementation="No implementation step was run.",
        review="Read-only or docs/review route.",
        fix="No fix step was run.",
        final_review="No final code review step was run.",
    )
    if stop_after(args, run_dir, "evaluation"):
        return 0
    final = run_final_report(
        args,
        config,
        common,
        budget,
        run_dir,
        name="05_claude_final_report",
        implementation="No implementation step was run.",
        review="Read-only or docs/review route.",
        fix="No fix step was run.",
        final_review="No final code review step was run.",
        evaluation=evaluation,
    )
    write_text(run_dir / "final_report.md", final)
    stop_after(args, run_dir, "report")
    print(f"Routed run complete: {run_dir}")
    return 0

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from autoagent.artifacts import render_template, write_text
from autoagent.config import Config
from autoagent.runner import claude_command, codex_exec_command, require_command, run_process


def run_simple_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    values = {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
    }
    plan_prompt = render_template("plan.md", values)

    if args.dry_run:
        write_text(run_dir / "01_plan_prompt.md", plan_prompt)
        print(f"Dry run written to {run_dir}")
        return 0

    claude = require_command(config.claude_command)
    codex = require_command(config.codex_command)

    plan = run_process(
        name="01_claude_plan",
        command=claude_command(claude),
        prompt=plan_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / "01_plan.md", plan)

    if args.plan_only:
        print(f"Plan written to {run_dir}")
        return 0

    execute_prompt = render_template(
        "execute.md",
        {
            "REQUEST": request,
            "WORKSPACE": str(config.workspace),
            "PLAN": plan,
        },
    )
    codex_result = run_process(
        name="02_codex_execute",
        command=codex_exec_command(config, codex, config.codex_sandbox),
        prompt=execute_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / "02_codex_result.md", codex_result)

    if args.skip_review:
        print(f"Codex result written to {run_dir}")
        return 0

    review_prompt = render_template(
        "review.md",
        {
            "REQUEST": request,
            "WORKSPACE": str(config.workspace),
            "PLAN": plan,
            "CODEX_RESULT": codex_result,
        },
    )
    review = run_process(
        name="03_claude_review",
        command=claude_command(claude),
        prompt=review_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / "03_review.md", review)

    print(f"Run complete: {run_dir}")
    return 0

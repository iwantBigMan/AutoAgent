from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import render_template, write_text
from autoagent.config import Config
from autoagent.runner import AgentCallBudget, claude_command, codex_exec_command, require_command, run_process, write_command_artifact
from autoagent.safety import codex_sandbox_for
from autoagent.workflows.routed_common import architecture_model_for, stop_after


def run_preamble(
    args: Namespace,
    config: Config,
    base_values: dict[str, str],
    route: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
) -> tuple[str, str, str, bool]:
    context_prompt = render_template("claude_context.md", base_values)
    if args.dry_run:
        write_text(run_dir / "01_claude_context_prompt.md", context_prompt)
        write_command_artifact(
            run_dir,
            "01_claude_context",
            claude_command(config.claude_command, config.claude_model, "plan"),
        )
        context = "[dry-run: Claude context output]"
    else:
        claude = require_command(config.claude_command)
        budget.before_call(next_step="context", out_dir=run_dir, dry_run=args.dry_run)
        context = run_process(
            name="01_claude_context",
            command=claude_command(claude, config.claude_model, "plan"),
            prompt=context_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "01_claude_context.md", context)
    if stop_after(args, run_dir, "context"):
        return context, "", "", True

    architecture_model = architecture_model_for(config, route, base_values["REQUEST"])
    architecture_prompt = render_template(
        "claude_architect.md",
        {
            **base_values,
            "CLAUDE_CONTEXT": context,
        },
    )
    if args.dry_run:
        write_text(run_dir / "02_claude_architecture_prompt.md", architecture_prompt)
        write_command_artifact(
            run_dir,
            "02_claude_architecture",
            claude_command(config.claude_command, architecture_model, "plan"),
        )
        architecture = "[dry-run: Claude architecture output]"
    else:
        claude = require_command(config.claude_command)
        budget.before_call(next_step="architecture", out_dir=run_dir, dry_run=args.dry_run)
        architecture = run_process(
            name="02_claude_architecture",
            command=claude_command(claude, architecture_model, "plan"),
            prompt=architecture_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "02_claude_architecture.md", architecture)
    if stop_after(args, run_dir, "architecture"):
        return context, architecture, "", True

    validation_prompt = render_template(
        "codex_validation.md",
        {
            **base_values,
            "CLAUDE_CONTEXT": context,
            "CLAUDE_ARCHITECTURE": architecture,
        },
    )
    sandbox = codex_sandbox_for(args.read_only, config.codex_sandbox)
    if args.dry_run:
        write_text(run_dir / "03_codex_validation_prompt.md", validation_prompt)
        write_command_artifact(
            run_dir,
            "03_codex_validation",
            codex_exec_command(config, config.codex_command, sandbox),
        )
        validation = "[dry-run: Codex validation output]"
    else:
        codex = require_command(config.codex_command)
        budget.before_call(next_step="validation", out_dir=run_dir, dry_run=args.dry_run)
        validation = run_process(
            name="03_codex_validation",
            command=codex_exec_command(config, codex, sandbox),
            prompt=validation_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "03_codex_validation.md", validation)
    stopped = stop_after(args, run_dir, "validation")
    return context, architecture, validation, stopped

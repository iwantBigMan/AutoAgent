"""routed preamble(계획 단계).

context(claude) -> architecture(claude) <-> validation(codex) 반복. codex 검증이
통과하거나 max_review_rounds가 소진될 때까지 이전 검증 피드백을 반영해 architecture를
재작성하며 검증을 반복한다. high-risk면 architecture를 opus + effort xhigh로 작성.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import render_template, write_text
from autoagent.config import Config
from autoagent.runner import AgentCallBudget, claude_command, codex_exec_command, require_command, run_process, write_command_artifact
from autoagent.safety import codex_sandbox_for, review_needs_changes
from autoagent.workflows.routed_common import architecture_effort_for, architecture_model_for, stop_after


def run_preamble(
    args: Namespace,
    config: Config,
    base_values: dict[str, str],
    route: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
) -> tuple[str, str, str, bool]:
    """(context, architecture, validation, stopped)를 반환. stopped면 상위에서 조기 종료."""
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
    architecture_effort = architecture_effort_for(config, route, base_values["REQUEST"])
    sandbox = codex_sandbox_for(args.read_only, config.codex_sandbox)

    def run_architecture(name: str, prior_validation: str) -> str:
        prompt = render_template(
            "claude_architect.md",
            {
                **base_values,
                "CLAUDE_CONTEXT": context,
                "PRIOR_VALIDATION": prior_validation,
            },
        )
        if args.dry_run:
            write_text(run_dir / f"{name}_prompt.md", prompt)
            write_command_artifact(
                run_dir,
                name,
                claude_command(config.claude_command, architecture_model, "plan", architecture_effort),
            )
            return "[dry-run: Claude architecture output]"
        claude = require_command(config.claude_command)
        budget.before_call(next_step="architecture", out_dir=run_dir, dry_run=args.dry_run)
        result = run_process(
            name=name,
            command=claude_command(claude, architecture_model, "plan", architecture_effort),
            prompt=prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / f"{name}.md", result)
        return result

    def run_validation(name: str, architecture: str) -> str:
        prompt = render_template(
            "codex_validation.md",
            {
                **base_values,
                "CLAUDE_CONTEXT": context,
                "CLAUDE_ARCHITECTURE": architecture,
            },
        )
        if args.dry_run:
            write_text(run_dir / f"{name}_prompt.md", prompt)
            write_command_artifact(
                run_dir, name, codex_exec_command(config, config.codex_command, sandbox)
            )
            return "[dry-run: Codex validation output]"
        codex = require_command(config.codex_command)
        budget.before_call(next_step="validation", out_dir=run_dir, dry_run=args.dry_run)
        result = run_process(
            name=name,
            command=codex_exec_command(config, codex, sandbox),
            prompt=prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / f"{name}.md", result)
        return result

    # 첫 아키텍처(캐노니컬 파일명 유지 — 다운스트림/체크포인트가 참조).
    architecture = run_architecture("02_claude_architecture", "")
    if stop_after(args, run_dir, "architecture"):
        return context, architecture, "", True

    # architecture <-> validation을 max_review_rounds만큼 반복한다. 검증이 통과하면
    # 조기 종료하고, 소진되면 마지막 아키텍처를 그대로 넘긴다(spec 3.3). 계획 검증은
    # 최소 1회 수행한다(기존 동작 유지).
    rounds = max(args.max_review_rounds, 1)
    validation = ""
    for r in range(1, rounds + 1):
        validation = run_validation(f"03_codex_validation_r{r}", architecture)
        if not args.dry_run:
            # approval_required.md 등이 참조하는 캐노니컬 파일을 최신본으로 유지.
            write_text(run_dir / "03_codex_validation.md", validation)
        if not review_needs_changes(validation):
            break
        if r == rounds:
            break
        architecture = run_architecture(f"02_claude_architecture_r{r}", validation)
        if not args.dry_run:
            write_text(run_dir / "02_claude_architecture.md", architecture)

    stopped = stop_after(args, run_dir, "validation")
    return context, architecture, validation, stopped

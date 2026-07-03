from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import render_template, write_json, write_text
from autoagent.config import Config
from autoagent.routing import route_task
from autoagent.runner import claude_command, codex_exec_command, require_command, run_process
from autoagent.safety import codex_sandbox_for, git_baseline_status, review_needs_changes


def run_routed_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    route = route_task(args.task_type, request)
    write_json(run_dir / "route.json", route)

    base_values = {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
        "TASK_TYPE": route["task_type"],
        "ROUTE_JSON": json.dumps(route, ensure_ascii=False, indent=2),
        "MAX_REVIEW_ROUNDS": str(max(args.max_review_rounds, 0)),
    }

    context, architecture, validation = run_preamble(args, config, base_values, run_dir)
    common = {
        **base_values,
        "CLAUDE_CONTEXT": context,
        "CLAUDE_ARCHITECTURE": architecture,
        "CODEX_VALIDATION": validation,
    }

    if args.read_only or route["task_type"] in {"docs", "review"}:
        return run_docs_route(args, config, common, run_dir)

    if not args.dry_run:
        ok, git_message = git_baseline_status(config.workspace)
        write_text(run_dir / "git_baseline_status.txt", git_message)
        if not ok:
            return block_implementation(run_dir, git_message)

    if route["task_type"] == "backend":
        return run_backend_route(args, config, common, run_dir)
    if route["task_type"] == "frontend":
        return run_frontend_route(args, config, common, run_dir)

    raise SystemExit(f"Unsupported routed task type: {route['task_type']}")


def run_preamble(
    args: Namespace,
    config: Config,
    base_values: dict[str, str],
    run_dir: Path,
) -> tuple[str, str, str]:
    context_prompt = render_template("claude_context.md", base_values)
    if args.dry_run:
        write_text(run_dir / "01_claude_context_prompt.md", context_prompt)
        context = "[dry-run: Claude context output]"
    else:
        claude = require_command(config.claude_command)
        context = run_process(
            name="01_claude_context",
            command=claude_command(claude),
            prompt=context_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "01_claude_context.md", context)

    architecture_prompt = render_template(
        "claude_architect.md",
        {
            **base_values,
            "CLAUDE_CONTEXT": context,
        },
    )
    if args.dry_run:
        write_text(run_dir / "02_claude_architecture_prompt.md", architecture_prompt)
        architecture = "[dry-run: Claude architecture output]"
    else:
        claude = require_command(config.claude_command)
        architecture = run_process(
            name="02_claude_architecture",
            command=claude_command(claude),
            prompt=architecture_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "02_claude_architecture.md", architecture)

    validation_prompt = render_template(
        "codex_validation.md",
        {
            **base_values,
            "CLAUDE_CONTEXT": context,
            "CLAUDE_ARCHITECTURE": architecture,
        },
    )
    if args.dry_run:
        write_text(run_dir / "03_codex_validation_prompt.md", validation_prompt)
        validation = "[dry-run: Codex validation output]"
    else:
        codex = require_command(config.codex_command)
        sandbox = codex_sandbox_for(args.read_only, config.codex_sandbox)
        validation = run_process(
            name="03_codex_validation",
            command=codex_exec_command(config, codex, sandbox),
            prompt=validation_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "03_codex_validation.md", validation)

    return context, architecture, validation


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


def run_docs_route(args: Namespace, config: Config, common: dict[str, Any], run_dir: Path) -> int:
    evaluation = run_evaluation(
        args,
        config,
        common,
        run_dir,
        name="04_codex_evaluation",
        implementation="No implementation step was run.",
        review="Read-only or docs/review route.",
        fix="No fix step was run.",
        final_review="No final code review step was run.",
    )
    final = run_final_report(
        args,
        config,
        common,
        run_dir,
        name="05_claude_final_report",
        implementation="No implementation step was run.",
        review="Read-only or docs/review route.",
        fix="No fix step was run.",
        final_review="No final code review step was run.",
        evaluation=evaluation,
    )
    write_text(run_dir / "final_report.md", final)
    print(f"Routed run complete: {run_dir}")
    return 0


def run_backend_route(args: Namespace, config: Config, common: dict[str, Any], run_dir: Path) -> int:
    impl_prompt = render_template("claude_backend_impl.md", common)
    if args.dry_run:
        write_text(run_dir / "04_claude_backend_impl_prompt.md", impl_prompt)
        implementation = "[dry-run: Claude backend implementation output]"
    else:
        claude = require_command(config.claude_command)
        implementation = run_process(
            name="04_claude_backend_impl",
            command=claude_command(claude),
            prompt=impl_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "04_claude_backend_impl.md", implementation)

    review_prompt = render_template(
        "codex_backend_review.md",
        {**common, "IMPLEMENTATION_RESULT": implementation},
    )
    if args.dry_run:
        write_text(run_dir / "05_codex_backend_review_prompt.md", review_prompt)
        review = "[dry-run: Codex backend review output]"
    else:
        codex = require_command(config.codex_command)
        review = run_process(
            name="05_codex_backend_review",
            command=codex_exec_command(config, codex, config.codex_sandbox),
            prompt=review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "05_codex_backend_review.md", review)

    fix = "No fix step was run."
    if args.max_review_rounds > 0 and review_needs_changes(review):
        fix = run_backend_fix(args, config, common, implementation, review, run_dir)

    final_review_prompt = render_template(
        "codex_final.md",
        {
            **common,
            "IMPLEMENTATION_RESULT": implementation,
            "REVIEW_RESULT": review,
            "FIX_RESULT": fix,
        },
    )
    if args.dry_run:
        write_text(run_dir / "07_codex_final_review_prompt.md", final_review_prompt)
        final_review = "[dry-run: Codex final review output]"
    else:
        codex = require_command(config.codex_command)
        final_review = run_process(
            name="07_codex_final_review",
            command=codex_exec_command(config, codex, config.codex_sandbox),
            prompt=final_review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "07_codex_final_review.md", final_review)

    evaluation = run_evaluation(
        args,
        config,
        common,
        run_dir,
        name="08_codex_evaluation",
        implementation=implementation,
        review=review,
        fix=fix,
        final_review=final_review,
    )
    final = run_final_report(
        args,
        config,
        common,
        run_dir,
        name="09_claude_final_report",
        implementation=implementation,
        review=review,
        fix=fix,
        final_review=final_review,
        evaluation=evaluation,
    )
    write_text(run_dir / "final_report.md", final)
    print(f"Routed run complete: {run_dir}")
    return 0


def run_backend_fix(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    implementation: str,
    review: str,
    run_dir: Path,
) -> str:
    fix_prompt = render_template(
        "claude_backend_fix.md",
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review},
    )
    if args.dry_run:
        write_text(run_dir / "06_claude_backend_fix_prompt.md", fix_prompt)
        return "[dry-run: Claude backend fix output]"

    claude = require_command(config.claude_command)
    fix = run_process(
        name="06_claude_backend_fix",
        command=claude_command(claude),
        prompt=fix_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / "06_claude_backend_fix.md", fix)
    return fix


def run_frontend_route(args: Namespace, config: Config, common: dict[str, Any], run_dir: Path) -> int:
    impl_prompt = render_template("codex_frontend_impl.md", common)
    if args.dry_run:
        write_text(run_dir / "04_codex_frontend_impl_prompt.md", impl_prompt)
        implementation = "[dry-run: Codex frontend implementation output]"
    else:
        codex = require_command(config.codex_command)
        implementation = run_process(
            name="04_codex_frontend_impl",
            command=codex_exec_command(config, codex, config.codex_sandbox),
            prompt=impl_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "04_codex_frontend_impl.md", implementation)

    review_prompt = render_template(
        "claude_frontend_review.md",
        {**common, "IMPLEMENTATION_RESULT": implementation},
    )
    if args.dry_run:
        write_text(run_dir / "05_claude_frontend_review_prompt.md", review_prompt)
        review = "[dry-run: Claude frontend review output]"
    else:
        claude = require_command(config.claude_command)
        review = run_process(
            name="05_claude_frontend_review",
            command=claude_command(claude),
            prompt=review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "05_claude_frontend_review.md", review)

    fix = "No fix step was run."
    if args.max_review_rounds > 0 and review_needs_changes(review):
        fix = run_frontend_fix(args, config, common, implementation, review, run_dir)

    final_review_prompt = render_template(
        "claude_final.md",
        {
            **common,
            "IMPLEMENTATION_RESULT": implementation,
            "REVIEW_RESULT": review,
            "FIX_RESULT": fix,
            "FINAL_REVIEW_RESULT": "Claude final review for frontend route.",
            "FINAL_EVALUATION": "Evaluation has not run yet.",
        },
    )
    if args.dry_run:
        write_text(run_dir / "07_claude_final_review_prompt.md", final_review_prompt)
        final_review = "[dry-run: Claude final review output]"
    else:
        claude = require_command(config.claude_command)
        final_review = run_process(
            name="07_claude_final_review",
            command=claude_command(claude),
            prompt=final_review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "07_claude_final_review.md", final_review)

    evaluation = run_evaluation(
        args,
        config,
        common,
        run_dir,
        name="08_codex_evaluation",
        implementation=implementation,
        review=review,
        fix=fix,
        final_review=final_review,
    )
    final = run_final_report(
        args,
        config,
        common,
        run_dir,
        name="09_claude_final_report",
        implementation=implementation,
        review=review,
        fix=fix,
        final_review=final_review,
        evaluation=evaluation,
    )
    write_text(run_dir / "final_report.md", final)
    print(f"Routed run complete: {run_dir}")
    return 0


def run_frontend_fix(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    implementation: str,
    review: str,
    run_dir: Path,
) -> str:
    fix_prompt = render_template(
        "codex_frontend_fix.md",
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review},
    )
    if args.dry_run:
        write_text(run_dir / "06_codex_frontend_fix_prompt.md", fix_prompt)
        return "[dry-run: Codex frontend fix output]"

    codex = require_command(config.codex_command)
    fix = run_process(
        name="06_codex_frontend_fix",
        command=codex_exec_command(config, codex, config.codex_sandbox),
        prompt=fix_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / "06_codex_frontend_fix.md", fix)
    return fix


def run_evaluation(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    run_dir: Path,
    *,
    name: str,
    implementation: str,
    review: str,
    fix: str,
    final_review: str,
) -> str:
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
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", evaluation_prompt)
        evaluation = "[dry-run: Codex evaluation output]"
    else:
        codex = require_command(config.codex_command)
        sandbox = codex_sandbox_for(args.read_only, config.codex_sandbox)
        evaluation = run_process(
            name=name,
            command=codex_exec_command(config, codex, sandbox),
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
    run_dir: Path,
    *,
    name: str,
    implementation: str,
    review: str,
    fix: str,
    final_review: str,
    evaluation: str,
) -> str:
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
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", final_prompt)
        return "[dry-run: final report output]"

    claude = require_command(config.claude_command)
    return run_process(
        name=name,
        command=claude_command(claude),
        prompt=final_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )

"""routed 워크플로우 공용 헬퍼.

- 승인 게이트: approval_required(판정) / block_for_human_approval / write_checkpoint.
- 재개용 상태 저장(checkpoint.json), 구현 차단(block_implementation).
- 모델·effort 선택: architecture_model_for / architecture_effort_for.
- 종료 제어: stop_after. 평가·보고: run_evaluation / run_final_report.
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import render_template, write_json, write_text
from autoagent.config import Config
from autoagent.runner import AgentCallBudget, claude_command, codex_exec_command, require_command, run_process, write_command_artifact
from autoagent.safety import codex_sandbox_for


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


def block_for_human_approval(run_dir: Path, route: dict[str, Any]) -> int:
    reason = "High-risk implementation requires human approval before code changes."
    status = {
        "status": "waiting_for_human_approval",
        "approved": False,
        "required": True,
        "reason": reason,
    }
    write_json(run_dir / "approval_status.json", status)
    write_text(
        run_dir / "approval_required.md",
        "# Human Approval Required\n\n"
        f"{reason}\n\n"
        "Review these artifacts before running an implementation command:\n\n"
        "- 01_claude_context.md\n"
        "- 02_claude_architecture.md\n"
        "- 03_codex_validation.md\n"
        "- route.json\n\n"
        "Route:\n\n"
        f"```json\n{json.dumps(route, ensure_ascii=False, indent=2)}\n```\n",
    )
    write_text(
        run_dir / "final_report.md",
        "# Waiting for Human Approval\n\n"
        f"{reason}\n\n"
        "No implementation step was run.\n",
    )
    print(f"Routed run waiting for human approval: {run_dir}")
    return 0


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
    sandbox = codex_sandbox_for(args.read_only, config.codex_sandbox)
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", evaluation_prompt)
        write_command_artifact(run_dir, name, codex_exec_command(config, config.codex_command, sandbox))
        evaluation = "[dry-run: Codex evaluation output]"
    else:
        codex = require_command(config.codex_command)
        budget.before_call(next_step="evaluation", out_dir=run_dir, dry_run=args.dry_run)
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
        write_command_artifact(run_dir, name, claude_command(config.claude_command, config.claude_model, "plan"))
        return "[dry-run: final report output]"

    claude = require_command(config.claude_command)
    budget.before_call(next_step="report", out_dir=run_dir, dry_run=args.dry_run)
    return run_process(
        name=name,
        command=claude_command(claude, config.claude_model, "plan"),
        prompt=final_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )


def architecture_model_for(config: Config, route: dict[str, Any], request: str) -> str:
    if is_high_risk(route, request):
        return config.claude_high_risk_model
    return config.claude_model


def architecture_effort_for(config: Config, route: dict[str, Any], request: str) -> str:
    if is_high_risk(route, request):
        return config.claude_high_risk_effort
    return config.claude_effort


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

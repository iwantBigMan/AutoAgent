"""simple 워크플로우: Claude plan -> Codex execute -> Claude review.

라우팅·승인 게이트 없이 세 단계를 선형 실행한다. --plan-only/--skip-review로
중간에 멈출 수 있고, 예산 소진 시 안전 종료한다.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from autoagent.artifacts import render_template, write_text
from autoagent.config import Config
from autoagent.runner import (
    AgentCallBudget,
    AgentCallBudgetStopped,
    claude_command,
    codex_exec_command,
    require_command,
    run_process,
    solo_command,
    write_command_artifact,
    solo_cli,
)


def run_simple_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    budget = AgentCallBudget(args.max_agent_calls)
    values = {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
    }
    plan_prompt = render_template("plan.md", values)

    if args.dry_run:
        write_text(run_dir / "01_plan_prompt.md", plan_prompt)
        cmd01 = (
            solo_command(config, intent="plan", resolved_command=solo_cli(config))
            if config.solo_provider else
            claude_command(config.claude_command, config.claude_model, allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
        )
        write_command_artifact(run_dir, "01_claude_plan", cmd01)
        print(f"Dry run written to {run_dir}")
        return 0

    claude = require_command(config.claude_command)
    codex = require_command(config.codex_command)

    try:
        budget.before_call(next_step="plan", out_dir=run_dir, dry_run=args.dry_run)
        plan = run_process(
            name="01_claude_plan",
            command=(
                solo_command(config, intent="plan", resolved_command=require_command(solo_cli(config)))
                if config.solo_provider else
                claude_command(claude, config.claude_model, allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
            ),
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
        budget.before_call(next_step="execute", out_dir=run_dir, dry_run=args.dry_run)
        codex_result = run_process(
            name="02_codex_execute",
            command=(
                solo_command(config, intent="execute", resolved_command=require_command(solo_cli(config)))
                if config.solo_provider else
                codex_exec_command(config, codex, config.codex_sandbox)
            ),
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
        budget.before_call(next_step="review", out_dir=run_dir, dry_run=args.dry_run)
        review = run_process(
            name="03_claude_review",
            command=(
                solo_command(config, intent="review", resolved_command=require_command(solo_cli(config)))
                if config.solo_provider else
                claude_command(claude, config.claude_model, allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
            ),
            prompt=review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "03_review.md", review)
    except AgentCallBudgetStopped as stopped:
        print(f"Simple run stopped by budget before {stopped.next_step}: {run_dir}")
        return 0

    print(f"Run complete: {run_dir}")
    return 0

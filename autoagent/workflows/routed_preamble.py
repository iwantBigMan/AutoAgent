"""routed preamble(계획 단계).

context(claude) -> architecture(claude) <-> validation(codex) 반복. codex 검증이
통과하거나 max_review_rounds가 소진될 때까지 이전 검증 피드백을 반영해 architecture를
재작성하며 검증을 반복한다. high-risk면 architecture를 opus + effort xhigh로 작성.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import DEFAULT_CONFIG, render_template, write_text
from autoagent.config import Config
from autoagent.roles import load_roles, resolve_role
from autoagent.runner import AgentCallBudget, require_command, run_process, write_command_artifact
from autoagent.safety import review_needs_changes
from autoagent.verification import run_verification_or_skip
from autoagent.workflows.routed_common import stop_after


def run_preamble(
    args: Namespace,
    config: Config,
    base_values: dict[str, str],
    route: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
) -> tuple[str, str, str, str, bool]:
    """(context, architecture, validation, verification_summary, stopped)를 반환.

    stopped면 상위에서 조기 종료.
    """
    # 지연 import: routed_impl.command_for_agent와의 순환 import 방지(roles.py의 관례와 동일).
    from autoagent.workflows.routed_impl import command_for_agent

    roles = load_roles(DEFAULT_CONFIG.parent)
    request = base_values["REQUEST"]

    # review 서브타입일 때만 리뷰 산출/검증 프롬프트로 분기한다(docs/backend/frontend 불변).
    is_review = route["task_type"] == "review"
    arch_prompt_name = "claude_review_route.md" if is_review else "claude_architect.md"
    val_prompt_name = "codex_review_route.md" if is_review else "codex_validation.md"
    verification_summary = ""  # review가 아니거나 dry-run이면 빈 문자열로 남는다.

    context_role = resolve_role(
        roles["context"], config=config, route=route, request=request, agent="claude", read_only=args.read_only
    )
    context_prompt = render_template("claude_context.md", base_values)
    if args.dry_run:
        write_text(run_dir / "01_claude_context_prompt.md", context_prompt)
        write_command_artifact(run_dir, "01_claude_context", command_for_agent(config, context_role))
        context = "[dry-run: Claude context output]"
    else:
        claude = require_command(config.claude_command)
        budget.before_call(next_step="context", out_dir=run_dir, dry_run=args.dry_run)
        context = run_process(
            name="01_claude_context",
            command=command_for_agent(config, context_role, resolved_command=claude),
            prompt=context_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "01_claude_context.md", context)
    if stop_after(args, run_dir, "context"):
        return context, "", "", verification_summary, True

    # Q2-A: review 라우트는 리뷰 분석 앞단에서 하네스가 직접 검증을 돌려 실측 근거를 만든다.
    # 읽기전용이라 부작용 없음. dry-run/skip/비활성/미설정은 run_verification_or_skip이 처리.
    if is_review and not args.dry_run and not getattr(args, "skip_verification", False) and config.verification_enabled:
        verification_summary, _ok = run_verification_or_skip(run_dir=run_dir, config=config)

    architect_role = resolve_role(
        roles["architect"], config=config, route=route, request=request, agent="claude", read_only=args.read_only
    )
    validation_role = resolve_role(
        roles["validation"], config=config, route=route, request=request, agent="codex", read_only=args.read_only
    )

    def run_architecture(name: str, prior_validation: str) -> str:
        prompt = render_template(
            arch_prompt_name,
            {
                **base_values,
                "CLAUDE_CONTEXT": context,
                "PRIOR_VALIDATION": prior_validation,
                "VERIFICATION_SUMMARY": verification_summary,
            },
        )
        if args.dry_run:
            write_text(run_dir / f"{name}_prompt.md", prompt)
            write_command_artifact(run_dir, name, command_for_agent(config, architect_role))
            return "[dry-run: Claude architecture output]"
        claude = require_command(config.claude_command)
        budget.before_call(next_step="architecture", out_dir=run_dir, dry_run=args.dry_run)
        result = run_process(
            name=name,
            command=command_for_agent(config, architect_role, resolved_command=claude),
            prompt=prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / f"{name}.md", result)
        return result

    def run_validation(name: str, architecture: str) -> str:
        prompt = render_template(
            val_prompt_name,
            {
                **base_values,
                "CLAUDE_CONTEXT": context,
                "CLAUDE_ARCHITECTURE": architecture,
                "VERIFICATION_SUMMARY": verification_summary,
            },
        )
        if args.dry_run:
            write_text(run_dir / f"{name}_prompt.md", prompt)
            write_command_artifact(run_dir, name, command_for_agent(config, validation_role))
            return "[dry-run: Codex validation output]"
        codex = require_command(config.codex_command)
        budget.before_call(next_step="validation", out_dir=run_dir, dry_run=args.dry_run)
        result = run_process(
            name=name,
            command=command_for_agent(config, validation_role, resolved_command=codex),
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
        return context, architecture, "", verification_summary, True

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
    return context, architecture, validation, verification_summary, stopped

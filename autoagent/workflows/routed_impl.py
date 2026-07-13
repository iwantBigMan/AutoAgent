"""routed 구현 라우트.

구현(04) -> 리뷰-수정 반복(05/06, max_review_rounds, 통과 시 조기 종료) ->
최종리뷰(07) -> 평가(08) -> 최종보고(09). 리뷰어는 항상 구현자와 반대 모델이고,
high-risk backend를 claude(opus)로 구현/수정할 때는 effort xhigh를 쓴다.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import DEFAULT_CONFIG, render_template, write_text
from autoagent.config import Config
from autoagent.roles import ResolvedRole, load_roles, resolve_role
from autoagent.runner import AgentCallBudget, claude_command, codex_exec_command, require_command, run_process, write_command_artifact
from autoagent.safety import review_needs_changes
from autoagent.workflows.routed_common import run_evaluation, run_final_report, stop_after


def run_impl_review_fix(
    *,
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    route: dict[str, Any],
    request: str,
    budget: AgentCallBudget,
    run_dir: Path,
) -> tuple[str, str, str, bool, bool]:
    """구현(04) -> 리뷰/수정 반복(05/06)을 돌고 (implementation, review, fix, resolved, stopped)를 반환.

    stop_after가 implementation/review 단계에서 실제로 매치되면 그 시점 부분 상태와 stopped=True를,
    아니면 최종 상태와 stopped=False를 돌려준다. stopped_after.md 기록은 여기서 1회만 한다.
    """
    # 라우트가 정한 구현자/리뷰어(서로 반대 모델)로 구현 단계를 수행한다.
    task_type = route["task_type"]
    implementation_agent = route["implementation_agent"]
    review_agent = route["review_agent"]

    implementation = run_role_step(
        args=args,
        config=config,
        run_dir=run_dir,
        budget=budget,
        agent=implementation_agent,
        role_id="implementer",
        name=f"04_{implementation_agent}_{task_type}_impl",
        prompt_name=f"{implementation_agent}_{task_type}_impl.md",
        prompt_values=common,
        next_step="implementation",
        dry_output=f"[dry-run: {implementation_agent} {task_type} implementation output]",
        route=route,
        request=request,
        mutating=True,
    )
    # 원본 순서 보존: 리뷰/수정 기본값과 resolved(rounds==0)를 루프 전에 세팅.
    # 리뷰-수정을 max_review_rounds만큼 반복한다. 리뷰가 통과하면 조기 종료하고,
    # 소진되면 마지막 수정본을 재검증 없이 다음 단계로 넘긴다(spec 3.4).
    rounds = max(args.max_review_rounds, 0)
    current_impl = implementation
    review = "Review skipped (max_review_rounds=0)."
    fix = "No fix step was run."
    resolved = rounds == 0
    if stop_after(args, run_dir, "implementation"):
        return current_impl, review, fix, resolved, True

    for r in range(1, rounds + 1):
        review = run_role_step(
            args=args,
            config=config,
            run_dir=run_dir,
            budget=budget,
            agent=review_agent,
            role_id="reviewer",
            name=f"05_{review_agent}_{task_type}_review_r{r}",
            prompt_name=f"{review_agent}_{task_type}_review.md",
            prompt_values={**common, "IMPLEMENTATION_RESULT": current_impl},
            next_step="review",
            dry_output=f"[dry-run: {review_agent} {task_type} review output]",
            route=route,
            request=request,
            mutating=False,
        )
        if stop_after(args, run_dir, "review"):
            return current_impl, review, fix, resolved, True
        if not review_needs_changes(review):
            resolved = True
            break
        fix = run_role_step(
            args=args,
            config=config,
            run_dir=run_dir,
            budget=budget,
            agent=implementation_agent,
            role_id="fix",
            name=f"06_{implementation_agent}_{task_type}_fix_r{r}",
            prompt_name=f"{implementation_agent}_{task_type}_fix.md",
            prompt_values={**common, "IMPLEMENTATION_RESULT": current_impl, "REVIEW_RESULT": review},
            next_step="fix",
            dry_output=f"[dry-run: {implementation_agent} {task_type} fix output]",
            route=route,
            request=request,
            mutating=True,
        )
        current_impl = fix

    return current_impl, review, fix, resolved, False


def run_implementation_route(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    route: dict[str, Any],
    request: str,
    budget: AgentCallBudget,
    run_dir: Path,
) -> int:
    # 구현->리뷰/수정 코어를 헬퍼로 돌리고, 헬퍼가 실제로 정지했을 때만 꼬리를 건너뛴다.
    implementation, review, fix, resolved, stopped = run_impl_review_fix(
        args=args,
        config=config,
        common=common,
        route=route,
        request=request,
        budget=budget,
        run_dir=run_dir,
    )
    if stopped:
        return 0

    # 이후 최종리뷰/평가/보고는 최신 반영본 기준으로 진행한다.
    write_text(
        run_dir / "review_loop_status.md",
        f"resolved: {str(resolved).lower()}\n"
        f"rounds_configured: {max(args.max_review_rounds, 0)}\n",
    )

    # 최종리뷰(07)는 routed와 실행기가 공유하는 헬퍼로 수행한다(DRY). stop_after는 호출부에 둔다.
    final_review = run_final_review(
        args=args,
        config=config,
        common=common,
        route=route,
        request=request,
        budget=budget,
        run_dir=run_dir,
        implementation=implementation,
        review=review,
        fix=fix,
    )
    if stop_after(args, run_dir, "final-review"):
        return 0

    evaluation = run_evaluation(
        args,
        config,
        common,
        budget,
        run_dir,
        name="08_codex_evaluation",
        implementation=implementation,
        review=review,
        fix=fix,
        final_review=final_review,
    )
    if stop_after(args, run_dir, "evaluation"):
        return 0

    final = run_final_report(
        args,
        config,
        common,
        budget,
        run_dir,
        name="09_claude_final_report",
        implementation=implementation,
        review=review,
        fix=fix,
        final_review=final_review,
        evaluation=evaluation,
    )
    write_text(run_dir / "final_report.md", final)
    stop_after(args, run_dir, "report")
    print(f"Routed run complete: {run_dir}")
    return 0


def run_final_review(
    *,
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    route: dict[str, Any],
    request: str,
    budget: AgentCallBudget,
    run_dir: Path,
    implementation: str,
    review: str,
    fix: str,
    name: str = "07_codex_final_review",
) -> str:
    """codex 최종리뷰(07). dry-run이면 프롬프트/커맨드만 렌더하고 [dry-run] 문자열 반환.

    routed_impl의 기존 07 로직을 그대로 옮긴 것으로, routed와 실행기가 공유한다.
    바이트 패리티: name 기본값·프롬프트 값·resolve_role 인자가 원본과 동일해야 한다.
    """
    # final-review 역할은 sandbox="configured"라 read_only를 무시하고 config.codex_sandbox를
    # 그대로 쓴다(현행 버그를 의도적으로 보존 — 수정은 별도 계획에서 다룬다).
    roles = load_roles(DEFAULT_CONFIG.parent)
    final_review_role = resolve_role(
        roles["final-review"], config=config, route=route, request=request, agent="codex", read_only=args.read_only
    )
    final_review_prompt = render_template(
        "codex_final.md",
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review, "FIX_RESULT": fix},
    )
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", final_review_prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, final_review_role))
        return "[dry-run: Codex final review output]"
    codex = require_command(config.codex_command)
    budget.before_call(next_step="final-review", out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name,
        command=command_for_agent(config, final_review_role, resolved_command=codex),
        prompt=final_review_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / f"{name}.md", result)
    return result


def run_role_step(
    *,
    args: Namespace,
    config: Config,
    run_dir: Path,
    budget: AgentCallBudget,
    agent: str,
    role_id: str,
    name: str,
    prompt_name: str,
    prompt_values: dict[str, Any],
    next_step: str,
    dry_output: str,
    route: dict[str, Any],
    request: str,
    mutating: bool,
) -> str:
    # 역할 레지스트리에서 role_id 엔트리를 읽어 route/모델 정책에 따라 실행 속성으로 해석한다.
    roles = load_roles(DEFAULT_CONFIG.parent)
    entry = roles[role_id]
    resolved = resolve_role(
        entry,
        config=config,
        route=route,
        request=request,
        agent=agent,
        read_only=args.read_only,
    )

    prompt = render_template(prompt_name, prompt_values)
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, resolved))
        return dry_output

    command_name = require_command(config.claude_command if agent == "claude" else config.codex_command)
    budget.before_call(next_step=next_step, out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name,
        command=command_for_agent(config, resolved, resolved_command=command_name),
        prompt=prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / f"{name}.md", result)
    return result


def command_for_agent(
    config: Config,
    resolved: ResolvedRole,
    resolved_command: str | None = None,
) -> list[str]:
    """ResolvedRole 하나로 실행 커맨드를 조립하는 얇은 빌더(권한/샌드박스 계산은 resolve_role이 담당)."""
    if resolved.agent == "claude":
        return claude_command(
            resolved_command or config.claude_command,
            resolved.model,
            resolved.permission_mode,
            resolved.effort,
            skip_permissions=resolved.skip_permissions,
        )
    if resolved.agent == "codex":
        return codex_exec_command(config, resolved_command or config.codex_command, resolved.sandbox, resolved.model)
    raise SystemExit(f"Unsupported agent: {resolved.agent}")

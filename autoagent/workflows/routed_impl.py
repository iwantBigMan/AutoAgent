"""routed 구현 라우트.

구현(04) -> 리뷰-수정 반복(05/06, max_review_rounds, 통과 시 조기 종료) ->
최종리뷰(07) -> 평가(08) -> 최종보고(09). 리뷰어는 항상 구현자와 반대 모델이고(07 최종리뷰 포함),
high-risk backend 구현/수정은 codex의 deep 티어(effort high)로 수행한다.
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
from autoagent.verification import run_verification_or_skip
from autoagent.workflows.routed_common import run_evaluation, run_final_report, stop_after


def maybe_prepend_adversarial(prompt: str, config: Config, is_review: bool) -> str:
    """solo 모드의 리뷰 역할이면 적대 프리앰블을 프롬프트 앞에 붙인다(아니면 원본).

    교차모델 리뷰어 부재 시 같은 모델의 rubber-stamp를 막는다. research 검증 프롬프트는
    이미 적대적이라 여기 오지 않는다(routed/decompose 리뷰 역할 전용).
    """
    if not getattr(config, "solo_provider", None) or not is_review:
        return prompt
    preamble = render_template("_solo_adversarial_preamble.md", {})
    return preamble + "\n" + prompt


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

    # 1단계 검증 스테이지(구현/수정 뒤, 최종리뷰 전): DB-free 커맨드를 실제 실행하고
    # 그 결과를 최종리뷰/평가/보고가 볼 수 있도록 implementation 문자열에 덧붙인다.
    # 공유 프롬프트 템플릿은 건드리지 않아 동시 실행 중인 다른 런에 영향을 주지 않는다.
    implementation = _maybe_run_verification(args, config, run_dir, implementation)
    if stop_after(args, run_dir, "verification"):
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


def _maybe_run_verification(args: Namespace, config: Config, run_dir: Path, implementation: str) -> str:
    """1단계 검증 스테이지를 실행하고 요약을 implementation 뒤에 덧붙여 반환한다.

    dry-run/--skip-verification/verification_enabled=False면 실행하지 않고 원본을 그대로
    돌려준다. 검증 실패는 예외를 던지지 않고 요약(PASS/FAIL)만 남긴다 — 최종리뷰/평가/보고가
    실제 실행 결과를 근거로 삼게 하는 것이 목적이다(하드 중단은 사람 판단에 맡긴다).
    """
    if args.dry_run or getattr(args, "skip_verification", False) or not config.verification_enabled:
        return implementation
    # 미설정이면 default_commands로 폴백하지 않고 스킵(Q3-A). LD는 자기 config로 커맨드를 갖는다.
    summary, ok = run_verification_or_skip(run_dir=run_dir, config=config)
    print(f"Verification stage: {'PASS' if ok else 'FAIL'} ({run_dir})")
    return f"{implementation}\n\n---\n{summary}"


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
    name: str | None = None,
) -> str:
    """최종리뷰(07). 리뷰어는 구현자의 반대 모델(route["review_agent"])이다.

    codex 구현이면 claude가, claude 구현이면 codex가 최종리뷰를 맡는다. 산출 파일명도
    05/06처럼 에이전트를 반영한다. dry-run이면 프롬프트/커맨드만 렌더하고 [dry-run]
    문자열을 반환한다. routed_impl과 decompose 실행기(task_exec)가 공유한다.
    """
    # 리뷰어 = 구현자 반대편. 파일명은 05_{review_agent}/06_{impl}과 일관되게 review_agent 반영.
    review_agent = route["review_agent"]
    if name is None:
        name = f"07_{review_agent}_final_review"
    # final-review 역할은 sandbox="configured"라 read_only를 무시하고 config.codex_sandbox를
    # 그대로 쓴다(codex가 리뷰어일 때만 의미; 현행 동작 보존). claude 리뷰어면 mutating=false라
    # resolve_role이 permission_mode=plan을 부여한다.
    roles = load_roles(DEFAULT_CONFIG.parent)
    final_review_role = resolve_role(
        roles["final-review"], config=config, route=route, request=request, agent=review_agent, read_only=args.read_only
    )
    # claude 리뷰어면 대칭 프롬프트(claude_final_review.md)를, codex면 기존 codex_final.md를 쓴다.
    prompt_name = "claude_final_review.md" if review_agent == "claude" else "codex_final.md"
    final_review_prompt = render_template(
        prompt_name,
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review, "FIX_RESULT": fix},
    )
    # solo 모드: final-review도 적대 프리앰블 주입.
    final_review_prompt = maybe_prepend_adversarial(final_review_prompt, config, is_review=True)
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", final_review_prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, final_review_role))
        return f"[dry-run: {review_agent} final review output]"
    command_name = require_command(config.claude_command if review_agent == "claude" else config.codex_command)
    budget.before_call(next_step="final-review", out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name,
        command=command_for_agent(config, final_review_role, resolved_command=command_name),
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
    # solo 모드: 리뷰 역할이면 적대 프리앰블을 붙여 자기검증 rubber-stamp를 막는다.
    prompt = maybe_prepend_adversarial(prompt, config, is_review=(role_id == "reviewer"))
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
            allowed_tools=config.mcp_allowed_tools,
            mcp_config_path=config.mcp_config_path,
        )
    if resolved.agent == "codex":
        return codex_exec_command(
            config, resolved_command or config.codex_command, resolved.sandbox, resolved.model, resolved.effort
        )
    raise SystemExit(f"Unsupported agent: {resolved.agent}")

"""routed 구현 라우트.

구현(04) -> 리뷰-수정 반복(05/06, max_review_rounds, 통과 시 조기 종료) ->
최종리뷰(07) -> 평가(08) -> 최종보고(09). 리뷰어는 항상 구현자와 반대 모델이고,
high-risk backend를 claude(opus)로 구현/수정할 때는 effort xhigh를 쓴다.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import render_template, write_text
from autoagent.config import Config
from autoagent.runner import AgentCallBudget, claude_command, codex_exec_command, require_command, run_process, write_command_artifact
from autoagent.safety import review_needs_changes
from autoagent.workflows.routed_common import is_high_risk, run_evaluation, run_final_report, stop_after


def run_implementation_route(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    route: dict[str, Any],
    request: str,
    budget: AgentCallBudget,
    run_dir: Path,
) -> int:
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
        name=f"04_{implementation_agent}_{task_type}_impl",
        prompt_name=f"{implementation_agent}_{task_type}_impl.md",
        prompt_values=common,
        next_step="implementation",
        dry_output=f"[dry-run: {implementation_agent} {task_type} implementation output]",
        route=route,
        request=request,
        mutating=True,
    )
    if stop_after(args, run_dir, "implementation"):
        return 0

    # 리뷰-수정을 max_review_rounds만큼 반복한다. 리뷰가 통과하면 조기 종료하고,
    # 소진되면 마지막 수정본을 재검증 없이 다음 단계로 넘긴다(spec 3.4).
    rounds = max(args.max_review_rounds, 0)
    current_impl = implementation
    review = "Review skipped (max_review_rounds=0)."
    fix = "No fix step was run."
    resolved = rounds == 0
    for r in range(1, rounds + 1):
        review = run_role_step(
            args=args,
            config=config,
            run_dir=run_dir,
            budget=budget,
            agent=review_agent,
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
            return 0
        if not review_needs_changes(review):
            resolved = True
            break
        fix = run_role_step(
            args=args,
            config=config,
            run_dir=run_dir,
            budget=budget,
            agent=implementation_agent,
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

    # 이후 최종리뷰/평가/보고는 최신 반영본 기준으로 진행한다.
    implementation = current_impl
    write_text(
        run_dir / "review_loop_status.md",
        f"resolved: {str(resolved).lower()}\n"
        f"rounds_configured: {rounds}\n",
    )

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
        write_command_artifact(
            run_dir,
            "07_codex_final_review",
            codex_exec_command(config, config.codex_command, config.codex_sandbox),
        )
        final_review = "[dry-run: Codex final review output]"
    else:
        codex = require_command(config.codex_command)
        budget.before_call(next_step="final-review", out_dir=run_dir, dry_run=args.dry_run)
        final_review = run_process(
            name="07_codex_final_review",
            command=codex_exec_command(config, codex, config.codex_sandbox),
            prompt=final_review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "07_codex_final_review.md", final_review)
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


def run_role_step(
    *,
    args: Namespace,
    config: Config,
    run_dir: Path,
    budget: AgentCallBudget,
    agent: str,
    name: str,
    prompt_name: str,
    prompt_values: dict[str, Any],
    next_step: str,
    dry_output: str,
    route: dict[str, Any],
    request: str,
    mutating: bool,
) -> str:
    prompt = render_template(prompt_name, prompt_values)
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", prompt)
        write_command_artifact(
            run_dir,
            name,
            command_for_agent(
                config,
                agent,
                model_for_agent(config, agent, route, request, mutating),
                effort=effort_for_agent(config, agent, route, request, mutating),
                mutating=mutating,
            ),
        )
        return dry_output

    command_name = require_command(config.claude_command if agent == "claude" else config.codex_command)
    budget.before_call(next_step=next_step, out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name,
        command=command_for_agent(
            config,
            agent,
            model_for_agent(config, agent, route, request, mutating),
            effort=effort_for_agent(config, agent, route, request, mutating),
            resolved_command=command_name,
            mutating=mutating,
        ),
        prompt=prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / f"{name}.md", result)
    return result


def command_for_agent(
    config: Config,
    agent: str,
    model: str | None,
    effort: str | None = None,
    resolved_command: str | None = None,
    mutating: bool = True,
) -> list[str]:
    if agent == "claude":
        # 비mutating(리뷰 등)은 plan 모드로 읽기 전용. mutating(구현/수정)은 config의
        # claude_impl_permission posture를 따른다. 헤드리스엔 승인 TTY가 없어 최소한
        # acceptEdits라야 파일 편집이 실제로 적용된다(그렇지 않으면 전부 거부됨).
        if not mutating:
            return claude_command(resolved_command or config.claude_command, model, "plan", effort)
        if config.claude_impl_permission == "bypassPermissions":
            # 편집+명령+네트워크까지 자율(무샌드박스). Codex의 --ask-for-approval never에 대응하나
            # OS 샌드박스는 없으므로 명시 opt-in일 때만.
            return claude_command(resolved_command or config.claude_command, model, None, effort, skip_permissions=True)
        # 기본: acceptEdits — 파일 편집만 자동, bash/네트워크는 헤드리스에서 차단.
        return claude_command(resolved_command or config.claude_command, model, "acceptEdits", effort)
    if agent == "codex":
        return codex_exec_command(config, resolved_command or config.codex_command, config.codex_sandbox, model)
    raise SystemExit(f"Unsupported agent: {agent}")


def model_for_agent(
    config: Config,
    agent: str,
    route: dict[str, Any],
    request: str,
    mutating: bool,
) -> str | None:
    if agent == "codex":
        return config.codex_model
    if agent == "claude" and mutating and route.get("task_type") == "backend" and is_high_risk(route, request):
        return config.claude_high_risk_model
    if agent == "claude":
        return config.claude_model
    return None


def effort_for_agent(
    config: Config,
    agent: str,
    route: dict[str, Any],
    request: str,
    mutating: bool,
) -> str | None:
    # high-risk backend를 opus로 구현/수정할 때는 최고 추론 강도(xhigh = ultracode 상당)로.
    if agent != "claude":
        return None
    if mutating and route.get("task_type") == "backend" and is_high_risk(route, request):
        return config.claude_high_risk_effort
    return config.claude_effort

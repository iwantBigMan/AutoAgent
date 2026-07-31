"""리서치 워크플로 오케스트레이터(중첩 루프 엔진, 최소경로 슬라이스).

최소경로: preamble seed(Claude) → a 회사리서치(Claude) → crossmodel 검증(Codex) →
derive 도출(Claude) → crossmodel 검증(Codex) → standalone HTML 리포트(바탕화면).

안쪽 루프는 리서치→검증→보정을 최대 3회 돌고, 통과하면 resolved, 소진되면
exhausted_unverified를 **명시 반환**한다(silent pass-through 금지, 스펙 §8 F1).
매 전이는 research_state.json에 영속한다(재개용 골격). 바깥 루프는 이 슬라이스에서
1회 고정이고, 2회 심화 루프·seed pin·수렴 게이트는 Slice 4가 이 파일을 확장한다.
"""
from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from autoagent.artifacts import (
    DEFAULT_CONFIG,
    extract_json_block,
    read_text,
    render_template,
    write_json,
    write_text,
)
from autoagent.config import Config
from autoagent.research.adapters import verify
from autoagent.research.convergence import decide_outer_pass, diff_verified_claims
from autoagent.research.coverage import (
    coverage_summary, render_coverage_matrix_html, render_warning_banner_html,
)
from autoagent.research.gates import evaluate_gate, pause_at_gate, should_pause
from autoagent.research.html_report import render_report_html, write_desktop_report
from autoagent.research.seed_contract import (
    build_seed_pin, detect_seed_violations, seed_pin_from_dict, seed_pin_to_dict,
)
from autoagent.research.snapshots import save_snapshot, write_sources_manifest
from autoagent.research.state import (
    is_stage_done, load_or_init_state, persist_state, pin_seed, resume_point, set_stage_status, STAGE_ORDER,
)
from autoagent.research.types import StageId, StageResult, Verdict
from autoagent.roles import load_roles, resolve_role
from autoagent.routing import choose_researcher
from autoagent.runner import (
    AgentCallBudget,
    require_command,
    run_process,
    write_command_artifact,
)

# 이 슬라이스의 최소경로 스테이지 순서. b/c/d는 다음 슬라이스에서 채운다.
MINIMAL_PATH: list[StageId] = ["a", "derive"]
STAGE_ADAPTER = {"a": "crossmodel", "b": "crossmodel", "c": "data_quality", "d": "source_grounding", "derive": "crossmodel"}
STAGE_PROMPT = {"a": "a_researcher.md", "b": "b_market_researcher.md", "c": "c_codex_research.md", "d": "d_fact_report.md", "derive": "derive.md"}
# 스테이지별 검증기 프롬프트. 기본은 crossmodel_verifier.md, b는 전용 프롬프트.
# c(코드검증)·d(source_grounding)는 crossmodel 프롬프트를 쓰지 않으므로 매핑에서 제외한다.
STAGE_VERIFIER_PROMPT = {"a": "crossmodel_verifier.md", "b": "b_market_verifier.md", "derive": "crossmodel_verifier.md"}
INNER_MAX = 3  # 안쪽 루프 상한(안전밸브)


@dataclass
class ResearchContext:
    """run_stage_loop가 소비하는 실행 컨텍스트(오케스트레이터가 채워 전달)."""

    args: Namespace
    config: Config
    request: str
    run_dir: Path
    budget: AgentCallBudget
    seed_contract: str
    stage_outputs: dict[str, str] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    # 계층 예산(§6.4, Task 29). Slice 1~5 최소경로에선 None 허용(전역 budget만 사용).
    tiered: "TieredCallCap | None" = None


def _persist_state(ctx: "ResearchContext") -> None:
    """research_state.json을 매 전이마다 갱신한다(재개 골격)."""
    write_json(ctx.run_dir / "research_state.json", ctx.state)


def _run_agent_step(
    ctx: "ResearchContext",
    *,
    agent: str,
    role_id: str,
    name: str,
    prompt_name: str,
    prompt_values: dict[str, str],
    next_step: str,
    dry_output: str,
) -> str:
    """리서치 스텝 1회 실행(dry-run이면 프롬프트/커맨드만 렌더). routed의 run_role_step 축약판.

    command_for_agent는 순환 import 방지를 위해 지연 import한다(레포 관례).
    """
    from autoagent.workflows.routed_impl import command_for_agent

    args = ctx.args
    config = ctx.config
    run_dir = ctx.run_dir
    roles = load_roles(DEFAULT_CONFIG.parent)
    route = {"task_type": "research", "risk_level": "medium", "subtype": "research"}
    resolved = resolve_role(
        roles[role_id], config=config, route=route, request=ctx.request, agent=agent, read_only=args.read_only
    )
    prompt = render_template(prompt_name, prompt_values)
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, resolved))
        return dry_output

    command_name = require_command(config.claude_command if agent == "claude" else config.codex_command)
    ctx.budget.before_call(next_step=next_step, out_dir=run_dir, dry_run=args.dry_run)
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


def _seed_fields(ctx: "ResearchContext") -> dict[str, str]:
    """seed_pin dict를 프롬프트가 쓰는 5+1 필드(SEED_COMPANY 등)로 분해한다.

    b/d 리서처·검증기 프롬프트가 개별 seed 필드 placeholder를 쓴다(SEED_CONTRACT 통짜 아님).
    seed_pin이 아직 없으면(dry-run 초기) 빈 문자열로 채워 미치환 잔존을 막는다.
    """
    pin = ctx.state.get("seed_pin") or {}
    return {
        "SEED_COMPANY": str(pin.get("company", "")),
        "SEED_MARKET": str(pin.get("market", "")),
        "SEED_CURRENCY": str(pin.get("base_currency", "")),
        "SEED_PERIOD": str(pin.get("period", "")),
        "SEED_UNIT": str(pin.get("unit", "")),
        "SEED_AS_OF": str(pin.get("as_of", "")),
    }


def _prior_stage_summary(ctx: "ResearchContext") -> str:
    """d 프롬프트용 선행 스테이지 요약(이미 resolved된 스테이지 산출물 발췌)."""
    parts = [f"[{s}] {out[:800]}" for s, out in ctx.stage_outputs.items() if out]
    return "\n\n".join(parts) if parts else "(선행 스테이지 요약 없음)"


def _inject_verified_claims(verdict, researcher_out: str) -> None:
    """검증 통과 시 리서처 산출물의 claims(+seed_candidate)를 verdict.raw에 실제 주입한다.

    B2 배선: 바깥 심화 루프(collect_verified_claims·_extract_seed_candidate)와 seed drift
    검출은 verdict.raw['verified_claims']/['seed_candidate']를 읽는다. 그러나 검증기 JSON(raw)
    자체엔 그 키가 없다 — 여기서 *리서처* stdout을 파싱해 채워 넣어야 실런에서 pass 2 심화·
    seed 위반 검출이 동작한다(안 채우면 항상 delta=0 → pass 1 직후 조기종료로 심화가 죽는다).
    """
    try:
        parsed = extract_json_block(researcher_out)
    except Exception:  # noqa: BLE001 - 리서처 JSON 파싱 실패는 빈 claim으로 취급
        return
    verdict.raw["verified_claims"] = parsed.get("claims", []) or []
    if parsed.get("seed_candidate"):
        verdict.raw["seed_candidate"] = parsed["seed_candidate"]


def _run_stage_c_verify(ctx: "ResearchContext", researcher_out: str) -> Verdict:
    """c 검증 경로: 리서처 stdout의 DATA_QUALITY_OUTPUT JSON을 코드 검증기로 검증한다(모델 0회).

    c 스테이지는 검증기=코드(data_quality 어댑터)다. crossmodel 프롬프트/모델 호출을 타지 않고,
    리서처가 낸 cleaned_files/transform_manifest/derived_claims/schema_expectations/sanity_rules를
    그대로 verify로 넘겨 원본 CSV에서 독립 재계산한다. verifier_agent는 계약상 반대모델(claude)로
    넘기되 data_quality 어댑터는 모델을 실제로 부르지 않는다. 이 슬라이스(1)에선 c가 순회에 없어
    호출되지 않지만, Slice 2가 STAGE_PROMPT["c"]를 채우면 run_stage_loop의 c 분기가 이 함수를 탄다.
    """
    try:
        stage_out = extract_json_block(researcher_out)  # DATA_QUALITY_OUTPUT fenced JSON
    except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 빈 스켈레톤으로 진행
        stage_out = {"cleaned_files": [], "transform_manifest": {"steps": []},
                     "derived_claims": [], "schema_expectations": {}, "sanity_rules": {}}
    return verify(
        "data_quality", stage_out, ctx.run_dir,
        verifier_agent="claude", config=ctx.config,
    )


def _parse_stage_out(raw: str) -> dict[str, Any]:
    """리서처 stdout에서 fenced JSON stage_out을 뽑는다(실패 시 빈 스켈레톤)."""
    try:
        return extract_json_block(raw)
    except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 최소 스켈레톤으로 진행
        return {"stage_id": "d", "claims": [], "sources": [], "report_md": raw[:2000]}


def _run_stage_d_verify(ctx: "ResearchContext", researcher_out: str, stage: str, outer_pass: int, inner: int):
    """d 검증 경로: 스냅샷 저장 → Codex 검증기 렌더/실행 → source_grounding verify.

    dry-run이면 검증기 stdout은 빈 문자열이고 결정론 검사만으로 verify가 돈다(모델 미호출).
    """
    stage_out = _parse_stage_out(researcher_out)
    # 리서처가 fetch한 sources[].fetched_text를 runs/sources/*.txt 스냅샷으로 고정.
    sources_dir = ctx.run_dir / "sources"
    snaps = []
    for s in stage_out.get("sources", []):
        snaps.append(save_snapshot(
            sources_dir, s.get("ref_id") or "s?", s.get("url") or "", s.get("fetched_text") or "",
            http_status=int(s.get("http_status") or 0), fetch_ts=s.get("fetch_ts"),
        ))
    write_sources_manifest(ctx.run_dir, snaps)

    # Codex 검증기(스냅샷만) 렌더/실행.
    import json as _json
    verifier_out = _run_agent_step(
        ctx, agent="codex", role_id="verifier",
        name=f"stage_{stage}_p{outer_pass}_r{inner}_verifier",
        prompt_name="d_grounding_verify.md",
        prompt_values={
            "REPORT_MD": stage_out.get("report_md", ""),
            "CLAIMS_JSON": _json.dumps(stage_out.get("claims", []), ensure_ascii=False),
            "SOURCES_SNAPSHOTS_JSON": _json.dumps(stage_out.get("sources", []), ensure_ascii=False),
        },
        next_step=f"verify:{stage}",
        dry_output="",  # dry-run: 모델 없이 결정론 검사만
    )
    return verify(
        "source_grounding", {**stage_out, "model_raw_text": verifier_out},
        ctx.run_dir, verifier_agent="codex", config=ctx.config,
    )


def run_stage_loop(stage: StageId, outer_pass: int, ctx: ResearchContext) -> StageResult:
    """안쪽 루프: 리서치→검증→보정 최대 3회. 통과=resolved, 소진=exhausted_unverified.

    silent pass-through 금지: 검증을 못 넘긴 채 상한에 도달하면 exhausted_unverified를
    명시 반환하고 상태에 기록한다(스펙 §8 F1). blocked verdict면 즉시 blocked 반환.
    """
    researcher, verifier, _reason = choose_researcher(stage)

    prior_feedback = ""
    last_verdict = None
    inner = 0
    for inner in range(1, INNER_MAX + 1):
        ctx.state.update({"outer_pass": outer_pass, "stage": stage, "inner_round": inner})
        _persist_state(ctx)

        # 스테이지별 값 dict. seed 5필드·MIN_FINDINGS·CSV 경로 등을 스테이지에 맞춰 채운다.
        values = {
            "REQUEST": ctx.request,
            "WORKSPACE": str(ctx.config.workspace),
            "SEED_CONTRACT": ctx.seed_contract,
            "SEED_PIN": json.dumps(ctx.state.get("seed_pin") or {}, ensure_ascii=False),
            "STAGE_ID": stage,
            "OUTER_PASS": str(outer_pass),
            "INNER_ROUND": str(inner),
            "PRIOR_FEEDBACK": prior_feedback,
            "INNER_FEEDBACK": prior_feedback,   # b 프롬프트 명칭
            "DEEPEN_DELTA": prior_feedback,     # pass 2 심화 delta(피드백 없으면 빈 값)
            "PRIOR_VERDICT_FEEDBACK": prior_feedback,       # d 프롬프트 명칭(CF-1)
            "PRIOR_STAGE_SUMMARY": _prior_stage_summary(ctx),  # d 프롬프트 명칭(CF-1)
            "STAGE_A_OUTPUT": ctx.stage_outputs.get("a", ""),
            # c 리서처(codex)용 CSV 경로. config에 있으면 그 값을, 없으면 워크스페이스 안내.
            "CSV_PATHS": getattr(ctx.config, "research_csv_paths", "") or "(워크스페이스의 입력 CSV)",
            # crossmodel 검증기의 최소 findings 쿼터(config crossmodel_min_findings, 기본 3).
            "MIN_FINDINGS": str(getattr(ctx.config, "crossmodel_min_findings", 3)),
        }
        values.update(_seed_fields(ctx))  # SEED_COMPANY/MARKET/CURRENCY/PERIOD/UNIT/AS_OF 분해 주입
        researcher_out = _run_agent_step(
            ctx, agent=researcher, role_id="researcher",
            name=f"stage_{stage}_p{outer_pass}_r{inner}_researcher",
            prompt_name=STAGE_PROMPT[stage], prompt_values=values,
            next_step=f"research:{stage}",
            dry_output=f"[dry-run: {researcher} {stage} researcher output]",
        )

        if stage == "c":
            # c: 리서처 stdout(DATA_QUALITY_OUTPUT)을 코드 검증기로 검증(모델 0회).
            verdict = _run_stage_c_verify(ctx, researcher_out)
        elif stage == "d":
            # d: 리서처 JSON 파싱 → 스냅샷 저장 → Codex 검증기(스냅샷만) → source_grounding verify.
            verdict = _run_stage_d_verify(ctx, researcher_out, stage, outer_pass, inner)
        else:
            # 스테이지별 검증기 프롬프트(b는 전용 b_market_verifier.md, 그 외 crossmodel).
            verifier_out = _run_agent_step(
                ctx, agent=verifier, role_id="verifier",
                name=f"stage_{stage}_p{outer_pass}_r{inner}_verifier",
                prompt_name=STAGE_VERIFIER_PROMPT.get(stage, "crossmodel_verifier.md"),
                prompt_values={
                    **values,  # seed 5필드·MIN_FINDINGS를 검증기 프롬프트에도 넘긴다(b_market_verifier 등)
                    "STAGE_ID": stage,
                    "RESEARCHER_OUTPUT": researcher_out,
                    "STAGE_OUTPUT_JSON": researcher_out,  # b 검증기 명칭
                },
                next_step=f"verify:{stage}",
                dry_output=(
                    # dry-run은 코드 재계산 경로를 타게 하려고 유효 verdict를 흉내낸다.
                    # unchallenged_but_weak를 채워 §4.1② 최소 findings 쿼터를 만족(dry-run pass 유지).
                    f"CROSSMODEL_VERDICT: pass\n```json\n"
                    f'{{"adapter":"crossmodel","stage_id":"{stage}","verdict":"pass",'
                    f'"findings":[],"coverage":{{"axes_checked":["support"],"axes_missing":[]}},'
                    f'"unchallenged_but_weak":["dry-run"],"tokens_seen":0}}\n```\n'
                ),
            )
            verdict = verify(
                STAGE_ADAPTER[stage], {"stage_id": stage, "verifier_raw_text": verifier_out},
                ctx.run_dir, verifier_agent=verifier, config=ctx.config,
            )
        last_verdict = verdict
        ctx.state.setdefault("stage_status", {})[stage] = verdict.status
        _persist_state(ctx)

        if verdict.status == "pass":
            _inject_verified_claims(verdict, researcher_out)  # B2: 리서처 claims→verdict.raw 실주입
            ctx.stage_outputs[stage] = researcher_out
            return StageResult(
                stage_id=stage, status="resolved",
                output_path=f"stage_{stage}_p{outer_pass}_r{inner}_researcher.md",
                verdict=verdict, inner_rounds=inner,
            )
        if verdict.status == "blocked":
            ctx.stage_outputs[stage] = researcher_out
            return StageResult(
                stage_id=stage, status="blocked",
                output_path=f"stage_{stage}_p{outer_pass}_r{inner}_researcher.md",
                verdict=verdict, inner_rounds=inner,
            )
        prior_feedback = "\n".join(f"- [{f.severity}] {f.category}: {f.fix_directive}" for f in verdict.findings)

    # 상한 도달, 미통과 → silent pass-through 금지: 명시적으로 미검증 표기.
    ctx.stage_outputs[stage] = ctx.stage_outputs.get(stage, "")
    return StageResult(
        stage_id=stage, status="exhausted_unverified",
        output_path=f"stage_{stage}_p{outer_pass}_r{inner}_researcher.md",
        verdict=last_verdict, inner_rounds=inner,
    )


def _coverage_matrix_md(results: list[StageResult]) -> str:
    """스테이지별 verify_status 표(상단 강제). 100% 미만이면 경고 배너 문구를 앞에 붙인다."""
    status_map = {"resolved": "passed", "exhausted_unverified": "exhausted_unverified", "blocked": "blocked"}
    rows = "\n".join(f"| {r.stage_id} | {status_map.get(r.status, r.status)} |" for r in results)
    table = "| stage | verify_status |\n| --- | --- |\n" + rows
    all_passed = all(r.status == "resolved" for r in results)
    banner = "" if all_passed else "**경고: 일부 스테이지가 검증을 통과하지 못했습니다(UNVERIFIED).**\n\n"
    return banner + table


# 리포트 커버리지 표에 쓸 스테이지 한글 라벨.
STAGE_LABELS = {"a": "회사 리서치", "b": "시장 분석", "c": "CSV 정제", "d": "팩트 리포트", "derive": "도출"}
# 바깥 루프 상한(스펙 §1: 심화 2회). config에 값이 있으면 그것을 우선한다.
DEFAULT_MAX_OUTER = 2
DEFAULT_MIN_NEW_CLAIMS = 2


def run_research_workflow(args: Namespace, config: Config, request: str | None, run_dir: Path) -> int:
    """리서치 워크플로 진입점(전체 파이프라인 + 바깥 루프 + 게이트 + 재개 + 커버리지).

    seed 확정·pin → 바깥 pass 1..N(스테이지 a..derive를 안쪽 루프로) → 스테이지 경계·심화
    진입 게이트 → 커버리지 매트릭스+배너를 상단에 박은 standalone HTML을 바탕화면에 저장한다.
    request=None은 --resume 진입(저장된 seed/상태에서 복원). dry-run이면 CLI 미호출.
    """
    budget = AgentCallBudget(args.max_agent_calls)
    state = load_or_init_state(run_dir)
    max_outer = getattr(config, "research_max_outer", DEFAULT_MAX_OUTER)
    min_new_claims = getattr(config, "research_min_new_claims", DEFAULT_MIN_NEW_CLAIMS)
    auto_nonbranch = getattr(args, "auto_approve_nonbranch", False)

    ctx = ResearchContext(
        args=args, config=config, request=request or "", run_dir=run_dir, budget=budget, seed_contract="",
        state=state,
    )

    # preamble: seed 확정 후 read-only pin(재개면 기존 pin 재사용, seed 스텝 스킵).
    if not state.get("seed_pin"):
        seed_out = _run_agent_step(
            ctx, agent="claude", role_id="researcher", name="00_seed_contract",
            prompt_name="seed_contract.md",
            prompt_values={"REQUEST": ctx.request, "WORKSPACE": str(config.workspace)},
            next_step="seed",
            dry_output='SEED_CONTRACT_JSON\n```json\n{"company":"[dry-run]","market":"[dry-run]",'
                       '"base_currency":"KRW","period":"2021-2025","unit":"억원"}\n```\n',
        )
        ctx.seed_contract = seed_out
        try:
            pin_seed(run_dir, state, extract_json_block(seed_out))
        except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 최소경로는 진행
            pin_seed(run_dir, state, {"company": "[dry-run]", "market": "-",
                                      "base_currency": "KRW", "period": "-", "unit": "-"})
    else:
        ctx.seed_contract = json.dumps(state["seed_pin"], ensure_ascii=False)

    resume_outer, _resume_stage, _resume_inner = resume_point(state)
    prev_claims: list[dict] = state.get("verified_claims", [])

    # M3: 루프가 한 번도 안 돌아도(예: max_outer=0) 최종 리포트에서 항상 바인딩되도록 선초기화.
    stage_results: list[StageResult] = []
    for outer_pass in range(resume_outer, max_outer + 1):
        state["outer_pass"] = outer_pass
        persist_state(run_dir, state)

        # 고비용 심화 진입 게이트(pass 2+, forced).
        deepen_trigger = evaluate_gate(event="deepen_entry", outer_pass=outer_pass,
                                       stage_results=[], contradiction=False, config=config)
        if should_pause(deepen_trigger, auto_approve_nonbranch=auto_nonbranch):
            return pause_at_gate(run_dir, deepen_trigger, state)

        stage_results = []  # 이 pass의 스테이지 결과(위에서 선초기화한 변수를 pass마다 재설정)
        for stage in STAGE_ORDER:
            state["stage"] = stage
            if is_stage_done(state, stage):
                continue  # 재개 시 resolved 스테이지 건너뜀
            result = run_stage_loop(stage, outer_pass, ctx)
            stage_results.append(result)
            set_stage_status(run_dir, state, stage, result.status)

            # 스테이지 경계 게이트(blocked·exhausted 다수).
            boundary = evaluate_gate(event="stage_boundary", outer_pass=outer_pass,
                                     stage_results=stage_results, contradiction=False, config=config)
            if should_pause(boundary, auto_approve_nonbranch=auto_nonbranch):
                return pause_at_gate(run_dir, boundary, state)

        # pass간 검증 claim 수집·delta·수렴/모순 판정.
        curr_claims = collect_verified_claims(stage_results)
        delta = diff_verified_claims(prev_claims, curr_claims)
        seed_violations = []
        if outer_pass > 1:
            seed_violations = detect_seed_violations(
                seed_pin_from_dict(state["seed_pin"]), _extract_seed_candidate(stage_results)
            )
        decision = decide_outer_pass(delta, seed_violations, outer_pass=outer_pass,
                                     max_outer=max_outer, min_new_claims=min_new_claims)
        state["verified_claims"] = prev_claims + delta.added
        state["outer_decision"] = {"action": decision.action, "reason": decision.reason,
                                   "contradictions": decision.contradictions}
        persist_state(run_dir, state)

        if decision.action == "gate":
            # 모순/seed위반 = forced 게이트(절대 생략 안 함).
            trigger = evaluate_gate(event="stage_boundary", outer_pass=outer_pass,
                                    stage_results=stage_results, contradiction=True, config=config)
            if trigger is not None:
                return pause_at_gate(run_dir, trigger, state)
        if decision.action in {"early_stop", "gate"}:
            break
        prev_claims = state["verified_claims"]

    # 커버리지 매트릭스+배너를 상단에 박은 최종 리포트.
    # M1 stage_status 키 규약 통일: set_stage_status(Task 25)가 평면 키(stage→status)로 쓰므로
    # 리포트도 평면 키만 읽는다(outer 프리픽스 조회 제거 — run_outer_loop의 "{outer}:{stage}" 규약은
    # 이 오케스트레이터에서 쓰지 않는다).
    stage_status_for_report = {s: state["stage_status"].get(s, "missing") for s in STAGE_ORDER}
    summary = coverage_summary(stage_status_for_report, STAGE_ORDER)
    matrix_html = render_coverage_matrix_html(stage_status_for_report, STAGE_ORDER, stage_labels=STAGE_LABELS)
    banner_html = render_warning_banner_html(summary)
    body_md = render_template("final_html_report.md", {
        "COVERAGE_BANNER": banner_html, "COVERAGE_MATRIX": matrix_html,
        "COVERAGE_MATRIX_MD": _coverage_matrix_md(stage_results),  # M3: 루프 앞 선초기화라 항상 바인딩됨
        "REQUEST": ctx.request, "SEED_CONTRACT": ctx.seed_contract,
        "STAGE_A_OUTPUT": ctx.stage_outputs.get("a", "(없음)"),
        "DERIVE_OUTPUT": ctx.stage_outputs.get("derive", "(없음)"),
    })
    html = render_report_html(title="리서치 리포트", body_md=body_md)
    write_text(run_dir / "final_report.html", html)
    if args.dry_run:
        print(f"Research dry run written to {run_dir}")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop_path = write_desktop_report(html, f"research_report_{stamp}.html")
    try:
        import os
        os.startfile(str(desktop_path))
    except Exception:  # noqa: BLE001
        pass
    print(f"Research run complete: {run_dir}\nReport: {desktop_path}")
    return 0


# --- Slice 4 배선: 바깥 루프 · seed 계약 · 수렴 게이트 (스펙 §5·§1) ---


def persist_research_state(run_dir: Path, state: dict) -> None:
    """매 전이마다 research_state.json을 다시 써 재개 가능하게 한다(task_exec.persist_status 패턴)."""
    write_json(run_dir / "research_state.json", state)


def load_research_state(run_dir: Path) -> dict | None:
    """재개 진입점: 이전 research_state.json이 있으면 읽어 반환, 없으면 None."""
    path = run_dir / "research_state.json"
    if not path.exists():
        return None
    return json.loads(read_text(path))


def collect_verified_claims(stage_results: list[StageResult]) -> list[dict]:
    """resolved 스테이지의 verdict에서 검증된 claim만 모은다.

    exhausted_unverified·blocked 스테이지의 claim은 제외한다(F1 silent pass-through 격리).
    verdict.raw['verified_claims']를 표준 소스로 본다.
    """
    claims: list[dict] = []
    for r in stage_results:
        if r.status != "resolved" or r.verdict is None:
            continue  # F1: 미검증/차단 스테이지는 delta 계산에서 배제
        claims.extend(r.verdict.raw.get("verified_claims", []) or [])
    return claims


def _extract_seed_candidate(stage_results: list[StageResult]) -> dict:
    """pass 산출물이 주장하는 canonical 값 후보를 모은다(seed 위반 검사용)."""
    candidate: dict = {}
    for r in stage_results:
        if r.verdict is None:
            continue
        candidate.update(r.verdict.raw.get("seed_candidate") or {})
    return candidate


def run_outer_loop(ctx, *, run_stage=None) -> dict:
    """바깥 심화 루프(최대 max_outer). preamble에서 seed pin을 굳히고, pass마다 스테이지
    루프→검증 claim 수집→pass간 diff→수렴/모순 판정을 하고 매 전이 research_state.json에
    영속한다. run_stage는 테스트 주입용(기본은 run_stage_loop).
    """
    if run_stage is None:
        run_stage = run_stage_loop

    existing = load_research_state(ctx.run_dir)
    if existing and existing.get("seed_pin"):
        seed_pin = seed_pin_from_dict(existing["seed_pin"])
    else:
        seed_pin = build_seed_pin(ctx.seed_raw)

    state = {
        "outer_pass": 0, "stage": None, "inner_round": 0,
        "seed_pin": seed_pin_to_dict(seed_pin),
        "verified_claims": (existing or {}).get("verified_claims", []),
        "stage_status": {}, "outer_decision": None,
    }
    persist_research_state(ctx.run_dir, state)

    prev_claims: list[dict] = state["verified_claims"]
    for outer_pass in range(1, ctx.max_outer + 1):
        state["outer_pass"] = outer_pass
        stage_results: list[StageResult] = []
        for stage in ctx.stages:
            state["stage"] = stage
            result = run_stage(stage, outer_pass, ctx)
            stage_results.append(result)
            state["stage_status"][f"{outer_pass}:{stage}"] = result.status
            state["inner_round"] = result.inner_rounds
            persist_research_state(ctx.run_dir, state)  # 매 전이 영속(§6.3)

        seed_violations = []
        if outer_pass > 1:
            seed_violations = detect_seed_violations(seed_pin, _extract_seed_candidate(stage_results))

        curr_claims = collect_verified_claims(stage_results)
        delta = diff_verified_claims(prev_claims, curr_claims)
        decision = decide_outer_pass(
            delta, seed_violations, outer_pass=outer_pass, max_outer=ctx.max_outer,
            min_new_claims=ctx.min_new_claims,
        )
        state["verified_claims"] = prev_claims + delta.added  # 모순/미검증은 누적 안 함
        state["outer_decision"] = {
            "action": decision.action, "reason": decision.reason, "contradictions": decision.contradictions,
        }
        persist_research_state(ctx.run_dir, state)

        if decision.action in {"early_stop", "gate"}:
            break  # 수렴 조기종료 또는 모순/seed위반 게이트 승격 — silent 진행 금지
        prev_claims = state["verified_claims"]

    return state

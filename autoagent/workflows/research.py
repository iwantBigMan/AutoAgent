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
    render_template,
    write_json,
    write_text,
)
from autoagent.config import Config
from autoagent.research.adapters import verify
from autoagent.research.html_report import render_report_html, write_desktop_report
from autoagent.research.snapshots import save_snapshot, write_sources_manifest
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
STAGE_PROMPT = {"a": "a_researcher.md", "c": "c_codex_research.md", "d": "d_fact_report.md", "derive": "derive.md"}
# 스테이지별 검증기 프롬프트. 기본은 crossmodel_verifier.md, b는 전용 프롬프트.
# c(코드검증)·d(source_grounding)는 crossmodel 프롬프트를 쓰지 않으므로 매핑에서 제외한다.
STAGE_VERIFIER_PROMPT = {"a": "crossmodel_verifier.md", "derive": "crossmodel_verifier.md"}
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
            sources_dir, s.get("ref_id", "s?"), s.get("url", ""), s.get("fetched_text", ""),
            http_status=int(s.get("http_status", 0)), fetch_ts=s.get("fetch_ts"),
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


def run_research_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    """리서치 워크플로 진입점(최소경로 슬라이스).

    seed 확정 → 최소경로 스테이지(a, derive)를 안쪽 루프로 돌리고 → HTML 리포트를
    바탕화면에 저장한다. dry-run이면 CLI 미호출로 프롬프트/커맨드/상태만 렌더한다.
    """
    budget = AgentCallBudget(args.max_agent_calls)
    ctx = ResearchContext(
        args=args, config=config, request=request, run_dir=run_dir, budget=budget, seed_contract="",
    )
    ctx.state = {"outer_pass": 1, "stage": "seed", "inner_round": 0, "seed_pin": {},
                 "verified_claims": [], "stage_status": {}}
    _persist_state(ctx)

    seed_out = _run_agent_step(
        ctx, agent="claude", role_id="researcher", name="00_seed_contract",
        prompt_name="seed_contract.md",
        prompt_values={"REQUEST": request, "WORKSPACE": str(config.workspace)},
        next_step="seed",
        dry_output='SEED_CONTRACT_JSON\n```json\n{"company":"[dry-run]","base_currency":"KRW"}\n```\n',
    )
    ctx.seed_contract = seed_out
    try:
        ctx.state["seed_pin"] = extract_json_block(seed_out)
    except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 최소경로는 진행
        ctx.state["seed_pin"] = {}
    _persist_state(ctx)

    results: list[StageResult] = []
    try:
        for stage in MINIMAL_PATH:
            result = run_stage_loop(stage, outer_pass=1, ctx=ctx)
            results.append(result)
            write_json(ctx.run_dir / f"stage_result_{stage}.json", {
                "stage_id": result.stage_id, "status": result.status,
                "output_path": result.output_path, "inner_rounds": result.inner_rounds,
                "verdict_status": (result.verdict.status if result.verdict else None),
            })
    except Exception as exc:  # 예산 소진(AgentCallBudgetStopped 포함)은 부분 상태로 안전 종료.
        from autoagent.runner import AgentCallBudgetStopped
        if isinstance(exc, AgentCallBudgetStopped):
            print(f"Research run stopped by budget before {exc.next_step}: {run_dir}")
            return 0
        raise

    body_md = render_template(
        "final_html_report.md",
        {
            "COVERAGE_MATRIX_MD": _coverage_matrix_md(results),
            "REQUEST": request,
            "SEED_CONTRACT": ctx.seed_contract,
            "STAGE_A_OUTPUT": ctx.stage_outputs.get("a", "(없음)"),
            "DERIVE_OUTPUT": ctx.stage_outputs.get("derive", "(없음)"),
        },
    )
    html = render_report_html(title="리서치 리포트", body_md=body_md)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"research_report_{stamp}.html"
    write_text(run_dir / "final_report.html", html)  # 감사추적용 사본(run_dir)
    if args.dry_run:
        print(f"Research dry run written to {run_dir}")
        return 0
    desktop_path = write_desktop_report(html, filename)
    try:
        import os
        os.startfile(str(desktop_path))  # Windows: 기본 브라우저로 열기
    except Exception:  # noqa: BLE001 - 오픈 실패해도 파일은 남았으므로 치명 아님
        pass
    print(f"Research run complete: {run_dir}\nReport: {desktop_path}")
    return 0

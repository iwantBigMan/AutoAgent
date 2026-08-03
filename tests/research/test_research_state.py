"""바깥 루프 배선·research_state 영속 결정론 테스트(모델 호출은 stub 주입)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from autoagent.research.types import StageResult, Verdict
from autoagent.workflows.research import (
    collect_verified_claims, load_research_state, persist_research_state, run_outer_loop,
)


def _verdict(claims):
    return Verdict(status="pass", adapter="crossmodel", stage_id="b", findings=[], raw={"verified_claims": claims})


def _resolved(stage, claims):
    return StageResult(stage_id=stage, status="resolved", output_path=f"{stage}.json",
                       verdict=_verdict(claims), inner_rounds=1)


def test_persist_and_load_roundtrip(tmp_path: Path):
    state = {"outer_pass": 1, "stage": "b", "inner_round": 2,
             "seed_pin": {"company": "X"}, "verified_claims": [], "stage_status": {}}
    persist_research_state(tmp_path, state)
    assert (tmp_path / "research_state.json").exists()
    assert load_research_state(tmp_path) == state


def test_collect_verified_claims_excludes_unverified():
    resolved = _resolved("b", [{"claim_id": "c1", "value": "1"}])
    exhausted = StageResult(stage_id="d", status="exhausted_unverified", output_path="d.json",
                            verdict=Verdict(status="needs_changes", adapter="source_grounding", stage_id="d",
                                            findings=[], raw={"verified_claims": [{"claim_id": "x"}]}),
                            inner_rounds=3)
    claims = collect_verified_claims([resolved, exhausted])
    assert [c["claim_id"] for c in claims] == ["c1"]


def _ctx(tmp_path, seed_raw, stage_claims_by_pass):
    calls = {"n": 0}

    def stub_run_stage(stage, outer_pass, ctx):
        claims = stage_claims_by_pass.get(outer_pass, {}).get(stage, [])
        calls["n"] += 1
        return _resolved(stage, claims)

    return SimpleNamespace(run_dir=tmp_path, stages=["b"], seed_raw=seed_raw,
                           max_outer=2, min_new_claims=2, calls=calls, run_stage=stub_run_stage)


def test_outer_loop_early_stops_on_convergence(tmp_path: Path):
    seed = {"company": "Acme", "market": "M", "base_currency": "KRW", "period": "2021-2025", "unit": "억원"}
    per_pass = {1: {"b": [{"claim_id": "c1"}, {"claim_id": "c2"}, {"claim_id": "c3"}]},
                2: {"b": [{"claim_id": "c1"}, {"claim_id": "c2"}, {"claim_id": "c3"}]}}
    ctx = _ctx(tmp_path, seed, per_pass)
    state = run_outer_loop(ctx, run_stage=ctx.run_stage)
    assert state["seed_pin"]["base_currency"] == "KRW"
    assert state["outer_pass"] == 2
    assert state["outer_decision"]["action"] == "early_stop"
    saved = json.loads((tmp_path / "research_state.json").read_text(encoding="utf-8"))
    assert saved["seed_pin"]["company"] == "Acme"


def test_outer_loop_gates_on_seed_violation(tmp_path: Path):
    seed = {"company": "Acme", "market": "M", "base_currency": "KRW", "period": "2021-2025", "unit": "억원"}

    def stub_run_stage(stage, outer_pass, ctx):
        if outer_pass == 2:
            return StageResult(stage_id=stage, status="resolved", output_path="b.json",
                               verdict=Verdict(status="pass", adapter="crossmodel", stage_id=stage, findings=[],
                                               raw={"verified_claims": [{"claim_id": "z"}],
                                                    "seed_candidate": {"base_currency": "USD"}}),
                               inner_rounds=1)
        return _resolved(stage, [{"claim_id": "c1"}, {"claim_id": "c2"}, {"claim_id": "c3"}])

    ctx = SimpleNamespace(run_dir=tmp_path, stages=["b"], seed_raw=seed,
                          max_outer=2, min_new_claims=2, calls={"n": 0}, run_stage=stub_run_stage)
    state = run_outer_loop(ctx, run_stage=stub_run_stage)
    assert state["outer_decision"]["action"] == "gate"
    assert "seed" in state["outer_decision"]["reason"]


def test_run_stage_loop_injects_researcher_claims_into_verdict_raw(tmp_path: Path, monkeypatch):
    """B2 실배선: run_stage_loop이 pass 시 *리서처* stdout의 claims를 verdict.raw에 실제 주입한다.

    손으로 raw={"verified_claims":...}를 넣지 않고, run_stage_loop의 실제 경로를 태워
    collect_verified_claims가 읽는 verdict.raw['verified_claims']가 채워지는지 검증한다.
    (안 채워지면 실런에서 delta=0 → pass 2 심화가 죽는다.)
    """
    import argparse

    from autoagent.config import load_config
    from autoagent.artifacts import DEFAULT_CONFIG
    from autoagent.workflows import research as R

    # 리서처 호출은 claims를 담은 유효 STAGE_OUTPUT_JSON을, 검증기 호출은 pass verdict를 돌려준다.
    researcher_json = (
        'STAGE_OUTPUT_JSON\n```json\n'
        '{"stage_id":"a","claims":[{"id":"a1","text":"t"}],'
        '"seed_candidate":{"base_currency":"KRW"}}\n```\n'
    )
    # 유효 pass verdict. unchallenged_but_weak를 채워 §4.1② 최소 findings 쿼터를 만족시킨다
    # (findings 0건이라도 무결을 소스로 증명한 경우 → 강등 안 됨). tokens_seen=0이라 evidence 교차검사도 무해.
    verifier_json = (
        'CROSSMODEL_VERDICT: pass\n```json\n'
        '{"adapter":"crossmodel","stage_id":"a","verdict":"pass","findings":[],'
        '"coverage":{"axes_checked":["support"],"axes_missing":[]},'
        '"unchallenged_but_weak":["s1: 근거는 있으나 표본이 작다"],"tokens_seen":0}\n```\n'
    )

    def fake_step(ctx, *, agent, role_id, name, prompt_name, prompt_values, next_step, dry_output):
        return researcher_json if role_id == "researcher" else verifier_json

    monkeypatch.setattr(R, "_run_agent_step", fake_step)

    cfg = load_config(DEFAULT_CONFIG)
    args = argparse.Namespace(dry_run=True, read_only=False, max_agent_calls=0)
    ctx = R.ResearchContext(args=args, config=cfg, request="r", run_dir=tmp_path,
                            budget=R.AgentCallBudget(0), seed_contract="")
    ctx.state = {"seed_pin": {"company": "Acme", "base_currency": "KRW"}, "stage_status": {}}
    result = R.run_stage_loop("a", 1, ctx)
    assert result.status == "resolved"
    assert result.verdict.raw["verified_claims"] == [{"id": "a1", "text": "t"}]
    assert result.verdict.raw["seed_candidate"] == {"base_currency": "KRW"}
    # collect_verified_claims가 실제로 이 claim을 걷는지까지 확인(실배선).
    assert collect_verified_claims([result]) == [{"id": "a1", "text": "t"}]

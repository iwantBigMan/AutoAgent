"""중첩 루프 오케스트레이터(run_stage_loop) 결정론 테스트.

모델 호출부(_run_agent_step)를 monkeypatch로 대체해 CLI 없이 안쪽 루프의 상태
전이만 검증한다. 핵심 불변식 2가지:
  1) silent pass-through 금지 — 3라운드 소진(항상 needs_changes)이면 exhausted_unverified 반환.
  2) verified_claims 실주입 — 검증 pass 시 리서처 stdout의 claims가 verdict.raw에 실제로 배선.
검증기 판정은 코드(어댑터)가 재계산하므로 verify는 실물을 그대로 쓰고, 검증기 stdout
텍스트만 pass/needs_changes로 바꿔 주입한다(어댑터 재계산 경로를 실제로 태운다).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from autoagent.config import load_config
from autoagent.artifacts import DEFAULT_CONFIG
from autoagent.runner import AgentCallBudget
from autoagent.workflows import research as R


def _config():
    return load_config(DEFAULT_CONFIG)


def _ctx(tmp_path: Path) -> R.ResearchContext:
    args = argparse.Namespace(dry_run=False, read_only=False, max_agent_calls=0)
    ctx = R.ResearchContext(
        args=args, config=_config(), request="삼성전자 리서치", run_dir=tmp_path,
        budget=AgentCallBudget(0), seed_contract="",
    )
    ctx.state = {"outer_pass": 1, "stage": "a", "inner_round": 0, "seed_pin": {},
                 "verified_claims": [], "stage_status": {}}
    return ctx


# crossmodel 어댑터가 pass로 재계산하는 최소 검증기 stdout(쿼터 만족 위해 unchallenged_but_weak 채움).
_PASS_VERDICT = (
    "CROSSMODEL_VERDICT: pass\n```json\n"
    '{"adapter":"crossmodel","stage_id":"a","verdict":"pass",'
    '"findings":[],"coverage":{"axes_checked":["support"],"axes_missing":[]},'
    '"unchallenged_but_weak":["ok"],"tokens_seen":0}\n```\n'
)
# 코드가 needs_changes로 재계산하는 검증기 stdout(major finding → 강등).
_NEEDS_VERDICT = (
    "CROSSMODEL_VERDICT: pass\n```json\n"
    '{"adapter":"crossmodel","stage_id":"a","verdict":"pass",'
    '"findings":[{"claim_id":"a1","severity":"major","category":"overreach",'
    '"rebuttal":"근거 부족","fix_directive":"출처 보강"}],'
    '"coverage":{"axes_checked":["support"],"axes_missing":[]},'
    '"unchallenged_but_weak":[],"tokens_seen":0}\n```\n'
)
# 리서처 stdout(주입 대상 claims + seed_candidate 포함).
_RESEARCHER_OUT = (
    "```json\n"
    '{"claims":[{"id":"c1","text":"매출 300조"}],'
    '"seed_candidate":{"company":"삼성전자","base_currency":"KRW"}}\n```\n'
)


def _patch_agent_step(monkeypatch, *, verdict_text: str, researcher_out: str = _RESEARCHER_OUT):
    """_run_agent_step을 리서처/검증기 역할에 따라 고정 stdout을 돌려주도록 대체한다."""

    def fake(ctx, *, agent, role_id, name, prompt_name, prompt_values, next_step, dry_output):
        if role_id == "verifier":
            return verdict_text
        return researcher_out

    monkeypatch.setattr(R, "_run_agent_step", fake)


def test_pass_injects_verified_claims(tmp_path: Path, monkeypatch) -> None:
    """검증 pass 시 resolved + 리서처 claims/seed_candidate가 verdict.raw에 실주입된다."""
    _patch_agent_step(monkeypatch, verdict_text=_PASS_VERDICT)
    ctx = _ctx(tmp_path)
    result = R.run_stage_loop("a", outer_pass=1, ctx=ctx)

    assert result.status == "resolved"
    assert result.inner_rounds == 1               # 첫 라운드에서 통과
    assert result.verdict is not None
    # B2 배선: 손주입이 아니라 리서처 stdout 파싱 결과가 실제로 들어와야 한다.
    assert result.verdict.raw["verified_claims"] == [{"id": "c1", "text": "매출 300조"}]
    assert result.verdict.raw["seed_candidate"] == {"company": "삼성전자", "base_currency": "KRW"}
    # 스테이지 산출물 캐시에도 리서처 stdout이 저장된다.
    assert ctx.stage_outputs["a"] == _RESEARCHER_OUT


def test_exhaustion_returns_exhausted_unverified(tmp_path: Path, monkeypatch) -> None:
    """3라운드 내내 needs_changes면 silent pass-through 금지 → exhausted_unverified 명시 반환."""
    _patch_agent_step(monkeypatch, verdict_text=_NEEDS_VERDICT)
    ctx = _ctx(tmp_path)
    result = R.run_stage_loop("a", outer_pass=1, ctx=ctx)

    assert result.status == "exhausted_unverified"   # 절대 pass/resolved로 조용히 넘어가지 않는다
    assert result.inner_rounds == R.INNER_MAX        # 상한(3)까지 다 돌았다
    # 소진 상태가 research_state.json 기록용 state에도 남는다(마지막 재계산 verdict = needs_changes).
    assert ctx.state["stage_status"]["a"] == "needs_changes"


def test_blocked_verdict_returns_immediately(tmp_path: Path, monkeypatch) -> None:
    """검증기가 판정 불가(마커 없음→blocked)면 루프를 더 돌지 않고 즉시 blocked 반환."""
    _patch_agent_step(monkeypatch, verdict_text="마커 없이 자유서술만 왔다.")
    ctx = _ctx(tmp_path)
    result = R.run_stage_loop("a", outer_pass=1, ctx=ctx)

    assert result.status == "blocked"
    assert result.inner_rounds == 1                  # 첫 라운드에서 즉시 종료


def test_c_stage_never_touched_in_minimal_path() -> None:
    """미구현 스테이지 안전: 최소경로에 c가 없어 data_quality 어댑터를 건드리지 않는다."""
    assert "c" not in R.MINIMAL_PATH
    assert R.MINIMAL_PATH == ["a", "derive"]

"""d 스테이지 **검증 경로** 락(오케스트레이터 행동 계약).

d 스테이지는 run_stage_loop 안에서 리서처(Claude) stdout을 파싱해 sources[]를
runs/sources/*.txt 스냅샷으로 저장하고(재fetch 없음), Codex 검증기를 스냅샷 텍스트만으로
렌더/호출한 뒤 source_grounding 어댑터로 verify한다(test_routing_c.py와 동형 락).

핵심 불변식 3가지를 여기서 고정한다:
  1) 리서처 stdout의 sources[].fetched_text가 runs/sources/<ref>.txt로 실제 저장된다
     (스냅샷 선행, 검증기는 이걸 읽는다 — 재fetch 금지).
  2) Codex 검증기 role이 정확히 1회(agent="codex") 호출되고, 프롬프트에 스냅샷 JSON이
     실려 간다(REPORT_MD/CLAIMS_JSON/SOURCES_SNAPSHOTS_JSON) — 재fetch용 웹 호출 없음.
  3) verify가 adapter="source_grounding"으로 불려 verdict.adapter가 그대로 나온다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from autoagent.config import load_config
from autoagent.artifacts import DEFAULT_CONFIG
from autoagent.runner import AgentCallBudget
from autoagent.workflows import research as R

_D_RESEARCHER_OUT = (
    "```json\n"
    + json.dumps(
        {
            "stage_id": "d",
            "report_md": "# 팩트리포트\n- Acme 매출은 12M이다 [s1]",
            "claims": [
                {
                    "id": "c1",
                    "text": "Acme revenue was 12M.",
                    "kind": "fact",
                    "cited_source_refs": ["s1"],
                    "quoted_span": "revenue of 12M",
                }
            ],
            "sources": [
                {
                    "ref_id": "s1",
                    "url": "https://example.com/acme",
                    "http_status": 200,
                    "fetched_text": "In 2024 Acme reported revenue of 12M USD.",
                    "fetch_ts": "2026-01-01T00:00:00Z",
                }
            ],
        },
        ensure_ascii=False,
    )
    + "\n```\n"
)

# Codex 검증기가 스냅샷만 보고 pass로 판정한 stdout(마커+JSON).
_D_VERIFIER_PASS = (
    "GROUNDING_VERDICT: pass\n```json\n"
    '{"schema_version": 1, "adapter": "source_grounding", "stage_id": "d", "verdict": "pass",'
    ' "claim_checks": [{"claim_id": "c1", "grounding": "supported",'
    ' "matched_quote": "revenue of 12M", "claim_span": "revenue was 12M",'
    ' "notes": "", "source_ref": "s1"}],'
    ' "orphan_claims": [], "dead_sources": [], "fabricated_sources": []}\n```\n'
)


def _config():
    return load_config(DEFAULT_CONFIG)


def _ctx(tmp_path: Path) -> R.ResearchContext:
    args = argparse.Namespace(dry_run=False, read_only=False, max_agent_calls=0)
    ctx = R.ResearchContext(
        args=args, config=_config(), request="d 스테이지 검증 경로 락", run_dir=tmp_path,
        budget=AgentCallBudget(0), seed_contract="",
    )
    ctx.state = {"outer_pass": 1, "stage": "d", "inner_round": 0, "seed_pin": {},
                 "verified_claims": [], "stage_status": {}}
    return ctx


def test_d_stage_saves_snapshot_then_source_grounding_verify(tmp_path: Path, monkeypatch) -> None:
    """d 스테이지: 리서처 stdout → 스냅샷 저장 → source_grounding verify(코드 재계산 pass)."""
    verifier_calls: list[dict] = []

    def fake_agent_step(ctx, *, agent, role_id, name, prompt_name, prompt_values, next_step, dry_output):
        if role_id == "verifier":
            verifier_calls.append(
                {"agent": agent, "prompt_name": prompt_name, "prompt_values": prompt_values}
            )
            return _D_VERIFIER_PASS
        return _D_RESEARCHER_OUT

    monkeypatch.setattr(R, "_run_agent_step", fake_agent_step)

    ctx = _ctx(tmp_path)
    result = R.run_stage_loop("d", outer_pass=1, ctx=ctx)

    # 핵심 계약 1: 스냅샷이 실제로 runs/sources/s1.txt에 저장됐다(재fetch 없이 이걸 검증기가 읽음).
    snapshot_path = tmp_path / "sources" / "s1.txt"
    assert snapshot_path.exists()
    assert snapshot_path.read_text(encoding="utf-8") == "In 2024 Acme reported revenue of 12M USD."
    assert (tmp_path / "sources_manifest.json").exists()

    # 핵심 계약 2: Codex 검증기가 정확히 1회, 스냅샷 JSON을 프롬프트에 싣고 호출됐다(재fetch 아님).
    assert len(verifier_calls) == 1
    call = verifier_calls[0]
    assert call["agent"] == "codex"
    assert call["prompt_name"] == "d_grounding_verify.md"
    assert "revenue of 12M" in call["prompt_values"]["SOURCES_SNAPSHOTS_JSON"]
    assert "WebFetch" not in call["prompt_values"]["SOURCES_SNAPSHOTS_JSON"]

    # 핵심 계약 3: verify가 source_grounding 어댑터로 불려 pass로 재계산됐다.
    assert result.verdict is not None
    assert result.verdict.adapter == "source_grounding"
    assert result.verdict.status == "pass"
    assert result.status == "resolved"


def test_d_stage_verifier_never_receives_raw_web_tool_access(tmp_path: Path, monkeypatch) -> None:
    """Codex 검증기 프롬프트 값에는 스냅샷 텍스트만 실리고, 리서처의 원래 sources 리스트를

    코드가 다시 fetch하지 않는다 — save_snapshot이 리서처가 보낸 fetched_text를 그대로
    파일에 고정하고, 검증기 프롬프트는 그 동일 텍스트(JSON 직렬화)만 받는다.
    """
    seen_prompt_values: dict = {}

    def fake_agent_step(ctx, *, agent, role_id, name, prompt_name, prompt_values, next_step, dry_output):
        if role_id == "verifier":
            seen_prompt_values.update(prompt_values)
            return _D_VERIFIER_PASS
        return _D_RESEARCHER_OUT

    monkeypatch.setattr(R, "_run_agent_step", fake_agent_step)

    ctx = _ctx(tmp_path)
    R.run_stage_loop("d", outer_pass=1, ctx=ctx)

    sources = json.loads(seen_prompt_values["SOURCES_SNAPSHOTS_JSON"])
    assert sources[0]["fetched_text"] == "In 2024 Acme reported revenue of 12M USD."
    assert sources[0]["ref_id"] == "s1"


def test_d_in_stage_adapter_and_prompt_maps() -> None:
    """d가 STAGE_ADAPTER/STAGE_PROMPT에 배선돼 있고, 최소경로(MINIMAL_PATH)엔 아직 없다."""
    assert R.STAGE_ADAPTER["d"] == "source_grounding"
    assert R.STAGE_PROMPT["d"] == "d_fact_report.md"
    assert "d" not in R.MINIMAL_PATH  # Slice 4에서 바깥 루프에 합류(이 태스크 범위 아님)

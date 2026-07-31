"""source_grounding 어댑터 테스트(§4.3-②③).

GROUNDING_VERDICT 마커+JSON 파싱, 결정론 findings와 병합, 결정적 위반의 모델 pass 강등을
코드로 못박는다(free-text 무시, 코드가 status 재계산). Verdict 계약 이름/필드 그대로 사용.
"""
from __future__ import annotations

from pathlib import Path

from autoagent.research.grounding import run_deterministic_checks
from autoagent.research.source_grounding import (
    merge_and_recompute, parse_grounding_verdict, verify_source_grounding,
)

MARKER_OK = """일부 서술...
GROUNDING_VERDICT: pass
```json
{"schema_version": 1, "adapter": "source_grounding", "stage_id": "d", "verdict": "pass",
 "claim_checks": [{"claim_id": "c1", "grounding": "supported", "matched_quote": "revenue of 12M",
                   "claim_span": "revenue was 12M", "notes": "", "source_ref": "s1"}],
 "orphan_claims": [], "dead_sources": [], "fabricated_sources": []}
```
꼬리 서술...
"""


def test_parse_marker_and_json():
    parsed = parse_grounding_verdict(MARKER_OK)
    assert parsed["verdict"] == "pass"
    assert parsed["claim_checks"][0]["claim_id"] == "c1"


def test_parse_missing_marker_is_defensive():
    parsed = parse_grounding_verdict("모델이 마커를 안 붙였습니다.")
    assert parsed["verdict"] is None
    assert parsed["claim_checks"] == []


def test_parse_stray_braces_in_prose_around_fence_still_parses():
    """마커+fenced JSON은 정상인데 fence 밖 산문에 stray { }가 섞인 경우(Task 4와 동일 함정).

    브레이스매칭 폴백(전체 텍스트 첫 { ~ 마지막 })으로 떨어지면 잘못된 span이 되어 파싱이
    깨지거나 엉뚱한 dict가 나온다. 마커 앵커드 fence 추출이면 fence 밖 산문은 무시되고
    정상적으로 verdict=pass, claim_id=c1이 나와야 한다.
    """
    text = (
        "검증기 서론입니다. 참고로 dict 예시 `{foo}` 같은 형태를 곧 보게 됩니다.\n"
        + MARKER_OK
        + "\n결론: 위 JSON을 참고하세요. 추가로 `{bar}` 같은 표기도 등장할 수 있습니다."
    )
    parsed = parse_grounding_verdict(text)
    assert parsed["verdict"] == "pass"
    assert parsed["claim_checks"][0]["claim_id"] == "c1"


def _stage_out_clean():
    return {
        "claims": [{"id": "c1", "text": "Acme revenue was 12M.", "kind": "fact",
                    "cited_source_refs": ["s1"], "quoted_span": "revenue of 12M"}],
        "sources": [{"ref_id": "s1", "url": "u1",
                     "fetched_text": "In 2024 Acme reported revenue of 12M USD.", "http_status": 200}],
    }


def test_clean_input_model_pass_stays_pass():
    so = _stage_out_clean()
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "pass"
    assert v.adapter == "source_grounding" and v.stage_id == "d"


def test_orphan_fact_downgrades_model_pass_to_needs_changes():
    so = _stage_out_clean()
    so["claims"].append({"id": "c2", "text": "Acme dominates.", "kind": "fact",
                         "cited_source_refs": [], "quoted_span": ""})
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "needs_changes"


def test_dead_source_blocks_even_if_model_pass():
    so = _stage_out_clean()
    so["sources"].append({"ref_id": "s2", "url": "u2", "fetched_text": "", "http_status": 404})
    so["claims"].append({"id": "c3", "text": "From dead.", "kind": "fact",
                         "cited_source_refs": ["s2"], "quoted_span": "x"})
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"], "s2": ""})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "blocked"


def test_fabricated_source_blocks():
    so = _stage_out_clean()
    so["claims"].append({"id": "c9", "text": "Ghost cite.", "kind": "fact",
                         "cited_source_refs": ["s99"], "quoted_span": "z"})
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "blocked"


def test_model_contradicted_forces_needs_changes():
    so = _stage_out_clean()
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    model = {"verdict": "pass", "claim_checks": [
        {"claim_id": "c1", "grounding": "contradicted", "notes": "소스는 반대를 말함"}]}
    v = merge_and_recompute(so, model, det, verifier_agent="codex", stage_id="d", raw_text="")
    assert v.status == "needs_changes"
    assert any(f.category == "contradicted" and f.severity == "critical" for f in v.findings)


def test_verify_persists_verdict_json(tmp_path: Path):
    from autoagent.research.snapshots import save_snapshot
    save_snapshot(tmp_path / "sources", "s1", "u1",
                  "In 2024 Acme reported revenue of 12M USD.", http_status=200)
    v = verify_source_grounding(_stage_out_clean(), tmp_path,
                                verifier_agent="codex", config=None, model_raw_text=MARKER_OK)
    assert v.status == "pass"
    assert (tmp_path / "d_grounding_verdict.json").exists()


def test_adapters_verify_dispatches_source_grounding(tmp_path: Path):
    from autoagent.research.adapters import verify
    from autoagent.research.snapshots import save_snapshot
    save_snapshot(tmp_path / "sources", "s1", "u1",
                  "In 2024 Acme reported revenue of 12M USD.", http_status=200)
    so = {**_stage_out_clean(), "model_raw_text": MARKER_OK}
    v = verify("source_grounding", so, tmp_path, verifier_agent="codex", config=None)
    assert v.adapter == "source_grounding" and v.status == "pass"

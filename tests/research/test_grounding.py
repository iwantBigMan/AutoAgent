"""결정론 grounding 검사 테스트(§4.3-①).

matched_quote ⊆ fetched_text 부분문자열·fabricated/dead/orphan 실측은 모델 없는
순수 코드라 pytest로 못박는다. 근거 날조를 부분문자열로 차단.
"""
from __future__ import annotations

from autoagent.research.grounding import (
    normalize_for_match, quote_is_grounded, run_deterministic_checks,
)


def test_normalize_collapses_whitespace_and_case():
    assert normalize_for_match("Acme   Corp\n reported") == "acme corp reported"


def test_quote_grounded_true_when_substring_present():
    fetched = "In 2024 Acme reported revenue of 12M USD across all regions."
    assert quote_is_grounded("Acme reported revenue of 12M", fetched) is True


def test_quote_grounded_ignores_whitespace_and_case_diff():
    fetched = "Acme reported\nrevenue of  12M"
    assert quote_is_grounded("acme REPORTED revenue of 12m", fetched) is True


def test_quote_not_grounded_when_absent():
    fetched = "Acme reported revenue of 12M USD."
    assert quote_is_grounded("Acme projects revenue of 50M by 2030", fetched) is False


def test_empty_quote_is_not_grounded():
    assert quote_is_grounded("", "any text") is False
    assert quote_is_grounded("   ", "any text") is False


def _stage_out():
    return {
        "claims": [
            {"id": "c1", "text": "Acme revenue was 12M in 2024.", "kind": "fact",
             "cited_source_refs": ["s1"], "quoted_span": "revenue of 12M"},
            {"id": "c2", "text": "Acme will dominate by 2030.", "kind": "fact",
             "cited_source_refs": [], "quoted_span": ""},
            {"id": "c3", "text": "Acme cites a ghost.", "kind": "fact",
             "cited_source_refs": ["s9"], "quoted_span": "ghost quote"},
            {"id": "c4", "text": "We recommend expanding.", "kind": "recommendation",
             "cited_source_refs": [], "quoted_span": ""},
            {"id": "c5", "text": "From dead source.", "kind": "fact",
             "cited_source_refs": ["s2"], "quoted_span": "anything"},
        ],
        "sources": [
            {"ref_id": "s1", "url": "u1", "fetched_text": "In 2024 Acme reported revenue of 12M USD.", "http_status": 200},
            {"ref_id": "s2", "url": "u2", "fetched_text": "", "http_status": 404},
        ],
    }


def test_orphan_fact_detected():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD."})
    assert "c2" in res.orphan_claims
    assert "c4" not in res.orphan_claims


def test_fabricated_source_detected():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD."})
    assert "s9" in res.fabricated_sources


def test_dead_source_detected():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    assert "s2" in res.dead_sources


def test_unverified_quote_when_not_substring():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    assert "c1" not in res.unverified_quotes
    so = _stage_out()
    so["claims"][0]["quoted_span"] = "revenue of 999B"
    res2 = run_deterministic_checks(so, {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    assert "c1" in res2.unverified_quotes


def test_findings_carry_severity_and_claim_id():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    cats = {(f.category, f.severity) for f in res.findings}
    assert ("fabricated_source", "critical") in cats
    assert ("dead_source", "critical") in cats
    assert ("orphan_claim", "major") in cats
    orphan = [f for f in res.findings if f.category == "orphan_claim"]
    assert orphan and orphan[0].claim_id == "c2"

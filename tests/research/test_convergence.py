"""pass간 검증 claim delta·모순 검출 결정론 로직 테스트(스펙 §5)."""
from __future__ import annotations

from autoagent.research.convergence import ClaimDelta, diff_verified_claims, normalize_claim_key


def test_normalize_claim_key_prefers_claim_id():
    assert normalize_claim_key({"claim_id": "c1", "text": "무관"}) == "c1"


def test_normalize_claim_key_hashes_text_when_no_id():
    k1 = normalize_claim_key({"text": "시장 규모는 5000억원 이다."})
    k2 = normalize_claim_key({"text": "시장 규모는  5000억원 이다."})
    assert k1 == k2
    assert k1 != normalize_claim_key({"text": "완전 다른 주장"})


def test_diff_added_and_delta_count():
    prev = [{"claim_id": "c1", "value": "5000"}]
    curr = [{"claim_id": "c1", "value": "5000"}, {"claim_id": "c2", "value": "12%"}]
    delta = diff_verified_claims(prev, curr)
    assert isinstance(delta, ClaimDelta)
    assert delta.delta_count == 1
    assert [c["claim_id"] for c in delta.added] == ["c2"]
    assert delta.unchanged == ["c1"]
    assert delta.contradictions == []


def test_diff_flags_contradiction_when_value_flips():
    prev = [{"claim_id": "c1", "value": "5000"}]
    curr = [{"claim_id": "c1", "value": "9000"}]
    delta = diff_verified_claims(prev, curr)
    assert delta.delta_count == 0
    assert len(delta.contradictions) == 1
    assert delta.contradictions[0]["claim_id"] == "c1"
    assert delta.contradictions[0]["prev_value"] == "5000"
    assert delta.contradictions[0]["curr_value"] == "9000"


def test_diff_as_of_difference_is_not_contradiction():
    prev = [{"claim_id": "c1", "value": "1300", "as_of": "2025-01-01"}]
    curr = [{"claim_id": "c1", "value": "1400", "as_of": "2025-06-01"}]
    delta = diff_verified_claims(prev, curr)
    assert delta.contradictions == []
    assert delta.delta_count == 1


def test_diff_empty_prev_all_added():
    curr = [{"claim_id": "c1", "value": "x"}, {"claim_id": "c2", "value": "y"}]
    delta = diff_verified_claims([], curr)
    assert delta.delta_count == 2
    assert delta.contradictions == []

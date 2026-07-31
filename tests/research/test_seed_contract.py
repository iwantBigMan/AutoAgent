"""seed 계약 결정론 로직 테스트(스펙 §5 seed read-only pin·위반 검출)."""
from __future__ import annotations

import pytest

from autoagent.research.seed_contract import (
    build_seed_pin, detect_seed_violations, seed_pin_from_dict, seed_pin_to_dict,
)


def _raw() -> dict:
    return {
        "company": "Acme Corp", "market": "국내 EV 충전 인프라", "base_currency": "KRW",
        "period": "2021-2025", "unit": "억원", "extra_noise": "무시돼야 함",
    }


def test_build_seed_pin_extracts_canonical_fields():
    pin = build_seed_pin(_raw())
    assert pin.company == "Acme Corp"
    assert pin.market == "국내 EV 충전 인프라"
    assert pin.base_currency == "KRW"
    assert pin.period == "2021-2025"
    assert pin.unit == "억원"
    assert pin.as_of is None


def test_build_seed_pin_missing_field_raises():
    raw = _raw()
    del raw["base_currency"]
    with pytest.raises(ValueError) as exc:
        build_seed_pin(raw)
    assert "base_currency" in str(exc.value)


def test_seed_pin_roundtrip_dict():
    pin = build_seed_pin(_raw())
    assert seed_pin_from_dict(seed_pin_to_dict(pin)) == pin


def test_detect_seed_violations_none_when_candidate_matches():
    pin = build_seed_pin(_raw())
    candidate = {"company": "Acme Corp", "base_currency": "KRW", "narrative": "더 깊은 분석"}
    assert detect_seed_violations(pin, candidate) == []


def test_detect_seed_violations_flags_changed_currency():
    pin = build_seed_pin(_raw())
    violations = detect_seed_violations(pin, {"company": "Acme Corp", "base_currency": "USD"})
    assert len(violations) == 1
    assert "base_currency" in violations[0]
    assert "KRW" in violations[0] and "USD" in violations[0]


def test_detect_seed_violations_ignores_absent_fields():
    pin = build_seed_pin(_raw())
    assert detect_seed_violations(pin, {"narrative": "시장 규모 심화"}) == []

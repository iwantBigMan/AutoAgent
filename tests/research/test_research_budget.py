"""계층 예산(tiered cap) + capture 절단 단위테스트(순수 로직)."""
from __future__ import annotations

import pytest

from autoagent.workflows.research import (
    MAX_CAPTURE_CHARS, TieredBudgetStopped, TieredCallCap, _truncate_capture,
)


def test_truncate_keeps_head_and_marks_when_over_limit() -> None:
    text = "가" * (MAX_CAPTURE_CHARS + 500)
    out = _truncate_capture(text)
    assert len(out) <= MAX_CAPTURE_CHARS + 64  # 절단 표식 여유
    assert out.startswith("가")
    assert "truncated" in out.lower()


def test_truncate_noop_under_limit() -> None:
    assert _truncate_capture("짧은 텍스트") == "짧은 텍스트"


def test_tiered_cap_charges_per_stage() -> None:
    cap = TieredCallCap(per_stage=2, per_outer=99)
    cap.charge(1, "a"); cap.charge(1, "a")
    with pytest.raises(TieredBudgetStopped):
        cap.charge(1, "a")  # 스테이지 a에서 3번째 → 초과


def test_tiered_cap_isolates_stages_and_passes() -> None:
    cap = TieredCallCap(per_stage=1, per_outer=99)
    cap.charge(1, "a")  # ok
    cap.charge(1, "b")  # 다른 스테이지 → 독립
    cap.charge(2, "a")  # 다른 outer_pass → 독립


def test_tiered_cap_charges_per_outer() -> None:
    cap = TieredCallCap(per_stage=99, per_outer=2)
    cap.charge(1, "a"); cap.charge(1, "b")
    with pytest.raises(TieredBudgetStopped):
        cap.charge(1, "c")  # outer_pass 1 전체 3번째 → 초과

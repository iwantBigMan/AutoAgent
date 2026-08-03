"""choose_researcher 라우팅 테이블·반대모델 계산 테스트.

리서처 배정(스펙 §3 테이블)과 verifier=반대모델 불변식을 고정한다.
choose_implementer는 건드리지 않음(쌍둥이 추가만).
"""
from __future__ import annotations

import pytest

from autoagent.routing import choose_researcher


@pytest.mark.parametrize(
    "stage,researcher,verifier",
    [
        ("a", "claude", "codex"),
        ("b", "claude", "codex"),
        ("c", "codex", "claude"),
        ("d", "claude", "codex"),
        ("derive", "claude", "codex"),
    ],
)
def test_researcher_table_and_opposite_verifier(stage, researcher, verifier) -> None:
    r, v, reason = choose_researcher(stage)
    assert (r, v) == (researcher, verifier)
    assert r != v
    assert stage in reason


def test_unknown_stage_rejected() -> None:
    with pytest.raises(SystemExit):
        choose_researcher("zz")

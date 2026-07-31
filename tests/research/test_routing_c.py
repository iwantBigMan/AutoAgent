"""c 스테이지 라우팅 계약 회귀 락(Slice 1 choose_researcher 소비)."""
from __future__ import annotations

from autoagent.routing import choose_researcher


def test_c_stage_researcher_is_codex() -> None:
    researcher, verifier, reason = choose_researcher("c")
    assert researcher == "codex"
    assert verifier == "claude"  # c의 검증기 모델은 반대편이나 실제 호출은 0회(코드 검증)
    assert isinstance(reason, str) and reason


def test_web_stages_researcher_is_claude() -> None:
    for stage in ("a", "b", "d", "derive"):
        researcher, verifier, _ = choose_researcher(stage)
        assert researcher == "claude"
        assert verifier == "codex"

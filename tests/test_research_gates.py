"""research/gates.py 게이트 트리거 판정 테스트(순수 함수, 부수효과 없음)."""
from __future__ import annotations

from dataclasses import dataclass

from autoagent.research.gates import GateTrigger, evaluate_gate, should_pause
from autoagent.research.types import StageResult


@dataclass
class _Cfg:
    research_exhausted_gate_threshold: int = 2


def _sr(stage_id: str, status: str) -> StageResult:
    return StageResult(stage_id=stage_id, status=status, output_path=f"{stage_id}.md", verdict=None, inner_rounds=1)


def test_deepen_entry_is_forced_gate() -> None:
    t = evaluate_gate(event="deepen_entry", outer_pass=2, stage_results=[], contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "high_cost_deepen" and t.forced is True
    assert should_pause(t, auto_approve_nonbranch=True) is True


def test_deepen_entry_pass1_not_a_gate() -> None:
    assert evaluate_gate(event="deepen_entry", outer_pass=1, stage_results=[], contradiction=False, config=_Cfg()) is None


def test_contradiction_is_forced_gate() -> None:
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=[_sr("a", "resolved")], contradiction=True, config=_Cfg())
    assert t is not None and t.kind == "contradiction" and t.forced is True
    assert should_pause(t, auto_approve_nonbranch=True) is True


def test_blocked_is_forced_gate() -> None:
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=[_sr("a", "blocked")], contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "blocked" and t.forced is True


def test_exhausted_many_triggers_at_threshold() -> None:
    rs = [_sr("a", "exhausted_unverified"), _sr("b", "exhausted_unverified")]
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "exhausted_unverified_many" and t.forced is False
    assert should_pause(t, auto_approve_nonbranch=False) is True
    assert should_pause(t, auto_approve_nonbranch=True) is False


def test_exhausted_below_threshold_no_gate() -> None:
    rs = [_sr("a", "exhausted_unverified"), _sr("b", "resolved")]
    assert evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg()) is None


def test_all_resolved_is_no_gate() -> None:
    rs = [_sr("a", "resolved"), _sr("b", "resolved")]
    assert evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg()) is None


def test_forced_precedence_blocked_over_exhausted() -> None:
    rs = [_sr("a", "blocked"), _sr("b", "exhausted_unverified"), _sr("c", "exhausted_unverified")]
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "blocked" and t.forced is True


def test_should_pause_none_is_false() -> None:
    assert should_pause(None, auto_approve_nonbranch=False) is False

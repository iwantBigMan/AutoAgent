"""리서치 워크플로 게이트 트리거 판정(스펙 §6.2, 분기점 전용).

게이트는 네 분기점에서만 트리거된다: (1)고비용 심화 진입 (2)모순 승격
(3)exhausted_unverified 다수 (4)blocked. 나머지 전이는 자동이다.
--auto-approve-nonbranch는 forced가 아닌 게이트만 자동 통과시키고,
고비용(high_cost_deepen)·모순(contradiction)·blocked는 forced라 절대 생략하지 않는다.
이 모듈은 순수 판정만 하고 정지 부수효과(산출물·stdout·checkpoint)는 pause_at_gate가 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from autoagent.research.types import StageResult

GateKind = Literal["high_cost_deepen", "contradiction", "exhausted_unverified_many", "blocked"]


@dataclass
class GateTrigger:
    """게이트 트리거 판정 결과. forced면 --auto-approve-nonbranch로도 생략 불가."""

    kind: GateKind
    reason: str
    forced: bool


def evaluate_gate(
    *, event: str, outer_pass: int, stage_results: list[StageResult], contradiction: bool, config: Any,
) -> GateTrigger | None:
    """분기점이면 GateTrigger, 아니면 None을 돌려준다.

    event는 전이 종류("deepen_entry"=바깥 pass 심화 진입, "stage_boundary"=스테이지 경계).
    forced 게이트(blocked > contradiction > high_cost_deepen)를 먼저 판정하고,
    그 다음 exhausted_unverified 다수(비-forced)를 본다.
    """
    # blocked 확인 (최고 우선순위 forced 게이트)
    if any(sr.status == "blocked" for sr in stage_results):
        blocked_ids = [sr.stage_id for sr in stage_results if sr.status == "blocked"]
        return GateTrigger(kind="blocked",
                           reason=f"Stage(s) blocked, verdict undecidable: {', '.join(blocked_ids)}.", forced=True)
    # contradiction 확인 (우선순위 2 forced 게이트)
    if contradiction:
        return GateTrigger(kind="contradiction",
                           reason="Verified claim reversed across passes (seed drift / contradiction).", forced=True)
    # high_cost_deepen 확인 (우선순위 3 forced 게이트)
    if event == "deepen_entry" and outer_pass >= 2:
        return GateTrigger(kind="high_cost_deepen", reason=f"Entering high-cost deepen pass {outer_pass}.", forced=True)
    # exhausted_unverified 다수 확인 (non-forced)
    threshold = getattr(config, "research_exhausted_gate_threshold", 2)
    exhausted = [sr.stage_id for sr in stage_results if sr.status == "exhausted_unverified"]
    if len(exhausted) >= threshold:
        return GateTrigger(kind="exhausted_unverified_many",
                           reason=f"{len(exhausted)} stage(s) exhausted_unverified (threshold {threshold}): {', '.join(exhausted)}.",
                           forced=False)
    return None


def should_pause(trigger: GateTrigger | None, *, auto_approve_nonbranch: bool) -> bool:
    """게이트에서 실제로 멈춰야 하는지. forced면 플래그 무시, 그 외는 플래그로 자동통과."""
    if trigger is None:
        return False
    if trigger.forced:
        return True
    return not auto_approve_nonbranch

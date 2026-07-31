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


def pause_at_gate(run_dir, trigger: GateTrigger, state: dict) -> int:
    """게이트 도달 시 정지 산출물을 남기고 stdout 고정 라인을 찍는다(§6.2, 무인 deadlock 차단).

    routed_common.block_for_human_approval의 규약을 리서치용으로 옮긴 것:
    - gate_status.json: 기계판독 상태(status/gate_kind/forced/reason/resume_command).
    - gate_required.md: 사람이 읽을 정지 사유 + 재개 명령.
    - stdout 고정 라인: RESEARCH_STATUS/RUN_DIR/RESUME_COMMAND/GATE_KIND — 구동 측(사람/CLI)이
      run_dir·재개 명령을 안정적으로 집도록 한다. resume_command는 routed와 동일 함수로 만든다.
    """
    from pathlib import Path

    from autoagent.artifacts import write_json, write_text
    from autoagent.workflows.routed_common import resume_command_for

    run_dir = Path(run_dir)
    resume_command = resume_command_for(run_dir)
    status = {
        "status": "waiting_for_human_approval", "approved": False, "required": True,
        "gate_kind": trigger.kind, "forced": trigger.forced, "reason": trigger.reason,
        "run_dir": str(run_dir), "resume_command": resume_command,
        "state": {"outer_pass": state.get("outer_pass"), "stage": state.get("stage"),
                  "inner_round": state.get("inner_round")},
    }
    write_json(run_dir / "gate_status.json", status)
    write_text(
        run_dir / "gate_required.md",
        "# 리서치 게이트 — 인간 승인 필요\n\n"
        f"게이트 종류: **{trigger.kind}** (forced={trigger.forced})\n\n"
        f"사유: {trigger.reason}\n\n"
        f"상태: outer_pass={state.get('outer_pass')}, stage={state.get('stage')}, "
        f"inner_round={state.get('inner_round')}\n\n"
        "검토 후 재개하려면(이 명령 실행 자체가 승인):\n\n"
        f"```powershell\n{resume_command}\n```\n",
    )
    print("RESEARCH_STATUS: waiting_for_human_approval")
    print(f"RUN_DIR: {run_dir}")
    print(f"RESUME_COMMAND: {resume_command}")
    print(f"GATE_KIND: {trigger.kind}")
    print(f"Research run waiting for human approval ({trigger.kind}): {run_dir}")
    return 0

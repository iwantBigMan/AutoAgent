"""리서치 워크플로 런타임 상태 영속/재개(스펙 §6.3).

research_state.json에 매 전이 영속하고, --resume는 이 파일을 읽어 done(resolved)
스테이지를 건너뛰고 미완 inner_round를 이어간다. seed_pin은 §5 canonical seed
불변식대로 최초 1회만 고정하고 이후 read-only다. task_exec의 load_exec_state(있으면
읽고 없으면 초기화)·persist_status(전이마다 재기록) 패턴과 동형이다.
"""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.artifacts import read_text, write_json

# 파이프라인 스테이지 고정 순서(§1). resume_point가 done 스킵에 쓴다.
STAGE_ORDER = ["a", "b", "c", "d", "derive"]


def load_or_init_state(run_dir: Path) -> dict:
    """research_state.json이 있으면 그대로, 없으면 초기 상태를 돌려준다(파일 미기록).

    초기화는 파일을 쓰지 않는다 — 첫 전이에서 persist_state가 기록한다(task_exec 관례).
    """
    path = run_dir / "research_state.json"
    if path.exists():
        return json.loads(read_text(path))
    return {
        "outer_pass": 1, "stage": "a", "inner_round": 0,
        "seed_pin": {}, "verified_claims": [], "stage_status": {},
    }


def persist_state(run_dir: Path, state: dict) -> None:
    """매 전이마다 research_state.json을 다시 써 재개 지점을 최신으로 유지한다."""
    write_json(run_dir / "research_state.json", state)


def set_stage_status(run_dir: Path, state: dict, stage: str, status: str) -> None:
    """단일 스테이지 status를 갱신하고 즉시 영속한다."""
    state.setdefault("stage_status", {})[stage] = status
    persist_state(run_dir, state)


def is_stage_done(state: dict, stage: str) -> bool:
    """스테이지가 재개 시 건너뛸 수 있는 완료 상태인지.

    resolved만 done이다. exhausted_unverified·blocked는 재개 시 다시 시도해야 하므로
    done이 아니다(§8 F1: 미검증을 조용히 통과시키지 않는다).
    """
    return state.get("stage_status", {}).get(stage) == "resolved"


def resume_point(state: dict) -> tuple[int, str, int]:
    """(outer_pass, 재개할 첫 미완 스테이지, inner_round)를 돌려준다.

    resolved 스테이지는 건너뛴다. 중단됐던 스테이지(state["stage"])가 아직 미완이면
    그 스테이지의 inner_round를 이어가고, 이미 넘어간 스테이지면 다음 미완 스테이지를
    새로 진입(inner_round=0)한다.
    """
    outer = state.get("outer_pass", 1)
    interrupted_stage = state.get("stage", "a")
    saved_inner = state.get("inner_round", 0)
    for stage in STAGE_ORDER:
        if is_stage_done(state, stage):
            continue
        inner = saved_inner if stage == interrupted_stage else 0
        return outer, stage, inner
    return outer, STAGE_ORDER[-1], saved_inner


def pin_seed(run_dir: Path, state: dict, seed: dict) -> None:
    """canonical seed를 최초 1회만 고정한다(§5 read-only 불변식).

    이미 seed_pin이 있으면 무시한다 — pass 2가 seed를 바꿔 계통 표류시키지 못하게 한다.
    """
    if state.get("seed_pin"):
        return
    state["seed_pin"] = dict(seed)
    persist_state(run_dir, state)

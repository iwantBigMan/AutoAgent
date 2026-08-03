"""research/state.py 상태 영속/재개 테스트(§6.3)."""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.research.state import (
    is_stage_done, load_or_init_state, persist_state, pin_seed, resume_point, set_stage_status,
)

STAGES = ["a", "b", "c", "d", "derive"]


def test_init_when_absent(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    assert st == {"outer_pass": 1, "stage": "a", "inner_round": 0,
                  "seed_pin": {}, "verified_claims": [], "stage_status": {}}
    assert not (tmp_path / "research_state.json").exists()


def test_persist_then_load_roundtrip(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["outer_pass"] = 2
    persist_state(tmp_path, st)
    assert load_or_init_state(tmp_path)["outer_pass"] == 2


def test_set_stage_status_persists(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    set_stage_status(tmp_path, st, "a", "resolved")
    on_disk = json.loads((tmp_path / "research_state.json").read_text(encoding="utf-8"))
    assert on_disk["stage_status"]["a"] == "resolved"
    assert st["stage_status"]["a"] == "resolved"


def test_is_stage_done_only_resolved(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["stage_status"] = {"a": "resolved", "b": "exhausted_unverified", "c": "blocked"}
    assert is_stage_done(st, "a") is True
    assert is_stage_done(st, "b") is False
    assert is_stage_done(st, "c") is False
    assert is_stage_done(st, "d") is False


def test_resume_point_skips_done(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["stage_status"] = {"a": "resolved", "b": "resolved"}
    st["inner_round"] = 2
    st["outer_pass"] = 1
    outer, stage, inner = resume_point(st)
    assert (outer, stage) == (1, "c")
    assert inner == 0


def test_resume_point_continues_incomplete_inner(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["stage_status"] = {"a": "resolved"}
    st["stage"] = "b"
    st["inner_round"] = 2
    outer, stage, inner = resume_point(st)
    assert (outer, stage, inner) == (1, "b", 2)


def test_pin_seed_is_read_only(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    pin_seed(tmp_path, st, {"company": "Acme", "currency": "USD"})
    assert st["seed_pin"] == {"company": "Acme", "currency": "USD"}
    pin_seed(tmp_path, st, {"company": "OTHER", "currency": "KRW"})
    assert st["seed_pin"] == {"company": "Acme", "currency": "USD"}

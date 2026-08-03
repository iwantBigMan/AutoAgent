"""게이트 정지 부수효과(pause_at_gate) 테스트: 산출물+고정 stdout 라인."""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.research.gates import GateTrigger, pause_at_gate


def test_pause_writes_status_and_prints_fixed_lines(tmp_path: Path, capsys) -> None:
    trigger = GateTrigger(kind="high_cost_deepen", reason="Entering high-cost deepen pass 2.", forced=True)
    state = {"outer_pass": 2, "stage": "b", "inner_round": 0}
    rc = pause_at_gate(tmp_path, trigger, state)
    assert rc == 0

    status = json.loads((tmp_path / "gate_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "waiting_for_human_approval"
    assert status["approved"] is False
    assert status["gate_kind"] == "high_cost_deepen"
    assert status["forced"] is True
    assert status["reason"] == "Entering high-cost deepen pass 2."
    assert status["run_dir"] == str(tmp_path)
    assert "--resume" in status["resume_command"]
    assert (tmp_path / "gate_required.md").exists()

    out = capsys.readouterr().out
    assert "RESEARCH_STATUS: waiting_for_human_approval" in out
    assert f"RUN_DIR: {tmp_path}" in out
    assert "RESUME_COMMAND: " in out
    assert "GATE_KIND: high_cost_deepen" in out


def test_pause_resume_command_matches_routed_convention(tmp_path: Path) -> None:
    from autoagent.workflows.routed_common import resume_command_for
    pause_at_gate(tmp_path, GateTrigger(kind="contradiction", reason="reversed", forced=True),
                  {"outer_pass": 1, "stage": "a", "inner_round": 1})
    status = json.loads((tmp_path / "gate_status.json").read_text(encoding="utf-8"))
    assert status["resume_command"] == resume_command_for(tmp_path)

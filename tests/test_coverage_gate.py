"""레이어 커버리지 프리미티브 단위테스트."""
from __future__ import annotations

import json

from autoagent.workflows.routed_common import missing_layers, coverage_banner_md, coverage_gate


def _route(*task_types):
    return {"layers": [{"task_type": t} for t in task_types]}


def test_missing_layers_none():
    assert missing_layers(_route("backend", "frontend"), ["backend", "frontend"]) == []


def test_missing_layers_reports_gap_in_order():
    assert missing_layers(_route("backend", "frontend"), ["backend"]) == ["frontend"]


def test_missing_layers_empty_route():
    assert missing_layers({"layers": []}, []) == []


def test_coverage_banner_complete():
    banner = coverage_banner_md(_route("backend", "frontend"), ["backend", "frontend"])
    assert "100%" in banner and "전 레이어" in banner


def test_coverage_banner_missing():
    banner = coverage_banner_md(_route("backend", "frontend"), ["backend"])
    assert "frontend" in banner and "미구현" in banner


def test_coverage_banner_empty_route_is_blank():
    assert coverage_banner_md({"layers": []}, []) == ""


def test_coverage_gate_writes_status_and_returns_zero(tmp_path):
    route = _route("backend", "frontend")
    rc = coverage_gate(tmp_path, route, ["frontend"])
    assert rc == 0
    status = json.loads((tmp_path / "coverage_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "blocked"
    assert status["kind"] == "layer_coverage"
    assert status["missing"] == ["frontend"]
    assert status["implemented"] == ["backend"]
    assert (tmp_path / "final_report.md").exists()

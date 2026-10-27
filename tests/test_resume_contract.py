"""재개 시 정책/예산 및 dry-run 상태 보존 검증."""
import json
from unittest.mock import patch

import pytest

from autoagent.cli import build_parser
from autoagent.config import load_config
from autoagent.run_state import save_execution_context, restore_execution_context, isolate_preview
from autoagent.runner import AgentCallBudget, AgentCallBudgetStopped
from autoagent.workflows.task_exec import run_task_graph_execution


def test_resume_restores_target_project_and_limits(tmp_path):
    cfg = load_config(tmp_path / "absent.json")
    cfg.workspace = tmp_path
    args = build_parser().parse_args(["--project", "sample", "--max-agent-calls", "2", "--max-review-rounds", "0"])
    save_execution_context(args, cfg, tmp_path, "original request")
    resumed = build_parser().parse_args(["--resume", str(tmp_path)])
    saved = restore_execution_context(resumed, tmp_path)
    assert (resumed.project, resumed.workspace, resumed.max_agent_calls, resumed.max_review_rounds) == ("sample", str(tmp_path), 2, 0)
    assert saved["request"] == "original request"
    override = build_parser().parse_args(["--resume", str(tmp_path), "--max-agent-calls", "10"])
    restore_execution_context(override, tmp_path)
    assert override.max_agent_calls == 10
    assert "data_go_kr_service_key" not in saved["policy"]


def test_budget_survives_restart_and_explicit_extension(tmp_path):
    budget = AgentCallBudget.for_run(1, tmp_path)
    budget.before_call(next_step="one", out_dir=tmp_path, dry_run=False)
    resumed = AgentCallBudget.for_run(1, tmp_path)
    with pytest.raises(AgentCallBudgetStopped):
        resumed.before_call(next_step="two", out_dir=tmp_path, dry_run=False)
    extended = AgentCallBudget.for_run(2, tmp_path)
    extended.before_call(next_step="two", out_dir=tmp_path, dry_run=False)
    assert json.loads((tmp_path / "agent_budget.json").read_text())["used_agent_calls"] == 2


def test_graph_dry_run_preserves_original_state(tmp_path):
    graph = {"goal": "goal", "tasks": [{"id": "001", "type": "backend", "status": "pending", "dependencies": []}]}
    path = tmp_path / "task_graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    before = path.read_bytes()
    cfg = load_config(tmp_path / "absent.json")
    cfg.workspace = tmp_path
    args = build_parser().parse_args(["--dry-run"])
    with patch("autoagent.workflows.task_exec.run_node", return_value="done"):
        assert run_task_graph_execution(args, cfg, tmp_path) == 0
    assert path.read_bytes() == before
    assert not (tmp_path / "agent_budget.json").exists()


def test_preview_does_not_overwrite_existing_artifacts(tmp_path):
    original = tmp_path / "approval_status.json"
    original.write_text('{"approved":false}', encoding="utf-8")
    preview = isolate_preview(tmp_path)
    (preview / original.name).write_text('{"approved":true}', encoding="utf-8")
    assert json.loads(original.read_text())["approved"] is False
    assert preview.parent == tmp_path / "previews"

"""임시 Git 저장소에서 의존 입력 전달과 통합 재개를 검증한다."""
import json
import subprocess
from unittest.mock import patch

import pytest

from autoagent.cli import build_parser
from autoagent.config import load_config
from autoagent.runner import AgentCallBudgetStopped
from autoagent.workflows import task_exec


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def setup_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    for key, value in [("user.name", "AutoAgent Test"), ("user.email", "test@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", str(tmp_path / "no-hooks"))]:
        git(repo, "config", key, value)
    (repo / "base.txt").write_text("baseline", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture baseline")
    cfg = load_config(tmp_path / "missing.json")
    cfg.workspace = repo
    return cfg


def test_dependencies_are_present_and_post_merge_resume_is_idempotent(tmp_path):
    cfg = setup_repo(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    graph = {"goal": "build A then B", "tasks": [
        {"id": "001", "type": "backend", "status": "pending", "dependencies": [], "allowed_paths": ["a.txt"]},
        {"id": "002", "type": "backend", "status": "pending", "dependencies": ["001"], "allowed_paths": ["b.txt"]},
    ]}
    (run / "task_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    args = build_parser().parse_args([])
    calls = []

    def implement(**kw):
        ws = kw["config"].workspace
        calls.append(ws.name)
        if ws.name == "001":
            (ws / "a.txt").write_text("A contract", encoding="utf-8")
        else:
            assert (ws / "a.txt").read_text() == "A contract"
            (ws / "b.txt").write_text("uses A", encoding="utf-8")
        return "IMPLEMENTATION_STATUS: completed", "REVIEW_STATUS: approved", "", True, False

    with patch.object(task_exec, "run_impl_review_fix", side_effect=implement), \
         patch("autoagent.workflows.routed_impl.run_final_review", return_value="FINAL_STATUS: approved") as review, \
         patch.object(task_exec, "run_evaluation", side_effect=[AgentCallBudgetStopped("evaluation", run), "EVALUATION_STATUS: passed"]), \
         patch.object(task_exec, "run_final_report", return_value="completed"):
        assert task_exec.run_task_graph_execution(args, cfg, run) == 0
        assert not json.loads((run / "integration_progress.json").read_text()).get("complete")
        assert task_exec.run_task_graph_execution(args, cfg, run) == 0
        assert calls == ["001", "002"]
        assert review.call_count == 3  # 두 node 리뷰 + 통합 리뷰. 재개 시 통합 리뷰를 반복하지 않는다.
        assert task_exec.run_task_graph_execution(args, cfg, run) == 0
    stamp = json.loads((run / "exec_state.json").read_text())["stamp"]
    assert git(cfg.workspace, "show", f"aa-integration/{stamp}:b.txt") == "uses A"
    assert git(cfg.workspace, "status", "--porcelain") == ""
    assert not (cfg.workspace / "a.txt").exists()


@pytest.mark.parametrize("tasks", [
    [{"id": "../outside"}], [{"id": "001"}, {"id": "001"}],
    [{"id": "001", "dependencies": ["missing"]}],
])
def test_invalid_graph_is_rejected_before_worktree_operations(tasks):
    with pytest.raises(task_exec.CyclicGraphError):
        task_exec.topological_waves(tasks)

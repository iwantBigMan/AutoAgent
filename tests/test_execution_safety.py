"""실행 안전 경계의 회귀 테스트. 실제 모델은 호출하지 않는다."""
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from autoagent.artifacts import DEFAULT_CONFIG, render_template
from autoagent.cli import build_parser
from autoagent.config import load_config
from autoagent.roles import load_roles, resolve_role
from autoagent.routing import route_task
from autoagent.runner import AgentCallBudget
from autoagent.workflows import task_exec


def config(tmp_path):
    cfg = load_config(tmp_path / "absent.json")
    cfg.workspace = tmp_path
    return cfg


@pytest.mark.parametrize("role", ["context", "architect", "validation", "reviewer", "final-review", "evaluation", "report"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_nonmutating_roles_are_readonly(tmp_path, role, agent):
    resolved = resolve_role(load_roles(DEFAULT_CONFIG.parent)[role], config=config(tmp_path),
                            route={}, request="", agent=agent, read_only=False)
    assert not resolved.mutating
    assert (resolved.sandbox == "read-only") if agent == "codex" else (resolved.permission_mode == "plan")


@pytest.mark.parametrize("text", ["API review only", "Do not modify React components; review only",
                                     "DB를 수정하지 말고 검토만 해줘", "React 코드 리뷰만 해줘"])
def test_review_intent_cannot_enter_implementation(text):
    route = route_task("auto", text)
    assert route["task_type"] == "review"
    assert not route["layers"]


def test_node_prompt_contains_exact_scope(tmp_path):
    node = {"id": "001", "description": "one change", "allowed_paths": ["only.py"], "blocked_paths": ["secret.py"]}
    values = task_exec._node_common(config(tmp_path), node, route_task("backend", "add endpoint"), "large goal", 1)
    assert "only.py" in values["REQUEST"] and "secret.py" in values["REQUEST"]
    assert "ONLY task 001" in values["REQUEST"]


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("task", ["backend", "frontend"])
@pytest.mark.parametrize("step,marker", [("impl", "IMPLEMENTATION_STATUS"), ("fix", "FIX_STATUS")])
def test_all_implementation_prompts_declare_machine_status(agent, task, step, marker):
    prompt = render_template(f"{agent}_{task}_{step}.md", {})
    for status in ("completed", "partial", "blocked"):
        assert f"{marker}: {status}" in prompt


@pytest.mark.parametrize("impl,stopped,final,scope,expected", [
    ("IMPLEMENTATION_STATUS: blocked", False, "FINAL_STATUS: approved", [], "failed"),
    ("IMPLEMENTATION_STATUS: completed", True, "FINAL_STATUS: approved", [], "stopped"),
    ("IMPLEMENTATION_STATUS: completed", False, "FINAL_STATUS: blocked", [], "failed"),
    ("IMPLEMENTATION_STATUS: completed", False, "FINAL_STATUS: approved", ["outside scope"], "blocked"),
    ("FIX_STATUS: completed", False, "FINAL_STATUS: approved", [], "done"),
])
def test_only_approved_in_scope_node_can_commit(tmp_path, impl, stopped, final, scope, expected):
    args = build_parser().parse_args([])
    node = {"id": "001", "type": "backend", "allowed_paths": ["ok.py"]}
    with patch("autoagent.worktree.add_worktree"), patch("autoagent.worktree.branch_exists", return_value=False), \
         patch("autoagent.worktree.scope_violations", return_value=scope), \
         patch.object(task_exec.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as git, \
         patch.object(task_exec, "run_impl_review_fix", return_value=(impl, "REVIEW_STATUS: blocked", "", False, stopped)), \
         patch("autoagent.workflows.routed_impl.run_final_review", return_value=final):
        result = task_exec.run_node(args=args, config=config(tmp_path), task_graph={"goal": "goal"}, node=node,
                                   budget=AgentCallBudget(9), run_dir=tmp_path, stamp="test", baseline="BASE")
    assert result == expected
    commits = [c for c in git.call_args_list if "commit" in c.args[0]]
    assert bool(commits) == (expected == "done")

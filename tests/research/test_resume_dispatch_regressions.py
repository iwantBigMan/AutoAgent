"""Research resume, provider dispatch, and downstream evidence contracts."""
import json
import os

import pytest

from autoagent.cli import build_parser
from autoagent.config import load_config
from autoagent.research.convergence import diff_verified_claims
from autoagent.research.types import StageResult, Verdict
from autoagent.runner import AgentCallBudget
from autoagent.workflows import research as R
from autoagent.workflows import routed_impl as I


def make_context(tmp_path, provider=None):
    config = load_config(tmp_path / "absent.json")
    config.workspace = tmp_path / "project"
    config.workspace.mkdir(exist_ok=True)
    config.solo_provider = provider
    args = build_parser().parse_args([])
    return R.ResearchContext(args=args, config=config, request="research request", run_dir=tmp_path,
                             budget=AgentCallBudget(30), seed_contract="{}", state={})


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("workflow", ["research", "routed"])
def test_solo_executes_only_selected_provider(tmp_path, monkeypatch, provider, workflow):
    ctx = make_context(tmp_path, provider)
    module = R if workflow == "research" else I
    captured = []
    selected = getattr(ctx.config, f"{provider}_command")

    def require(command):
        assert command == selected
        return f"resolved-{provider}"

    monkeypatch.setattr(module, "require_command", require)
    monkeypatch.setattr(module, "run_process", lambda **kw: captured.append(kw) or "OK")
    kwargs = dict(agent="codex" if provider == "claude" else "claude", role_id="reviewer",
                  name="review", prompt_name="codex_backend_review.md", prompt_values={},
                  next_step="review", dry_output="")
    if workflow == "research":
        R._run_agent_step(ctx, **kwargs)
    else:
        I.run_role_step(args=ctx.args, config=ctx.config, run_dir=tmp_path, budget=ctx.budget,
                        route={}, request="review", mutating=False, **kwargs)
    command = captured[0]["command"]
    assert command[0] == f"resolved-{provider}"
    if provider == "claude" and workflow == "research":
        assert "--allowedTools" in command
    else:
        assert ("--permission-mode" if provider == "claude" else "--sandbox") in command


@pytest.mark.parametrize("read_only", [False, True])
def test_csv_write_scope_is_isolated_from_project(tmp_path, monkeypatch, read_only):
    ctx = make_context(tmp_path)
    ctx.args.read_only = read_only
    captured = []
    monkeypatch.setattr(R, "require_command", lambda command: command)
    monkeypatch.setattr(R, "run_process", lambda **kw: captured.append(kw) or "OK")
    R._run_agent_step(ctx, agent="codex", role_id="researcher", name="stage_c_p1_r1",
                      prompt_name="c_codex_research.md", prompt_values={}, next_step="c", dry_output="")
    call = captured[0]
    command = call["command"]
    if read_only:
        assert call["cwd"] == ctx.config.workspace
        assert command[command.index("--sandbox") + 1] == "read-only"
    else:
        assert call["cwd"].is_relative_to(tmp_path / "data")
        assert command[command.index("-C") + 1] == str(call["cwd"])
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        assert str(call["cwd"]) in call["prompt"]
    assert ctx.config.workspace == tmp_path / "project"


def test_deepen_approval_resumes_new_pass_and_preserves_all_evidence(tmp_path, monkeypatch):
    ctx = make_context(tmp_path)
    ctx.config.research_max_outer = 2
    ctx.config.research_min_new_claims = 1
    calls = []
    monkeypatch.setattr(R, "_run_agent_step", lambda *a, **k: json.dumps({
        "company": "example", "market": "example", "base_currency": "KRW",
        "period": "2025", "unit": "won",
    }))
    monkeypatch.setattr(R, "_run_openapi_collection", lambda ctx: None)
    monkeypatch.setattr(R, "write_desktop_report", lambda html, name: tmp_path / name)
    monkeypatch.setattr(os, "startfile", lambda path: None, raising=False)

    def stage(stage, outer_pass, ctx):
        calls.append((outer_pass, stage))
        ctx.stage_outputs[stage] = f"EVIDENCE_{stage}_PASS_{outer_pass}"
        ctx.state["stage_outputs"] = dict(ctx.stage_outputs)
        return StageResult(stage_id=stage, status="resolved", output_path="output.md", inner_rounds=1,
                           verdict=Verdict(status="pass", adapter="test", stage_id=stage, findings=[],
                                           raw={"verified_claims": [{"id": f"{stage}-{outer_pass}", "value": 1}]}))

    monkeypatch.setattr(R, "run_stage_loop", stage)
    assert R.run_research_workflow(ctx.args, ctx.config, ctx.request, tmp_path) == 0
    assert calls == [(1, s) for s in R.STAGE_ORDER]
    gate = json.loads((tmp_path / "gate_status.json").read_text(encoding="utf-8"))
    assert gate["gate_kind"] == "high_cost_deepen"
    assert gate["status"] == "waiting_for_human_approval"
    assert not (tmp_path / "final_report.html").exists()

    ctx.args.resume = str(tmp_path)
    assert R.run_research_workflow(ctx.args, ctx.config, None, tmp_path) == 0
    assert calls == [(p, s) for p in (1, 2) for s in R.STAGE_ORDER]
    report = (tmp_path / "final_report.html").read_text(encoding="utf-8")
    for s in R.STAGE_ORDER:
        assert f"EVIDENCE_{s}_PASS_2" in report
    state = json.loads((tmp_path / "research_state.json").read_text(encoding="utf-8"))
    assert state["approved_deepen_passes"] == [2]
    assert state["request"] == ctx.request
    assert len(state["verified_claims"]) == 10
    assert R.run_research_workflow(ctx.args, ctx.config, None, tmp_path) == 0
    assert len(calls) == 10


def test_prompt_claim_ids_detect_reversals():
    delta = diff_verified_claims([{"id": "revenue", "text": "up"}],
                                 [{"id": "revenue", "text": "down"}])
    assert delta.delta_count == 0
    assert delta.contradictions[0]["claim_id"] == "revenue"

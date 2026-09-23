"""수집 스테이지 배선 테스트: 키 없음 스킵 / 계획→실행→manifest→state / 프롬프트 미치환 잔존 없음."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from autoagent import config as config_mod
from autoagent.data import openapi as oa
from autoagent.artifacts import render_template
from autoagent.runner import AgentCallBudget
from autoagent.workflows import research as research_mod


def _ctx(tmp_path: Path, key: str | None) -> research_mod.ResearchContext:
    cfg = config_mod.load_config(tmp_path / "absent.json")
    cfg.workspace = tmp_path
    cfg.data_go_kr_service_key = key
    args = Namespace(dry_run=False, read_only=False, max_agent_calls=0)
    return research_mod.ResearchContext(args=args, config=cfg, request="최근 소프트웨어 용역 입찰공고 조사",
                                        run_dir=tmp_path, budget=AgentCallBudget(0), seed_contract="{}", state={})


def test_no_key_skips(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    ctx = _ctx(tmp_path, None)
    research_mod._run_openapi_collection(ctx)
    assert ctx.state["openapi"]["skipped"] == "no_key"
    assert "[openapi]" in capsys.readouterr().out
    assert not (tmp_path / "openapi_manifest.json").exists()


def test_plan_execute_manifest_state(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, "KEY")
    reg = oa.load_registry()
    sid = "user_info" if reg.services["user_info"]["operations"] else next(s for s, v in reg.services.items() if v["operations"])
    op = next(iter(reg.services[sid]["operations"]))
    plan_text = (f"{oa.PLAN_MARKER}\n```json\n" + json.dumps({"calls": [
        {"id": "c1", "service": sid, "operation": op, "params": {"numOfRows": "1"}, "purpose": "t"},
        {"id": "c2", "service": "ghost", "operation": "x", "params": {}}]}) + "\n```\n")
    captured: dict = {}

    def fake_step(ctx_, **kw):
        captured.update(kw)
        return plan_text

    monkeypatch.setattr(research_mod, "_run_agent_step", fake_step)
    body = json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": [{"x": 1}]}}})
    research_mod._run_openapi_collection(ctx, fetch=lambda u, t: oa.FetchResult(200, body))
    assert captured["name"] == "01_openapi_plan" and captured["role_id"] == "openapi_planner"
    assert "KEY" not in captured["prompt_values"]["OPENAPI_CATALOG"]
    manifest = json.loads((tmp_path / "openapi_manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["row_count"] == 1 and manifest["rejected"][0]["id"] == "c2"
    assert ctx.state["openapi"] == {"calls": 1, "rejected": 1, "errors": 0, "manifest": "openapi_manifest.json"}
    assert (tmp_path / "research_state.json").exists()
    # 재호출은 스킵(재개 안전).
    monkeypatch.setattr(research_mod, "_run_agent_step", lambda *a, **k: (_ for _ in ()).throw(AssertionError("재호출")))
    research_mod._run_openapi_collection(ctx)


def test_stage_prompts_render_openapi_data():
    values = {k: "v" for k in (
        "REQUEST", "WORKSPACE", "SEED_CONTRACT", "SEED_PIN", "STAGE_ID", "OUTER_PASS", "INNER_ROUND", "PRIOR_FEEDBACK",
        "INNER_FEEDBACK", "DEEPEN_DELTA", "PRIOR_VERDICT_FEEDBACK", "PRIOR_STAGE_SUMMARY", "STAGE_A_OUTPUT", "CSV_PATHS",
        "MIN_FINDINGS", "SEED_COMPANY", "SEED_MARKET", "SEED_CURRENCY", "SEED_PERIOD", "SEED_UNIT", "SEED_AS_OF")}
    values["OPENAPI_DATA"] = "TABLE_MARK"
    for name in ("a_researcher.md", "b_market_researcher.md", "c_codex_research.md", "d_fact_report.md", "derive.md"):
        out = render_template(name, values)
        assert "TABLE_MARK" in out and "{{" not in out, name
    plan = render_template("openapi_plan.md", {"REQUEST": "r", "SEED_CONTRACT": "s", "OPENAPI_CATALOG": "CAT", "MAX_CALLS": "12"})
    assert "CAT" in plan and oa.PLAN_MARKER in plan and "{{" not in plan


def test_config_key_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "ENVKEY")
    assert config_mod.load_config(tmp_path / "absent.json").data_go_kr_service_key == "ENVKEY"
    p = tmp_path / "c.json"
    p.write_text('{"workspace": ".", "data_go_kr_service_key": "CFGKEY", "openapi_max_calls": 3}', encoding="utf-8")
    cfg = config_mod.load_config(p)
    assert cfg.data_go_kr_service_key == "CFGKEY" and cfg.openapi_max_calls == 3

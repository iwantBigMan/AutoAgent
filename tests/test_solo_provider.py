"""solo_provider 폴백 단위테스트."""
from __future__ import annotations

import json

import pytest

from autoagent.config import load_config


def _write_cfg(tmp_path, extra):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"workspace": str(tmp_path), **extra}), encoding="utf-8")
    return p


def test_solo_provider_default_none(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert cfg.solo_provider is None


def test_solo_provider_valid(tmp_path):
    assert load_config(_write_cfg(tmp_path, {"solo_provider": "claude"})).solo_provider == "claude"
    assert load_config(_write_cfg(tmp_path, {"solo_provider": "codex"})).solo_provider == "codex"


def test_solo_provider_invalid_rejected(tmp_path):
    with pytest.raises(SystemExit):
        load_config(_write_cfg(tmp_path, {"solo_provider": "gpt4"}))


def test_codex_light_tier_symmetric(tmp_path):
    # solo=codex에서 light tier 역할(context/report)이 KeyError 안 나도록 codex 팔레트에 light 존재.
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert "light" in cfg.tiers["codex"]
    assert cfg.tiers["codex"]["light"]["model"] == cfg.codex_model


from autoagent.roles import load_roles, resolve_role
from autoagent.artifacts import DEFAULT_CONFIG

_ROUTE = {"task_type": "backend", "risk_level": "medium", "subtype": "general"}


def _roles():
    return load_roles(DEFAULT_CONFIG.parent)


def test_resolve_role_null_is_noop(tmp_path):
    # solo_provider=null이면 agent 인자 그대로(교차모델 동형).
    cfg = load_config(_write_cfg(tmp_path, {}))
    roles = _roles()
    r = resolve_role(roles["implementer"], config=cfg, route=_ROUTE, request="add api", agent="codex", read_only=False)
    assert r.agent == "codex"


def test_resolve_role_solo_claude_overrides(tmp_path):
    # solo=claude면 상류 agent가 codex여도 claude로 덮이고 tier는 claude 팔레트에서.
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    roles = _roles()
    r = resolve_role(roles["implementer"], config=cfg, route=_ROUTE, request="add api", agent="codex", read_only=False)
    assert r.agent == "claude"
    assert r.model == cfg.tiers["claude"]["standard"]["model"]


def test_resolve_role_solo_codex_light_role_no_keyerror(tmp_path):
    # solo=codex + light tier 역할(report)이 codex 팔레트 light를 조회해 KeyError 없이 resolve.
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "codex"}))
    roles = _roles()
    r = resolve_role(roles["report"], config=cfg, route=_ROUTE, request="write report", agent="claude", read_only=False)
    assert r.agent == "codex"
    assert r.model == cfg.tiers["codex"]["light"]["model"]


from autoagent.runner import solo_command


def test_solo_command_claude_plan(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    cmd = solo_command(cfg, intent="plan", resolved_command="claude.cmd")
    assert cmd[0] == "claude.cmd"
    assert "--permission-mode" in cmd and "plan" in cmd


def test_solo_command_claude_execute_acceptedits(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    cmd = solo_command(cfg, intent="execute", resolved_command="claude.cmd")
    assert "acceptEdits" in cmd  # 기본 claude_impl_permission


def test_solo_command_codex_review_readonly(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "codex"}))
    cmd = solo_command(cfg, intent="review", resolved_command="codex.cmd")
    assert cmd[0] == "codex.cmd"
    assert "--sandbox" in cmd and "read-only" in cmd


def test_solo_command_codex_execute_uses_config_sandbox(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "codex", "codex_sandbox": "workspace-write"}))
    cmd = solo_command(cfg, intent="execute", resolved_command="codex.cmd")
    assert "workspace-write" in cmd


from autoagent.workflows.routed_impl import maybe_prepend_adversarial


def test_preamble_null_is_noop(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert maybe_prepend_adversarial("BODY", cfg, is_review=True) == "BODY"


def test_preamble_solo_non_review_is_noop(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    assert maybe_prepend_adversarial("BODY", cfg, is_review=False) == "BODY"


def test_preamble_solo_review_prepends(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    out = maybe_prepend_adversarial("BODY", cfg, is_review=True)
    assert out.endswith("BODY")
    assert "적대적 리뷰 지침" in out
    assert out != "BODY"

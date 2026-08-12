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

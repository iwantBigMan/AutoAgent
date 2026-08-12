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

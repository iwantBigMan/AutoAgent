"""포터빌리티(하드코딩 제거+자동 감지) 단위테스트: CLI 명령 감지·workspace 미지정."""
from __future__ import annotations

import pytest

from autoagent import config as config_mod
from autoagent.artifacts import ROOT


def test_autodetect_cli_explicit_passthrough(monkeypatch):
    # 명시값은 which를 묻지 않고 그대로 반환한다(무회귀 계약).
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    assert config_mod.autodetect_cli("my-claude.exe", ["claude.cmd", "claude"]) == "my-claude.exe"


def test_autodetect_cli_auto_and_none_pick_first_found(monkeypatch):
    # 미지정(None)과 "auto"는 동일 취급 — 후보 순회 첫 발견 '이름'을 반환.
    monkeypatch.setattr(
        config_mod.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    assert config_mod.autodetect_cli(None, ["claude.cmd", "claude"]) == "claude"
    assert config_mod.autodetect_cli("auto", ["claude.cmd", "claude"]) == "claude"


def test_autodetect_cli_none_found_falls_back_to_first(monkeypatch):
    # 전부 미발견이어도 raise하지 않는다(dry-run은 CLI 없이도 돌아야 함).
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    assert config_mod.autodetect_cli(None, ["codex.cmd", "codex"]) == "codex.cmd"


def test_load_config_workspace_none_without_sources(tmp_path, monkeypatch):
    # config 파일도 env도 없으면 workspace는 None(하드코딩 폴백 제거).
    monkeypatch.delenv("AUTOAGENT_WORKSPACE", raising=False)
    cfg = config_mod.load_config(tmp_path / "absent.json")
    assert cfg.workspace is None


def test_load_config_workspace_env_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAGENT_WORKSPACE", str(tmp_path))
    cfg = config_mod.load_config(tmp_path / "absent.json")
    assert cfg.workspace == tmp_path


def test_load_config_explicit_commands_preserved(tmp_path, monkeypatch):
    # config 명시값은 which 결과와 무관하게 그대로(소유자 머신 무회귀).
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    p = tmp_path / "c.json"
    p.write_text(
        '{"workspace": ".", "claude_command": "claude.cmd", "codex_command": "codex.cmd"}',
        encoding="utf-8",
    )
    cfg = config_mod.load_config(p)
    assert cfg.claude_command == "claude.cmd"
    assert cfg.codex_command == "codex.cmd"


def test_write_home_pointer_creates_and_is_idempotent(tmp_path):
    from autoagent.cli import write_home_pointer
    pointer = tmp_path / ".autoagent" / "home"
    write_home_pointer(pointer)
    assert pointer.read_text(encoding="utf-8").strip() == str(ROOT)
    before = pointer.stat().st_mtime_ns
    write_home_pointer(pointer)  # 같은 내용이면 재기록하지 않는다
    assert pointer.stat().st_mtime_ns == before


def test_write_home_pointer_failure_warns_and_survives(tmp_path, capsys):
    from autoagent.cli import write_home_pointer
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # 부모 자리에 파일을 놓아 mkdir 실패 유도
    write_home_pointer(blocker / "home")       # 예외가 밖으로 새면 안 된다
    assert "[home]" in capsys.readouterr().out


def test_write_home_pointer_heals_corrupted_pointer(tmp_path):
    """F2: 기존 포인터 파일이 비UTF8 바이트로 손상돼 있어도 크래시하지 않고
    올바른 ROOT 내용으로 자가 치유(재기록)해야 한다."""
    from autoagent.cli import write_home_pointer
    pointer = tmp_path / ".autoagent" / "home"
    pointer.parent.mkdir(parents=True)
    pointer.write_bytes(b"\xff\xfe garbage")  # UTF-8로 디코딩 불가능한 바이트열
    write_home_pointer(pointer)  # UnicodeDecodeError 없이 통과해야 한다
    assert pointer.read_text(encoding="utf-8").strip() == str(ROOT)


def test_require_workspace_raises_guidance():
    from types import SimpleNamespace
    from autoagent.cli import require_workspace
    with pytest.raises(SystemExit) as exc:
        require_workspace(SimpleNamespace(workspace=None))
    assert "AUTOAGENT_WORKSPACE" in str(exc.value)


def test_require_workspace_passes_when_set(tmp_path):
    from types import SimpleNamespace
    from autoagent.cli import require_workspace
    require_workspace(SimpleNamespace(workspace=tmp_path))  # raise 없음

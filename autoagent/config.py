from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Config:
    workspace: Path
    claude_command: str
    codex_command: str
    codex_sandbox: str
    codex_approval: str
    timeout_seconds: int


def load_config(path: Path) -> Config:
    raw: dict[str, Any] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))

    workspace = Path(
        raw.get("workspace")
        or os.environ.get("AUTOAGENT_WORKSPACE")
        or r"C:\Users\systran\Desktop\LanguageDetection"
    )

    return Config(
        workspace=workspace,
        claude_command=raw.get("claude_command") or "claude.cmd",
        codex_command=raw.get("codex_command") or "codex.cmd",
        codex_sandbox=raw.get("codex_sandbox") or "workspace-write",
        codex_approval=raw.get("codex_approval") or "never",
        timeout_seconds=int(raw.get("timeout_seconds") or 3600),
    )

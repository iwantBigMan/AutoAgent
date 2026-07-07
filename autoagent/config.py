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
    claude_model: str
    claude_high_risk_model: str
    codex_model: str
    codex_reasoning_effort: str
    default_max_agent_calls_review: int
    default_max_agent_calls_implementation: int


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
        claude_model=raw.get("claude_model") or "sonnet",
        claude_high_risk_model=raw.get("claude_high_risk_model") or "opus",
        codex_model=raw.get("codex_model") or "gpt-5.5",
        codex_reasoning_effort=raw.get("codex_reasoning_effort") or "high",
        default_max_agent_calls_review=int(raw.get("default_max_agent_calls_review") or 5),
        default_max_agent_calls_implementation=int(raw.get("default_max_agent_calls_implementation") or 9),
    )

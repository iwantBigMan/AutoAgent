"""설정(Config) 로딩.

autoagent.config.json(없으면 기본값)에서 모델·명령·샌드박스·effort·예산 등
하네스 전역 설정을 읽어 Config 데이터클래스로 만든다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """하네스 전역 설정 값 묶음(모델/effort/샌드박스/타임아웃/예산 기본값 등)."""

    workspace: Path
    claude_command: str
    codex_command: str
    codex_sandbox: str
    codex_approval: str
    timeout_seconds: int
    claude_model: str
    claude_high_risk_model: str
    claude_effort: str
    claude_high_risk_effort: str
    codex_model: str
    codex_reasoning_effort: str
    default_max_agent_calls_review: int
    default_max_agent_calls_implementation: int


def load_config(path: Path) -> Config:
    """config JSON을 읽어 Config를 만든다. 파일/키가 없으면 각 항목 기본값을 쓴다.

    workspace는 config > AUTOAGENT_WORKSPACE env > 하드코딩 기본값 순으로 결정.
    """
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
        # headless `claude -p --effort`는 low/medium/high/xhigh/max만 받는다("ultracode"는
        # 대화형 전용이라 무시됨). high-risk(opus)는 ultracode의 추론 강도에 해당하는 xhigh.
        claude_effort=raw.get("claude_effort") or "high",
        claude_high_risk_effort=raw.get("claude_high_risk_effort") or "xhigh",
        codex_model=raw.get("codex_model") or "gpt-5.5",
        codex_reasoning_effort=raw.get("codex_reasoning_effort") or "high",
        default_max_agent_calls_review=int(raw.get("default_max_agent_calls_review") or 5),
        default_max_agent_calls_implementation=int(raw.get("default_max_agent_calls_implementation") or 9),
    )

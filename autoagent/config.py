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

from autoagent.artifacts import validate_project_name


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
    claude_impl_permission: str
    codex_model: str
    codex_reasoning_effort: str
    default_max_agent_calls_review: int
    default_max_agent_calls_implementation: int
    max_parallel_lanes: int = 2


def load_config(path: Path, project: str | None = None) -> Config:
    """config JSON을 읽어 Config를 만든다. 파일/키가 없으면 각 항목 기본값을 쓴다.

    workspace는 (프로젝트 config) > 전역 config > AUTOAGENT_WORKSPACE env >
    하드코딩 기본값 순으로 결정. project가 없으면 전역 config만 읽어 기존과 동일하게 동작한다.
    """
    raw: dict[str, Any] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))

    if project is not None:
        # 경로 이탈(path traversal) 방지: project는 반드시 단일 path segment여야 한다.
        # 빈 문자열도 여기서 validate_project_name이 거부한다(if project:면 falsy라 조용히 폴백).
        validate_project_name(project)
        # path 기본값이 ROOT/autoagent.config.json이라 path.parent가 곧 ROOT.
        # cli.py의 roles 로딩(DEFAULT_CONFIG.parent)과 같은 결합을 이미 저장소가 쓰고 있다.
        project_config_path = path.parent / "projects" / project / "config.json"
        if not project_config_path.exists():
            raise SystemExit(f"Project config not found: {project_config_path}")
        project_raw = json.loads(project_config_path.read_text(encoding="utf-8-sig"))
        raw = {**raw, **project_raw}  # 얕은 병합: 프로젝트 config가 전역을 키 단위로 덮는다.

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
        # mutating(구현/수정) Claude 스텝의 권한 posture. 헤드리스에선 승인 TTY가 없어
        # 편집이 차단되므로 최소 acceptEdits가 필요하다.
        #   "acceptEdits"      = 파일 편집만 자동, bash/네트워크는 차단(안전 기본값).
        #   "bypassPermissions"= --dangerously-skip-permissions, 명령·네트워크까지 자율(opt-in, 무샌드박스).
        claude_impl_permission=raw.get("claude_impl_permission") or "acceptEdits",
        codex_model=raw.get("codex_model") or "gpt-5.5",
        codex_reasoning_effort=raw.get("codex_reasoning_effort") or "high",
        default_max_agent_calls_review=int(raw.get("default_max_agent_calls_review") or 5),
        default_max_agent_calls_implementation=int(raw.get("default_max_agent_calls_implementation") or 9),
        max_parallel_lanes=int(raw.get("max_parallel_lanes") or 2),
    )

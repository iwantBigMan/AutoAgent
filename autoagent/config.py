"""설정(Config) 로딩.

autoagent.config.json(없으면 기본값)에서 모델·명령·샌드박스·effort·예산 등
하네스 전역 설정을 읽어 Config 데이터클래스로 만든다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoagent.artifacts import validate_project_name


def _effort_default(value: str | None, default: str) -> str:
    """None(키 누락/null)이면 기본값을, 명시적 ""(주입 생략 opt-out)는 그대로 보존한다.

    다른 설정은 `raw.get(k) or default` 관용구를 쓰지만 codex effort만은 빈 문자열이
    "주입하지 않음"이라는 의미를 갖기 때문에 falsy 폴백을 쓰지 않는다.
    """
    return default if value is None else value


def _merge_tiers(
    default: dict[str, dict[str, dict[str, Any]]],
    override: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """기본 팔레트에 config override를 (agent, 티어) 단위로 필드 병합한다.

    override가 준 티어의 필드만 기본값 위에 덮는다(effort만 바꾸는 부분 override 허용).
    override에만 있는 agent/티어는 그대로 추가한다.
    """
    merged = {a: {t: dict(fields) for t, fields in tiers.items()} for a, tiers in default.items()}
    for agent, tiers in override.items():
        dst = merged.setdefault(agent, {})
        for tname, fields in tiers.items():
            base = dict(dst.get(tname, {}))
            base.update(fields or {})
            dst[tname] = base
    return merged


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
    # codex 기본 추론 강도(medium). high-risk가 아니면 이 값을 CLI에 주입한다.
    codex_reasoning_effort: str
    # high-risk 조건을 만족할 때만 승격하는 codex 추론 강도(high).
    codex_high_risk_effort: str
    default_max_agent_calls_review: int
    default_max_agent_calls_implementation: int
    max_parallel_lanes: int = 2
    # 1단계 검증 스테이지(구현/수정 뒤 DB-free 실행 검증). 기본 활성.
    verification_enabled: bool = True
    # 실행할 검증 커맨드 목록(allowlist). 비어 있으면 verification.default_commands로 채운다.
    # 각 항목: {"name": str, "command": [str, ...], "cwd": 선택(workspace 상대)}.
    verification_commands: list[dict[str, Any]] = field(default_factory=list)
    verification_timeout_seconds: int = 1800
    # Claude 서브프로세스에 주입할 MCP 툴 allowlist(예: ["mcp__serena", "mcp__context7"]).
    # 비어 있으면(기본) --allowedTools를 붙이지 않아 기존 명령과 바이트 동일하다(opt-in).
    mcp_allowed_tools: list[str] = field(default_factory=list)
    # 하네스가 Claude에 --mcp-config로 넘길 MCP 서버 정의(.mcp.json의 mcpServers 형태).
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    # mcp_servers로 생성한 Claude용 config 파일 경로(cli가 런타임에 채움; config JSON엔 없음).
    mcp_config_path: str | None = None
    # 역할↔모델 매핑 팔레트: tiers[agent][tier명] = {"model": str, "effort": str | None}.
    # load_config가 기존 전역값에서 기본 팔레트를 합성하고 config의 "tiers"로 덮는다.
    tiers: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # 크로스모델 검증기가 강제하는 최소 findings 쿼터(§4.1②). 미만이고 unchallenged_but_weak도
    # 비었으면 코드가 needs_changes로 강등한다(무결 자유선언 방지).
    crossmodel_min_findings: int = 3
    # 계층 예산(§6.4): 전역 max_agent_calls 위에 얹는 스테이지별/outer별 상한 + capture 절단.
    research_per_stage_calls: int = 6
    research_per_outer_calls: int = 40
    research_max_capture_chars: int = 12000


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

    # 모델/effort 기본값(팔레트 합성과 Config 양쪽에서 재사용).
    claude_model = raw.get("claude_model") or "sonnet"
    claude_high_risk_model = raw.get("claude_high_risk_model") or "opus"
    claude_effort = raw.get("claude_effort") or "high"
    claude_high_risk_effort = raw.get("claude_high_risk_effort") or "xhigh"
    codex_model = raw.get("codex_model") or "gpt-5.6-sol"
    codex_reasoning_effort = _effort_default(raw.get("codex_reasoning_effort"), "medium")
    codex_high_risk_effort = _effort_default(raw.get("codex_high_risk_effort"), "high")

    # 기본 팔레트 — 기존 전역값에서 합성해 동작을 보존한다. cheap 티어는 재튜닝/B 대비 "정의만".
    default_tiers = {
        "claude": {
            "standard": {"model": claude_model, "effort": claude_effort},
            "deep": {"model": claude_high_risk_model, "effort": claude_high_risk_effort},
            "light": {"model": claude_model, "effort": None},
            "cheap": {"model": "haiku", "effort": None},
        },
        "codex": {
            "standard": {"model": codex_model, "effort": codex_reasoning_effort},
            "deep": {"model": codex_model, "effort": codex_high_risk_effort},
            "cheap": {"model": "gpt-5.6-terra", "effort": "low"},
        },
    }
    tiers = _merge_tiers(default_tiers, raw.get("tiers") or {})

    return Config(
        workspace=workspace,
        claude_command=raw.get("claude_command") or "claude.cmd",
        codex_command=raw.get("codex_command") or "codex.cmd",
        codex_sandbox=raw.get("codex_sandbox") or "workspace-write",
        codex_approval=raw.get("codex_approval") or "never",
        timeout_seconds=int(raw.get("timeout_seconds") or 3600),
        claude_model=claude_model,
        claude_high_risk_model=claude_high_risk_model,
        # headless `claude -p --effort`는 low/medium/high/xhigh/max만 받는다("ultracode"는
        # 대화형 전용이라 무시됨). high-risk(opus)는 ultracode의 추론 강도에 해당하는 xhigh.
        claude_effort=claude_effort,
        claude_high_risk_effort=claude_high_risk_effort,
        # mutating(구현/수정) Claude 스텝의 권한 posture. 헤드리스에선 승인 TTY가 없어
        # 편집이 차단되므로 최소 acceptEdits가 필요하다.
        #   "acceptEdits"      = 파일 편집만 자동, bash/네트워크는 차단(안전 기본값).
        #   "bypassPermissions"= --dangerously-skip-permissions, 명령·네트워크까지 자율(opt-in, 무샌드박스).
        claude_impl_permission=raw.get("claude_impl_permission") or "acceptEdits",
        codex_model=codex_model,
        # codex effort는 `codex -c model_reasoning_effort=...`로 실제 주입한다(minimal/low/
        # medium/high/xhigh). 기본 medium, high-risk 구현/수정 때만 high로 승격.
        codex_reasoning_effort=codex_reasoning_effort,
        codex_high_risk_effort=codex_high_risk_effort,
        default_max_agent_calls_review=int(raw.get("default_max_agent_calls_review") or 5),
        default_max_agent_calls_implementation=int(raw.get("default_max_agent_calls_implementation") or 9),
        max_parallel_lanes=int(raw.get("max_parallel_lanes") or 2),
        mcp_allowed_tools=list(raw.get("mcp_allowed_tools") or []),
        mcp_servers=dict(raw.get("mcp_servers") or {}),
        verification_enabled=bool(raw.get("verification_enabled", True)),
        verification_commands=list(raw.get("verification_commands") or []),
        verification_timeout_seconds=int(raw.get("verification_timeout_seconds") or 1800),
        tiers=tiers,
    )

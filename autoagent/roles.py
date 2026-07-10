"""역할 레지스트리.

roles.default.json(+roles.json override)에서 역할 엔트리를 읽어들이고,
route/모델 정책을 적용해 실행 가능한 ResolvedRole로 해석한다(resolve_role, Task 2).
Plan A는 동작 보존이 목표라 default 엔트리는 현행 규칙을 그대로 인코딩한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ResolvedRole:
    """한 스텝 실행에 필요한 최종 실행 속성(command_for_agent가 소비)."""

    agent: str            # "claude" | "codex"
    model: str | None
    effort: str | None
    mutating: bool
    permission_mode: str | None  # claude 전용(plan/acceptEdits/None)
    skip_permissions: bool       # claude 전용(--dangerously-skip-permissions; bypass posture)
    sandbox: str | None          # codex 전용


def load_roles(config_dir: Path) -> dict[str, dict[str, Any]]:
    """roles.default.json을 읽고 roles.json(있으면)으로 얕게 override한다."""
    default_path = config_dir / "roles.default.json"
    base: dict[str, Any] = json.loads(default_path.read_text(encoding="utf-8-sig"))
    roles: dict[str, dict[str, Any]] = {r["id"]: r for r in base["roles"]}
    override_path = config_dir / "roles.json"
    if override_path.exists():
        extra = json.loads(override_path.read_text(encoding="utf-8-sig"))
        for r in extra.get("roles", []):
            roles[r["id"]] = {**roles.get(r["id"], {}), **r}
    return roles

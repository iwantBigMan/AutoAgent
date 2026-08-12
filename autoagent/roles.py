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

from autoagent.config import Config
from autoagent.safety import codex_sandbox_for


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


def validate_roles(
    roles: dict[str, Any],
    config_dir: Path,
    tiers: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """시작 시 레지스트리 정합성 검사. 문제가 있으면 즉시 종료한다."""
    required = {"context", "architect", "validation", "implementer", "reviewer",
                "fix", "final-review", "evaluation", "report"}
    missing = required - set(roles)
    if missing:
        raise SystemExit(f"roles.default.json에 필수 역할 누락: {sorted(missing)}")
    valid_cond = {"none", "any_high_risk", "backend_high_risk_mutating"}
    for rid, r in roles.items():
        if r.get("high_risk_condition") not in valid_cond:
            raise SystemExit(f"역할 {rid}: high_risk_condition 값 오류 {r.get('high_risk_condition')!r}")
        if r.get("agent") not in {"claude", "codex", "route"}:
            raise SystemExit(f"역할 {rid}: agent 값 오류 {r.get('agent')!r}")
        # 티어 참조 정합성: 역할이 참조하는 tier/high_risk_tier가 가능한 agent 팔레트에 있어야 한다.
        agents = ["claude", "codex"] if r.get("agent") == "route" else [r.get("agent")]
        names = [r.get("tier")] + ([r["high_risk_tier"]] if r.get("high_risk_tier") else [])
        for ag in agents:
            for tname in names:
                if tname not in tiers.get(ag, {}):
                    raise SystemExit(f"역할 {rid}: 티어 '{tname}'가 agent '{ag}' 팔레트에 없음")
        # high_risk_condition이 있는데 high_risk_tier가 없으면 경고(동작은 tier로 폴백).
        if r.get("high_risk_condition") not in (None, "none") and not r.get("high_risk_tier"):
            print(f"[roles] 경고: 역할 {rid}는 high_risk_condition이 있으나 high_risk_tier가 없어 tier로 폴백")


def _is_high_risk(route: dict[str, Any], request: str) -> bool:
    # routed_common.is_high_risk와 동일 판정(순환 import 방지 위해 지연 import).
    from autoagent.workflows.routed_common import is_high_risk
    return is_high_risk(route, request)


def resolve_role(
    entry: dict[str, Any],
    *,
    config: Config,
    route: dict[str, Any],
    request: str,
    agent: str,
    read_only: bool,
) -> ResolvedRole:
    """레지스트리 엔트리를 route/모델 정책에 따라 실행 속성으로 해석한다.

    agent는 이미 결정된 구체 에이전트(claude/codex). entry["agent"]가 "route"면
    호출부가 route에서 뽑아 넘긴다. 동작은 현행 리졸버들과 바이트 단위로 일치해야 한다.
    """
    # solo 폴백: solo_provider가 설정되면 모든 역할을 그 프로바이더가 겸직한다.
    # 상류에서 배정된 agent(architect="claude"/evaluator="codex"/반대모델 등)를 여기서 덮는다.
    # 정상(null)이면 no-op이라 교차모델 경로는 바이트 동형.
    if getattr(config, "solo_provider", None):
        agent = config.solo_provider

    mutating = bool(entry["mutating"])

    # high-risk 조건 판정(역할별 비대칭 그대로).
    cond = entry["high_risk_condition"]
    if cond == "any_high_risk":
        escalate = _is_high_risk(route, request)
    elif cond == "backend_high_risk_mutating":
        escalate = mutating and route.get("task_type") == "backend" and _is_high_risk(route, request)
    else:
        escalate = False

    # 모델·effort — 팔레트 티어 조회로 결정한다. escalate면 high_risk_tier(있을 때), 아니면 tier.
    # agent는 이미 해석된 claude/codex라 route 역할도 agent별로 자동 해결된다.
    tier_name = entry["high_risk_tier"] if (escalate and entry.get("high_risk_tier")) else entry["tier"]
    tier = config.tiers[agent][tier_name]
    model: str | None = tier.get("model")
    effort: str | None = tier.get("effort")

    # 권한/샌드박스 — 병합된 command_for_agent(config-gated posture)와 동일하게 재현.
    permission_mode = None
    skip_permissions = False
    sandbox = None
    if agent == "claude":
        if not mutating:
            permission_mode = "plan"
        elif config.claude_impl_permission == "bypassPermissions":
            skip_permissions = True          # --dangerously-skip-permissions (무샌드박스 opt-in)
        else:
            permission_mode = "acceptEdits"  # 기본: 편집만 자동, bash/네트워크 차단
    elif agent == "codex":
        sb = entry.get("sandbox", "configured")
        sandbox = codex_sandbox_for(read_only, config.codex_sandbox) if sb == "from_read_only" else config.codex_sandbox

    return ResolvedRole(agent=agent, model=model, effort=effort, mutating=mutating,
                        permission_mode=permission_mode, skip_permissions=skip_permissions, sandbox=sandbox)

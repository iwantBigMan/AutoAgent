"""researcher/verifier 역할 + 리서치 프롬프트 렌더 테스트.

역할이 validate_roles를 통과하고 resolve_role로 기대 posture(researcher=구현자류,
verifier=plan/mutating:false)로 풀리는지, 프롬프트 5종이 별칭으로 렌더되고 핵심
placeholder가 치환되는지 확인한다.
"""
from __future__ import annotations

from autoagent.artifacts import DEFAULT_CONFIG, render_template
from autoagent.config import load_config
from autoagent.roles import load_roles, resolve_role, validate_roles

CONFIG_DIR = DEFAULT_CONFIG.parent


def _config():
    return load_config(DEFAULT_CONFIG)


def test_roles_present_and_valid() -> None:
    roles = load_roles(CONFIG_DIR)
    assert "researcher" in roles and "verifier" in roles
    validate_roles(roles, CONFIG_DIR, _config().tiers)


def test_verifier_is_readonly_plan_posture() -> None:
    roles = load_roles(CONFIG_DIR)
    cfg = _config()
    route = {"task_type": "research", "risk_level": "medium", "subtype": "research"}
    resolved = resolve_role(roles["verifier"], config=cfg, route=route, request="x", agent="claude", read_only=False)
    assert resolved.mutating is False
    assert resolved.permission_mode == "plan"


def test_researcher_resolves_for_both_agents() -> None:
    roles = load_roles(CONFIG_DIR)
    cfg = _config()
    route = {"task_type": "research", "risk_level": "medium", "subtype": "research"}
    r_claude = resolve_role(roles["researcher"], config=cfg, route=route, request="x", agent="claude", read_only=False)
    r_codex = resolve_role(roles["researcher"], config=cfg, route=route, request="x", agent="codex", read_only=False)
    assert r_claude.agent == "claude" and r_claude.model is not None
    assert r_codex.agent == "codex" and r_codex.sandbox is not None


def test_prompts_render_with_placeholders() -> None:
    values = {
        "REQUEST": "삼성전자 회사 리서치",
        "WORKSPACE": "C:/tmp/ws",
        "SEED_CONTRACT": "회사=삼성전자; 통화=KRW",
        "STAGE_ID": "a",
        "OUTER_PASS": "1",
        "INNER_ROUND": "1",
        "PRIOR_FEEDBACK": "",
        "RESEARCHER_OUTPUT": "산출물 본문",
        "STAGE_A_OUTPUT": "a 산출물",
        "DERIVE_OUTPUT": "derive 산출물",
        "COVERAGE_MATRIX_MD": "| a | passed |",
        "COVERAGE_BANNER": "",
        "COVERAGE_MATRIX": "<table></table>",
        "REPORT_BODY_MD": "# 리포트",
    }
    for name in ["seed_contract.md", "a_researcher.md", "crossmodel_verifier.md", "derive.md", "final_html_report.md"]:
        text = render_template(name, values)
        assert "{{" not in text
        assert text.strip()

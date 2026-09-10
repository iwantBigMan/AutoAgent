"""프롬프트 정제 게이트 단위테스트(역할/템플릿/게이트 파일/재개)."""
from __future__ import annotations

from pathlib import Path

from autoagent.artifacts import ROOT
from autoagent.roles import load_roles


def test_refine_role_registered():
    # roles.default.json에 refine 역할이 Claude light/plan/비변이로 등록되어야 한다.
    roles = load_roles(ROOT)
    entry = roles["refine"]
    assert entry["agent"] == "claude"
    assert entry["tier"] == "light"
    assert entry["mutating"] is False
    assert entry["permission"] == "plan"
    assert entry["high_risk_condition"] == "none"


def test_refine_prompt_template_renders():
    # alias가 걸려 있고 4개 플레이스홀더가 모두 치환되어야 한다.
    from autoagent.artifacts import render_template
    rendered = render_template(
        "claude_refine.md",
        {
            "REQUEST": "로그인 화면 버그 고쳐줘",
            "WORKFLOW": "routed",
            "TASK_TYPE": "auto",
            "WORKSPACE": "C:/ws",
        },
    )
    assert "로그인 화면 버그 고쳐줘" in rendered
    assert "routed" in rendered
    assert "{{" not in rendered  # 치환 누락 없음

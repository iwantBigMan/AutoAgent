"""프롬프트 정제 게이트 단위테스트(역할/템플릿/게이트 파일/재개)."""
from __future__ import annotations

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
    assert entry["sandbox"] == "from_read_only"


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


import json
from argparse import Namespace

from autoagent.artifacts import read_text


def _args(**overrides):
    # cli.build_parser의 기본값과 같은 모양의 Namespace(스냅샷 대상 필드 전부 포함).
    base = dict(
        workflow="routed", task_type="auto", implementer="auto", read_only=False,
        max_review_rounds=1, max_agent_calls=9, stop_after="none",
        skip_verification=False, require_human_approval=False, plan_only=False,
        skip_review=False, auto_approve_nonbranch=False, project="LangDet", config="C:/cfg.json",
        no_refine=False, dry_run=False, workspace=None, resume=None,
    )
    base.update(overrides)
    return Namespace(**base)


def test_write_refine_gate_and_pending(tmp_path):
    from autoagent.refine import write_refine_gate, refine_gate_pending, REFINED_REQUEST, REFINE_STATUS
    config = Namespace(workspace=tmp_path / "ws")
    write_refine_gate(tmp_path, args=_args(), config=config, refined="## 목표\n정제본")
    assert refine_gate_pending(tmp_path)
    status = json.loads(read_text(tmp_path / REFINE_STATUS))
    assert status["status"] == "waiting_for_prompt_approval"
    assert status["fallback"] is False
    assert status["args"]["workflow"] == "routed"
    assert status["args"]["max_agent_calls"] == 9
    assert "--resume" in status["resume_command"]
    assert read_text(tmp_path / REFINED_REQUEST) == "## 목표\n정제본"
    assert (tmp_path / "refine_required.md").exists()


def test_refine_gate_pending_false_cases(tmp_path):
    from autoagent.refine import refine_gate_pending, REFINE_STATUS
    assert not refine_gate_pending(tmp_path)  # 파일 없음
    (tmp_path / REFINE_STATUS).write_text('{"status": "approved"}', encoding="utf-8")
    assert not refine_gate_pending(tmp_path)  # 이미 승인됨


def test_write_refine_gate_fallback_records_reason(tmp_path):
    from autoagent.refine import write_refine_gate, REFINE_STATUS
    config = Namespace(workspace=tmp_path / "ws")
    write_refine_gate(tmp_path, args=_args(), config=config, refined="원문 그대로",
                      fallback_reason="empty refine output")
    status = json.loads(read_text(tmp_path / REFINE_STATUS))
    assert status["fallback"] is True
    assert status["fallback_reason"] == "empty refine output"


def test_resume_from_refine_gate_adopts_edited_file_and_restores_args(tmp_path):
    from autoagent.refine import write_refine_gate, resume_from_refine_gate, REFINED_REQUEST, REFINE_STATUS
    config = Namespace(workspace=tmp_path / "ws")
    original = _args(workflow="research", max_agent_calls=7, auto_approve_nonbranch=True)
    write_refine_gate(tmp_path, args=original, config=config, refined="정제본 v1")
    # 사용자가 정제본을 편집했다고 가정
    (tmp_path / REFINED_REQUEST).write_text("편집된 정제본", encoding="utf-8")
    # resume 시점의 args는 CLI 기본값(workflow=routed 등)
    resumed = _args()
    request, status = resume_from_refine_gate(resumed, tmp_path)
    assert request == "편집된 정제본"
    assert resumed.workflow == "research"       # 스냅샷 복원
    assert resumed.max_agent_calls == 7
    assert resumed.auto_approve_nonbranch is True
    assert json.loads(read_text(tmp_path / REFINE_STATUS))["status"] == "approved"
    assert read_text(tmp_path / "00_request.md") == "편집된 정제본"


def test_resume_from_refine_gate_rejects_empty_refined(tmp_path):
    import pytest
    from autoagent.refine import write_refine_gate, resume_from_refine_gate, REFINED_REQUEST
    config = Namespace(workspace=tmp_path / "ws")
    write_refine_gate(tmp_path, args=_args(), config=config, refined="정제본")
    (tmp_path / REFINED_REQUEST).write_text("   \n", encoding="utf-8")
    with pytest.raises(SystemExit):
        resume_from_refine_gate(_args(), tmp_path)

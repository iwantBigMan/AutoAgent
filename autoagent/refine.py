"""프롬프트 정제(refine) 스테이지.

사용자 원문 요청을 가벼운 정제 콜 1회(역할 refine: Claude light, solo면 스왑)로
구조화하고 승인 게이트에서 정지한다. --resume이 곧 승인이며,
00_refined_request.md를 편집한 뒤 재개하면 편집본이 채택된다.
cli.py의 워크플로 분기 전에 호출되어 전 워크플로 공통으로 적용된다.
설계: docs/superpowers/specs/2026-09-10-prompt-refine-gate-design.md
"""
from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import DEFAULT_CONFIG, read_text, render_template, write_json, write_text
from autoagent.config import Config
from autoagent.roles import load_roles, resolve_role
from autoagent.runner import require_command, run_process, write_command_artifact

REFINE_STATUS = "refine_status.json"
REFINED_REQUEST = "00_refined_request.md"
RAW_REQUEST = "00_request_raw.md"
STAGE_NAME = "00_refine"

# refine_status.json에 저장해 --resume 때 그대로 복원할 args 필드들.
# 게이트 정지 후 재개 명령엔 플래그가 없으므로 원 런의 인자를 여기 스냅샷한다.
SNAPSHOT_KEYS = [
    "workflow", "task_type", "implementer", "read_only", "max_review_rounds",
    "max_agent_calls", "stop_after", "skip_verification", "require_human_approval",
    "plan_only", "skip_review", "project", "config", "no_refine",
]


def args_snapshot(args: Namespace) -> dict[str, Any]:
    """재개 재디스패치에 필요한 args 값들을 dict로 뜬다."""
    return {key: getattr(args, key) for key in SNAPSHOT_KEYS}


def resume_command_for(run_dir: Path) -> str:
    """어느 cwd에서든 붙여넣어 실행 가능한 표준 재개 명령(routed_common과 동일 규약)."""
    run_py = DEFAULT_CONFIG.parent / "run.py"
    return f'python "{run_py}" --resume "{run_dir}"'


def write_refine_gate(
    run_dir: Path,
    *,
    args: Namespace,
    config: Any,
    refined: str,
    fallback_reason: str | None = None,
) -> None:
    """게이트 산출물(정제본/status/안내문)을 기록하고 stdout 핸드오프를 찍는다.

    fallback_reason이 있으면 정제 콜 실패로 refined에 원문이 담긴 경우다.
    config는 workspace 속성만 읽으므로 테스트에선 Namespace로 대체 가능하다.
    """
    resume_command = resume_command_for(run_dir)
    write_text(run_dir / REFINED_REQUEST, refined)
    status = {
        "status": "waiting_for_prompt_approval",
        "run_dir": str(run_dir),
        "resume_command": resume_command,
        "workspace": str(config.workspace),
        "fallback": bool(fallback_reason),
        "fallback_reason": fallback_reason,
        "args": args_snapshot(args),
    }
    write_json(run_dir / REFINE_STATUS, status)
    fallback_note = (
        f"\n> 정제 콜 실패로 **원문이 그대로** 담겨 있습니다(사유: {fallback_reason}).\n"
        if fallback_reason
        else ""
    )
    write_text(
        run_dir / "refine_required.md",
        "# Prompt Refine Gate\n\n"
        "정제된 요청이 준비되었습니다. 진행 전에 확인하세요.\n"
        f"{fallback_note}\n"
        f"- 원문: {RAW_REQUEST}\n"
        f"- 정제본: {REFINED_REQUEST} (필요하면 이 파일을 직접 수정하세요 - 수정본이 채택됩니다)\n\n"
        "승인(=진행)하려면:\n\n"
        f"```powershell\n{resume_command}\n```\n\n"
        "Running that resume command IS the act of approval.\n",
    )
    # 파싱 가능한 핸드오프(비-cp949 문자 금지: ASCII+한글만).
    print("AA_STATUS: waiting_for_prompt_approval")
    print(f"RUN_DIR: {run_dir}")
    print(f"RESUME_COMMAND: {resume_command}")
    print(f"Refine gate waiting for prompt approval: {run_dir}")


def run_refine_stage(args: Namespace, config: Config, request: str, run_dir: Path) -> str | None:
    """정제 스테이지 실행. None=게이트 정지(호출부 exit 0), str=그 텍스트로 계속.

    dry-run은 프롬프트/커맨드 아티팩트만 렌더하고 원문을 그대로 반환한다 -
    다운스트림 렌더가 실제 요청을 담아야 라우팅·프롬프트 검증이라는 dry-run의
    목적이 유지되기 때문(플레이스홀더면 auto 라우팅이 오판한다).
    정제 콜은 워크플로 진입 전이라 AgentCallBudget과 별도 계정이다.
    """
    # 지연 import: routed_impl -> routed_common -> runner 체인과의 순환 방지(관례).
    from autoagent.workflows.routed_impl import command_for_agent

    write_text(run_dir / RAW_REQUEST, request)
    prompt = render_template(
        "claude_refine.md",
        {
            "REQUEST": request,
            "WORKFLOW": args.workflow,
            "TASK_TYPE": args.task_type,
            "WORKSPACE": str(config.workspace),
        },
    )
    roles = load_roles(DEFAULT_CONFIG.parent)
    # route는 high_risk_condition="none"이라 판정에 안 쓰이지만 시그니처상 필요({} 전달).
    resolved = resolve_role(
        roles["refine"], config=config, route={}, request=request, agent="claude", read_only=True
    )

    if args.dry_run:
        write_text(run_dir / f"{STAGE_NAME}_prompt.md", prompt)
        write_command_artifact(run_dir, STAGE_NAME, command_for_agent(config, resolved))
        return request

    cli = require_command(config.claude_command if resolved.agent == "claude" else config.codex_command)
    try:
        refined = run_process(
            name=STAGE_NAME,
            command=command_for_agent(config, resolved, resolved_command=cli),
            prompt=prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        ).strip()
    except (SystemExit, subprocess.TimeoutExpired) as exc:
        # 콜 실패로 런을 죽이지 않는다 - 원문 폴백으로 게이트를 세워 사용자가
        # 원문 승인 또는 직접 편집으로 진행할 수 있게 한다.
        write_refine_gate(run_dir, args=args, config=config, refined=request, fallback_reason=str(exc))
        return None

    if not refined:
        write_refine_gate(run_dir, args=args, config=config, refined=request, fallback_reason="empty refine output")
        return None

    write_refine_gate(run_dir, args=args, config=config, refined=refined)
    return None


def refine_gate_pending(run_dir: Path) -> bool:
    """refine 게이트에서 정지한 run인지 판정한다(--resume 분기 최우선 체크)."""
    path = run_dir / REFINE_STATUS
    if not path.exists():
        return False
    return json.loads(read_text(path)).get("status") == "waiting_for_prompt_approval"


def resume_from_refine_gate(args: Namespace, run_dir: Path) -> tuple[str, dict[str, Any]]:
    """refine 게이트 재개: 정제본(편집 포함)을 읽고 approved 갱신, args 복원.

    반환 (request, status). --resume 실행 자체가 승인 행위다.
    project 반영 config 재로딩과 workspace 복원은 호출부(cli.main)가 담당한다.
    """
    status = json.loads(read_text(run_dir / REFINE_STATUS))
    refined_path = run_dir / REFINED_REQUEST
    if not refined_path.exists():
        raise SystemExit(f"No {REFINED_REQUEST} in {run_dir}; cannot resume the refine gate.")
    request = read_text(refined_path).strip()
    if not request:
        raise SystemExit(f"{REFINED_REQUEST} is empty in {run_dir}; edit it and resume again.")
    # 원 런의 인자를 복원한다(재개 명령엔 플래그가 없으므로 CLI 기본값을 덮는다).
    for key, value in (status.get("args") or {}).items():
        setattr(args, key, value)
    status["status"] = "approved"
    write_json(run_dir / REFINE_STATUS, status)
    # 다운스트림 전 단계가 참조하는 정본 요청 파일을 이 시점에 기록한다.
    write_text(run_dir / "00_request.md", request)
    return request, status

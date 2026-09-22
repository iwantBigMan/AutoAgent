"""CLI 진입점(argparse) + 워크플로우 분기.

인자를 파싱하고 config를 로드한 뒤, --resume면 재개 경로로, 아니면 run 폴더를
만들고 simple/routed/decompose 워크플로우 중 하나로 분기한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from autoagent.artifacts import DEFAULT_CONFIG, ensure_project_config, make_run_dir, read_text, write_metadata, write_text
from autoagent.config import load_config
from autoagent.workflows.decompose import run_decompose_workflow
from autoagent.workflows.research import run_research_workflow
from autoagent.workflows.routed import resume_routed_workflow, run_routed_workflow
from autoagent.workflows.simple import run_simple_workflow
from autoagent.workflows.task_exec import run_task_graph_execution


def solo_banner(provider: str) -> str:
    """solo 모드 시작 경고 배너 문자열. Windows cp949 stdout에서도 인코딩 가능해야 하므로
    비-cp949 문자(예: em dash \\u2014)를 쓰지 않는다(ASCII 하이픈 사용)."""
    return (
        f"[solo] SOLO MODE: {provider} 단독 - 교차모델 대신 "
        "단일 프로바이더 적대검증(엄격도 감소)."
    )


def write_home_pointer(pointer: Path | None = None) -> None:
    """~/.autoagent/home에 하네스 ROOT 절대경로 한 줄을 기록한다(자기등록).

    /aa·/aar 커맨드가 클론 위치를 하드코딩 없이 찾는 근거 파일. 내용이 이미
    같으면 재기록하지 않고, 기록 실패(권한 등)는 경고만 남기고 런을 계속한다.
    """
    target = pointer if pointer is not None else Path.home() / ".autoagent" / "home"
    line = str(DEFAULT_CONFIG.parent)
    try:
        if target.exists() and target.read_text(encoding="utf-8").strip() == line:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(line + "\n", encoding="utf-8")
    except OSError as exc:
        # 자기등록은 편의 기능 - 실패해도 런 자체는 계속한다(ASCII+한글만 출력).
        print(f"[home] 홈 포인터 기록 실패(계속 진행): {exc}")


def require_workspace(config) -> None:
    """workspace 미지정(None)이면 해결 방법 3가지를 안내하고 종료한다."""
    if config.workspace is None:
        raise SystemExit(
            "Workspace not set. Fix one of: (1) copy autoagent.config.example.json to "
            'autoagent.config.json and set "workspace", (2) set AUTOAGENT_WORKSPACE, '
            "(3) pass --workspace <path>."
        )


def load_request(args: argparse.Namespace) -> str:
    """요청 텍스트를 --request-file > --request > stdin 순으로 읽는다. 없으면 종료."""
    if args.request_file:
        return read_text(Path(args.request_file))
    if args.request:
        return args.request
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide a request with --request, --request-file, or stdin.")


def resume_mode(run_dir: Path) -> str:
    """checkpoint.json의 mode를 읽어 재개 분기 키를 돌려준다.

    mode가 없거나 "routed_impl"이면 기존 routed 재개(하위호환 기본),
    "task_graph"이면 decompose 병렬 실행기로 간다.
    """
    checkpoint_path = run_dir / "checkpoint.json"
    if not checkpoint_path.exists():
        raise SystemExit(f"No checkpoint.json in {run_dir}; cannot resume.")
    checkpoint = json.loads(read_text(checkpoint_path))
    return checkpoint.get("mode") or "routed_impl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude Code + Codex CLI local harness MVP")
    parser.add_argument("--request", help="Task request text")
    parser.add_argument("--request-file", help="Path to a markdown request file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config JSON")
    parser.add_argument("--project", help="Project registry name under projects/<name>/ (config + runs)")
    parser.add_argument("--workspace", help="Override target workspace path")
    parser.add_argument("--workflow", choices=["simple", "routed", "decompose", "research"], default="simple", help="Workflow to run")
    parser.add_argument(
        "--task-type",
        choices=["auto", "backend", "frontend", "docs", "review"],
        default="auto",
        help="Task route for routed workflow",
    )
    parser.add_argument(
        "--implementer",
        choices=["auto", "claude", "codex"],
        default="auto",
        help="Implementation agent for routed implementation workflows",
    )
    parser.add_argument("--read-only", action="store_true", help="Run routed workflow without implementation steps")
    parser.add_argument("--max-review-rounds", type=int, default=1, help="Maximum fix rounds after a review")
    parser.add_argument("--max-agent-calls", type=int, default=0, help="Maximum Claude/Codex subprocess calls")
    parser.add_argument(
        "--stop-after",
        choices=[
            "none",
            "context",
            "architecture",
            "validation",
            "implementation",
            "review",
            "verification",
            "final-review",
            "evaluation",
            "report",
        ],
        default="none",
        help="Stop after a routed workflow stage completes",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip the post-implementation DB-free verification stage",
    )
    parser.add_argument(
        "--require-human-approval",
        action="store_true",
        help="Stop before implementation until a human approves the run",
    )
    parser.add_argument("--plan-only", action="store_true", help="Run Claude planning only")
    parser.add_argument("--skip-review", action="store_true", help="Skip final Claude review")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts without calling CLIs")
    parser.add_argument(
        "--auto-approve-nonbranch", action="store_true",
        help="Research workflow: auto-pass non-branch gates (never skips forced high-cost/contradiction/blocked gates)",
    )
    parser.add_argument(
        "--no-refine", action="store_true",
        help="Skip the prompt refine gate and run with the raw request",
    )
    parser.add_argument(
        "--resume",
        help="Resume a gated routed run from its run directory; loads checkpoint.json and continues into implementation",
    )
    return parser


def dispatch_workflow(args: argparse.Namespace, config, request: str, run_dir: Path) -> int:
    """요청 텍스트로 워크플로를 시작한다(신규 런과 refine 게이트 재개가 공용)."""
    if args.workflow == "routed":
        return run_routed_workflow(args, config, request, run_dir)
    if args.workflow == "decompose":
        return run_decompose_workflow(args, config, request, run_dir)
    if args.workflow == "research":
        return run_research_workflow(args, config, request, run_dir)
    return run_simple_workflow(args, config, request, run_dir)


def main() -> int:
    args = build_parser().parse_args()
    write_home_pointer()  # 자기등록: /aa·/aar가 이 클론을 찾을 수 있게 한다(dry-run 포함)
    if args.project:
        # --project가 요구하는 config를 미리 보장한다. workspace는 --workspace(abs) 우선, 없으면 cwd.
        ws = Path(args.workspace).resolve() if args.workspace else Path.cwd()
        ensure_project_config(Path(args.config).parent, args.project, ws)
    config = load_config(Path(args.config), project=args.project)
    from autoagent.roles import load_roles, validate_roles
    validate_roles(load_roles(DEFAULT_CONFIG.parent), DEFAULT_CONFIG.parent, config.tiers)
    # MCP(증분 2): Codex의 [mcp_servers.*]와 서버 대칭을 시작 시 검사한다(불일치는 경고, 차단 아님).
    # Claude용 config 파일(.aa_mcp.json)은 run_dir이 정해진 뒤 생성한다(실행별 격리 + dry-run 무영향).
    from autoagent.mcp import write_claude_mcp_config, check_mcp_symmetry
    for _mcp_warning in check_mcp_symmetry(config):
        print(f"[mcp] {_mcp_warning}")
    if config.solo_provider:
        print(solo_banner(config.solo_provider))
    if args.workspace:
        config.workspace = Path(args.workspace)

    # --resume는 게이트에서 정지했던 run을 이어받아 구현 단계로 재개한다.
    # 새 요청을 받는 게 아니므로 --request/--request-file과 함께 쓸 수 없다.
    if args.resume:
        if args.request or args.request_file:
            raise SystemExit("--resume cannot be combined with --request/--request-file.")
        run_dir = Path(args.resume)
        from autoagent.refine import refine_gate_pending, resume_from_refine_gate
        # refine 게이트 재개는 다른 재개 체크(research_state/checkpoint)보다 먼저 -
        # 이 시점엔 그 파일들이 아직 없다. --resume 실행 자체가 프롬프트 승인이다.
        if refine_gate_pending(run_dir):
            request, refine_status = resume_from_refine_gate(args, run_dir)
            # 원 런의 project/config로 재로딩(티어·solo 등 반영) 후 workspace 복원.
            config = load_config(Path(args.config), project=args.project)
            config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)
            if args.workspace:
                config.workspace = Path(args.workspace)
            else:
                config.workspace = Path(refine_status["workspace"])
            if not config.workspace.exists():
                raise SystemExit(f"Workspace does not exist: {config.workspace}")
            return dispatch_workflow(args, config, request, run_dir)
        # 재개 run의 run_dir 밑에 Claude용 MCP config 생성(dry-run이면 경로만, 파일 미기록).
        config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)
        if (run_dir / "research_state.json").exists():
            # research 재개는 config workspace를 그대로 쓴다(상태파일에 복원 없음) - None 방어.
            require_workspace(config)
            return run_research_workflow(args, config, None, run_dir)
        mode = resume_mode(run_dir)
        if mode == "task_graph":
            # task_graph 재개도 config workspace를 직접 쓴다 - None 방어.
            require_workspace(config)
            return run_task_graph_execution(args, config, run_dir)
        return resume_routed_workflow(args, config)

    require_workspace(config)
    if not config.workspace.exists():
        raise SystemExit(f"Workspace does not exist: {config.workspace}")

    request = load_request(args).strip()
    if not request:
        raise SystemExit("Request is empty.")

    run_dir = make_run_dir(project=args.project)
    config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)
    write_metadata(
        run_dir,
        {
            "project": args.project,
            "workspace": str(config.workspace),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "workflow": args.workflow,
            "task_type": args.task_type,
            "implementer": args.implementer,
            "read_only": args.read_only,
            "max_review_rounds": args.max_review_rounds,
            "max_agent_calls": args.max_agent_calls,
            "stop_after": args.stop_after,
            "skip_verification": args.skip_verification,
            "require_human_approval": args.require_human_approval,
            "plan_only": args.plan_only,
            "skip_review": args.skip_review,
            "dry_run": args.dry_run,
            "no_refine": args.no_refine,
            "claude_model": config.claude_model,
            "claude_high_risk_model": config.claude_high_risk_model,
            "claude_effort": config.claude_effort,
            "claude_high_risk_effort": config.claude_high_risk_effort,
            "claude_impl_permission": config.claude_impl_permission,
            "codex_model": config.codex_model,
            "codex_reasoning_effort": config.codex_reasoning_effort,
            "codex_high_risk_effort": config.codex_high_risk_effort,
            "solo_provider": config.solo_provider,
        },
    )

    if not args.no_refine:
        from autoagent.refine import run_refine_stage
        refined = run_refine_stage(args, config, request, run_dir)
        if refined is None:
            # 게이트 정지 - 정제본 검토 후 --resume으로 승인·계속한다.
            return 0
        request = refined  # dry-run 통과 경로(원문 그대로)
    write_text(run_dir / "00_request.md", request)
    return dispatch_workflow(args, config, request, run_dir)

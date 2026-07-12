"""decompose 워크플로우.

대규모 요청을 task_graph로 분해(claude) -> 계획 리뷰(codex) -> 승인 대기에서 정지한다.
구현은 절대 하지 않는다. task_graph.json과 승인 안내·최종 리포트만 남긴다.
(승인된 그래프의 순차 실행은 후속 워크플로우 — docs/specs 참조.)
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import extract_json_block, render_template, write_json, write_text
from autoagent.config import Config
from autoagent.runner import claude_command, codex_exec_command, require_command, run_process, write_command_artifact
from autoagent.workflows.routed_common import resume_command_for


def run_decompose_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    """요청을 분해해 task_graph를 만들고 codex 리뷰까지 한 뒤 승인 대기 상태로 정지한다."""
    values = {
        "REQUEST": request,
        "WORKSPACE": str(config.workspace),
    }

    decomposition_prompt = render_template("claude_decompose.md", values)
    if args.dry_run:
        write_text(run_dir / "01_claude_decomposition_prompt.md", decomposition_prompt)
        write_command_artifact(
            run_dir,
            "01_claude_decomposition",
            claude_command(config.claude_command, config.claude_model, "plan"),
        )
        decomposition = dry_run_task_graph(request)
        write_text(run_dir / "01_claude_decomposition.md", decomposition)
    else:
        claude = require_command(config.claude_command)
        decomposition = run_process(
            name="01_claude_decomposition",
            command=claude_command(claude, config.claude_model, "plan"),
            prompt=decomposition_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "01_claude_decomposition.md", decomposition)

    task_graph, extracted = extract_task_graph(decomposition, run_dir)

    review_prompt = render_template(
        "codex_plan_review.md",
        {
            **values,
            "CLAUDE_DECOMPOSITION": decomposition,
            "TASK_GRAPH_JSON": json.dumps(task_graph, ensure_ascii=False, indent=2) if task_graph else "",
        },
    )
    if args.dry_run:
        write_text(run_dir / "02_codex_plan_review_prompt.md", review_prompt)
        write_command_artifact(
            run_dir,
            "02_codex_plan_review",
            codex_exec_command(config, config.codex_command, "read-only"),
        )
        plan_review = "PLAN_REVIEW_STATUS: approved\n\n[dry-run: Codex plan review output]"
        write_text(run_dir / "02_codex_plan_review.md", plan_review)
    else:
        codex = require_command(config.codex_command)
        plan_review = run_process(
            name="02_codex_plan_review",
            command=codex_exec_command(config, codex, "read-only"),
            prompt=review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "02_codex_plan_review.md", plan_review)

    resume_command = resume_command_for(run_dir)
    # task_graph가 추출된 경우에만 브리핑/체크포인트를 쓴다(추출 실패면 기존 안내로 폴백).
    if task_graph is not None:
        write_text(run_dir / "approval_brief.md", render_task_graph_brief(task_graph, resume_command))
        write_task_graph_checkpoint(run_dir, request=request, config=config, args=args)
    write_approval_required(run_dir)
    write_final_report(run_dir, task_graph, extracted, plan_review)

    print("ROUTED_STATUS: waiting_for_human_approval")
    print(f"RUN_DIR: {run_dir}")
    print(f"RESUME_COMMAND: {resume_command}")
    print(f"Decompose run complete: {run_dir}")
    return 0


def render_task_graph_brief(task_graph: dict[str, Any], resume_command: str) -> str:
    """승인된 task_graph를 사람이 읽는 approval_brief.md 마크다운으로 결정론적으로 렌더한다.

    JSON을 그대로 반영하므로 별도 에이전트 호출이 없고(비용 0) JSON과 항상 일치한다.
    실행 순서표는 위상정렬 파도 순서로, high-risk/approval_required 노드는 별도 섹션에 강조한다.
    resume_command는 하단 '다음 단계'에 코드펜스로 임베드한다.
    """
    from autoagent.workflows.task_exec import topological_waves  # 순환 import 회피(지연 import)

    tasks = task_graph.get("tasks", []) or []
    by_id = {t.get("id"): t for t in tasks}
    waves = topological_waves(tasks)  # list[list[str]] — 파도별 노드 id

    lines: list[str] = []
    lines.append("# Task Graph 승인 브리핑\n")
    lines.append(f"- 목표: {task_graph.get('goal', '')}")
    lines.append(f"- 그래프 risk_level: {task_graph.get('risk_level', 'unknown')}")
    lines.append(f"- 노드 수: {len(tasks)}")
    lines.append(f"- 최대 병렬 파도 폭: {max((len(w) for w in waves), default=0)}\n")

    lines.append("## 실행 순서 (위상정렬 파도)\n")
    lines.append("| 파도 | id | title | type | risk | allowed_paths | 의존성 |")
    lines.append("|---|---|---|---|---|---|---|")
    for wave_index, wave in enumerate(waves, start=1):
        for node_id in wave:
            t = by_id.get(node_id, {})
            allowed = ", ".join(t.get("allowed_paths") or []) or "-"
            deps = ", ".join(t.get("dependencies") or []) or "-"
            lines.append(
                f"| {wave_index} | {node_id} | {t.get('title', '')} | "
                f"{t.get('type', '')} | {t.get('risk_level', '')} | {allowed} | {deps} |"
            )
    lines.append("")

    lines.append("## 노드 설명\n")
    for t in tasks:
        lines.append(f"- **{t.get('id')}** ({t.get('type')}): {t.get('description', '')}")
    lines.append("")

    high_risk = [t for t in tasks if t.get("risk_level") == "high" or t.get("approval_required") is True]
    lines.append("## 위험 노드 (high-risk / approval_required)\n")
    if high_risk:
        for t in high_risk:
            lines.append(f"- **{t.get('id')}** ({t.get('type')}, risk={t.get('risk_level')}): {t.get('title', '')}")
    else:
        lines.append("- 없음")
    lines.append("")

    lines.append("## 검증 명령 (validation_commands)\n")
    for t in tasks:
        cmds = t.get("validation_commands") or []
        if cmds:
            lines.append(f"- {t.get('id')}: {', '.join(cmds)}")
    lines.append("")

    lines.append("## 다음 단계\n")
    lines.append(
        "이 계획대로 진행하려면 아래 재개 명령을 실행하세요(재개 실행 자체가 승인입니다). "
        "특정 노드를 빼거나 고치려면 task_graph.json을 수정한 뒤 재실행하세요.\n"
    )
    lines.append("```powershell")
    lines.append(resume_command)
    lines.append("```\n")
    return "\n".join(lines) + "\n"


def write_task_graph_checkpoint(run_dir: Path, *, request: str, config: Config, args: Namespace) -> None:
    """실행기 재개(--resume)에 필요한 상태를 mode:"task_graph"로 저장한다(routed checkpoint와 구분)."""
    checkpoint = {
        "version": 1,
        "mode": "task_graph",
        "stage": "awaiting_approval",
        "request": request,
        "workspace": str(config.workspace),
        "config_path": args.config,
        "task_graph": "task_graph.json",
        "max_review_rounds": args.max_review_rounds,
        "max_agent_calls": args.max_agent_calls,
    }
    write_json(run_dir / "checkpoint.json", checkpoint)


def extract_task_graph(decomposition: str, run_dir: Path) -> tuple[dict[str, Any] | None, bool]:
    try:
        task_graph = extract_json_block(decomposition)
    except Exception as exc:
        write_text(
            run_dir / "task_graph_extract_failed.md",
            "# Task Graph Extract Failed\n\n"
            f"Could not extract task graph JSON from Claude decomposition.\n\n"
            f"Error: {exc}\n",
        )
        return None, False

    write_json(run_dir / "task_graph.json", task_graph)
    return task_graph, True


def write_approval_required(run_dir: Path) -> None:
    write_text(
        run_dir / "approval_required.md",
        "# Task Graph Approval Required\n\n"
        "이 run은 요청을 분해만 했습니다(구현 없음).\n\n"
        "먼저 읽어 보세요:\n"
        "- approval_brief.md (사람이 읽는 실행 계획 요약)\n"
        "- 01_claude_decomposition.md\n"
        "- 02_codex_plan_review.md\n"
        "- task_graph.json\n\n"
        "이 계획을 승인하려면 재개 명령을 실행하세요(재개 실행 = 승인).\n"
        f"```powershell\n{resume_command_for(run_dir)}\n```\n",
    )


def write_final_report(
    run_dir: Path,
    task_graph: dict[str, Any] | None,
    extracted: bool,
    plan_review: str,
) -> None:
    tasks = task_graph.get("tasks", []) if task_graph else []
    high_risk_tasks = [
        task
        for task in tasks
        if task.get("risk_level") == "high" or task.get("approval_required") is True
    ]
    approval_required = bool(task_graph.get("requires_human_approval")) if task_graph else True
    status = first_status_line(plan_review, "PLAN_REVIEW_STATUS")
    write_text(
        run_dir / "final_report.md",
        "# Decompose Final Report\n\n"
        f"- decompose_success: true\n"
        f"- task_graph_created: {str(extracted).lower()}\n"
        f"- plan_review_status: {status}\n"
        f"- task_count: {len(tasks)}\n"
        f"- high_risk_task_count: {len(high_risk_tasks)}\n"
        f"- approval_required: {str(approval_required).lower()}\n\n"
        "No implementation was run. The next step is human approval of the task graph.\n",
    )


def first_status_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix + ":"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def dry_run_task_graph(request: str) -> str:
    graph = {
        "version": 1,
        "goal": request,
        "risk_level": "medium",
        "requires_human_approval": True,
        "tasks": [
            {
                "id": "001",
                "title": "Inspect current structure",
                "type": "review",
                "description": "Review the current repository structure and identify affected areas.",
                "rationale": "A safe decomposition starts with a read-only inventory.",
                "allowed_paths": [],
                "blocked_paths": [],
                "expected_files": [],
                "validation_commands": ["git status --short", "rg --files"],
                "dependencies": [],
                "risk_level": "low",
                "approval_required": False,
                "status": "pending",
            }
        ],
    }
    return "# Decomposition Summary\n\n[dry-run: Claude decomposition output]\n\n# TASK_GRAPH_JSON\n\n```json\n" + json.dumps(
        graph, ensure_ascii=False, indent=2
    ) + "\n```\n"

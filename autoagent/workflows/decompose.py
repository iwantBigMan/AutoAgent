from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import extract_json_block, render_template, write_json, write_text
from autoagent.config import Config
from autoagent.runner import claude_command, codex_exec_command, require_command, run_process, write_command_artifact


def run_decompose_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
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

    write_approval_required(run_dir)
    write_final_report(run_dir, task_graph, extracted, plan_review)
    print(f"Decompose run complete: {run_dir}")
    return 0


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
        "This run only decomposed the request. No implementation was run.\n\n"
        "Review:\n"
        "- 01_claude_decomposition.md\n"
        "- 02_codex_plan_review.md\n"
        "- task_graph.json\n\n"
        "Next phase is not implemented yet.\n"
        "Approve the task graph manually before running future task execution workflow.\n",
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

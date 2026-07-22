"""docs/review·read-only 라우트: 구현 없이 평가·최종보고만 수행한다.

구현/리뷰/수정 단계가 없으므로 해당 자리에 "실행 안 함" 표시를 채워
평가(run_evaluation)와 최종 보고(run_final_report)만 돌린다.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import write_text
from autoagent.config import Config
from autoagent.runner import AgentCallBudget
from autoagent.workflows.routed_common import run_evaluation, run_final_report, stop_after


def run_docs_route(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    budget: AgentCallBudget,
    run_dir: Path,
) -> int:
    # review 라우트는 실제 리뷰(02)와 검증 요약을 평가/보고에 넘긴다. docs(문서)는 기존 문자열 유지.
    if common.get("TASK_TYPE") == "review":
        impl_arg = "No implementation step was run (read-only review route)."
        review_arg = common.get("CLAUDE_ARCHITECTURE") or "No review produced."
        final_review_arg = common.get("VERIFICATION_SUMMARY") or "No verification stage was run."
    else:
        impl_arg = "No implementation step was run."
        review_arg = "Read-only or docs/review route."
        final_review_arg = "No final code review step was run."

    evaluation = run_evaluation(
        args,
        config,
        common,
        budget,
        run_dir,
        name="04_codex_evaluation",
        implementation=impl_arg,
        review=review_arg,
        fix="No fix step was run.",
        final_review=final_review_arg,
    )
    if stop_after(args, run_dir, "evaluation"):
        return 0
    final = run_final_report(
        args,
        config,
        common,
        budget,
        run_dir,
        name="05_claude_final_report",
        implementation=impl_arg,
        review=review_arg,
        fix="No fix step was run.",
        final_review=final_review_arg,
        evaluation=evaluation,
    )
    write_text(run_dir / "final_report.md", final)
    stop_after(args, run_dir, "report")
    print(f"Routed run complete: {run_dir}")
    return 0

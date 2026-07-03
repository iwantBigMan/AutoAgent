from __future__ import annotations

import subprocess
from pathlib import Path


def git_baseline_status(workspace: Path) -> tuple[bool, str]:
    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if status.returncode != 0:
        return False, status.stderr.strip() or status.stdout.strip()

    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if head.returncode != 0:
        return False, "Git repository has no committed HEAD baseline."

    return True, "Git repository has a committed HEAD baseline."


def codex_sandbox_for(read_only: bool, configured_sandbox: str) -> str:
    return "read-only" if read_only else configured_sandbox


def review_needs_changes(review: str) -> bool:
    lowered = review.lower()
    status_markers = [
        "review_status: needs_changes",
        "review_status: rejected",
        "review_status: blocked",
    ]
    if any(marker in lowered for marker in status_markers):
        return True
    if "review_status: approved" in lowered:
        return False
    fallback_markers = ["needs changes", "must fix", "blocking", "blocker", "critical", "수정 필요"]
    return any(marker in lowered for marker in fallback_markers)

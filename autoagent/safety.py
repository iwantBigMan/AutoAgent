"""안전 가드 모듈.

- git_baseline_status: 구현을 시작해도 되는 안전한 git 상태인지 확인한다.
- codex_sandbox_for: read-only 여부에 따라 Codex 샌드박스 모드를 정한다.
- review_needs_changes: 리뷰 결과가 "수정 필요"인지 판정한다(루프 종료 조건).
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_baseline_status(workspace: Path) -> tuple[bool, str]:
    """작업공간이 커밋된 HEAD 베이스라인을 가진 git 저장소인지 확인한다.

    (성공여부, 메시지)를 반환. 더티 트리 자체는 막지 않고, HEAD가 없거나
    git status가 실패하는 경우에만 False. 구현 라우트는 True일 때만 진행한다.
    """
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
    """read_only면 무조건 "read-only", 아니면 설정된 샌드박스 모드를 쓴다."""
    return "read-only" if read_only else configured_sandbox


def review_needs_changes(review: str) -> bool:
    """리뷰 텍스트가 "수정 필요"를 뜻하는지 판정한다(리뷰-수정 루프의 종료 조건).

    1) 명시적 `REVIEW_STATUS:` 마커를 최우선으로 본다(신뢰도 높음).
    2) 마커가 없으면 fallback 키워드로 추정한다(오탐 가능 — 그래서 프롬프트에
       REVIEW_STATUS 첫 줄 계약을 두어 마커가 항상 나오도록 유도한다).
    """
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

"""git worktree/통합/스코프 헬퍼.

decompose 병렬 실행기가 쓰는 순수 git 조작만 담당한다(오케스트레이션은 task_exec가).
worktree 추가/제거, 레인 브랜치 삭제, 통합 브랜치 생성/병합, allowed_paths 겹침 경고,
git diff 기반 soft scope 가드를 제공한다. 자동 충돌 해결·하드 샌드박스는 범위 밖.
"""
from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # 타깃 레포에서 git을 실행하고 CompletedProcess를 반환(호출부가 returncode 판정).
    return subprocess.run(
        ["git", "-C", str(target), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


@dataclass
class MergeResult:
    """통합 브랜치 병합 결과. ok=False면 conflicts에 충돌 파일 목록이 담긴다."""
    ok: bool
    conflicts: list[str]
    message: str


def warn_path_overlap(nodes: list[dict[str, Any]]) -> list[str]:
    # 노드 쌍의 allowed_paths가 하나라도 겹치면 경고 문자열을 만든다(차단 아님).
    warnings: list[str] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a = set(nodes[i].get("allowed_paths") or [])
            b = set(nodes[j].get("allowed_paths") or [])
            shared = sorted(a & b)
            if shared:
                warnings.append(
                    f"경로 겹침: 노드 {nodes[i].get('id')} 와 {nodes[j].get('id')} 가 "
                    f"{shared} 를 공유합니다(통합 시 충돌 가능)."
                )
    return warnings


def scope_violations(target: Path, worktree: Path, allowed: list[str], blocked: list[str]) -> list[str]:
    # worktree에서 staged된(git add -A 이후) 변경 파일이 allowed_paths 밖(또는 blocked_paths 안)이면 플래그.
    # git diff --cached는 신규(untracked) 파일도 staged로 잡으므로, 커밋 전 신규 파일 스코프 검사가 유효하다(imp 5).
    # (git diff HEAD는 untracked 신규 파일을 누락한다.)
    proc = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--cached", "--name-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    changed = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    violations: list[str] = []
    for path in changed:
        # blocked 우선: blocked 패턴에 걸리면 무조건 위반.
        if any(fnmatch.fnmatch(path, pat) for pat in (blocked or [])):
            violations.append(f"blocked 경로 변경: {path}")
            continue
        # allowed가 지정됐는데 어느 패턴에도 안 맞으면 범위 밖 변경.
        if allowed and not any(fnmatch.fnmatch(path, pat) for pat in allowed):
            violations.append(f"allowed 밖 변경: {path}")
    return violations


def add_worktree(target: Path, path: Path, branch: str, baseline: str) -> None:
    # baseline에서 새 브랜치로 worktree를 추가. path는 run_dir 밑(타깃 워킹트리를 안 더럽힘).
    proc = _git(target, "worktree", "add", str(path), "-b", branch, baseline)
    if proc.returncode != 0:
        raise SystemExit(f"worktree add 실패({branch}): {proc.stderr.strip() or proc.stdout.strip()}")


def remove_worktree(target: Path, path: Path) -> None:
    # 성공 정리용. Windows 잠금 등으로 실패하면 --force로 한 번 더 시도한다.
    proc = _git(target, "worktree", "remove", str(path))
    if proc.returncode != 0:
        _git(target, "worktree", "remove", "--force", str(path))


def delete_branch(target: Path, branch: str) -> None:
    # 레인 브랜치 삭제(통합 후 정리). 이미 없으면 무해하게 넘어간다.
    _git(target, "branch", "-D", branch)


def branch_exists(target: Path, branch: str) -> bool:
    # 레인 브랜치가 이미 있는지 확인(재개 시 멱등 재-add 판단용, crit 4).
    proc = _git(target, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return proc.returncode == 0


def create_integration_branch(target: Path, name: str, baseline: str) -> None:
    # baseline에서 통합 브랜치를 만든다(레인 브랜치를 여기로 순차 병합).
    proc = _git(target, "branch", name, baseline)
    if proc.returncode != 0:
        raise SystemExit(f"통합 브랜치 생성 실패({name}): {proc.stderr.strip() or proc.stdout.strip()}")


def merge_branch(target: Path, branch: str) -> MergeResult:
    # 현재 체크아웃된 통합 브랜치(target=통합 worktree)에 레인 브랜치를 병합.
    # 충돌 시 abort하고 충돌 파일을 돌려준다.
    proc = _git(target, "merge", "--no-ff", "--no-edit", branch)
    if proc.returncode == 0:
        return MergeResult(ok=True, conflicts=[], message=proc.stdout.strip())
    diff = _git(target, "diff", "--name-only", "--diff-filter=U")
    conflicts = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    _git(target, "merge", "--abort")
    return MergeResult(ok=False, conflicts=conflicts, message=f"병합 충돌({branch}): {conflicts}")

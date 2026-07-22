"""1단계 검증 스테이지 (DB-free).

구현/수정 단계 뒤에 워크스페이스에서 설정된 검증 커맨드 목록(allowlist)을 순차
실행하고, 종료코드/출력을 캡처해 산출물(04b_verification.md + .json + 커맨드별 로그)로
남긴다. 검증 실패는 예외로 런을 죽이지 않고 요약 문자열과 통과 여부(overall_ok)로
돌려줘, 상위 오케스트레이터가 최종리뷰/평가/보고 프롬프트에 실제 실행 결과를 흘린다.

Alembic 왕복 등 실 PostgreSQL이 필요한 검증은 2단계 소관이라 여기에 넣지 않는다.
커맨드는 하네스가 정한 고정 목록(설정으로만 교체 가능)이라 에이전트가 임의 명령을
실행하지 못한다(allowlist 성격).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from autoagent.artifacts import write_json, write_text

# 프롬프트에 실을 커맨드별 출력 상한(문자). 초과분은 tail만 남긴다.
MAX_CAPTURE_CHARS = 6000


def default_commands(workspace: Path) -> list[dict[str, Any]]:
    """LanguageDetection 기본 DB-free 검증 3종(venv311 python + npm).

    - compileall: 문법/임포트 깨짐 조기 검출
    - pytest tests tests_legacy: 전부 sqlite:///:memory: 픽스처라 PostgreSQL 불필요
    - frontend build: tsc --noEmit + vite build
    """
    py = str(workspace / "venv311" / "Scripts" / "python.exe")
    return [
        {"name": "compileall", "command": [py, "-m", "compileall", "-q", "src/lang_detect"]},
        {"name": "pytest", "command": [py, "-m", "pytest", "tests", "tests_legacy", "-q"]},
        {"name": "frontend_build", "command": ["npm", "--prefix", "frontend", "run", "build"]},
    ]


def _resolve(cmd0: str, workspace: Path) -> str | None:
    """커맨드 첫 토큰을 실행 파일 경로로 해석한다.

    경로형(슬래시 포함)이면 존재 여부를 확인하고, 아니면 PATH에서 조회한다.
    상대경로는 workspace 기준으로 붙인다.
    """
    if ("\\" in cmd0) or ("/" in cmd0):
        p = Path(cmd0)
        if not p.is_absolute():
            p = workspace / p
        return str(p) if p.exists() else None
    return shutil.which(cmd0)


def _tail(text: str, limit: int = MAX_CAPTURE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return "...(output truncated, tail only)...\n" + text[-limit:]


def _write_cmd_log(run_dir: Path, name: str, cname: str, stdout: str, stderr: str, rc: int | None) -> None:
    body = (
        f"# {cname} (exit={rc})\n\n"
        f"## stdout\n\n```\n{stdout}\n```\n\n"
        f"## stderr\n\n```\n{stderr}\n```\n"
    )
    write_text(run_dir / f"{name}_{cname}.md", body)


def _render_summary(results: list[dict[str, Any]], overall_ok: bool) -> str:
    lines = [
        "# 자동 검증 결과 (하네스 1단계, DB-free)",
        "",
        f"**overall: {'PASS' if overall_ok else 'FAIL'}**",
        "",
        "| 커맨드 | 상태 | exit |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['name']} | {r['status']} | {r.get('returncode')} |")
    for r in results:
        if r["status"] != "pass":
            lines.append("")
            lines.append(f"### {r['name']} - {r['status']}")
            if r.get("detail"):
                lines.append("")
                lines.append(str(r["detail"]))
            if r.get("output_tail"):
                lines.append("")
                lines.append("```")
                lines.append(str(r["output_tail"]))
                lines.append("```")
    return "\n".join(lines) + "\n"


def run_verification(
    *,
    run_dir: Path,
    workspace: Path,
    commands: list[dict[str, Any]],
    timeout_seconds: int,
    name: str = "04b_verification",
) -> tuple[str, bool]:
    """검증 커맨드들을 순차 실행하고 (요약 markdown, overall_ok)를 반환한다.

    예외를 던지지 않는다(검증 실패로 런을 죽이지 않기 위함). 각 커맨드 결과는
    커맨드별 로그 + 통합 요약(.md) + 기계판독 요약(.json)으로 run_dir에 남긴다.
    """
    results: list[dict[str, Any]] = []
    overall_ok = True

    for idx, spec in enumerate(commands, 1):
        cname = str(spec.get("name") or f"cmd{idx}")
        command = list(spec.get("command") or [])
        rel_cwd = spec.get("cwd")
        cwd = (workspace / rel_cwd) if rel_cwd else workspace

        entry: dict[str, Any] = {"name": cname, "command": command, "returncode": None}

        if not command:
            entry.update(status="error", detail="empty command")
            overall_ok = False
            results.append(entry)
            continue

        exe = _resolve(command[0], workspace)
        if exe is None:
            entry.update(status="missing", detail=f"executable not found: {command[0]}")
            overall_ok = False
            _write_cmd_log(run_dir, name, cname, "", f"executable not found: {command[0]}", None)
            results.append(entry)
            continue

        # Windows .cmd/.bat은 CreateProcess로 직접 실행되지 않으므로 cmd /c로 감싼다.
        if exe.lower().endswith((".cmd", ".bat")):
            run_cmd = ["cmd", "/c", exe, *command[1:]]
        else:
            run_cmd = [exe, *command[1:]]

        try:
            completed = subprocess.run(
                run_cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            rc = completed.returncode
            stdout, stderr = completed.stdout or "", completed.stderr or ""
            entry.update(status="pass" if rc == 0 else "fail", returncode=rc)
            if rc != 0:
                overall_ok = False
            _write_cmd_log(run_dir, name, cname, stdout, stderr, rc)
            combined = stdout + ("\n[stderr]\n" + stderr if stderr.strip() else "")
            entry["output_tail"] = _tail(combined)
        except subprocess.TimeoutExpired:
            entry.update(status="timeout", detail=f"timed out after {timeout_seconds}s")
            overall_ok = False
            _write_cmd_log(run_dir, name, cname, "", f"timeout after {timeout_seconds}s", None)
        except Exception as exc:  # 방어적: 어떤 예외도 런을 죽이지 않는다.
            entry.update(status="error", detail=f"{type(exc).__name__}: {exc}")
            overall_ok = False
            _write_cmd_log(run_dir, name, cname, "", f"{type(exc).__name__}: {exc}", None)

        results.append(entry)

    summary_md = _render_summary(results, overall_ok)
    write_text(run_dir / f"{name}.md", summary_md)
    write_json(run_dir / f"{name}.json", {"overall_ok": overall_ok, "results": results})
    return summary_md, overall_ok


def run_verification_or_skip(
    *, run_dir: Path, config: Any, name: str = "04b_verification"
) -> tuple[str, bool]:
    """config.verification_commands가 있으면 실행, 없으면 명시적 스킵 요약을 남긴다.

    미설정 프로젝트를 LD 하드코딩(default_commands)으로 폴백하지 않는다. 대신 '검증
    커맨드 미설정(실행 근거 없음)'을 기록해, 리뷰/평가 프롬프트가 근거 부재를 알게 한다.
    (요약 markdown, overall_ok)를 반환한다.
    """
    # 미설정: 조용히 스킵하되 그 사실을 산출물로 남긴다(정직한 스킵 > 남의 경로로 실패).
    if not config.verification_commands:
        summary = (
            "# 자동 검증 결과 (하네스 1단계, DB-free)\n\n"
            "**overall: SKIPPED**\n\n"
            "이 프로젝트는 verification_commands가 미설정이라 검증을 실행하지 않았습니다"
            "(실행 근거 없음). projects/<name>/config.json에 커맨드를 추가하면 활성화됩니다.\n"
        )
        write_text(run_dir / f"{name}.md", summary)
        write_json(run_dir / f"{name}.json", {"overall_ok": True, "skipped": True, "results": []})
        return summary, True
    # 설정됨: 기존 실행기에 위임(폴백 없이 config 값만 사용).
    return run_verification(
        run_dir=run_dir,
        workspace=config.workspace,
        commands=config.verification_commands,
        timeout_seconds=config.verification_timeout_seconds,
        name=name,
    )

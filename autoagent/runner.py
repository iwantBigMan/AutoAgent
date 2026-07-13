"""에이전트 실행 유틸.

claude/codex CLI 명령을 조립하고(claude_command/codex_exec_command),
서브프로세스로 실행해 산출물을 run_dir에 남기며(run_process),
에이전트 호출 총량을 제한한다(AgentCallBudget).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from autoagent.artifacts import write_text
from autoagent.config import Config


def require_command(command: str) -> str:
    """PATH에서 실행 파일을 찾아 절대경로를 반환. 없으면 즉시 종료."""
    resolved = shutil.which(command)
    if not resolved:
        raise SystemExit(f"Command not found: {command}")
    return resolved


class AgentCallBudgetStopped(Exception):
    def __init__(self, next_step: str, out_dir: Path) -> None:
        super().__init__(f"Stopped before {next_step} by agent call budget.")
        self.next_step = next_step
        self.out_dir = out_dir


@dataclass
class AgentCallBudget:
    max_agent_calls: int
    used_agent_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def before_call(self, *, next_step: str, out_dir: Path, dry_run: bool) -> None:
        # 매 에이전트 호출 직전에 부른다. dry_run은 실제 호출이 아니므로 카운트/체크하지 않는다.
        if dry_run:
            return
        # 병렬 레인이 하나의 예산 풀을 공유하므로 check-then-increment를 락으로 원자화한다.
        with self._lock:
            if self.max_agent_calls > 0 and self.used_agent_calls >= self.max_agent_calls:
                write_text(
                    out_dir / "stopped_by_budget.md",
                    "# Stopped by Agent Call Budget\n\n"
                    "The run stopped before the next agent call.\n\n"
                    f"- max_agent_calls: {self.max_agent_calls}\n"
                    f"- used_agent_calls: {self.used_agent_calls}\n"
                    f"- next_step: {next_step}\n"
                    "- reason: Agent call budget exhausted.\n",
                )
                raise AgentCallBudgetStopped(next_step, out_dir)
            self.used_agent_calls += 1


def claude_command(
    claude: str,
    model: str | None = None,
    permission_mode: str | None = None,
    effort: str | None = None,
    skip_permissions: bool = False,
) -> list[str]:
    """headless `claude -p ...` 명령 리스트를 조립한다(model/permission-mode/effort 선택).

    skip_permissions=True면 --dangerously-skip-permissions를 붙인다. 헤드리스 `claude -p`
    기본 권한 모드는 파일 편집/명령 실행에 승인을 요구하는데, 서브프로세스엔 승인해줄 TTY가
    없어 mutating 스텝의 Edit/Write가 전부 거부된다(구현이 디스크에 반영되지 않는 원인).
    구현/수정 같은 mutating 스텝은 이 플래그로 자율 실행하게 하며, Codex의
    `--ask-for-approval never`와 대칭이다. permission_mode와는 상호배타적으로 쓴다.
    """
    command = [claude, "-p"]
    if model:
        command.extend(["--model", model])
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    if skip_permissions:
        command.append("--dangerously-skip-permissions")
    if effort:
        # 유효값: low/medium/high/xhigh/max. 그 외는 claude가 경고 후 기본값으로 무시.
        command.extend(["--effort", effort])
    command.extend(["--input-format", "text"])
    return command


def codex_exec_command(config: Config, codex: str, sandbox: str, model: str | None = None) -> list[str]:
    """`codex exec ...` 명령 리스트를 조립한다(승인 정책·모델·샌드박스·작업공간 포함)."""
    command = [
        codex,
        "--ask-for-approval",
        config.codex_approval,
        "exec",
    ]
    selected_model = model or config.codex_model
    if selected_model:
        command.extend(["-m", selected_model])
    command.extend(
        [
            "-C",
            str(config.workspace),
            "--sandbox",
            sandbox,
            "--skip-git-repo-check",
            "-",
        ]
    )
    return command


def write_command_artifact(out_dir: Path, name: str, command: list[str]) -> None:
    write_text(out_dir / f"{name}_command.json", json.dumps(command, ensure_ascii=False, indent=2))


def run_process(
    *,
    name: str,
    command: list[str],
    prompt: str,
    cwd: Path,
    out_dir: Path,
    timeout_seconds: int,
) -> str:
    """프롬프트를 stdin으로 넣어 명령을 실행하고, 프롬프트/명령/stdout/stderr/exit를
    run_dir에 남긴 뒤 stdout을 반환한다. 종료코드가 0이 아니면 즉시 종료한다.
    """
    write_text(out_dir / f"{name}_prompt.md", prompt)
    write_text(out_dir / f"{name}_command.json", json.dumps(command, ensure_ascii=False, indent=2))

    completed = subprocess.run(
        command,
        input=prompt,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )

    write_text(out_dir / f"{name}_stdout.md", completed.stdout)
    write_text(out_dir / f"{name}_stderr.txt", completed.stderr)
    write_text(out_dir / f"{name}_exit_code.txt", str(completed.returncode))

    if completed.returncode != 0:
        raise SystemExit(
            f"{name} failed with exit code {completed.returncode}. "
            f"See {out_dir / (name + '_stderr.txt')}"
        )

    return completed.stdout

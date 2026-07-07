from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from autoagent.artifacts import write_text
from autoagent.config import Config


def require_command(command: str) -> str:
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

    def before_call(self, *, next_step: str, out_dir: Path, dry_run: bool) -> None:
        if dry_run:
            return
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
) -> list[str]:
    command = [claude, "-p"]
    if model:
        command.extend(["--model", model])
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    command.extend(["--input-format", "text"])
    return command


def codex_exec_command(config: Config, codex: str, sandbox: str, model: str | None = None) -> list[str]:
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

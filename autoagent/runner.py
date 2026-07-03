from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from autoagent.artifacts import write_text
from autoagent.config import Config


def require_command(command: str) -> str:
    resolved = shutil.which(command)
    if not resolved:
        raise SystemExit(f"Command not found: {command}")
    return resolved


def claude_command(claude: str) -> list[str]:
    return [claude, "-p", "--input-format", "text"]


def codex_exec_command(config: Config, codex: str, sandbox: str) -> list[str]:
    return [
        codex,
        "--ask-for-approval",
        config.codex_approval,
        "exec",
        "-C",
        str(config.workspace),
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "-",
    ]


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

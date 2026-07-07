from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "autoagent.config.json"
PROMPT_ALIASES = {
    "plan.md": "simple/plan.md",
    "execute.md": "simple/execute.md",
    "review.md": "simple/review.md",
    "claude_decompose.md": "decompose/claude_decompose.md",
    "codex_plan_review.md": "decompose/codex_plan_review.md",
    "claude_context.md": "routed/context/claude_context.md",
    "claude_architect.md": "routed/context/claude_architect.md",
    "codex_validation.md": "routed/context/codex_validation.md",
    "claude_backend_impl.md": "routed/backend/claude_impl.md",
    "codex_backend_impl.md": "routed/backend/codex_impl.md",
    "claude_backend_review.md": "routed/backend/claude_review.md",
    "codex_backend_review.md": "routed/backend/codex_review.md",
    "claude_backend_fix.md": "routed/backend/claude_fix.md",
    "codex_backend_fix.md": "routed/backend/codex_fix.md",
    "claude_frontend_impl.md": "routed/frontend/claude_impl.md",
    "codex_frontend_impl.md": "routed/frontend/codex_impl.md",
    "claude_frontend_review.md": "routed/frontend/claude_review.md",
    "codex_frontend_review.md": "routed/frontend/codex_review.md",
    "claude_frontend_fix.md": "routed/frontend/claude_fix.md",
    "codex_frontend_fix.md": "routed/frontend/codex_fix.md",
    "claude_final.md": "routed/final/claude_final.md",
    "codex_final.md": "routed/final/codex_final.md",
    "codex_evaluator.md": "routed/final/codex_evaluator.md",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def render_template(name: str, values: dict[str, str]) -> str:
    template = read_text(prompt_path(name))
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def prompt_path(name: str) -> Path:
    relative = PROMPT_ALIASES.get(name, name)
    return ROOT / "prompts" / relative


def make_run_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"_{attempt:02d}"
        path = ROOT / "runs" / f"{stamp}{suffix}"
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise SystemExit("Could not create a unique run directory.")


def write_metadata(path: Path, data: dict[str, Any]) -> None:
    write_json(path / "metadata.json", data)


def extract_json_block(text: str) -> dict[str, Any]:
    task_graph_match = re.search(
        r"TASK_GRAPH_JSON\s*```(?:json)?\s*(\{.*?\})\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if task_graph_match:
        return json.loads(task_graph_match.group(1))

    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE):
        candidate = json.loads(match.group(1))
        if isinstance(candidate, dict) and "tasks" in candidate:
            return candidate

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = json.loads(text[start : end + 1])
        if isinstance(candidate, dict):
            return candidate

    raise ValueError("No JSON object found in text.")

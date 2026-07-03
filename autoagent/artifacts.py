from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "autoagent.config.json"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def render_template(name: str, values: dict[str, str]) -> str:
    template = read_text(ROOT / "prompts" / name)
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


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

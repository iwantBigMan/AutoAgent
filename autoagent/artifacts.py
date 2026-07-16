"""산출물·프롬프트·JSON 입출력 유틸.

- read_text/write_text/write_json: UTF-8 파일 입출력.
- render_template + PROMPT_ALIASES: 프롬프트 템플릿의 {{KEY}} 치환.
- make_run_dir: runs/타임스탬프 실행 폴더 생성.
- extract_json_block: 텍스트에서 task_graph JSON 블록 추출.
"""
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
    """프롬프트 템플릿을 읽어 {{KEY}}를 values로 치환한다.

    단순 문자열 치환이라 values에 없는 placeholder는 {{KEY}} 그대로 남고,
    템플릿에 없는 key는 무시된다(무해). 그래서 라운드 피드백 같은 선택 값을
    빈 문자열로 넘겨도 안전하다.
    """
    template = read_text(prompt_path(name))
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def prompt_path(name: str) -> Path:
    relative = PROMPT_ALIASES.get(name, name)
    return ROOT / "prompts" / relative


def validate_project_name(project: str) -> None:
    """project 이름이 단일 path segment인지 검증한다.

    '..', '/', '\\'가 섞이면 projects/<name> 경계를 벗어나 다른 위치의 config를
    읽거나 runs 디렉터리를 만들 수 있어(경로 이탈) 여기서 명확히 막는다.
    """
    if not project or project in {".", ".."} or "/" in project or "\\" in project:
        raise SystemExit(f"Invalid project name: {project!r}")


def ensure_project_config(config_dir: Path, project: str, workspace: Path) -> Path:
    """projects/<project>/config.json이 없으면 workspace만 채워 생성한다(있으면 무동작).

    반환은 config 경로. 이름은 validate_project_name으로 검증한다(경로 이탈 방지).
    생성 시 안내를 출력한다. projects/*/config.json은 gitignored라 커밋되지 않는다.
    """
    validate_project_name(project)
    cfg = config_dir / "projects" / project / "config.json"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            json.dumps({"workspace": str(workspace)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[project] 새 프로젝트 config 생성: {cfg} (workspace={workspace})")
    return cfg


def make_run_dir(project: str | None = None) -> Path:
    """runs 폴더를 만든다. project가 있으면 projects/<name>/runs 아래, 없으면 기존 ROOT/runs 아래."""
    if project is not None:
        # 빈 문자열('')도 명시 지정이면 거부한다. if project:면 falsy라 조용히 ROOT/runs로 폴백됨.
        validate_project_name(project)
    base = ROOT / "projects" / project / "runs" if project else ROOT / "runs"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"_{attempt:02d}"
        path = base / f"{stamp}{suffix}"
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise SystemExit("Could not create a unique run directory.")


def write_metadata(path: Path, data: dict[str, Any]) -> None:
    write_json(path / "metadata.json", data)


def extract_json_block(text: str) -> dict[str, Any]:
    """분해 결과 텍스트에서 task_graph JSON을 추출한다.

    1) `TASK_GRAPH_JSON` 마커 뒤 코드펜스를 우선 찾고,
    2) 없으면 tasks 키를 가진 아무 JSON 펜스,
    3) 그래도 없으면 첫 { ~ 마지막 } 범위를 시도한다. 모두 실패하면 예외.
    """
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

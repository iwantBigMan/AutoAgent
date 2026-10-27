"""재개 실행의 대상/정책 스냅샷과 미리보기 격리."""
from __future__ import annotations

import dataclasses
import json
import shutil
from argparse import Namespace
from pathlib import Path

from autoagent.artifacts import read_text, write_json


ARG_KEYS = (
    "workflow", "task_type", "implementer", "read_only", "max_review_rounds",
    "max_agent_calls", "stop_after", "skip_verification", "require_human_approval",
    "plan_only", "skip_review", "auto_approve_nonbranch", "project", "config", "no_refine",
)
# 인증정보는 새 복사본에 저장하지 않고 기존 config에서만 다시 읽는다.
SECRET_FIELDS = {"workspace", "data_go_kr_service_key", "mcp_servers", "mcp_config_path", "mcp_allowed_tools"}


def save_execution_context(args, config, run_dir: Path, request: str) -> None:
    if args.dry_run:
        return
    write_json(run_dir / "execution_context.json", {
        "workspace": str(config.workspace.resolve()),
        "request": request,
        "args": {key: getattr(args, key, None) for key in ARG_KEYS},
        "policy": {key: value for key, value in dataclasses.asdict(config).items() if key not in SECRET_FIELDS},
    })


def restore_execution_context(args, run_dir: Path) -> dict:
    """명시적 재개 옵션만 우선하고, 기존 run도 checkpoint/metadata로 복원한다."""
    explicit = set(getattr(args, "_explicit_options", ()))
    context_path = run_dir / "execution_context.json"
    if context_path.exists():
        saved = json.loads(read_text(context_path))
    else:
        metadata = json.loads(read_text(run_dir / "metadata.json")) if (run_dir / "metadata.json").exists() else {}
        checkpoint = json.loads(read_text(run_dir / "checkpoint.json")) if (run_dir / "checkpoint.json").exists() else {}
        refine = json.loads(read_text(run_dir / "refine_status.json")) if (run_dir / "refine_status.json").exists() else {}
        values = {**metadata, **refine.get("args", {}), **checkpoint}
        if values.get("config_path"):
            values["config"] = values["config_path"]
        saved = {"args": values, "workspace": checkpoint.get("workspace") or refine.get("workspace") or metadata.get("workspace")}
        # 구형 run은 절대 경로를 증명할 수 없으면 명시적 workspace를 요구한다.
        if saved["workspace"] and not Path(saved["workspace"]).is_absolute() and "workspace" not in explicit:
            raise SystemExit("Legacy run has a relative workspace; resume with --workspace <absolute path>.")
    for key in ARG_KEYS:
        if key not in explicit and key in saved.get("args", {}):
            setattr(args, key, saved["args"][key])
    if "workspace" not in explicit and saved.get("workspace"):
        args.workspace = saved["workspace"]
    return saved


def isolate_preview(run_dir: Path) -> Path:
    """재개 dry-run은 원본 상태/산출물과 분리된 하위 디렉터리에 쓴다."""
    for index in range(1000):
        preview = run_dir / "previews" / f"preview_{index:03d}"
        try:
            preview.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            continue
    else:
        raise SystemExit("Cannot allocate preview directory.")
    for source in run_dir.iterdir():
        if source.is_file():
            shutil.copy2(source, preview / source.name)
    return preview

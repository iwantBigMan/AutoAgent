"""소스 스냅샷 저장 층(§2.1).

Claude WebFetch/defuddle이 긁어온 원문 텍스트를 결정론적으로 runs/sources/*.txt에
고정하고 fetch 메타(url·fetch_ts·http_status·sha256·char_count)를 남긴다. 이후 모든
대조(Codex 검증기 포함)는 재fetch 없이 이 스냅샷만 읽어 링크썩음·본문변동을 배제한다.
순수 함수라 pytest로 못박는다(모델 호출 없음).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoagent.artifacts import write_json, write_text

# 파일명 세그먼트 허용 문자: 영숫자·하이픈·언더스코어. 나머지는 '_'로 접는다.
_SAFE_SEGMENT = re.compile(r"[^a-z0-9_-]+")


@dataclass
class SourceSnapshot:
    """한 소스의 스냅샷 파일 + fetch 메타(sources_manifest.json 항목이자 검증 입력)."""

    ref_id: str
    url: str
    snapshot_path: str   # run_dir 기준 상대경로(예: "sources/s1.txt")
    fetch_ts: str        # ISO8601 UTC
    http_status: int
    sha256: str
    char_count: int


def slugify_ref(ref_id: str) -> str:
    """ref_id를 안전한 파일명 세그먼트로 정규화한다(경로이탈·비ASCII 차단).

    소문자화 후 [a-z0-9_-] 외 문자를 '_'로 접고 양끝 '_'·'-'를 다듬는다. '..'가
    남지 않도록 점은 애초에 허용문자에서 빠져 '_'가 된다. 결과가 비면(전부 불법문자)
    조용히 빈 파일명을 쓰지 않고 ValueError를 던진다(정직한 에러).
    """
    lowered = ref_id.strip().lower()
    slug = _SAFE_SEGMENT.sub("_", lowered).strip("_-")
    if not slug:
        raise ValueError(f"ref_id로 안전한 파일명을 만들 수 없음: {ref_id!r}")
    return slug


def save_snapshot(
    sources_dir: Path, ref_id: str, url: str, fetched_text: str, *,
    http_status: int, fetch_ts: str | None = None,
) -> SourceSnapshot:
    """원문 텍스트를 sources_dir/<slug>.txt에 저장하고 SourceSnapshot을 만든다.

    sha256/char_count는 저장한 원문 그대로에서 계산한다(부분문자열 대조의 기준).
    fetch_ts 미지정이면 지금(UTC) 시각을 ISO8601로 채운다. snapshot_path는 run_dir
    기준 상대경로("sources/<slug>.txt")로 넣어 아티팩트 이식성을 유지한다.
    """
    slug = slugify_ref(ref_id)
    path = sources_dir / f"{slug}.txt"
    write_text(path, fetched_text)  # utf-8, newline="\n"
    sha256 = hashlib.sha256(fetched_text.encode("utf-8")).hexdigest()
    ts = fetch_ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SourceSnapshot(
        ref_id=ref_id, url=url, snapshot_path=f"sources/{slug}.txt", fetch_ts=ts,
        http_status=int(http_status), sha256=sha256, char_count=len(fetched_text),
    )


def write_sources_manifest(run_dir: Path, snapshots: list[SourceSnapshot]) -> Path:
    """스냅샷 메타 배열을 run_dir/sources_manifest.json에 기록하고 경로를 반환한다."""
    path = run_dir / "sources_manifest.json"
    write_json(path, {"sources": [asdict(s) for s in snapshots]})
    return path


def load_snapshot_text(sources_dir: Path, ref_id: str) -> str:
    """저장된 스냅샷 원문을 utf-8로 되읽는다(검증 코드층의 부분문자열 대조용)."""
    slug = slugify_ref(ref_id)
    return (sources_dir / f"{slug}.txt").read_text(encoding="utf-8")

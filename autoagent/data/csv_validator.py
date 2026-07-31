"""CSV 품질 실측(data_quality 어댑터 c 스테이지용).

stdlib `csv`만 쓴다(pandas 불필요, 의존성 0). 인코딩은 cp949 gotcha를 고려해
`utf-8 → utf-8-sig → cp949` 순서로 자동 폴백하고, 모두 실패하면 조용히 skip하지
않고 정직하게 예외를 올린다. 입력 파일 sha256을 provenance로 고정한다.

여기서 산출하는 CSVQualityMetrics는 순수 함수 결과라 결정론이며, adapters.py의
data_quality 분기가 이 지표 + transform_manifest/claim 재계산을 합쳐 Verdict를 만든다.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# 인코딩 폴백 순서(고정). cp949는 한국어 CSV 덤프에서 흔한 마지막 보루.
_ENCODING_FALLBACKS: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp949")


@dataclass
class CSVQualityMetrics:
    """단일 CSV의 결정론적 품질 지표 묶음(계약 고정 필드)."""

    path: str
    row_count: int
    column_count: int
    columns: list[str]
    null_ratio_by_column: dict[str, float]
    duplicate_row_count: int
    duplicate_ratio: float
    format_anomalies: list[str] = field(default_factory=list)
    encoding_detected: str = ""


def _sha256_of_file(path: Path) -> str:
    """파일 바이트의 sha256 hex digest(provenance 고정용)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_csv_rows(path: Path) -> tuple[str, list[str], list[list[str]]]:
    """인코딩 폴백으로 CSV를 읽어 (감지 인코딩, header, data_rows)를 반환한다.

    utf-8→utf-8-sig→cp949 순서로 디코드를 시도하고, 처음 성공한 인코딩으로 파싱한다.
    셋 다 실패하면 조용한 skip 대신 ValueError로 정직하게 올린다(cp949 gotcha).

    주의: BOM(``\\xef\\xbb\\xbf``)이 있는 파일은 순수 ``utf-8`` 디코드도 예외 없이
    성공해버린다(BOM 세 바이트가 U+FEFF 한 글자로 그대로 남을 뿐이라 폴백이 트리거되지
    않음). 그래서 시도 순서를 그대로 따르되, 파일 앞부분에 UTF-8 BOM이 있으면
    `utf-8-sig`를 먼저 시도해 BOM을 올바르게 벗겨내도록 순서를 조정한다.
    """
    raw = path.read_bytes()
    encodings = _ENCODING_FALLBACKS
    if raw.startswith(codecs.BOM_UTF8):
        # utf-8은 BOM을 에러 없이 그냥 문자로 남기므로, BOM이 보이면 utf-8-sig를 우선한다.
        encodings = ("utf-8-sig",) + tuple(e for e in _ENCODING_FALLBACKS if e != "utf-8-sig")

    last_error: Exception | None = None
    for enc in encodings:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_error = exc
            continue
        reader = csv.reader(text.splitlines())
        rows = list(reader)
        if not rows:
            return enc, [], []
        header, *data = rows
        return enc, header, data
    raise ValueError(
        f"failed to decode CSV with any of {_ENCODING_FALLBACKS}: {path} ({last_error})"
    )

"""validate_csv 품질 지표 실측 단위테스트(결정론)."""
from __future__ import annotations

from pathlib import Path

import pytest

from autoagent.data.csv_validator import CSVQualityMetrics, validate_csv


def _w(tmp_path: Path, name: str, text: str, enc: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return p


def test_basic_shape_and_columns(tmp_path: Path) -> None:
    p = _w(tmp_path, "a.csv", "id,name\n1,kim\n2,lee\n")
    m = validate_csv(p)
    assert isinstance(m, CSVQualityMetrics)
    assert m.row_count == 2
    assert m.column_count == 2
    assert m.columns == ["id", "name"]
    assert m.encoding_detected == "utf-8"


def test_null_ratio_counts_blank_and_whitespace(tmp_path: Path) -> None:
    p = _w(tmp_path, "n.csv", "id,name\n1,\n2,   \n3,kim\n")
    m = validate_csv(p)
    assert m.null_ratio_by_column["name"] == pytest.approx(2 / 3)
    assert m.null_ratio_by_column["id"] == 0.0


def test_duplicate_rows_full_tuple(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "id,name\n1,kim\n1,kim\n2,lee\n")
    m = validate_csv(p)
    assert m.duplicate_row_count == 1
    assert m.duplicate_ratio == pytest.approx(1 / 3)


def test_ragged_row_flagged_as_anomaly(tmp_path: Path) -> None:
    p = _w(tmp_path, "r.csv", "id,name\n1,kim,extra\n")
    m = validate_csv(p)
    assert any("column count" in a.lower() for a in m.format_anomalies)


def test_empty_file_is_honest(tmp_path: Path) -> None:
    p = _w(tmp_path, "e.csv", "")
    m = validate_csv(p)
    assert m.row_count == 0
    assert m.column_count == 0
    assert m.columns == []


def test_undecodable_propagates(tmp_path: Path) -> None:
    p = tmp_path / "junk.csv"
    p.write_bytes(b"\x81\x00\xff\xfe\x9d\x8f\n")
    with pytest.raises(ValueError):
        validate_csv(p)


def test_quoted_field_with_embedded_newline_is_preserved(tmp_path: Path) -> None:
    # [T8]: csv.reader(text.splitlines())는 quoted 필드 내부의 개행을 splitlines()가
    # 먼저 잘라버려 RFC-4180 위반으로 깨뜨린다. io.StringIO 경유로 고쳤으니, 개행을
    # 품은 필드가 한 행으로 유지되고 row/column count도 올바르게 나와야 한다.
    p = _w(tmp_path, "multiline.csv", 'id,note\n1,"a\nb"\n2,plain\n')
    m = validate_csv(p)
    assert m.row_count == 2       # 개행이 새 행으로 오분할되지 않음
    assert m.column_count == 2
    assert m.duplicate_row_count == 0

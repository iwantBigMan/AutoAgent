"""csv_validator의 인코딩 폴백·sha256 결정성 단위테스트."""
from __future__ import annotations

import codecs
import hashlib
from pathlib import Path

import pytest

from autoagent.data.csv_validator import _read_csv_rows, _sha256_of_file


def _write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_utf8_plain(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "u8.csv", "name,city\n가,서울\n".encode("utf-8"))
    enc, header, rows = _read_csv_rows(p)
    assert enc == "utf-8"
    assert header == ["name", "city"]
    assert rows == [["가", "서울"]]


def test_utf8_sig_bom(tmp_path: Path) -> None:
    # 주의: "text".encode("utf-8-sig")가 이미 BOM을 붙이므로, 소스 문자열에
    # 리터럴 BOM(U+FEFF)을 직접 넣으면 이중 BOM 바이트가 되어버린다(cp949 gotcha류).
    # codecs.BOM_UTF8 + 순수 utf-8 바이트로 명시적으로 조립해 단일 BOM만 보장한다.
    content = "name,city\n가,서울\n".encode("utf-8")
    p = _write_bytes(tmp_path, "bom.csv", codecs.BOM_UTF8 + content)
    enc, header, rows = _read_csv_rows(p)
    assert enc == "utf-8-sig"
    assert header == ["name", "city"]


def test_cp949_fallback(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "cp949.csv", "이름,도시\n가,서울\n".encode("cp949"))
    enc, header, rows = _read_csv_rows(p)
    assert enc == "cp949"
    assert header == ["이름", "도시"]
    assert rows == [["가", "서울"]]


def test_undecodable_raises_honest_error(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "junk.csv", b"\x81\x00\xff\xfe\x9d\x8f\n")
    with pytest.raises(ValueError, match="decode"):
        _read_csv_rows(p)


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    data = b"name,city\na,b\n"
    p = _write_bytes(tmp_path, "h.csv", data)
    assert _sha256_of_file(p) == hashlib.sha256(data).hexdigest()

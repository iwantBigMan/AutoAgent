"""run_data_quality 집계·verdict 재계산 단위테스트(모델 0회)."""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.research.data_quality import run_data_quality


def _w(d: Path, name: str, text: str) -> Path:
    p = d / name
    p.write_bytes(text.encode("utf-8"))
    return p


def _stage_out(source: Path, cleaned: Path, **over) -> dict:
    base = {
        "cleaned_files": [{"path": str(cleaned), "source_dump_path": str(source)}],
        "transform_manifest": {"steps": []},
        "derived_claims": [],
        "schema_expectations": {},
    }
    base.update(over)
    return base


def test_clean_passthrough_is_pass(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "id,amt\n1,10\n2,20\n")
    cln = _w(tmp_path, "c.csv", "id,amt\n1,10\n2,20\n")
    out = _stage_out(src, cln, schema_expectations={"id": "int", "amt": "int"})
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "pass"
    assert v.adapter == "data_quality"
    assert v.stage_id == "c"
    assert v.findings == []
    assert (tmp_path / "c_data_quality.json").exists()
    raw = json.loads((tmp_path / "c_data_quality.json").read_text(encoding="utf-8"))
    assert raw["overall_ok"] is True
    assert raw["adapter"] == "data_quality"
    assert "provenance" in raw


def test_claim_mismatch_downgrades_to_needs_changes(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "region,amt\nseoul,10\nseoul,30\n")
    cln = _w(tmp_path, "c.csv", "region,amt\nseoul,10\nseoul,30\n")
    out = _stage_out(src, cln,
        derived_claims=[{"id": "k1", "text": "sum", "backing_stat": {"metric": "sum", "col": "amt", "value": 999}}])
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "needs_changes"
    assert any(f.claim_id == "k1" for f in v.findings)


def test_unreadable_source_is_blocked(tmp_path: Path) -> None:
    src = tmp_path / "junk.csv"
    src.write_bytes(b"\x81\x00\xff\xfe\x9d\x8f\n")
    cln = _w(tmp_path, "c.csv", "id\n1\n")
    out = _stage_out(src, cln)
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "blocked"
    assert any(f.category == "file_read_error" for f in v.findings)


def test_unexplained_drop_needs_changes(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "id\n1\n2\n3\n4\n")
    cln = _w(tmp_path, "c.csv", "id\n1\n")
    out = _stage_out(src, cln)
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "needs_changes"
    assert any(f.category == "unexplained_row_loss" for f in v.findings)

"""adapters.verify의 data_quality 디스패치 배선 테스트(모델 0회)."""
from __future__ import annotations

from pathlib import Path

from autoagent.research.adapters import verify


def _w(d: Path, name: str, text: str) -> Path:
    p = d / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_verify_dispatches_data_quality(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "id,amt\n1,10\n2,20\n")
    cln = _w(tmp_path, "c.csv", "id,amt\n1,10\n2,20\n")
    stage_out = {
        "cleaned_files": [{"path": str(cln), "source_dump_path": str(src)}],
        "transform_manifest": {"steps": []},
        "derived_claims": [],
        "schema_expectations": {"id": "int", "amt": "int"},
    }
    v = verify("data_quality", stage_out, tmp_path, verifier_agent="code", config=None)
    assert v.adapter == "data_quality"
    assert v.stage_id == "c"
    assert v.status == "pass"
    assert (tmp_path / "c_data_quality.json").exists()

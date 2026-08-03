"""data_quality 결정론 체크 세트 단위테스트(모델 0회)."""
from __future__ import annotations

from pathlib import Path

from autoagent.data.csv_validator import validate_csv
from autoagent.research.data_quality import (
    check_claims, check_row_conservation, check_sanity, check_schema,
    recompute_claim, tolerance_for,
)


def _w(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_tolerance_exact_for_count_sum_rowcount() -> None:
    assert tolerance_for("count") == 0.0
    assert tolerance_for("sum") == 0.0
    assert tolerance_for("row_count") == 0.0


def test_tolerance_one_percent_for_ratio_cagr_mean() -> None:
    assert tolerance_for("ratio") == 0.01
    assert tolerance_for("cagr") == 0.01
    assert tolerance_for("mean") == 0.01


def test_row_conservation_fully_explained_passes(tmp_path: Path) -> None:
    src = validate_csv(_w(tmp_path, "s.csv", "id\n1\n2\n3\n4\n"))
    cln = validate_csv(_w(tmp_path, "c.csv", "id\n1\n2\n3\n"))
    manifest = {"steps": [{"op": "dedup", "target_cols": ["id"], "params": {"dropped": 1}}]}
    delta, findings = check_row_conservation(src, cln, manifest)
    assert delta["source_rows"] == 4
    assert delta["cleaned_rows"] == 3
    assert delta["dropped"] == 1
    assert findings == []


def test_row_conservation_unexplained_drop_is_finding(tmp_path: Path) -> None:
    src = validate_csv(_w(tmp_path, "s.csv", "id\n1\n2\n3\n4\n5\n"))
    cln = validate_csv(_w(tmp_path, "c.csv", "id\n1\n2\n"))
    manifest = {"steps": [{"op": "dedup", "params": {"dropped": 1}}]}
    delta, findings = check_row_conservation(src, cln, manifest)
    assert delta["dropped"] == 3
    assert any(f.severity in {"critical", "major"} for f in findings)
    assert any("unexplained" in f.detail.lower() for f in findings)


def test_recompute_count(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nbusan,20\nseoul,30\n")
    val = recompute_claim(p, {"metric": "count", "col": "region", "filter": {"region": "seoul"}})
    assert val == 2


def test_recompute_sum(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nbusan,20\nseoul,30\n")
    val = recompute_claim(p, {"metric": "sum", "col": "amt", "filter": {"region": "seoul"}})
    assert val == 40.0


def test_check_claims_exact_mismatch_flags(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nseoul,30\n")
    claims = [{"id": "k1", "text": "seoul sum", "backing_stat": {"metric": "sum", "col": "amt", "value": 41}}]
    recompute, findings = check_claims(p, claims)
    assert recompute[0]["claim_id"] == "k1"
    assert recompute[0]["recomputed_value"] == 40.0
    assert recompute[0]["match"] is False
    assert any(f.claim_id == "k1" for f in findings)


def test_check_claims_ratio_within_tolerance_matches(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nseoul,30\nbusan,10\n")
    claims = [{"id": "r1", "text": "seoul share",
               "backing_stat": {"metric": "ratio", "col": "region", "value": 0.67, "filter": {"region": "seoul"}}}]
    recompute, findings = check_claims(p, claims)
    assert recompute[0]["match"] is True
    assert findings == []


def test_schema_diff_type_mismatch_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,x\n2,y\n"))
    diff, findings = check_schema(cln, {"id": "int", "amt": "int"})
    amt = next(d for d in diff if d["col"] == "amt")
    assert amt["ok"] is False
    assert any("amt" in f.detail for f in findings)


def test_schema_diff_all_ok(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,10\n2,20\n"))
    diff, findings = check_schema(cln, {"id": "int", "amt": "int"})
    assert all(d["ok"] for d in diff)
    assert findings == []


def test_sanity_negative_revenue_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,revenue\n1,100\n2,-5\n"))
    checks, findings = check_sanity(cln, {"non_negative_cols": ["revenue"]})
    assert any(c["status"] == "fail" for c in checks)
    assert any("revenue" in f.detail for f in findings)


def test_sanity_duplicate_key_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,v\n1,a\n1,b\n"))
    checks, findings = check_sanity(cln, {"unique_cols": ["id"]})
    assert any(c["status"] == "fail" and "id" in c.get("col", "") for c in checks)
    assert any("id" in f.detail for f in findings)


def test_sanity_out_of_range_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,100\n2,999999\n"))
    checks, findings = check_sanity(cln, {"range_cols": {"amt": [0, 1000]}})
    check = next(c for c in checks if c["name"] == "range[amt]")
    assert check["status"] == "fail"
    assert check["metric_actual"] == 1
    assert any(f.category == "out_of_range" and "amt" in f.detail for f in findings)


def test_sanity_in_range_passes_no_finding(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,100\n2,200\n"))
    checks, findings = check_sanity(cln, {"range_cols": {"amt": [0, 1000]}})
    check = next(c for c in checks if c["name"] == "range[amt]")
    assert check["status"] == "pass"
    assert findings == []


def test_sanity_range_malformed_bounds_skipped_no_crash(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,100\n2,200\n"))
    checks, findings = check_sanity(cln, {"range_cols": {"amt": [0]}})
    assert not any(c["name"] == "range[amt]" for c in checks)
    assert findings == []


def test_sanity_future_date_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,order_date\n1,2026-08-01\n2,2026-08-10\n"))
    checks, findings = check_sanity(
        cln, {"future_date_cols": ["order_date"], "as_of_date": "2026-08-03"},
    )
    check = next(c for c in checks if c["name"] == "future_date[order_date]")
    assert check["status"] == "fail"
    assert check["metric_actual"] == 1
    assert any(f.category == "future_date" and "order_date" in f.detail for f in findings)


def test_sanity_date_on_or_before_reference_passes(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,order_date\n1,2026-08-03\n2,2026-08-01\n"))
    checks, findings = check_sanity(
        cln, {"future_date_cols": ["order_date"], "as_of_date": "2026-08-03"},
    )
    check = next(c for c in checks if c["name"] == "future_date[order_date]")
    assert check["status"] == "pass"
    assert findings == []


def test_sanity_unparseable_date_skipped_not_counted(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,order_date\n1,not-a-date\n2,2026-08-01\n"))
    checks, findings = check_sanity(
        cln, {"future_date_cols": ["order_date"], "as_of_date": "2026-08-03"},
    )
    check = next(c for c in checks if c["name"] == "future_date[order_date]")
    assert check["status"] == "pass"
    assert check["metric_actual"] == 0
    assert findings == []

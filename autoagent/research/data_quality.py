"""data_quality 어댑터의 결정론 체크 세트(c 스테이지, 모델 0회).

스펙 §4.2의 4대 체크를 순수 함수로 구현한다:
(1) 행수 보존 — dropped가 transform_manifest로 100% 설명되는가,
(2) claim 재계산 — 원본 CSV에서 **독립 경로**로 재산출(manifest 재실행 아님),
(3) 스키마 정합 — 기대 dtype과 실측 열 타입 대조,
(4) sanity — 중복키·음수매출 등 상식 위반.

tolerance는 metric kind별로 코드가 고정한다(합계·행수=정확일치, 비율·CAGR=1%).
임계값을 여기 하드코딩하는 이유: 에이전트가 못 바꿔야 tautology(자기 기준 통과)를
차단할 수 있기 때문. Finding은 Slice 1 types.py의 계약을 쓴다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from autoagent.data.csv_validator import CSVQualityMetrics, _read_csv_rows
from autoagent.research.types import Finding

if TYPE_CHECKING:
    from autoagent.research.types import Verdict

# verdict schema_version.
CHECK_SET_VERSION = 1

# metric kind별 상대 허용오차(고정). 합계·행수·카운트는 정확일치, 비율류만 1%.
_EXACT_METRICS = {"count", "sum", "row_count"}
_RATIO_METRICS = {"ratio", "cagr", "mean"}


def tolerance_for(metric: str) -> float:
    """metric kind별 상대 허용오차. 정확일치=0.0, 비율/CAGR/평균=0.01(1%)."""
    key = metric.lower()
    if key in _EXACT_METRICS:
        return 0.0
    if key in _RATIO_METRICS:
        return 0.01
    return 0.0  # 미지 metric은 보수적으로 정확일치(느슨함 방지)


def _values_match(claimed: float, actual: float, tol: float) -> bool:
    """claimed가 actual의 tol(상대) 안이면 일치. tol=0이면 정확일치."""
    if tol == 0.0:
        return claimed == actual
    if actual == 0.0:
        return abs(claimed) <= tol
    return abs(claimed - actual) / abs(actual) <= tol


def check_row_conservation(
    source_metrics: CSVQualityMetrics, cleaned_metrics: CSVQualityMetrics, manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[Finding]]:
    """행수 보존 체크. dropped가 manifest step params로 100% 설명 안 되면 major."""
    source_rows = source_metrics.row_count
    cleaned_rows = cleaned_metrics.row_count
    dropped = source_rows - cleaned_rows

    explained = 0
    breakdown: dict[str, int] = {}
    for step in manifest.get("steps", []) or []:
        op = str(step.get("op", "unknown"))
        d = int((step.get("params") or {}).get("dropped", 0))
        if d:
            explained += d
            breakdown[op] = breakdown.get(op, 0) + d

    findings: list[Finding] = []
    if dropped < 0:
        findings.append(Finding(
            severity="major", category="row_growth",
            detail=f"cleaned rows ({cleaned_rows}) > source rows ({source_rows}); row growth of {-dropped} rows",
            fix_directive="join/derive로 인한 행 증가를 manifest에 명시하거나 제거하세요.",
        ))
    elif dropped != explained:
        findings.append(Finding(
            severity="major", category="unexplained_row_loss",
            detail=f"unexplained row loss: dropped={dropped} but manifest explains {explained} (breakdown={breakdown})",
            fix_directive="유실 행 전부를 transform_manifest step의 params.dropped로 설명하세요.",
        ))

    delta = {
        "source_rows": source_rows, "cleaned_rows": cleaned_rows, "dropped": dropped,
        "explained_dropped": explained, "drop_reason_breakdown": breakdown,
    }
    return delta, findings


def _load_records(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """CSV를 헤더+dict 레코드 리스트로 읽는다(체크용 공통 로더)."""
    _enc, header, rows = _read_csv_rows(path)
    records: list[dict[str, str]] = []
    for row in rows:
        rec = {col: (row[i] if i < len(row) else "") for i, col in enumerate(header)}
        records.append(rec)
    return header, records


def _passes_filter(rec: dict[str, str], filt: dict[str, Any] | None) -> bool:
    """단순 equality 필터(모든 키가 문자열 일치해야 통과)."""
    if not filt:
        return True
    return all(str(rec.get(k, "")) == str(v) for k, v in filt.items())


def recompute_claim(source_path: Path, backing_stat: dict[str, Any]) -> float | None:
    """원본 CSV에서 backing_stat을 독립 재산출한다(manifest 재실행 아님).

    지원 metric: count / sum / mean / ratio. 필터는 equality만. 산출 불가면 None.
    """
    metric = str(backing_stat.get("metric", "")).lower()
    col = backing_stat.get("col")
    filt = backing_stat.get("filter")
    _header, records = _load_records(source_path)
    matched = [r for r in records if _passes_filter(r, filt)]

    if metric == "count":
        return float(len(matched))
    if metric == "ratio":
        return float(len(matched)) / len(records) if records else 0.0
    if col is None:
        return None
    nums: list[float] = []
    for r in matched:
        raw = r.get(str(col), "")
        try:
            nums.append(float(raw))
        except (TypeError, ValueError):
            continue
    if metric == "sum":
        return float(sum(nums))
    if metric == "mean":
        return float(sum(nums) / len(nums)) if nums else 0.0
    return None


def check_claims(source_path: Path, derived_claims: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Finding]]:
    """derived_claims를 원본에서 재계산해 tolerance 내 일치 여부를 판정한다."""
    recompute: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for claim in derived_claims:
        stat = claim.get("backing_stat") or {}
        metric = str(stat.get("metric", ""))
        claimed = stat.get("value")
        recomputed = recompute_claim(source_path, stat)
        tol = tolerance_for(metric)
        if recomputed is None or claimed is None:
            match = False
        else:
            match = _values_match(float(claimed), recomputed, tol)
        recompute.append({
            "claim_id": claim.get("id"), "claimed_value": claimed,
            "recomputed_value": recomputed, "tolerance": tol, "match": match,
        })
        if not match:
            findings.append(Finding(
                severity="major", category="claim_mismatch",
                detail=f"claim {claim.get('id')}: claimed {claimed} but recomputed {recomputed} (metric={metric}, tol={tol})",
                fix_directive="원본 데이터에서 재산출한 값과 일치하도록 claim을 정정하세요.",
                claim_id=claim.get("id"),
            ))
    return recompute, findings


def _infer_dtype(values: list[str]) -> str:
    """빈칸 제외 실제 셀들을 보고 int/float/str을 추정한다."""
    seen = [v for v in values if v.strip() != ""]
    if not seen:
        return "empty"
    is_int = True
    is_float = True
    for v in seen:
        try:
            int(v)
        except ValueError:
            is_int = False
        try:
            float(v)
        except ValueError:
            is_float = False
    if is_int:
        return "int"
    if is_float:
        return "float"
    return "str"


def check_schema(
    cleaned_metrics: CSVQualityMetrics, schema_expectations: dict[str, str],
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """기대 dtype vs 실측 추정 dtype 대조(int 기대인데 float도 불일치)."""
    diff: list[dict[str, Any]] = []
    findings: list[Finding] = []
    _header, records = _load_records(Path(cleaned_metrics.path))
    for col, expected in schema_expectations.items():
        if col not in cleaned_metrics.columns:
            diff.append({"col": col, "expected_dtype": expected, "actual_dtype": "missing", "ok": False})
            findings.append(Finding(
                severity="major", category="schema_missing_col",
                detail=f"expected column '{col}' missing from cleaned data",
                fix_directive=f"스키마 기대에 맞춰 '{col}' 열을 산출하거나 기대를 수정하세요.",
            ))
            continue
        actual = _infer_dtype([r.get(col, "") for r in records])
        ok = actual == expected or (expected == "float" and actual == "int") or actual == "empty"
        diff.append({"col": col, "expected_dtype": expected, "actual_dtype": actual, "ok": ok})
        if not ok:
            findings.append(Finding(
                severity="major", category="schema_type_mismatch",
                detail=f"column '{col}': expected {expected} but inferred {actual}",
                fix_directive=f"'{col}' 열 타입을 {expected}로 정제하거나 기대 스키마를 정정하세요.",
            ))
    return diff, findings


def check_sanity(
    cleaned_metrics: CSVQualityMetrics, sanity_rules: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """상식 위반 탐지: 음수 금지 열(non_negative_cols), 유니크 키(unique_cols)."""
    checks: list[dict[str, Any]] = []
    findings: list[Finding] = []
    _header, records = _load_records(Path(cleaned_metrics.path))

    for col in sanity_rules.get("non_negative_cols", []) or []:
        bad = 0
        for r in records:
            raw = r.get(col, "")
            try:
                if float(raw) < 0:
                    bad += 1
            except (TypeError, ValueError):
                continue
        status = "fail" if bad else "pass"
        checks.append({"name": f"non_negative[{col}]", "status": status, "col": col,
                       "metric_expected": 0, "metric_actual": bad, "detail": f"{bad} negative values"})
        if bad:
            findings.append(Finding(
                severity="major", category="negative_value",
                detail=f"column '{col}' has {bad} negative value(s)",
                fix_directive=f"'{col}'의 음수 값을 조사·정정하세요(데이터 오류 가능).",
            ))

    for col in sanity_rules.get("unique_cols", []) or []:
        seen: set[str] = set()
        dups = 0
        for r in records:
            v = r.get(col, "")
            if v in seen:
                dups += 1
            else:
                seen.add(v)
        status = "fail" if dups else "pass"
        checks.append({"name": f"unique[{col}]", "status": status, "col": col,
                       "metric_expected": 0, "metric_actual": dups, "detail": f"{dups} duplicate keys"})
        if dups:
            findings.append(Finding(
                severity="major", category="duplicate_key",
                detail=f"unique column '{col}' has {dups} duplicate key(s)",
                fix_directive=f"'{col}'의 중복 키를 dedup하거나 유니크 가정을 수정하세요.",
            ))
    return checks, findings


def run_data_quality(stage_out: dict[str, Any], run_dir: Path, *, verifier_agent: str) -> "Verdict":
    """c 스테이지 data_quality 검증(코드 실측만, 모델 0회).

    stage_out에서 cleaned_files/manifest/claims/schema를 읽어 4대 체크를 돌리고, 코드가
    findings를 집계해 status를 재계산한다. 파일을 못 읽으면 blocked, 위반 있으면
    needs_changes, 전부 통과면 pass. verdict raw를 c_data_quality.json으로 남긴다.
    verifier_agent는 계약상 받되 여기선 'code' 고정(모델 미호출) — provenance에만 기록.
    """
    from autoagent.artifacts import write_json
    from autoagent.data.csv_validator import _sha256_of_file, validate_csv
    from autoagent.research.types import Verdict

    all_findings: list[Finding] = []
    checks: list[dict[str, Any]] = []
    recompute_all: list[dict[str, Any]] = []
    schema_diff_all: list[dict[str, Any]] = []
    row_delta: dict[str, Any] = {}
    provenance: dict[str, Any] = {"files_read": [], "verifier_agent": verifier_agent}
    has_error = False

    manifest = stage_out.get("transform_manifest") or {"steps": []}
    schema_expectations = stage_out.get("schema_expectations") or {}
    derived_claims = stage_out.get("derived_claims") or []

    for entry in stage_out.get("cleaned_files") or []:
        cleaned_path = Path(entry.get("path", ""))
        source_path = Path(entry.get("source_dump_path", ""))
        try:
            source_metrics = validate_csv(source_path)
            cleaned_metrics = validate_csv(cleaned_path)
            provenance["files_read"].append(str(cleaned_path))
            provenance["files_read"].append(str(source_path))
            provenance.setdefault("hashes", {})[str(source_path)] = _sha256_of_file(source_path)
            provenance["hashes"][str(cleaned_path)] = _sha256_of_file(cleaned_path)
        except (ValueError, FileNotFoundError, OSError) as exc:
            has_error = True
            checks.append({"name": "file_read", "status": "error", "file": str(cleaned_path),
                           "detail": f"{type(exc).__name__}: {exc}"})
            all_findings.append(Finding(
                severity="critical", category="file_read_error",
                detail=f"cannot read {cleaned_path} / {source_path}: {exc}",
                fix_directive="입력 CSV 경로·인코딩을 확인하세요(조용한 skip 금지).",
            ))
            continue

        delta, rc_findings = check_row_conservation(source_metrics, cleaned_metrics, manifest)
        row_delta = delta
        all_findings.extend(rc_findings)
        checks.append({"name": "row_conservation", "status": "pass" if not rc_findings else "fail",
                       "file": str(cleaned_path), "detail": str(delta)})

        diff, sc_findings = check_schema(cleaned_metrics, schema_expectations)
        schema_diff_all.extend(diff)
        all_findings.extend(sc_findings)
        checks.append({"name": "schema", "status": "pass" if not sc_findings else "fail", "file": str(cleaned_path)})

        sanity_rules = stage_out.get("sanity_rules") or {}
        if sanity_rules:
            sanity_checks, sn_findings = check_sanity(cleaned_metrics, sanity_rules)
            checks.extend(sanity_checks)
            all_findings.extend(sn_findings)
        else:
            checks.append({"name": "sanity", "status": "skipped", "detail": "no sanity_rules"})

        rc_list, cl_findings = check_claims(source_path, derived_claims)
        recompute_all.extend(rc_list)
        all_findings.extend(cl_findings)
        checks.append({"name": "claim_recompute", "status": "pass" if not cl_findings else "fail",
                       "file": str(source_path)})

    checks_ok = all(c["status"] in {"pass", "skipped"} for c in checks)
    recompute_ok = all(r["match"] for r in recompute_all)
    schema_ok = all(d["ok"] for d in schema_diff_all)
    overall_ok = checks_ok and recompute_ok and schema_ok and not has_error

    if has_error:
        status: str = "blocked"
    elif overall_ok:
        status = "pass"
    else:
        status = "needs_changes"

    raw = {
        "schema_version": CHECK_SET_VERSION, "adapter": "data_quality", "stage_id": "c",
        "overall_ok": overall_ok, "checks": checks, "recompute": recompute_all,
        "row_delta": row_delta, "schema_diff": schema_diff_all, "provenance": provenance,
    }
    write_json(run_dir / "c_data_quality.json", raw)
    return Verdict(status=status, adapter="data_quality", stage_id="c", findings=all_findings, raw=raw)

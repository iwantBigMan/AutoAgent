"""research/coverage.py 커버리지 매트릭스+배너 렌더 테스트(§2.3, §8 F1)."""
from __future__ import annotations

from autoagent.research.coverage import (
    coverage_summary, render_coverage_matrix_html, render_warning_banner_html,
)

STAGES = ["a", "b", "c", "d", "derive"]


def test_summary_all_resolved_is_complete() -> None:
    s = coverage_summary({x: "resolved" for x in STAGES}, STAGES)
    assert s["total"] == 5 and s["resolved"] == 5 and s["pct_resolved"] == 100.0
    assert s["complete"] is True and s["unverified"] == 0 and s["blocked"] == 0 and s["missing"] == 0


def test_summary_counts_unverified_blocked_missing() -> None:
    ss = {"a": "resolved", "b": "exhausted_unverified", "c": "blocked"}
    s = coverage_summary(ss, STAGES)
    assert s["resolved"] == 1 and s["unverified"] == 1 and s["blocked"] == 1 and s["missing"] == 2
    assert s["pct_resolved"] == 20.0 and s["complete"] is False


def test_matrix_marks_exhausted_as_unverified_badge() -> None:
    html = render_coverage_matrix_html({"a": "resolved", "b": "exhausted_unverified"}, ["a", "b"])
    assert "UNVERIFIED" in html
    assert "<table" in html and "</table>" in html
    assert "PASSED" in html


def test_matrix_uses_stage_labels_when_given() -> None:
    html = render_coverage_matrix_html({"a": "resolved"}, ["a"], stage_labels={"a": "회사 리서치"})
    assert "회사 리서치" in html


def test_banner_empty_when_complete() -> None:
    s = coverage_summary({x: "resolved" for x in STAGES}, STAGES)
    assert render_warning_banner_html(s) == ""


def test_banner_present_and_lists_gaps_when_incomplete() -> None:
    s = coverage_summary({"a": "resolved", "b": "exhausted_unverified", "c": "blocked"}, STAGES)
    banner = render_warning_banner_html(s)
    assert banner != ""
    assert "20.0%" in banner or "20%" in banner
    assert "UNVERIFIED" in banner or "b" in banner
    assert "blocked" in banner or "c" in banner


def test_matrix_escapes_html_in_labels() -> None:
    html = render_coverage_matrix_html({"a": "resolved"}, ["a"], stage_labels={"a": "<script>x</script>"})
    assert "<script>" not in html and "&lt;script&gt;" in html

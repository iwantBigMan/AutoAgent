"""커버리지 매트릭스 + 경고 배너 HTML 렌더(스펙 §2.3, §8 F1).

최종 리포트 상단에 스테이지별 검증 상태 표를 강제로 넣고, 전부 resolved가 아니면
100% 미만 경고 배너를 붙인다. exhausted_unverified 스테이지는 UNVERIFIED 배지로
격리 표기한다(§8 F1: 미검증을 신뢰 결과와 섞지 않는다). pandoc·외부 자원 없이
인라인 CSS만 쓴다(deliver-local-html 준수, standalone HTML).
"""
from __future__ import annotations

from html import escape

# 런타임 status → (표시 라벨, 전경색, 배경색). exhausted는 UNVERIFIED로 격리.
_STATUS_BADGE = {
    "resolved": ("PASSED", "#1a7f37", "#dafbe1"),
    "exhausted_unverified": ("UNVERIFIED", "#9a6700", "#fff8c5"),
    "blocked": ("BLOCKED", "#cf222e", "#ffebe9"),
    "missing": ("SKIPPED", "#57606a", "#eaeef2"),
}


def coverage_summary(stage_status: dict[str, str], stages: list[str]) -> dict:
    """스테이지 상태를 집계한다. stages에 있으나 status 없으면 missing으로 센다."""
    resolved = unverified = blocked = missing = 0
    for stage in stages:
        status = stage_status.get(stage)
        if status == "resolved":
            resolved += 1
        elif status == "exhausted_unverified":
            unverified += 1
        elif status == "blocked":
            blocked += 1
        else:
            missing += 1
    total = len(stages)
    pct = round(resolved / total * 100, 1) if total else 0.0
    return {
        "total": total, "resolved": resolved, "unverified": unverified, "blocked": blocked,
        "missing": missing, "pct_resolved": pct, "complete": resolved == total and total > 0,
    }


def _badge_html(status: str) -> str:
    label, fg, bg = _STATUS_BADGE.get(status, _STATUS_BADGE["missing"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        f'font-size:12px;font-weight:600;color:{fg};background:{bg};">{label}</span>'
    )


def render_coverage_matrix_html(
    stage_status: dict[str, str], stages: list[str], *, stage_labels: dict[str, str] | None = None,
) -> str:
    """스테이지별 검증 상태 표를 HTML로 렌더한다(리포트 상단 강제용).

    각 행: 스테이지 라벨 + 상태 배지. exhausted_unverified는 UNVERIFIED 배지로 격리.
    라벨은 escape로 주입을 막는다.
    """
    stage_labels = stage_labels or {}
    rows = []
    for stage in stages:
        status = stage_status.get(stage) or "missing"
        label = escape(stage_labels.get(stage, stage))
        rows.append(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #d0d7de;">{label}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #d0d7de;">{_badge_html(status)}</td></tr>'
        )
    return (
        '<table style="border-collapse:collapse;width:100%;max-width:640px;margin:0 0 16px;'
        'font-family:system-ui,-apple-system,sans-serif;">'
        '<thead><tr>'
        '<th style="text-align:left;padding:6px 12px;border-bottom:2px solid #24292f;">스테이지</th>'
        '<th style="text-align:left;padding:6px 12px;border-bottom:2px solid #24292f;">검증 상태</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_warning_banner_html(summary: dict) -> str:
    """커버리지 100% 미만이면 경고 배너 HTML, 완전하면 빈 문자열."""
    if summary.get("complete"):
        return ""
    parts = []
    if summary["unverified"]:
        parts.append(f'{summary["unverified"]}개 스테이지 UNVERIFIED')
    if summary["blocked"]:
        parts.append(f'{summary["blocked"]}개 스테이지 blocked')
    if summary["missing"]:
        parts.append(f'{summary["missing"]}개 스테이지 미착수(SKIPPED)')
    detail = ", ".join(parts) if parts else "일부 스테이지가 검증을 통과하지 못했습니다"
    return (
        '<div style="border:2px solid #cf222e;background:#ffebe9;border-radius:8px;'
        'padding:12px 16px;margin:0 0 16px;font-family:system-ui,-apple-system,sans-serif;">'
        f'<strong style="color:#cf222e;">⚠ 검증 커버리지 {summary["pct_resolved"]}% '
        "(100% 미만) — 이 리포트는 완전 검증본이 아닙니다.</strong>"
        f'<div style="margin-top:6px;color:#57606a;font-size:14px;">{escape(detail)}. '
        "UNVERIFIED/blocked 스테이지의 주장은 도출(derive)·신뢰도 계산에서 제외되었습니다.</div>"
        "</div>"
    )

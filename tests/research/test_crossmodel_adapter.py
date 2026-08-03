"""crossmodel verdict 파싱·재계산 테스트(결정론 코드 핵심).

핵심 불변식: 검증기가 'pass'라 적어도 major/critical finding이 있으면 코드가
needs_changes로 강등한다. axes_missing 비어있음 + critical/major 0건 + blocked 아님 → pass.
마커/JSON 없음 → blocked(판정 불가).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autoagent.research.adapters import parse_crossmodel_verdict, verify


def _verdict_text(status: str, findings_json: str, axes_missing: str = "[]") -> str:
    return (
        f"CROSSMODEL_VERDICT: {status}\n"
        "```json\n{\n"
        '  "schema_version": 1, "adapter": "crossmodel", "stage_id": "a",\n'
        f'  "verdict": "{status}",\n'
        f'  "findings": {findings_json},\n'
        f'  "coverage": {{"axes_checked": ["support"], "axes_missing": {axes_missing}}},\n'
        '  "unchallenged_but_weak": [], "reviewer_model": "codex", "tokens_seen": 10\n'
        "}\n```\n"
    )


def test_clean_pass() -> None:
    v = parse_crossmodel_verdict(_verdict_text("pass", "[]"), "a")
    assert v.status == "pass"
    assert v.adapter == "crossmodel" and v.stage_id == "a"
    assert v.findings == []


def test_major_finding_downgrades_declared_pass() -> None:
    findings = '[{"claim_id": "a1", "severity": "major", "category": "overreach", "rebuttal": "r", "fix_directive": "f"}]'
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a")
    assert v.status == "needs_changes"
    assert v.findings[0].severity == "major"
    assert v.findings[0].fix_directive == "f"


def test_minor_only_stays_pass_when_axes_complete() -> None:
    # evidence_pointer가 있는 minor finding: §4.1② tokens_seen 교차검사에 안 걸리고 pass 유지.
    findings = ('[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", '
                '"rebuttal": "r", "fix_directive": "f", "evidence_pointer": "s1"}]')
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a")
    assert v.status == "pass"


def test_tokens_seen_without_evidence_pointer_downgrades() -> None:
    # §4.1② anti-gaming: tokens_seen>0(번들 봤음)인데 어느 finding도 소스를 안 가리키면 강등.
    findings = '[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", "rebuttal": "r", "fix_directive": "f"}]'
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a")
    assert v.status == "needs_changes"


def test_below_min_findings_quota_downgrades_when_config_given() -> None:
    # §4.1② 쿼터: config crossmodel_min_findings=3, findings 1개 < 3, unchallenged_but_weak 비었으면 강등.
    from types import SimpleNamespace
    findings = ('[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", '
                '"rebuttal": "r", "fix_directive": "f", "evidence_pointer": "s1"}]')
    cfg = SimpleNamespace(crossmodel_min_findings=3)
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a", config=cfg)
    assert v.status == "needs_changes"


def test_axes_missing_forces_needs_changes() -> None:
    v = parse_crossmodel_verdict(_verdict_text("pass", "[]", axes_missing='["omission"]'), "a")
    assert v.status == "needs_changes"


def test_declared_blocked_stays_blocked() -> None:
    v = parse_crossmodel_verdict(_verdict_text("blocked", "[]"), "a")
    assert v.status == "blocked"


def test_missing_marker_is_blocked() -> None:
    v = parse_crossmodel_verdict("검증기가 마커 없이 자유서술만 했다.", "a")
    assert v.status == "blocked"
    assert v.adapter == "crossmodel"


def test_unparseable_json_is_blocked() -> None:
    v = parse_crossmodel_verdict("CROSSMODEL_VERDICT: pass\n(no json block here)", "a")
    assert v.status == "blocked"


def test_verify_dispatch_crossmodel(tmp_path: Path) -> None:
    stage_out = {"verifier_raw_text": _verdict_text("pass", "[]")}
    v = verify("crossmodel", stage_out, tmp_path, verifier_agent="codex", config=None)
    assert v.status == "pass"
    assert (tmp_path / "verdict_crossmodel_a.json").exists()


def test_verify_unknown_adapter_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        verify("weird", {"verifier_raw_text": ""}, tmp_path, verifier_agent="claude", config=None)


def test_stray_braces_in_prose_around_fence_still_parses() -> None:
    """마커+fenced JSON은 정상인데 fence 밖 산문에 stray { }가 섞인 경우.

    extract_json_block의 브레이스매칭 폴백(전체 텍스트 첫 { ~ 마지막 })으로 떨어지면
    잘못된 span이 되어 JSON 파싱이 실패하고 blocked로 조용히 삼켜진다(버그). 마커
    앵커드 fence 추출이면 fence 밖 산문은 무시되어 정상 pass가 나와야 한다.
    """
    findings = '[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", ' \
        '"rebuttal": "r", "fix_directive": "f", "evidence_pointer": "s1"}]'
    text = (
        "검증기 서론입니다. 참고로 dict 예시 `{foo}` 같은 형태를 곧 보게 됩니다.\n"
        + _verdict_text("pass", findings)
        + "\n결론: 위 JSON을 참고하세요. 추가로 `{bar}` 같은 표기도 등장할 수 있습니다."
    )
    v = parse_crossmodel_verdict(text, "a")
    assert v.status == "pass"
    assert v.findings[0].claim_id == "a1"

"""research.types 공유 dataclass 스모크 테스트.

계약(고정 시그니처)이 필드·기본값·타입 그대로 존재하는지만 확인한다.
로직이 없는 순수 데이터 타입이므로 구조 검증에 한정한다.
"""
from __future__ import annotations

from dataclasses import fields

from autoagent.research.types import Finding, StageResult, Verdict


def test_finding_fields_and_default() -> None:
    f = Finding(severity="major", category="overreach", detail="추론이 사실을 넘음", fix_directive="근거 추가")
    assert f.claim_id is None
    assert f.severity == "major"
    names = [x.name for x in fields(Finding)]
    assert names == ["severity", "category", "detail", "fix_directive", "claim_id"]


def test_verdict_holds_findings_and_raw() -> None:
    f = Finding(severity="critical", category="unsupported", detail="d", fix_directive="fx")
    v = Verdict(status="needs_changes", adapter="crossmodel", stage_id="a", findings=[f], raw={"k": 1})
    assert v.findings[0] is f
    assert v.raw == {"k": 1}
    names = [x.name for x in fields(Verdict)]
    assert names == ["status", "adapter", "stage_id", "findings", "raw"]


def test_stage_result_fields() -> None:
    v = Verdict(status="pass", adapter="crossmodel", stage_id="a", findings=[], raw={})
    r = StageResult(stage_id="a", status="resolved", output_path="a/out.md", verdict=v, inner_rounds=2)
    assert r.verdict is v
    names = [x.name for x in fields(StageResult)]
    assert names == ["stage_id", "status", "output_path", "verdict", "inner_rounds"]

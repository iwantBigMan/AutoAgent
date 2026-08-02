"""검증 어댑터 디스패치 + crossmodel 어댑터.

verify(adapter, ...)가 어댑터별 검증기로 라우팅한다. crossmodel/data_quality/
source_grounding 세 어댑터 모두 배선 완료(각각 아래 verify() 분기 참고). crossmodel은
이 모듈에 직접 구현하고, data_quality/source_grounding은 각자의 모듈로 위임한다.
crossmodel은 검증기 원문에서
마커+fenced JSON을 파싱하고, **코드가 findings를 집계해 status를 재계산**한다
(검증기가 pass라 적어도 major/critical이 있으면 needs_changes로 강등 — 자기모순 방지).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autoagent.artifacts import extract_json_block, write_json
from autoagent.research.types import Finding, Verdict


CROSSMODEL_MARKER = "CROSSMODEL_VERDICT:"

# 마커 다음에 오는 fenced ```json ... ``` 블록만 잡는다(마커 이전 산문은 대상 밖).
_MARKER_FENCE_RE = re.compile(
    re.escape(CROSSMODEL_MARKER) + r".*?```(?:json)?\s*(\{.*?\})\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)


def _extract_crossmodel_json(raw_text: str) -> dict[str, Any]:
    """crossmodel verdict 원문에서 JSON을 마커 앵커드로 추출한다.

    fence 밖 산문(예: "dict 예시 `{foo}` 참고")에 있는 stray brace가 공유
    extract_json_block의 브레이스매칭 폴백(전체 텍스트 첫 { ~ 마지막 })을 오염시켜
    잘못된 span/무효 JSON으로 떨어지는 문제를 막는다. 마커 뒤 fenced 블록을 직접
    정규식으로 찾아 그 안(fence 내부)만 파싱 대상으로 삼는다. fenced 블록이 없을
    때만 마커 이후 텍스트로 한정한 채 기존 extract_json_block 폴백을 쓴다(공유
    유틸 자체는 수정하지 않는다 — TASK_GRAPH_JSON 호출부 영향 방지).
    """
    fence_match = _MARKER_FENCE_RE.search(raw_text)
    if fence_match:
        return json.loads(fence_match.group(1))

    marker_idx = raw_text.find(CROSSMODEL_MARKER)
    tail = raw_text[marker_idx:] if marker_idx != -1 else raw_text
    return extract_json_block(tail)


def parse_crossmodel_verdict(raw_text: str, stage_id: str, *, config: Any = None) -> Verdict:
    """검증기 원문 → Verdict. status는 코드가 재계산한다(모델 자유선언 불신).

    판정 규칙:
    - 마커(CROSSMODEL_VERDICT:)가 없거나 JSON 파싱 실패 → blocked(판정 불가).
    - 검증기가 blocked라 선언 → blocked 유지.
    - findings에 severity∈{critical,major}가 하나라도 있으면 → needs_changes(강등).
    - coverage.axes_missing가 비어있지 않으면 → needs_changes(누락 축).
    - (§4.1② anti-gaming) tokens_seen>0인데 어느 finding도 evidence_pointer가 없으면 → needs_changes.
    - (§4.1② quota) config 있고 len(findings)<crossmodel_min_findings이며 unchallenged_but_weak가
      비었으면 → needs_changes(무결 증명 미흡 강등). config=None이면 이 쿼터는 건너뛴다.
    - 위에 걸리지 않고 검증기 verdict가 pass면 → pass.
    """
    # 1) 마커 부재는 판정 불가(blocked). 자유서술만 온 경우를 명확히 격리한다.
    if CROSSMODEL_MARKER not in raw_text:
        return Verdict(status="blocked", adapter="crossmodel", stage_id=stage_id, findings=[], raw={})
    # 2) 마커 앵커드 fenced JSON 파싱. 실패 시 blocked.
    try:
        data = _extract_crossmodel_json(raw_text)
    except Exception:  # noqa: BLE001 - JSON 없음/깨짐 전부 판정 불가로 격리
        return Verdict(status="blocked", adapter="crossmodel", stage_id=stage_id, findings=[], raw={})

    raw_findings = data.get("findings") or []
    findings = _findings_from(raw_findings)
    declared = str(data.get("verdict") or "").strip().lower()
    coverage = data.get("coverage") or {}
    axes_missing = coverage.get("axes_missing") or []
    # §4.1②: tokens_seen 교차검사 + 최소 findings 쿼터에 필요한 원본 신호를 뽑는다.
    tokens_seen = int(data.get("tokens_seen") or 0)
    has_evidence_pointer = any((f.get("evidence_pointer") or "") for f in raw_findings)
    unchallenged_weak = data.get("unchallenged_but_weak") or []
    min_findings = int(getattr(config, "crossmodel_min_findings", 3)) if config is not None else None
    status = _recompute_status(
        declared=declared, findings=findings, axes_missing=axes_missing,
        tokens_seen=tokens_seen, has_evidence_pointer=has_evidence_pointer,
        unchallenged_weak_empty=not unchallenged_weak, min_findings=min_findings,
    )
    return Verdict(status=status, adapter="crossmodel", stage_id=stage_id, findings=findings, raw=data)


def _findings_from(items: list[dict[str, Any]]) -> list[Finding]:
    """검증기 JSON의 findings 배열을 공유 Finding 타입으로 정규화한다.

    crossmodel 스키마의 rebuttal을 detail로, fix_directive를 그대로 매핑한다.
    severity가 알 수 없는 값이면 안전 방향으로 major 취급(강등 유발).
    """
    out: list[Finding] = []
    for it in items:
        sev = str(it.get("severity") or "").strip().lower()
        if sev not in {"critical", "major", "minor"}:
            sev = "major"  # 미상 severity는 보수적으로 강등쪽
        out.append(
            Finding(
                severity=sev,  # type: ignore[arg-type]
                category=str(it.get("category") or "unspecified"),
                detail=str(it.get("rebuttal") or it.get("detail") or ""),
                fix_directive=str(it.get("fix_directive") or ""),
                claim_id=(it.get("claim_id") if it.get("claim_id") not in ("", None) else None),
            )
        )
    return out


def _recompute_status(
    *,
    declared: str,
    findings: list[Finding],
    axes_missing: list[Any],
    tokens_seen: int = 0,
    has_evidence_pointer: bool = False,
    unchallenged_weak_empty: bool = True,
    min_findings: int | None = None,
) -> str:
    """findings/axes로 최종 status를 코드가 재계산한다(스펙 §4.1 pass 기준 + anti-gaming §4.1②)."""
    if declared == "blocked":
        return "blocked"
    blocking = any(f.severity in {"critical", "major"} for f in findings)
    if blocking or axes_missing:
        return "needs_changes"
    # §4.1② tokens_seen 교차검사: 번들을 봤다(tokens_seen>0)면서 어느 finding도 소스를
    # 가리키지(evidence_pointer) 않으면, 근거 없는 무결 선언으로 보고 자동 강등한다.
    if tokens_seen > 0 and findings and not has_evidence_pointer:
        return "needs_changes"
    # §4.1② 최소 findings 쿼터: config가 주어졌을 때만 강제. 약점이 쿼터 미만인데
    # unchallenged_but_weak(약하지만 통과)도 비었으면 무결 증명이 부실하다고 보고 강등.
    if min_findings is not None and len(findings) < min_findings and unchallenged_weak_empty:
        return "needs_changes"
    if declared == "pass":
        return "pass"
    return "needs_changes" if declared == "needs_changes" else "pass"


def verify(
    adapter: str,
    stage_out: dict[str, Any],
    run_dir: Path,
    *,
    verifier_agent: str,
    config: Any,
) -> Verdict:
    """어댑터별 검증 디스패치. crossmodel만 이 슬라이스에서 구현한다.

    crossmodel: stage_out["verifier_raw_text"](검증기 stdout)를 파싱·재계산하고
    verdict를 run_dir/verdict_crossmodel_<stage>.json으로 남긴다(감사추적). 모델 호출
    자체는 오케스트레이터(research.py)가 수행해 결과 텍스트를 여기로 넘긴다.
    """
    if adapter == "crossmodel":
        stage_id = str(stage_out.get("stage_id") or "a")
        raw_text = str(stage_out.get("verifier_raw_text") or "")
        verdict = parse_crossmodel_verdict(raw_text, stage_id, config=config)
        write_json(
            run_dir / f"verdict_crossmodel_{stage_id}.json",
            {
                "status": verdict.status, "adapter": verdict.adapter, "stage_id": verdict.stage_id,
                "verifier_agent": verifier_agent,
                "findings": [f.__dict__ for f in verdict.findings], "raw": verdict.raw,
            },
        )
        return verdict
    if adapter == "data_quality":
        # data_quality: 코드 실측 전용(모델 0회). config·verifier_agent 모델 미사용.
        from autoagent.research.data_quality import run_data_quality
        return run_data_quality(stage_out, run_dir, verifier_agent=verifier_agent)
    if adapter == "source_grounding":
        # d 스테이지 하이브리드: 스냅샷 결정론 + Codex 의미대조. 모델 stdout은 오케스트레이터가
        # stage_out["model_raw_text"]에 실어 전달한다(verify 계약 시그니처 불변 유지).
        from autoagent.research.source_grounding import verify_source_grounding
        return verify_source_grounding(
            stage_out, run_dir, verifier_agent=verifier_agent, config=config,
            model_raw_text=stage_out.get("model_raw_text", ""),
        )
    raise SystemExit(f"Unknown verify adapter: {adapter!r}")

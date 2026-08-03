"""source_grounding 어댑터(§4.3 하이브리드).

Codex 검증기 stdout의 GROUNDING_VERDICT 마커+fenced JSON을 파싱하고(free-text 무시),
Task 16의 결정론 검사와 병합해 코드가 status를 재계산한다. 결정적 위반(fabricated/dead=
blocked, orphan/quote 미검증=needs_changes)은 모델이 pass라 적어도 강등한다(§4.3 F4).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autoagent.artifacts import extract_json_block, write_json
from autoagent.research.grounding import DeterministicGrounding, run_deterministic_checks
from autoagent.research.snapshots import load_snapshot_text
from autoagent.research.types import Finding, Verdict


_MARKER_RE = re.compile(r"GROUNDING_VERDICT:\s*(pass|needs_changes|blocked)", re.IGNORECASE)
_MARKER_TEXT = "GROUNDING_VERDICT:"

# 마커 다음에 오는 fenced ```json ... ``` 블록만 잡는다(마커 이전 산문은 대상 밖).
# crossmodel._extract_crossmodel_json과 동일 패턴(Task 4에서 배운 stray-brace 방지)을
# 국소 구현한다 — 공유 extract_json_block 자체는 건드리지 않는다.
_MARKER_FENCE_RE = re.compile(
    re.escape(_MARKER_TEXT) + r".*?```(?:json)?\s*(\{.*?\})\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)


def _extract_grounding_json(raw_text: str) -> dict[str, Any]:
    """grounding verdict 원문에서 JSON을 마커 앵커드로 추출한다.

    fence 밖 산문(예: "dict 예시 `{foo}` 참고")에 있는 stray brace가 공유
    extract_json_block의 브레이스매칭 폴백(전체 텍스트 첫 { ~ 마지막 })을 오염시켜
    잘못된 span/무효 JSON으로 떨어지는 문제를 막는다. 마커 뒤 fenced 블록을 직접
    정규식으로 찾아 그 안(fence 내부)만 파싱 대상으로 삼는다. fenced 블록이 없을
    때만 마커 이후 텍스트로 한정한 채 기존 extract_json_block 폴백을 쓴다(공유
    유틸 자체는 수정하지 않는다).
    """
    fence_match = _MARKER_FENCE_RE.search(raw_text)
    if fence_match:
        return json.loads(fence_match.group(1))

    marker_idx = raw_text.find(_MARKER_TEXT)
    tail = raw_text[marker_idx:] if marker_idx != -1 else raw_text
    return extract_json_block(tail)


def parse_grounding_verdict(raw_text: str) -> dict[str, Any]:
    """Codex stdout에서 GROUNDING_VERDICT 마커 + fenced JSON을 파싱한다.

    마커를 최우선으로 verdict를 읽되, 실제 구조는 마커 앵커드 fenced JSON에서 취한다
    (fence 밖 산문의 stray brace에 견고 — Task 4와 동일 함정을 국소 재구현으로 방지).
    마커/JSON이 없으면 예외 대신 방어 기본값을 돌려줘 런을 죽이지 않는다(코드가 결정론
    findings만으로 needs_changes를 만들 수 있게).
    """
    marker = _MARKER_RE.search(raw_text)
    verdict = marker.group(1).lower() if marker else None
    try:
        data = _extract_grounding_json(raw_text)
    except Exception:  # noqa: BLE001 — JSON 부재/파싱실패 모두 방어값으로
        data = {}
    return {
        "verdict": data.get("verdict", verdict),
        "claim_checks": data.get("claim_checks", []),
        "orphan_claims": data.get("orphan_claims", []),
        "dead_sources": data.get("dead_sources", []),
        "fabricated_sources": data.get("fabricated_sources", []),
        "schema_version": data.get("schema_version"),
    }


def _model_findings(model_json: dict[str, Any]) -> list[Finding]:
    """모델 claim_checks의 contradicted/unsupported를 Finding으로 승격한다.

    grounding∈{contradicted}=critical, {unsupported}=major. supported/partially_supported/
    no_source는 여기서 finding으로 만들지 않는다(no_source는 결정론 orphan 검사가 잡음).
    """
    findings: list[Finding] = []
    for chk in model_json.get("claim_checks", []):
        grounding = chk.get("grounding")
        cid = chk.get("claim_id")
        if grounding == "contradicted":
            findings.append(Finding(
                severity="critical", category="contradicted",
                detail=f"주장 {cid}가 인용 소스와 모순됩니다: {chk.get('notes', '')}",
                fix_directive=f"주장 {cid}를 소스가 실제로 지지하는 내용으로 수정하거나 제거하세요.",
                claim_id=cid,
            ))
        elif grounding == "unsupported":
            findings.append(Finding(
                severity="major", category="unsupported",
                detail=f"주장 {cid}가 인용 소스로 지지되지 않습니다: {chk.get('notes', '')}",
                fix_directive=f"주장 {cid}에 지지 근거를 스냅샷에서 인용하거나 강등하세요.",
                claim_id=cid,
            ))
    return findings


def _recompute_status(findings: list[Finding], det: DeterministicGrounding) -> str:
    """코드가 status를 재계산한다(모델 자유서술 무시).

    - 결정적 dead/fabricated가 있으면 blocked(판정 불가 → 게이트, §4.3).
    - critical/major finding이 하나라도 있으면 needs_changes(모델 pass여도 강등).
    - 그 외 pass.
    """
    if det.dead_sources or det.fabricated_sources:
        return "blocked"
    if any(f.severity in {"critical", "major"} for f in findings):
        return "needs_changes"
    return "pass"


def merge_and_recompute(
    stage_out: dict[str, Any], model_json: dict[str, Any], det: DeterministicGrounding, *,
    verifier_agent: str, stage_id: str, raw_text: str,
) -> Verdict:
    """모델 verdict + 결정론 findings를 병합하고 코드가 status를 재계산한 Verdict를 만든다.

    findings = 결정론(det.findings) + 모델(contradicted/unsupported). status는 결정적
    위반 우선으로 코드가 재계산해 모델 pass를 무시할 수 있다(강등). raw에 원문/모델 JSON/
    결정론 요약을 담아 감사추적을 남긴다.
    """
    findings = list(det.findings) + _model_findings(model_json)
    status = _recompute_status(findings, det)
    raw = {
        "adapter": "source_grounding", "stage_id": stage_id,
        "model_verdict": model_json.get("verdict"), "recomputed_status": status,
        "deterministic": {
            "fabricated_sources": det.fabricated_sources, "dead_sources": det.dead_sources,
            "orphan_claims": det.orphan_claims, "unverified_quotes": det.unverified_quotes,
        },
        "model_claim_checks": model_json.get("claim_checks", []), "raw_text": raw_text,
    }
    return Verdict(status=status, adapter="source_grounding", stage_id=stage_id, findings=findings, raw=raw)


def verify_source_grounding(
    stage_out: dict[str, Any], run_dir: Path, *, verifier_agent: str, config: Any, model_raw_text: str,
) -> Verdict:
    """d 스테이지 하이브리드 검증: 스냅샷 로드 → 결정론 검사 → 모델 병합 → 영속.

    model_raw_text는 Codex 검증기 stdout(오케스트레이터가 주입). 스냅샷은 run_dir/sources/
    에서 ref_id별로 되읽어 stage_out.sources[].fetched_text보다 우선한다(단일 소스 오브
    트루스). verdict JSON을 run_dir/d_grounding_verdict.json에 남긴다.
    """
    sources_dir = run_dir / "sources"
    snapshot_texts: dict[str, str] = {}
    for s in stage_out.get("sources", []):
        ref = s.get("ref_id")
        try:
            snapshot_texts[ref] = load_snapshot_text(sources_dir, ref)
        except (FileNotFoundError, ValueError):
            pass  # 스냅샷 파일 부재 시 stage_out.fetched_text로 폴백(run_deterministic_checks가 처리)

    det = run_deterministic_checks(stage_out, snapshot_texts)
    model_json = parse_grounding_verdict(model_raw_text)
    verdict = merge_and_recompute(
        stage_out, model_json, det, verifier_agent=verifier_agent, stage_id="d", raw_text=model_raw_text,
    )
    write_json(run_dir / "d_grounding_verdict.json", {
        "status": verdict.status, "adapter": verdict.adapter, "stage_id": verdict.stage_id,
        "findings": [
            {"severity": f.severity, "category": f.category, "detail": f.detail,
             "fix_directive": f.fix_directive, "claim_id": f.claim_id}
            for f in verdict.findings
        ],
        "raw": verdict.raw,
    })
    return verdict

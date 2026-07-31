"""결정론 source-grounding 검사(§4.3-①).

모델 없이 코드로 fabricated/dead/orphan/부분문자열을 실측한다. matched_quote가
스냅샷 원문(fetched_text)의 부분문자열이 아니면 근거 날조로 보고 unsupported를 강제한다.
정규화는 공백·대소문자 차이만 흡수하고(스냅샷 줄바꿈 차이) 내용은 보존한다(느슨화 금지).
결과는 Slice 1의 Finding으로 집계해 어댑터 병합(Task 17)이 소비한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from autoagent.research.types import Finding


_WS = re.compile(r"\s+")

# Finding 1: 정규화된 인용이 최소 이 개수 이상의 whitespace 토큰(단어)을 가져야 grounded
# 후보가 된다. 단어 1~2개짜리 조각은 소스에 우연히 겹치는 리터럴 부분문자열이어도
# "실제로 소스를 읽고 인용했음"의 증거가 되지 못하므로(인용 날조 차단이 이 태스크의
# 목적) 미달 시 부분문자열 여부와 무관하게 근거 없음(False)으로 판정한다.
_MIN_QUOTE_TOKENS = 3


def normalize_for_match(text: str) -> str:
    """부분문자열 대조용 정규화: 소문자화 + 연속 공백류를 단일 스페이스로.

    스냅샷 저장 시 개행/들여쓰기 차이만 흡수한다. 구두점·숫자·단어는 건드리지 않아
    의미 왜곡(paraphrase)은 그대로 불일치로 남긴다(느슨화로 날조를 통과시키지 않음).
    """
    return _WS.sub(" ", text.strip().lower())


def quote_is_grounded(matched_quote: str, fetched_text: str) -> bool:
    """정규화 후 matched_quote가 fetched_text의 부분문자열인지 판정한다.

    빈 quote(또는 공백뿐)는 근거 없음(False) — '인용 없이 supported' 날조를 차단한다.
    최소 길이 가드(Finding 1): 정규화된 인용의 whitespace 토큰 수가 _MIN_QUOTE_TOKENS
    미만이면, 그 조각이 fetched_text의 부분문자열이더라도 grounded 후보에서 제외한다
    (단어 하나·두 개짜리는 소스와 우연히 겹칠 뿐 "실제로 읽고 인용했다"는 증거가 아님).
    """
    q = normalize_for_match(matched_quote)
    if not q:
        return False
    if len(q.split(" ")) < _MIN_QUOTE_TOKENS:
        return False
    return q in normalize_for_match(fetched_text)


@dataclass
class DeterministicGrounding:
    """코드 결정론 검사 결과(§4.3-①). 어댑터 병합이 모델 verdict와 합칠 원자료."""

    fabricated_sources: list[str] = field(default_factory=list)
    dead_sources: list[str] = field(default_factory=list)
    orphan_claims: list[str] = field(default_factory=list)
    unverified_quotes: list[str] = field(default_factory=list)  # quote⊄fetched_text인 claim id
    findings: list[Finding] = field(default_factory=list)


def run_deterministic_checks(stage_out: dict, snapshot_texts: dict[str, str]) -> DeterministicGrounding:
    """§4.3-① 결정적 실측. stage_out(claims/sources)와 {ref_id: 스냅샷원문}을 받는다.

    네 검사: (1)fabricated=claim이 인용한 ref가 sources에 없음, (2)dead=status≠200이거나
    본문 빈 source, (3)orphan=kind==fact인데 인용 없음(추천/추론 면제), (4)unverified_quote=
    quoted_span이 인용 소스 스냅샷의 부분문자열이 아님. snapshot_texts를 우선 쓰되 없으면
    stage_out.sources[].fetched_text로 폴백한다(호출부가 스냅샷 dict를 안 넘겨도 동작).
    """
    sources = {s["ref_id"]: s for s in stage_out.get("sources", [])}
    texts = dict(snapshot_texts)
    for ref, s in sources.items():
        texts.setdefault(ref, s.get("fetched_text", ""))

    res = DeterministicGrounding()
    seen_fabricated: set[str] = set()
    seen_dead: set[str] = set()

    # (2) dead sources: status≠200 or 본문 빈.
    for ref, s in sources.items():
        body = texts.get(ref, "") or ""
        # Finding 2: http_status가 비숫자 문자열/None이면 int() 변환이 ValueError/
        # TypeError로 grounding 전체를 크래시시킨다. 결정적 판정은 크래시하지 않고
        # 안전측(비200=dead)으로 degrade해야 하므로 파싱 실패를 200이 아닌 것으로 본다.
        try:
            status_ok = int(s.get("http_status", 0)) == 200
        except (TypeError, ValueError):
            status_ok = False
        if not status_ok or not body.strip():
            if ref not in seen_dead:
                seen_dead.add(ref)
                res.dead_sources.append(ref)
                res.findings.append(Finding(
                    severity="critical", category="dead_source",
                    detail=f"소스 {ref}가 죽었거나 본문이 비었습니다(status={s.get('http_status')}).",
                    fix_directive=f"소스 {ref}를 살아있는 URL로 교체하거나 이 소스에 의존하는 인용을 제거하세요.",
                    claim_id=None,
                ))

    for claim in stage_out.get("claims", []):
        cid = claim.get("id")
        kind = claim.get("kind", "fact")
        refs = claim.get("cited_source_refs") or []

        # (3) orphan: fact인데 무인용(추천/추론은 면제).
        if kind == "fact" and not refs:
            res.orphan_claims.append(cid)
            res.findings.append(Finding(
                severity="major", category="orphan_claim",
                detail=f"사실 주장 {cid}에 인용이 없습니다.",
                fix_directive=f"주장 {cid}에 스냅샷 소스를 인용하거나 추론/추천으로 강등하세요.",
                claim_id=cid,
            ))
            continue

        # (1) fabricated: 인용 ref가 sources에 없음.
        for ref in refs:
            if ref not in sources and ref not in seen_fabricated:
                seen_fabricated.add(ref)
                res.fabricated_sources.append(ref)
                res.findings.append(Finding(
                    severity="critical", category="fabricated_source",
                    detail=f"주장 {cid}가 존재하지 않는 소스 {ref}를 인용합니다.",
                    fix_directive=f"소스 {ref}를 sources 목록의 실재 스냅샷으로 교체하세요.",
                    claim_id=cid,
                ))

        # (4) unverified quote: quoted_span이 인용 소스 스냅샷의 부분문자열이 아님.
        span = claim.get("quoted_span") or ""
        if kind == "fact" and span.strip():
            live_refs = [r for r in refs if r in sources and r not in seen_dead]
            grounded = any(quote_is_grounded(span, texts.get(r, "")) for r in live_refs)
            if live_refs and not grounded:
                res.unverified_quotes.append(cid)
                res.findings.append(Finding(
                    severity="major", category="unverified_quote",
                    detail=f"주장 {cid}의 인용문이 스냅샷 원문에 그대로 존재하지 않습니다(날조 의심).",
                    fix_directive=f"주장 {cid}의 quoted_span을 스냅샷 원문의 축자 인용으로 교체하거나 unsupported로 표기하세요.",
                    claim_id=cid,
                ))

    return res

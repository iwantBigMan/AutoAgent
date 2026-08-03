"""pass간 검증 claim delta·모순 검출 + 수렴 판정(스펙 §5).

바깥 루프 pass N vs N-1의 검증된 claim을 정규화 key로 대조한다:
- 같은 key인데 값이 뒤집히면 '심화 아닌 모순'(contradiction) → 게이트 신호.
- 단, as-of가 다르면 시점차 갱신이라 모순이 아니라 added(심화)로 본다(§5 as-of 메타).
- 새로 검증된 claim 수(delta_count)가 임계 이하면 수렴 → 조기 종료.
순수 함수(모델 호출 없음). claim은 어댑터 표현과 무관하게 dict로 다룬다.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_WS = re.compile(r"\s+")


def normalize_claim_key(claim: dict) -> str:
    """claim의 안정 key. claim_id가 있으면 그대로, 없으면 정규화 텍스트 sha1(12자)."""
    cid = claim.get("claim_id")
    if cid:
        return str(cid)
    text = _WS.sub(" ", str(claim.get("text", ""))).strip().lower()
    return "h:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _value_of(claim: dict) -> str:
    # 값 비교 대상. value 우선, 없으면 정규화 텍스트로 폴백.
    if "value" in claim:
        return str(claim["value"]).strip()
    return _WS.sub(" ", str(claim.get("text", ""))).strip().lower()


@dataclass
class ClaimDelta:
    """pass간 검증 claim 비교 결과."""

    added: list[dict]           # 이번 pass에서 새로 검증된 claim(as-of 갱신 포함)
    unchanged: list[str]        # 값·시점 그대로인 claim key
    contradictions: list[dict]  # 같은 key·같은 as-of인데 값이 뒤집힌 모순
    delta_count: int            # len(added) — 수렴 게이트 입력


def diff_verified_claims(prev: list[dict], curr: list[dict]) -> ClaimDelta:
    """이전/이번 pass의 검증 claim 목록을 대조해 ClaimDelta를 만든다."""
    prev_by_key: dict[str, dict] = {normalize_claim_key(c): c for c in prev}
    added: list[dict] = []
    unchanged: list[str] = []
    contradictions: list[dict] = []
    for c in curr:
        key = normalize_claim_key(c)
        if key not in prev_by_key:
            added.append(c)
            continue
        p = prev_by_key[key]
        if str(c.get("as_of") or "") != str(p.get("as_of") or ""):
            added.append(c)  # as-of 시점차 → 심화(added)
            continue
        if _value_of(c) != _value_of(p):
            contradictions.append(
                {"claim_id": c.get("claim_id") or key,
                 "prev_value": _value_of(p), "curr_value": _value_of(c)}
            )
        else:
            unchanged.append(key)
    return ClaimDelta(added=added, unchanged=unchanged, contradictions=contradictions, delta_count=len(added))


def is_converged(delta: ClaimDelta, *, min_new_claims: int) -> bool:
    """새로 검증된 claim이 임계 미만이고 모순이 없으면 수렴(조기 종료 가능)으로 판정한다."""
    return delta.delta_count < max(min_new_claims, 1) and not delta.contradictions


from typing import Literal


@dataclass
class OuterPassDecision:
    """바깥 루프의 다음 행동. gate는 §6.2에 따라 절대 생략 불가."""

    action: Literal["continue", "early_stop", "gate"]
    reason: str
    contradictions: list[dict]


def decide_outer_pass(
    delta: ClaimDelta, seed_violations: list[str], *,
    outer_pass: int, max_outer: int, min_new_claims: int,
) -> OuterPassDecision:
    """pass 결과로 다음 행동을 결정한다. 우선순위: 모순/seed위반 gate > 수렴/마지막 early_stop > continue."""
    # (1) 모순 또는 seed 계약 위반 = 분기점 게이트(절대 생략 안 함, §6.2).
    if delta.contradictions or seed_violations:
        bits = []
        if delta.contradictions:
            bits.append(f"검증 claim 모순 {len(delta.contradictions)}건")
        if seed_violations:
            bits.append(f"seed 계약 위반 {len(seed_violations)}건")
        return OuterPassDecision(action="gate", reason="; ".join(bits), contradictions=delta.contradictions)
    # (2) 수렴(신규 검증 claim이 임계 미만) 또는 마지막 pass 도달 = 조기/정상 종료.
    if delta.delta_count < max(min_new_claims, 1):
        return OuterPassDecision(
            action="early_stop",
            reason=f"수렴(신규 검증 claim {delta.delta_count} < 임계 {min_new_claims})",
            contradictions=[],
        )
    if outer_pass >= max_outer:
        return OuterPassDecision(
            action="early_stop", reason=f"바깥 루프 상한 도달(pass {outer_pass}/{max_outer})", contradictions=[],
        )
    # (3) 개선 충분 + 여지 있음 = 다음 pass 진행.
    return OuterPassDecision(
        action="continue",
        reason=f"개선 지속(신규 검증 claim {delta.delta_count}), 다음 pass 진입", contradictions=[],
    )

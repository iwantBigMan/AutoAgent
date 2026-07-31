"""canonical seed 계약(스펙 §5 seed 계약).

첫 outer pass에서 회사 식별자·시장 정의·기준통화·기간·단위를 확정해 read-only로
pin한다. pass 2는 seed를 못 바꾸고 심화만 허용 — 바꾸면 detect_seed_violations가
결정론적으로 잡아 모순 게이트 신호로 승격한다. 순수 함수(모델 호출 없음).
"""
from __future__ import annotations

from dataclasses import dataclass, replace


# canonical seed의 필수 5필드. 이 이름으로 raw dict에서 뽑고, 이 이름으로 위반을 검사한다.
CANONICAL_FIELDS = ("company", "market", "base_currency", "period", "unit")


@dataclass(frozen=True)
class SeedPin:
    """바깥 루프 불변식으로 굳힌 canonical seed. frozen이라 코드 경로에서 변형 불가(read-only pin)."""

    company: str
    market: str
    base_currency: str
    period: str
    unit: str
    as_of: str | None = None  # 시점 의존 seed의 as-of 날짜(주가·환율 기준일 등, 선택)


def build_seed_pin(raw: dict) -> SeedPin:
    """자유 dict에서 canonical 5필드를 뽑아 SeedPin을 만든다. 누락 필드는 ValueError."""
    missing = [f for f in CANONICAL_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        raise ValueError(f"seed에 canonical 필드 누락(확정 실패): {missing}")
    return SeedPin(
        company=str(raw["company"]).strip(), market=str(raw["market"]).strip(),
        base_currency=str(raw["base_currency"]).strip(), period=str(raw["period"]).strip(),
        unit=str(raw["unit"]).strip(), as_of=(str(raw["as_of"]).strip() if raw.get("as_of") else None),
    )


def seed_pin_to_dict(pin: SeedPin) -> dict:
    """research_state.json의 seed_pin 필드로 직렬화한다."""
    return {
        "company": pin.company, "market": pin.market, "base_currency": pin.base_currency,
        "period": pin.period, "unit": pin.unit, "as_of": pin.as_of,
    }


def seed_pin_from_dict(d: dict) -> SeedPin:
    """재개 시 research_state.json의 seed_pin을 역직렬화한다(빈 pin이면 ValueError)."""
    return build_seed_pin(d)


def detect_seed_violations(pinned: SeedPin, candidate: dict) -> list[str]:
    """pass 2+ 산출물이 pin된 canonical 값과 다른 값을 주장하면 위반 문자열 목록을 반환한다.

    candidate가 어떤 canonical 필드를 아예 언급 안 하면(부분 심화 산출) 위반이 아니다.
    언급했는데 값이 다르면 seed drift 모순으로 본다(스펙 §5 pass간 diff 모순감지).
    """
    violations: list[str] = []
    pinned_map = seed_pin_to_dict(pinned)
    for field in CANONICAL_FIELDS:
        if field not in candidate:
            continue
        got = str(candidate[field]).strip()
        expected = str(pinned_map[field]).strip()
        if got and got != expected:
            violations.append(
                f"seed 계약 위반: {field} pin='{expected}' 인데 pass 산출물이 '{got}'로 변경 시도"
            )
    return violations


def pin_as_of(pin: SeedPin, as_of: str) -> SeedPin:
    """as-of 날짜만 확정/보강한 새 pin을 반환한다(canonical 5필드는 불변)."""
    return replace(pin, as_of=as_of.strip() or None)

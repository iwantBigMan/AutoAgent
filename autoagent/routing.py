"""키워드 기반 라우팅.

요청 텍스트의 키워드 점수로 task_type(backend/frontend/docs/review)·subtype·
risk_level을 정하고, 구현자/리뷰어 모델(구현자와 반대)을 선택한다.
DB 관련 용어가 있으면 subtype=db·risk_level=high로 고정(승인 게이트 대상).
"""
from __future__ import annotations

import re
from typing import Any


# 아래 용어 목록은 요청 텍스트를 소문자화해 부분일치로 점수를 매기는 데 쓰인다.
TASK_TYPES = {"backend", "frontend", "docs", "review"}
DB_TERMS = [
    "db",
    "database",
    "schema",
    "migration",
    "alembic",
    "table",
    "column",
    "index",
    "constraint",
    "transaction",
    "locking",
    "rollback",
    "backfill",
    "seed",
    "postgres",
    "sql",
    "repository",
    "foreign key",
    "unique",
    "nullable",
]
HIGH_RISK_TERMS = [
    "migration",
    "auth",
    "payment",
    "production",
    "backfill",
    "rollback",
]
# auto 라우팅의 "구현 의도" 감지용 동사 목록.
# 명사 점수가 docs를 가리켜도 아래 구현 의도가 있으면 backend/frontend로 되돌린다.
# 한국어는 조사가 붙어도 안전한 부분일치(substring) 매칭.
KO_IMPL_INTENT_TERMS = [
    "구현",
    "수정",
    "추가",
    "삭제",
    "제거",
    "교체",
    "리팩터",
    "리팩토링",
    "반영",
    "만들",
    "고쳐",
    "바꿔",
    "통합",
]
# 영어는 \b 단어경계 정규식으로 오염 방지(prefix→fix, address→add 오매칭 차단).
# update/write/review/document는 문서·리뷰 신호라 의도 목록에서 의도적으로 제외한다.
EN_IMPL_INTENT_PATTERN = re.compile(
    r"\b(?:implement|refactor|rewrite|integrate|fix|add|remove|build|create|rename|wire)\b"
)


def db_term_count(text: str) -> int:
    """DB_TERMS 중 요청에 등장한 개수. 'db' 코드 심볼(db_score) 오발만 좁게 배제한다.

    이건 승인 게이트의 입력이라 실제 DB 용어 '누락'(under-match)이 최우선 위험이다.
    그래서 대부분 용어는 느슨한 부분일치를 그대로 써서 'postgres'→postgresql,
    'sql'→mysql, 'db'→mongodb 같은 결합어까지 계속 잡는다(과다발동은 안전 방향).
    유일한 짧은 오발원 'db'만, snake_case 식별자(db_score, my_db 등 언더스코어 인접)일
    때 제외한다. standalone 'db'와 결합어(mongodb)의 'db'는 계속 센다.

    입력은 어떤 대소문자든 받아 내부에서 소문자화한다(공개 헬퍼 오용 방지).
    """
    lowered = text.lower()
    count = 0
    for term in DB_TERMS:
        if term == "db":
            # 언더스코어에 인접하지 않은 'db'만 센다(코드 식별자 조각 배제).
            if re.search(r"(?<!_)db(?!_)", lowered):
                count += 1
        elif term in lowered:      # 나머지는 느슨한 부분일치(결합어·복수형 모두 포함)
            count += 1
    return count


def route_task(task_type: str, request: str, requested_implementer: str = "auto") -> dict[str, Any]:
    """요청을 라우팅해 route dict를 만든다.

    task_type이 auto면 키워드 점수로 backend/frontend/docs를 고르고, 명시되면 그대로 쓴다.
    DB 용어가 있으면 subtype=db·risk_level=high, high-risk 용어가 있으면 risk_level=high.
    구현자/리뷰어는 choose_implementer가 정한다(리뷰어는 항상 구현자와 반대 모델).
    """
    lowered = request.lower()
    db_score = db_term_count(lowered)   # 기존 sum(... in lowered) substring 오발을 토큰 매칭으로 대체
    high_risk_score = sum(1 for term in HIGH_RISK_TERMS if term in lowered)

    if task_type != "auto":
        chosen = task_type
        reason = f"Task type explicitly set to {task_type}."
        confidence = 1.0
        if db_score > 0:
            reason = f"{reason} DB subtype keywords matched: {db_score}."
        if high_risk_score > 0:
            reason = f"{reason} High-risk keywords matched: {high_risk_score}."
    else:
        backend_terms = [
            "api",
            "db",
            "database",
            "server",
            "fastapi",
            "backend",
            "service",
            "repository",
            "migration",
            "auth",
            "worker",
        ]
        frontend_terms = [
            "ui",
            "react",
            "css",
            "layout",
            "component",
            "page",
            "frontend",
            "design",
            "interaction",
        ]
        docs_terms = [
            "readme",
            "docs",
            "document",
            "spec",
            "architecture",
            "risk",
            "review",
            "planning",
            "plan",
        ]

        scores = {
            "backend": sum(1 for term in backend_terms if term in lowered),
            "frontend": sum(1 for term in frontend_terms if term in lowered),
            "docs": sum(1 for term in docs_terms if term in lowered),
        }
        chosen = max(scores, key=scores.get)
        if scores[chosen] == 0:
            chosen = "docs"
            confidence = 0.45
            reason = "No strong implementation keywords found; defaulting to docs/review."
        else:
            total = sum(scores.values())
            confidence = round(max(0.55, scores[chosen] / max(total, 1)), 2)
            reason = f"Keyword routing scores: {scores}."

        # 구현 의도 가드: 명사 점수가 docs를 가리켜도 구현 동사가 있으면
        # backend/frontend 구현 라우트로 되돌린다(read-only no-op 오분류 교정).
        # db_score/high_risk_score 오버라이드보다 반드시 앞에 둔다.
        ko_intent = [term for term in KO_IMPL_INTENT_TERMS if term in lowered]
        en_intent = EN_IMPL_INTENT_PATTERN.findall(lowered)
        impl_intent = ko_intent + en_intent
        if chosen == "docs" and impl_intent:
            # 구현 라우트로 되돌린다. 단, 파일명 속 'design'(-design.md) 같은 단발 프론트
            # 키워드가 backend=0을 이겨 frontend로 도메인을 뒤집는 오분류를 막는다.
            # frontend는 신호 2개 이상 확실할 때만 택하고, 그 외에는 backend(구현 기본).
            if scores["frontend"] >= 2 and scores["frontend"] > scores["backend"]:
                chosen = "frontend"
            else:
                chosen = "backend"
            confidence = 0.6
            reason = (
                f"Implementation intent overrode docs routing "
                f"({len(impl_intent)} intent keyword(s)); scores {scores}."
            )

        if db_score > 0:
            chosen = "backend"
            confidence = max(confidence, 0.9 if db_score >= 2 else 0.75)
            reason = f"{reason} DB subtype keywords matched: {db_score}."
        if high_risk_score > 0:
            reason = f"{reason} High-risk keywords matched: {high_risk_score}."

    if chosen == "backend":
        if db_score > 0:
            subtype = "db"
            risk_level = "high"
        elif any(term in lowered for term in ["api", "fastapi", "endpoint", "route"]):
            subtype = "api"
            risk_level = "medium"
        elif any(term in lowered for term in ["service", "repository", "business logic"]):
            subtype = "service"
            risk_level = "medium"
        elif any(term in lowered for term in ["infra", "config", "deploy", "worker"]):
            subtype = "infra"
            risk_level = "medium"
        else:
            subtype = "general"
            risk_level = "medium"
        if high_risk_score > 0:
            risk_level = "high"
    elif chosen == "frontend":
        subtype = "ui"
        risk_level = "medium"
    else:
        subtype = "review" if chosen == "review" else "docs"
        risk_level = "low"

    implementation_agent, review_agent, implementer_reason = choose_implementer(
        requested_implementer=requested_implementer,
        task_type=chosen,
    )

    return {
        "task_type": chosen,
        "subtype": subtype,
        "confidence": confidence,
        "reason": reason,
        "requested_implementer": requested_implementer,
        "implementation_agent": implementation_agent,
        "review_agent": review_agent,
        "implementer_reason": implementer_reason,
        "architect_agent": "claude",
        "evaluator_agent": "codex",
        "risk_level": risk_level,
    }


def choose_implementer(
    *,
    requested_implementer: str,
    task_type: str,
) -> tuple[str, str, str]:
    """(구현자, 리뷰어, 사유)를 반환. 리뷰어는 항상 구현자와 반대 모델이다.

    명시 지정이 우선. auto면 모든 구현(backend·frontend)은 Codex가 맡고 리뷰는 반대편
    Claude가 맡는다. docs/review 라우트는 구현 스텝이 없어 claude를 구현자 자리에 둔다.
    """
    if requested_implementer == "claude":
        return "claude", "codex", "Implementer explicitly set to Claude."
    if requested_implementer == "codex":
        return "codex", "claude", "Implementer explicitly set to Codex."

    if task_type in {"backend", "frontend"}:
        return "codex", "claude", f"{task_type.capitalize()} implementation defaults to Codex."
    if task_type in {"docs", "review"}:
        return "claude", "codex", "Docs/review routes have no implementation step."

    return "claude", "codex", "Fallback implementer selection."

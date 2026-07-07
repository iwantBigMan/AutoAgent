from __future__ import annotations

from typing import Any


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
CODEX_IMPLEMENTER_TERMS = [
    "test failure",
    "test failed",
    "pytest",
    "failing test",
    "lint",
    "type error",
    "typecheck",
    "build error",
    "build failed",
    "diff",
    "patch",
    "error log",
    "stack trace",
    "traceback",
    "fix the failing",
    "run tests",
    "테스트 실패",
    "테스트 돌려",
    "에러 로그",
    "빌드 에러",
    "수정해줘",
]


def route_task(task_type: str, request: str, requested_implementer: str = "auto") -> dict[str, Any]:
    lowered = request.lower()
    db_score = sum(1 for term in DB_TERMS if term in lowered)
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
        subtype=subtype,
        request=request,
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
    subtype: str,
    request: str,
) -> tuple[str, str, str]:
    if requested_implementer == "claude":
        return "claude", "codex", "Implementer explicitly set to Claude."
    if requested_implementer == "codex":
        return "codex", "claude", "Implementer explicitly set to Codex."

    if task_type == "frontend":
        return "codex", "claude", "Frontend defaults to Codex implementation."
    if task_type in {"docs", "review"}:
        return "claude", "codex", "Docs/review routes have no implementation step."
    if task_type == "backend":
        lowered = request.lower()
        matched = [term for term in CODEX_IMPLEMENTER_TERMS if term in lowered]
        if subtype != "db" and matched:
            return (
                "codex",
                "claude",
                f"Backend request is test/build/diff-fix oriented; matched {len(matched)} keyword(s).",
            )
        return (
            "claude",
            "codex",
            "Backend defaults to Claude unless the request is test/build/diff-fix oriented.",
        )

    return "claude", "codex", "Fallback implementer selection."

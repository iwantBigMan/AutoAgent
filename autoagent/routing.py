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


def route_task(task_type: str, request: str) -> dict[str, Any]:
    lowered = request.lower()
    db_score = sum(1 for term in DB_TERMS if term in lowered)

    if task_type != "auto":
        chosen = task_type
        reason = f"Task type explicitly set to {task_type}."
        confidence = 1.0
        if db_score > 0:
            reason = f"{reason} DB subtype keywords matched: {db_score}."
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

    if chosen == "backend":
        implementation_agent = "claude"
        review_agent = "codex"
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
    elif chosen == "frontend":
        implementation_agent = "codex"
        review_agent = "claude"
        subtype = "ui"
        risk_level = "medium"
    else:
        implementation_agent = "claude"
        review_agent = "codex"
        subtype = "review" if chosen == "review" else "docs"
        risk_level = "low"

    return {
        "task_type": chosen,
        "subtype": subtype,
        "confidence": confidence,
        "reason": reason,
        "implementation_agent": implementation_agent,
        "review_agent": review_agent,
        "architect_agent": "claude",
        "evaluator_agent": "codex",
        "risk_level": risk_level,
    }

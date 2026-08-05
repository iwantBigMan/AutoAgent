"""build_layers/_layer_subtype_risk 결정론 단위테스트(멀티레이어 라우팅 코어)."""
from __future__ import annotations

from autoagent.routing import build_layers, _layer_subtype_risk


def _scores(backend=0, frontend=0, docs=0):
    return {"backend": backend, "frontend": frontend, "docs": docs}


def test_layer_subtype_risk_all_branches():
    # 추출된 헬퍼가 기존 route_task 인라인 로직의 모든 분기를 동형 재현하는지 못박는다.
    assert _layer_subtype_risk("backend", "db migration", db_score=1, high_risk_score=0) == ("db", "high")
    assert _layer_subtype_risk("backend", "add api endpoint", db_score=0, high_risk_score=0) == ("api", "medium")
    assert _layer_subtype_risk("backend", "repository service layer", db_score=0, high_risk_score=0) == ("service", "medium")
    assert _layer_subtype_risk("backend", "deploy infra worker", db_score=0, high_risk_score=0) == ("infra", "medium")
    assert _layer_subtype_risk("backend", "plain logic", db_score=0, high_risk_score=0) == ("general", "medium")
    assert _layer_subtype_risk("backend", "plain logic", db_score=0, high_risk_score=1) == ("general", "high")
    assert _layer_subtype_risk("frontend", "react page", db_score=0, high_risk_score=0) == ("ui", "medium")
    assert _layer_subtype_risk("docs", "readme", db_score=0, high_risk_score=0) == ("docs", "low")
    assert _layer_subtype_risk("review", "review this", db_score=0, high_risk_score=0) == ("review", "low")


def test_single_backend_only():
    # backend만 신호 → [backend] 하나(기존 단일 동작 동형).
    layers = build_layers("backend", _scores(backend=2), "add api endpoint", 0, 0, "auto")
    assert [l["task_type"] for l in layers] == ["backend"]
    assert layers[0]["implementation_agent"] == "codex"
    assert layers[0]["review_agent"] == "claude"


def test_single_frontend_only():
    # frontend>=2, backend=0 → [frontend] 하나.
    layers = build_layers("frontend", _scores(frontend=2), "react component page", 0, 0, "auto")
    assert [l["task_type"] for l in layers] == ["frontend"]


def test_backend_and_frontend_ordered():
    # 둘 다 임계 넘음 → [backend, frontend] 순서 고정.
    layers = build_layers("backend", _scores(backend=2, frontend=2), "api and react page component", 0, 0, "auto")
    assert [l["task_type"] for l in layers] == ["backend", "frontend"]


def test_high_risk_keeps_set_and_raises_only_backend():
    # db override로 chosen=backend여도 frontend(>=2)는 재추가되고, risk는 backend만 high.
    layers = build_layers("backend", _scores(backend=3, frontend=2), "db migration and react dashboard page", db_score=1, high_risk_score=1, requested_implementer="auto")
    by_type = {l["task_type"]: l for l in layers}
    assert set(by_type) == {"backend", "frontend"}
    assert by_type["backend"]["risk_level"] == "high"
    assert by_type["frontend"]["risk_level"] == "medium"


def test_frontend_single_keyword_not_added():
    # frontend 신호가 1개(<2)면 오검출 방지 — 집합에 넣지 않음.
    layers = build_layers("backend", _scores(backend=2, frontend=1), "api with a design note", 0, 0, "auto")
    assert [l["task_type"] for l in layers] == ["backend"]


def test_frontend_pure_with_highrisk_term_no_phantom_backend():
    # 순수 프론트(backend=0)인데 high_risk_score>0여도 허깨비 backend를 만들지 않음.
    layers = build_layers("frontend", _scores(frontend=2), "react payment page component", db_score=0, high_risk_score=1, requested_implementer="auto")
    assert [l["task_type"] for l in layers] == ["frontend"]


def test_docs_chosen_returns_empty():
    assert build_layers("docs", _scores(docs=1), "write readme", 0, 0, "auto") == []
    assert build_layers("review", _scores(), "review this", 0, 0, "auto") == []


def test_route_task_auto_multilayer():
    # 백+프론트 요청 → layers 두 개, 기존 task_type(주 레이어)도 유지.
    from autoagent.routing import route_task
    route = route_task("auto", "add a FastAPI endpoint and build a React page component")
    assert [l["task_type"] for l in route["layers"]] == ["backend", "frontend"]
    assert route["task_type"] in {"backend", "frontend"}  # 주 레이어 키 보존


def test_route_task_auto_single_backend_layers():
    # 백엔드만 신호 → [backend] 하나.
    from autoagent.routing import route_task
    route = route_task("auto", "add a database migration to the repository service")
    assert [l["task_type"] for l in route["layers"]] == ["backend"]


def test_route_task_docs_empty_layers():
    # docs 라우팅 → layers 비어있음(구현 스텝 없음).
    from autoagent.routing import route_task
    route = route_task("auto", "write the readme documentation")
    assert route["layers"] == []


def test_route_task_explicit_is_single_layer():
    # 명시 --task-type backend → 멀티검출 안 함, [backend]만.
    from autoagent.routing import route_task
    route = route_task("backend", "backend and a react page component and ui")
    assert [l["task_type"] for l in route["layers"]] == ["backend"]

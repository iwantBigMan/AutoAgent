# routed 멀티레이어 구현 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** routed 워크플로우가 한 요청의 backend·frontend 레이어를 모두 구현하고, 누락 시 최종 리뷰가 아니라 게이트에서 막는다.

**Architecture:** 라우팅이 단일 `task_type` 대신 순서 있는 레이어 집합(`route["layers"]`)을 함께 반환하고(키워드 임계 기반, 결정론), 구현 라우트가 레이어별로 impl→review⇄fix 사이클을 순차 실행한 뒤 forced 커버리지 게이트로 누락을 차단하고 리포트에 커버리지 배너를 prepend한다. 기존 단일-레이어·docs/review·승인 게이트·resume는 바이트 동형으로 보존한다.

**Tech Stack:** Python 3.x, dataclass 없음(dict route), pytest(결정론 순수함수), dry-run(오케스트레이션).

## Global Constraints

- 모든 모듈은 **한국어 docstring**, 함수는 한국어 인라인 주석(식별자만 영문).
- `from __future__ import annotations`, PEP 604 타입(`str | None`).
- **하위호환 필수**: 단일 backend/frontend 요청은 `layers == [해당 레이어]` 하나로 기존 구현 경로와 **바이트 동형**. docs/review/read-only는 `layers == []`. 승인 게이트·resume·checkpoint는 무변경.
- **공유 프롬프트 템플릿(`claude_final.md`/`codex_final.md` 등) 변경 금지** — decompose 실행기(`task_exec`)도 렌더하므로 새 placeholder는 KeyError를 유발한다. 리포트 커버리지는 **코드측 prepend**로만 처리.
- **직교하는 공유 안전망 무변경**: `routed_common.is_high_risk`(요청 텍스트의 migration/auth/payment/production/backfill/rollback 스캔)는 승인 게이트와 공유하므로 손대지 않는다. 이번 설계는 "라우팅 오버라이드(db_score/high_risk_score)로 인한 프론트 실종/승격"만 고친다.
- **명시 `--task-type`은 단일 레이어**: 사용자가 명시하면 그 레이어만(멀티검출 안 함). auto만 멀티검출.
- routed 오케스트레이션은 유닛테스트가 없다 → **순수함수는 pytest, 배선은 dry-run**으로 검증. 라이브 모델 런은 사용자 인계.
- 스펙: `docs/superpowers/specs/2026-08-03-routed-multilayer-design.md`.

---

### Task 1: 레이어 서브라우트 순수함수 (`_layer_subtype_risk`, `_make_layer`, `build_layers`)

**Files:**
- Modify: `autoagent/routing.py` (route_task의 인라인 subtype/risk 로직을 헬퍼로 추출 + 신규 함수 3개, choose_implementer 정의부 뒤 ~257행 근처에 추가)
- Test: `tests/test_routing_layers.py` (신규)

**Interfaces:**
- Consumes: 기존 `choose_implementer(*, requested_implementer, task_type) -> tuple[str, str, str]` (routing.py:236).
- Produces:
  - `_layer_subtype_risk(task_type: str, lowered: str, db_score: int, high_risk_score: int) -> tuple[str, str]`
  - `_make_layer(task_type: str, lowered: str, db_score: int, high_risk_score: int, requested_implementer: str) -> dict[str, Any]` — `{task_type, subtype, risk_level, implementation_agent, review_agent}`
  - `build_layers(chosen: str, scores: dict[str, int], lowered: str, db_score: int, high_risk_score: int, requested_implementer: str) -> list[dict[str, Any]]`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_routing_layers.py`

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_routing_layers.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_layers'`.

- [ ] **Step 3: `_layer_subtype_risk` 추출 + `_make_layer`/`build_layers` 구현**

`autoagent/routing.py`의 `choose_implementer`(routing.py:236-256) 정의 **직후**(RESEARCHER_BY_STAGE 앞)에 추가:

```python
def _layer_subtype_risk(task_type: str, lowered: str, db_score: int, high_risk_score: int) -> tuple[str, str]:
    """레이어(task_type)별 (subtype, risk_level)을 계산한다. route_task 인라인 로직과 동형.

    backend: db>api>service>infra>general 순으로 subtype 결정, high_risk_score>0면 risk=high로 상향.
    frontend: 항상 (ui, medium). docs/review: (docs|review, low).
    """
    if task_type == "backend":
        if db_score > 0:
            subtype, risk_level = "db", "high"
        elif any(t in lowered for t in ["api", "fastapi", "endpoint", "route"]):
            subtype, risk_level = "api", "medium"
        elif any(t in lowered for t in ["service", "repository", "business logic"]):
            subtype, risk_level = "service", "medium"
        elif any(t in lowered for t in ["infra", "config", "deploy", "worker"]):
            subtype, risk_level = "infra", "medium"
        else:
            subtype, risk_level = "general", "medium"
        if high_risk_score > 0:
            risk_level = "high"
        return subtype, risk_level
    if task_type == "frontend":
        return "ui", "medium"
    return ("review" if task_type == "review" else "docs"), "low"


def _make_layer(
    task_type: str, lowered: str, db_score: int, high_risk_score: int, requested_implementer: str
) -> dict[str, Any]:
    """단일 레이어 서브라우트 dict를 만든다(subtype/risk + 구현자/리뷰어 배정)."""
    subtype, risk_level = _layer_subtype_risk(task_type, lowered, db_score, high_risk_score)
    impl_agent, review_agent, _reason = choose_implementer(
        requested_implementer=requested_implementer, task_type=task_type
    )
    return {
        "task_type": task_type,
        "subtype": subtype,
        "risk_level": risk_level,
        "implementation_agent": impl_agent,
        "review_agent": review_agent,
    }


def build_layers(
    chosen: str,
    scores: dict[str, int],
    lowered: str,
    db_score: int,
    high_risk_score: int,
    requested_implementer: str,
) -> list[dict[str, Any]]:
    """주 레이어(chosen) 위에 임계를 넘은 코드 레이어를 얹어 순서 있는 서브라우트 리스트를 만든다.

    - chosen이 코드 레이어(backend/frontend)가 아니면 [](구현 스텝 없음).
    - 집합 = {chosen} ∪ {backend if backend>=1} ∪ {frontend if frontend>=2}.
    - 축소 금지는 재추가로 달성: route_task의 db override가 chosen=backend로 바꿔도 scores.frontend는
      그대로라 frontend>=2면 여기서 복구된다. high_risk_score는 chosen을 안 바꾸므로 순수-프론트
      요청에 허깨비 backend를 만들지 않기 위해 force-add는 두지 않는다.
    - 순서 고정: backend 먼저, frontend 나중.
    """
    if chosen not in {"backend", "frontend"}:
        return []
    selected = {chosen}
    if scores.get("backend", 0) >= 1:
        selected.add("backend")
    if scores.get("frontend", 0) >= 2:
        selected.add("frontend")
    return [
        _make_layer(task_type, lowered, db_score, high_risk_score, requested_implementer)
        for task_type in ("backend", "frontend")  # 순서 고정
        if task_type in selected
    ]
```

- [ ] **Step 4: route_task 인라인 subtype/risk 블록을 헬퍼 호출로 교체(동형)**

`autoagent/routing.py`의 route_task 안, `if chosen == "backend": ... elif chosen == "frontend": ... else: ...`
subtype/risk 계산 블록(대략 routing.py:191-214) **전체**를 아래 한 줄로 대체한다:

```python
    subtype, risk_level = _layer_subtype_risk(chosen, lowered, db_score, high_risk_score)
```

이는 Step 3에서 추출한 `_layer_subtype_risk`가 그 인라인 로직과 동일하므로 **동작 보존**이다
(Step 1의 `test_layer_subtype_risk_all_branches`가 전 분기를 가드한다). `chosen == "review"` 케이스도
헬퍼의 `("review" if task_type == "review" else "docs", "low")`가 그대로 재현한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_routing_layers.py -q`
Expected: PASS (8 passed).

- [ ] **Step 6: 커밋**

```bash
git add autoagent/routing.py tests/test_routing_layers.py
git commit -m "feat(routing): subtype/risk 헬퍼 추출 + build_layers 레이어 서브라우트 + 단위테스트"
```

---

### Task 2: `route_task`에 `layers` 배선 + `routed.py` 분기 전환

**Files:**
- Modify: `autoagent/routing.py` (route_task가 `layers` 키 추가; explicit/auto 양분기)
- Modify: `autoagent/workflows/routed.py:71` (코드-레이어 진입 조건을 `route["layers"]` 기반으로)
- Test: `tests/test_routing_layers.py` (route_task 반환 검증 추가)

**Interfaces:**
- Consumes: Task 1의 `build_layers`, `_make_layer`.
- Produces: `route_task(...)` 반환 dict에 `route["layers"]: list[dict]` 키 추가(기존 키 전부 유지).

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_routing_layers.py` 하단에 append

```python
from autoagent.routing import route_task


def test_route_task_auto_multilayer():
    # 백+프론트 요청 → layers 두 개, 기존 task_type(주 레이어)도 유지.
    route = route_task("auto", "add a FastAPI endpoint and build a React page component")
    assert [l["task_type"] for l in route["layers"]] == ["backend", "frontend"]
    assert route["task_type"] in {"backend", "frontend"}  # 주 레이어 키 보존


def test_route_task_auto_single_backend_layers():
    route = route_task("auto", "add a database migration to the repository service")
    assert [l["task_type"] for l in route["layers"]] == ["backend"]


def test_route_task_docs_empty_layers():
    route = route_task("auto", "write the readme documentation")
    assert route["layers"] == []


def test_route_task_explicit_is_single_layer():
    # 명시 --task-type backend → 멀티검출 안 함, [backend]만.
    route = route_task("backend", "backend and a react page component and ui")
    assert [l["task_type"] for l in route["layers"]] == ["backend"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_routing_layers.py -q`
Expected: FAIL — `KeyError: 'layers'`.

- [ ] **Step 3: route_task가 layers를 반환하도록 수정**

`autoagent/routing.py` route_task의 반환 직전(routing.py:216 `implementation_agent, review_agent, ... = choose_implementer(...)` 뒤, `return {...}` 앞)에 layers 계산을 넣는다.

명시(`task_type != "auto"`)와 auto를 나눈다. 명시 모드에는 `scores`가 없으므로 단일 레이어로 처리:

```python
    implementation_agent, review_agent, implementer_reason = choose_implementer(
        requested_implementer=requested_implementer,
        task_type=chosen,
    )

    # 레이어 서브라우트 집합. 명시 task_type은 단일 레이어(멀티검출 안 함), auto만 멀티검출.
    if task_type != "auto":
        layers = (
            [_make_layer(chosen, lowered, db_score, high_risk_score, requested_implementer)]
            if chosen in {"backend", "frontend"}
            else []
        )
    else:
        layers = build_layers(chosen, scores, lowered, db_score, high_risk_score, requested_implementer)

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
        "layers": layers,
    }
```

주의: `scores`는 auto 분기(routing.py:149) 안에서만 정의되므로, 위 `else` 가지(auto)에서만 참조된다 — 명시 분기는 `scores`를 건드리지 않는다.

- [ ] **Step 4: `routed.py` 분기 전환**

`autoagent/workflows/routed.py:71`:

```python
        if route["layers"]:
            return run_implementation_route(args, config, common, route, request, budget, run_dir)
```

(기존 `if route["task_type"] in {"backend", "frontend"}:`를 대체. docs/review·read-only 분기(routed.py:58)와 승인 게이트(routed.py:61)는 무변경 — 주 `task_type` 기준.)

- [ ] **Step 5: 테스트 + dry-run 확인**

Run: `python -m pytest tests/test_routing_layers.py -q`
Expected: PASS (12 passed).

Run: `python .\run.py --dry-run --workflow routed --task-type auto --request "add a FastAPI endpoint and a React page component"`
Expected: exit 0, `route.json`에 `"layers"`가 backend·frontend 둘 다 포함(다음 Task가 두 레이어를 실제로 렌더).

- [ ] **Step 6: 커밋**

```bash
git add autoagent/routing.py autoagent/workflows/routed.py tests/test_routing_layers.py
git commit -m "feat(routing): route에 layers 배선 + routed 진입 조건 전환"
```

---

### Task 3: 커버리지 게이트 프리미티브 (`missing_layers`, `coverage_banner_md`, `coverage_gate`)

**Files:**
- Modify: `autoagent/workflows/routed_common.py` (순수함수 2개 + 게이트 1개 추가)
- Test: `tests/test_coverage_gate.py` (신규)

**Interfaces:**
- Produces:
  - `missing_layers(route: dict[str, Any], implemented: list[str]) -> list[str]`
  - `coverage_banner_md(route: dict[str, Any], implemented: list[str]) -> str`
  - `coverage_gate(run_dir: Path, route: dict[str, Any], missing: list[str]) -> int`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_coverage_gate.py`

```python
"""레이어 커버리지 프리미티브 단위테스트."""
from __future__ import annotations

import json

from autoagent.workflows.routed_common import missing_layers, coverage_banner_md, coverage_gate


def _route(*task_types):
    return {"layers": [{"task_type": t} for t in task_types]}


def test_missing_layers_none():
    assert missing_layers(_route("backend", "frontend"), ["backend", "frontend"]) == []


def test_missing_layers_reports_gap_in_order():
    assert missing_layers(_route("backend", "frontend"), ["backend"]) == ["frontend"]


def test_missing_layers_empty_route():
    assert missing_layers({"layers": []}, []) == []


def test_coverage_banner_complete():
    banner = coverage_banner_md(_route("backend", "frontend"), ["backend", "frontend"])
    assert "100%" in banner and "전 레이어" in banner


def test_coverage_banner_missing():
    banner = coverage_banner_md(_route("backend", "frontend"), ["backend"])
    assert "frontend" in banner and "미구현" in banner


def test_coverage_banner_empty_route_is_blank():
    assert coverage_banner_md({"layers": []}, []) == ""


def test_coverage_gate_writes_status_and_returns_zero(tmp_path):
    route = _route("backend", "frontend")
    rc = coverage_gate(tmp_path, route, ["frontend"])
    assert rc == 0
    status = json.loads((tmp_path / "coverage_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "blocked"
    assert status["kind"] == "layer_coverage"
    assert status["missing"] == ["frontend"]
    assert status["implemented"] == ["backend"]
    assert (tmp_path / "final_report.md").exists()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_coverage_gate.py -q`
Expected: FAIL — `ImportError: cannot import name 'missing_layers'`.

- [ ] **Step 3: 구현** — `autoagent/workflows/routed_common.py` 하단(stop_after 뒤)에 추가

```python
def missing_layers(route: dict[str, Any], implemented: list[str]) -> list[str]:
    """route.layers가 요구한 task_type 중 implemented에 없는 것들(요구 순서 보존)."""
    expected = [layer["task_type"] for layer in (route.get("layers") or [])]
    done = set(implemented)
    return [task_type for task_type in expected if task_type not in done]


def coverage_banner_md(route: dict[str, Any], implemented: list[str]) -> str:
    """final_report.md 상단에 prepend할 레이어 커버리지 배너(markdown). layers 없으면 빈 문자열."""
    expected = [layer["task_type"] for layer in (route.get("layers") or [])]
    if not expected:
        return ""
    missing = missing_layers(route, implemented)
    pct = round(len(implemented) / len(expected) * 100)
    status = "✅ 전 레이어 구현됨" if not missing else f"⚠ 미구현: {', '.join(missing)}"
    return (
        f"> **레이어 커버리지 {pct}%** — 요구: {', '.join(expected)} / "
        f"구현: {', '.join(implemented) or '없음'}. {status}\n\n"
    )


def coverage_gate(run_dir: Path, route: dict[str, Any], missing: list[str]) -> int:
    """미구현 레이어가 있을 때 forced 정지. coverage_status.json 기록 + stdout 핸드오프.

    승인/재개로 우회 불가한 forced 게이트 — 라우팅이 요구한 레이어가 실제로 안 돌았다는 건
    진짜 버그 신호이므로 사람이 입력/요청을 손봐야 한다(resume_command을 제공하지 않는다).
    """
    expected = [layer["task_type"] for layer in (route.get("layers") or [])]
    missing_set = set(missing)
    implemented = [task_type for task_type in expected if task_type not in missing_set]
    reason = f"라우팅이 요구한 레이어 중 미구현: {', '.join(missing)}. 조용한 누락 대신 게이트에서 정지."
    write_json(
        run_dir / "coverage_status.json",
        {
            "status": "blocked",
            "kind": "layer_coverage",
            "expected": expected,
            "implemented": implemented,
            "missing": missing,
            "reason": reason,
            "run_dir": str(run_dir),
        },
    )
    write_text(
        run_dir / "final_report.md",
        "# Layer Coverage Blocked\n\n"
        f"{reason}\n\n"
        f"- 요구 레이어: {', '.join(expected)}\n"
        f"- 구현됨: {', '.join(implemented) or '없음'}\n"
        f"- 미구현: {', '.join(missing)}\n",
    )
    print("ROUTED_STATUS: blocked_layer_coverage")
    print(f"RUN_DIR: {run_dir}")
    print(f"Routed run blocked on layer coverage: {run_dir}")
    return 0
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_coverage_gate.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: 커밋**

```bash
git add autoagent/workflows/routed_common.py tests/test_coverage_gate.py
git commit -m "feat(routed): 레이어 커버리지 프리미티브(missing_layers/banner/gate)"
```

---

### Task 4: `run_implementation_route` 레이어 루프 + 게이트·리포트 배너 통합

**Files:**
- Modify: `autoagent/workflows/routed_impl.py` (run_implementation_route를 레이어 루프로; import 추가)
- Test: dry-run(순수함수 유닛은 Task1/3에서 커버; 여기선 오케스트레이션 배선 검증)

**Interfaces:**
- Consumes: 기존 `run_impl_review_fix(*, args, config, common, route, request, budget, run_dir) -> tuple[str, str, str, bool, bool]` (routed_impl.py:22, **무변경** — 레이어별 effective route/common을 넘겨 그대로 재사용). Task 3의 `missing_layers`/`coverage_banner_md`/`coverage_gate`.
- Produces: `run_implementation_route`가 `route["layers"]`를 순차 순회.

- [ ] **Step 1: import 추가** — `autoagent/workflows/routed_impl.py:19`

```python
from autoagent.workflows.routed_common import (
    coverage_banner_md,
    coverage_gate,
    missing_layers,
    run_evaluation,
    run_final_report,
    stop_after,
)
```

또한 파일 상단 import 블록에 `import json`을 추가한다(routed_impl.py에 아직 없으면).

- [ ] **Step 2: `run_implementation_route` 교체** — routed_impl.py:112-195 전체를 아래로 대체

```python
def run_implementation_route(
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    route: dict[str, Any],
    request: str,
    budget: AgentCallBudget,
    run_dir: Path,
) -> int:
    """route["layers"]를 순차 순회하며 레이어별 impl→리뷰/수정 사이클을 돌린다.

    각 레이어는 effective route(task_type/subtype/risk/agents를 레이어값으로)와 layer_common
    (TASK_TYPE/ROUTE_JSON을 레이어값으로)으로 기존 run_impl_review_fix를 그대로 태운다.
    루프 후 forced 커버리지 게이트로 누락을 차단하고, 검증/최종리뷰/평가/보고는 1회만 수행한다.
    단일 레이어면 집계가 항등이라 기존 동작과 바이트 동형이다.
    """
    layers = route.get("layers") or []
    raw_impls: list[str] = []
    implemented: list[str] = []
    last_review = "Review skipped (max_review_rounds=0)."
    last_fix = "No fix step was run."
    all_resolved = True

    for layer in layers:
        # 레이어별 effective route: task_type/subtype/risk/agents를 이 레이어값으로 덮는다.
        effective_route = {
            **route,
            "task_type": layer["task_type"],
            "subtype": layer["subtype"],
            "risk_level": layer["risk_level"],
            "implementation_agent": layer["implementation_agent"],
            "review_agent": layer["review_agent"],
        }
        # 프롬프트 값도 이 레이어에 맞춘다(TASK_TYPE/ROUTE_JSON이 프롬프트 본문에 노출될 수 있음).
        layer_common = {
            **common,
            "TASK_TYPE": layer["task_type"],
            "ROUTE_JSON": json.dumps(effective_route, ensure_ascii=False, indent=2),
        }
        implementation, review, fix, resolved, stopped = run_impl_review_fix(
            args=args,
            config=config,
            common=layer_common,
            route=effective_route,
            request=request,
            budget=budget,
            run_dir=run_dir,
        )
        if stopped:
            return 0
        raw_impls.append(implementation)
        implemented.append(layer["task_type"])
        last_review, last_fix = review, fix
        all_resolved = all_resolved and resolved
        write_text(
            run_dir / f"review_loop_status_{layer['task_type']}.md",
            f"layer: {layer['task_type']}\n"
            f"resolved: {str(resolved).lower()}\n"
            f"rounds_configured: {max(args.max_review_rounds, 0)}\n",
        )

    # forced 커버리지 게이트: 요구한 레이어가 모두 구현됐는가.
    missing = missing_layers(route, implemented)
    if missing:
        return coverage_gate(run_dir, route, missing)

    # 집계 구현본. 단일 레이어면 항등(바이트 동형), 다중이면 레이어 헤더로 결합.
    if len(raw_impls) == 1:
        implementation = raw_impls[0]
    else:
        implementation = "\n\n---\n\n".join(
            f"## Layer: {task_type}\n\n{body}" for task_type, body in zip(implemented, raw_impls)
        )
    review, fix = last_review, last_fix

    # 1단계 검증 스테이지(구현/수정 뒤, 최종리뷰 전).
    implementation = _maybe_run_verification(args, config, run_dir, implementation)
    if stop_after(args, run_dir, "verification"):
        return 0

    write_text(
        run_dir / "review_loop_status.md",
        f"resolved: {str(all_resolved).lower()}\n"
        f"rounds_configured: {max(args.max_review_rounds, 0)}\n",
    )

    final_review = run_final_review(
        args=args,
        config=config,
        common=common,
        route=route,
        request=request,
        budget=budget,
        run_dir=run_dir,
        implementation=implementation,
        review=review,
        fix=fix,
    )
    if stop_after(args, run_dir, "final-review"):
        return 0

    evaluation = run_evaluation(
        args, config, common, budget, run_dir,
        name="08_codex_evaluation",
        implementation=implementation, review=review, fix=fix, final_review=final_review,
    )
    if stop_after(args, run_dir, "evaluation"):
        return 0

    final = run_final_report(
        args, config, common, budget, run_dir,
        name="09_claude_final_report",
        implementation=implementation, review=review, fix=fix,
        final_review=final_review, evaluation=evaluation,
    )
    # 리포트 커버리지 배너를 코드측에서 prepend(공유 템플릿 무변경).
    write_text(run_dir / "final_report.md", coverage_banner_md(route, implemented) + final)
    stop_after(args, run_dir, "report")
    print(f"Routed run complete: {run_dir}")
    return 0
```

주의: `run_impl_review_fix`·`run_final_review`·`_maybe_run_verification`·`run_role_step`·`command_for_agent`는 **변경하지 않는다**. `run_final_review`/`run_evaluation`/`run_final_report`에는 주 `common`(주 task_type)을 넘긴다 — 집계 리포트라 주 레이어 기준이 맞다.

- [ ] **Step 3: dry-run — 멀티레이어 두 레이어 렌더 확인**

Run: `python .\run.py --dry-run --workflow routed --task-type auto --request "add a FastAPI endpoint and build a React page component"`
Expected: exit 0. run_dir에 다음 프롬프트 아티팩트가 모두 존재:
- `04_codex_backend_impl_prompt.md` **및** `04_codex_frontend_impl_prompt.md`
- `05_claude_backend_review_r1_prompt.md` 및 `05_claude_frontend_review_r1_prompt.md`(max_review_rounds>0일 때)
- `09_claude_final_report_prompt.md`
- `final_report.md` 상단에 "레이어 커버리지 100%" 배너.
- `coverage_status.json`은 **없음**(누락 없을 때).

확인: `python -c "import glob,os; d=sorted(glob.glob('runs/**/04_*_impl_prompt.md',recursive=True)); print(d[-2:])"` 로 backend·frontend 두 impl 프롬프트가 찍히는지 본다(경로는 projects/<name>/runs일 수 있음).

- [ ] **Step 4: dry-run — 단일 backend 바이트 동형 확인**

Run: `python .\run.py --dry-run --workflow routed --task-type backend --request "add a database migration"`
Expected: exit 0. `04_codex_backend_impl_prompt.md` 하나만(프론트 없음). `final_report.md`에 "레이어 커버리지 100% — 요구: backend" 배너. `implementation` 집계가 항등이라 최종리뷰/평가/보고 입력이 기존과 동일.

- [ ] **Step 5: dry-run 전체 스모크(회귀 없음)**

Run: `python .\run.py --dry-run --workflow routed --task-type docs --request "update the readme"`
Expected: exit 0, docs 라우트로 진입(`layers==[]`이라 구현 라우트 안 탐), 기존과 동일.

- [ ] **Step 6: 커밋**

```bash
git add autoagent/workflows/routed_impl.py
git commit -m "feat(routed): 구현 라우트 레이어 루프 + forced 커버리지 게이트 + 리포트 배너"
```

---

## Self-Review (플랜 작성자 수행 완료)

**1. Spec coverage:**
- §1 build_layers → Task 1. §2 route_task 확장 → Task 2. §3 routed.py 분기 → Task 2 Step 4.
- §4 레이어 루프 → Task 4. §5 coverage_gate → Task 3 + Task 4 통합. §6 리포트 prepend → Task 4 Step 2.
- 하위호환 체크리스트(단일 backend/frontend/docs/승인/resume) → Task 2·4의 dry-run 스텝으로 커버. resume는 checkpoint의 route(layers 포함)를 그대로 읽어 무변경 동작 — Task 4가 run_implementation_route만 바꾸므로 resume 경로(routed.py:136 동일 함수 호출)도 자동 반영.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드 포함. "적절히 처리" 류 없음.

**3. Type consistency:** `build_layers` 시그니처(chosen, scores, lowered, db_score, high_risk_score, requested_implementer)가 Task 1 정의와 Task 2 호출부 일치. `route["layers"]` 원소 키 5개(task_type/subtype/risk_level/implementation_agent/review_agent)가 build_layers 생산·run_implementation_route 소비에서 일치. `missing_layers`/`coverage_banner_md`/`coverage_gate` 시그니처가 Task 3 정의와 Task 4 호출 일치.

**주의(구현자 유의):** `run_impl_review_fix`는 절대 수정하지 않는다 — effective route/common 주입만으로 레이어별 동작을 얻는다. `scores`는 route_task의 auto 분기에서만 정의되므로 명시 분기에서 참조 금지.

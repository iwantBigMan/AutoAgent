# routed 멀티레이어 구현 설계 (backend+frontend 동시 구현)

> **작성일:** 2026-08-03
> **범위:** `routed` 워크플로우 한정. `decompose`는 이미 노드별 멀티레이어라 무변경.

## 배경 / 문제

`routed` 워크플로우의 라우팅(`autoagent/routing.py` `route_task`)은 backend/frontend/docs
점수 중 **하나만** 뽑는 winner-take-all이다(`chosen = max(scores, key=scores.get)`,
routing.py:154). 게다가 db_score>0 또는 high_risk_score>0이면 `chosen`을 **통째로
backend로 하드 오버라이드**하고 risk를 high로 올린다(routing.py:184, 188, 207).

그 결과 한 요청이 backend와 frontend를 모두 요구해도:
- 단일 `task_type`만 결정되고,
- 구현 단계(`routed_impl.py`)는 그 단일 `task_type` 프롬프트 한 벌만 렌더/실행하며
  (`04_{agent}_{task_type}_impl`, routed_impl.py:49),
- **특히 high-risk/DB가 뜨면 무조건 backend로 붕괴 → frontend 구현이 통째로 누락**된다.

실제 런에서 라우팅이 backend로 잡혀 Codex가 백엔드만 구현하고 프론트는 계획만 남았으며,
최종 리뷰가 이를 BLOCKING 회귀로 정확히 지적했다. 최종 리뷰까지 가서야 잡히는 것도 문제다.

## 목표

1. 한 요청이 backend+frontend를 걸치면 **두 레이어를 모두 구현**한다.
2. high-risk/DB 신호가 떠도 **레이어 집합을 축소하지 않는다**(백만 구현 방지).
3. 어떤 이유로든 레이어가 누락되면 **최종 리뷰가 아니라 게이트에서** 막는다(forced).
4. 하위호환: 기존 단일-레이어 라우팅·docs/review 라우트·승인 게이트·resume는 그대로 동작.

## 비목표 (YAGNI)

- 의미기반(architect 선언) 레이어 검출 — 이번엔 키워드 임계만. 추후 확장 여지로 남김.
- decompose 경로 변경 — 이미 노드별 route 파생으로 멀티레이어를 올바르게 처리함.
- 레이어 병렬 구현 — 순차(backend→frontend)로 충분. 병렬은 decompose의 몫.
- 3개 이상 레이어 — 코드 레이어는 backend/frontend 둘뿐(프롬프트도 그 둘만 존재).

## 결정 사항 (확정)

| 축 | 결정 |
|---|---|
| 멀티레이어 판정 | **키워드 임계 기반**. 라우팅 코드가 레이어 집합을 결정(결정론). |
| high-risk/DB 오버라이드 | **레이어별 risk**. 집합은 축소 금지, 해당 레이어 risk만 상향. |
| 커버리지 게이트 | **forced(강)** — 미구현 레이어 있으면 승인으로도 auto-pass 불가한 정지 + 리포트에도 커버리지 표기 병행. |
| 게이트 시점 | **impl 루프 직후**(review/eval 전). |
| 루프 구조 | **레이어별 완전 사이클(impl→review⇄fix) 순차**, backend→frontend. eval/report는 합쳐 1회. |

## 아키텍처

### 유닛 경계

| 유닛 | 책임 | 의존 |
|---|---|---|
| `routing.build_layers` (신규 순수함수) | 기존 점수/오버라이드 신호 → 순서 있는 레이어 서브라우트 리스트 | 없음(순수·결정론) |
| `routing.route_task` (확장) | 기존 반환 dict에 `layers` 키 추가(기존 키 불변) | `build_layers` |
| `routed.run_routed_workflow` (수정) | 코드-레이어 분기를 `route["layers"]` 기반으로 | route |
| `routed_impl.run_implementation_route` (수정) | `route["layers"]` 순차 순회 + 레이어별 사이클 + 커버리지 게이트 호출 + eval/report 1회 | route, 게이트 |
| `routed_common.coverage_gate` (신규) | 기대 레이어 vs 구현된 레이어 대조 → forced 정지 판정 | route |
| `routed_impl`(리포트 커버리지 prepend) | `final_report.md`에 레이어 커버리지 배너를 코드측에서 prepend | — |

### 1. `build_layers` — 레이어 검출 (순수·결정론)

```python
def build_layers(
    chosen: str,               # 기존 route_task가 계산한 주 레이어
    scores: dict[str, int],    # {"backend": int, "frontend": int, "docs": int}
    db_score: int,
    high_risk_score: int,
) -> list[dict]:
    """주 레이어 위에 부 코드 레이어를 얹어 순서 있는 서브라우트 리스트를 만든다.

    - docs/review(chosen이 코드 아님) → [] (구현 스텝 없음).
    - 코드 레이어면 집합 = {chosen} ∪ {backend if backend_score>=1} ∪ {frontend if frontend_score>=2}.
    - 임계: backend >= 1, frontend >= 2 (routing.py:174 anti-flip 규칙과 동일).
    - **축소 금지는 재추가로 달성**: route_task의 db override가 chosen을 backend로 바꿔도(기존 동작
      유지) scores.frontend는 그대로라 frontend가 >=2면 여기서 다시 들어온다 → 프론트 복구.
      backend 강제 포함(force-add)은 두지 않는다 — high_risk_score는 chosen을 바꾸지 않으므로
      순수-프론트 요청(예: "React payment page")에 허깨비 backend 레이어를 만들지 않기 위함.
    - 순서: 항상 backend 먼저, frontend 나중.
    - 각 원소: {task_type, subtype, risk_level, implementation_agent, review_agent}.
      subtype/risk는 레이어별 `_layer_subtype_risk`로 계산(backend는 기존 subtype 로직 + high_risk/DB면
      risk=high, frontend는 항상 ui/medium). → high-risk/DB는 backend 레이어의 risk만 올린다.
    """
```

**불변식:**
- 반환 리스트가 비지 않으면 첫 원소는 항상 backend가 될 수 있으면 backend(순서 고정).
- 어떤 신호도 집합을 축소하지 않는다(오버라이드는 추가/상향만).
- `chosen`이 docs/review면 항상 `[]`.
- 단일 코드 레이어 요청은 정확히 `[chosen 레이어]` 하나 → **기존 동작과 바이트 동형**.

**직교하는 공유 안전망(무변경):** `resolve_role`의 `_is_high_risk`는 route.risk_level/subtype
**외에** 요청 텍스트의 `HIGH_RISK_REQUEST_TERMS`(migration/auth/payment/production/backfill/rollback)도
스캔한다(`routed_common.is_high_risk`, 승인 게이트와 공유). 따라서 db_score/high_risk_score 축(라우팅
오버라이드)은 이 설계로 backend 레이어에만 국한되지만, **요청 문장 자체에 저 6개 단어가 있으면** frontend
레이어도 함께 deep로 승격될 수 있다. 이는 의도된 기존 안전망이라 이번 범위에서 변경하지 않는다(같은
함수를 승인 게이트도 씀). 이번 설계가 고치는 것은 "라우팅 오버라이드로 인한 프론트 실종/승격"이다.

### 2. `route_task` 확장

기존 반환 dict의 모든 키(`task_type`, `subtype`, `confidence`, `risk_level`,
`implementation_agent`, `review_agent`, ...)는 **그대로 유지**한다(주 레이어 기준).
`route["layers"] = build_layers(...)`만 추가한다. checkpoint.json이 route를 JSON으로
round-trip하므로 `layers`는 JSON 직렬화 가능(dict 리스트)이어야 한다 — 만족.

### 3. `routed.py` 분기 수정

- `routed.py:58` docs/review·read-only 분기는 그대로(주 `task_type` 기준).
- `routed.py:71`의 `if route["task_type"] in {"backend","frontend"}:` →
  **`if route["layers"]:`** 로 바꿔 코드 레이어가 하나라도 있으면 구현 라우트 진입.
- `resume_routed_workflow`는 checkpoint에서 route를 그대로 읽어 `run_implementation_route`를
  호출하므로 **추가 수정 불필요**(route에 layers가 이미 실려 있음).

### 4. `routed_impl.run_implementation_route` — 레이어 루프

```
implemented = []
for layer in route["layers"]:            # backend → frontend 순
    impl  = 렌더/실행 04_{layer.impl_agent}_{layer.task_type}_impl
    for r in review rounds:              # 기존 review⇄fix 루프 그대로
        review = 05_{layer.review_agent}_{layer.task_type}_review
        if 통과: break
        fix    = 06_{layer.impl_agent}_{layer.task_type}_fix
    implemented.append(layer.task_type)

# forced 커버리지 게이트 (routed_common)
missing = [L.task_type for L in route["layers"] if L.task_type not in implemented]
if missing: return coverage_gate(run_dir, route, missing)   # forced 정지

# eval/report: 두 레이어 합쳐 1회 (기존 final 단계 재사용)
```

- 아티팩트 이름에 레이어 task_type이 이미 들어가므로(`04_codex_backend_impl` vs
  `04_codex_frontend_impl`) 레이어 간 파일 충돌 없음.
- 각 레이어의 `implementation_agent`/`review_agent`는 해당 서브라우트에서 취한다
  (backend=codex/claude, frontend=codex/claude — 교차모델 계약 레이어마다 성립).
- budget(`AgentCallBudgetStopped`)은 루프 어느 지점에서든 기존처럼 graceful 종료.

### 5. `routed_common.coverage_gate` (신규)

리서치 `pause_at_gate`(gates.py)와 동형의 forced 정지:
- 신규 산출물 `coverage_status.json`에 `status="blocked"`, `kind="layer_coverage"`,
  `expected`(route.layers의 task_type 리스트), `implemented`, `missing`를 기록한다.
  이는 승인-재개형 `checkpoint.json`과 별개다(auto-pass·재개로 우회 불가).
- stdout에 `RUN_DIR`/사유(어느 레이어가 빠졌나)/재개 불가 사유를 명시.
- 승인으로 auto-pass 불가 — 라우팅이 요구한 레이어가 실제로 안 돌았다는 건 진짜 버그
  신호이므로 사람이 입력/요청을 손봐야 한다.

### 6. 리포트 커버리지 표기

**공유 프롬프트 템플릿은 건드리지 않는다.** `claude_final.md`/`codex_final.md`는 decompose
실행기(`task_exec`)도 렌더하므로 새 `{LAYER_COVERAGE}` placeholder를 넣으면 그쪽에서 KeyError가
난다. 대신 research의 커버리지 prepend와 동형으로 **코드측에서** 처리한다:
- `run_implementation_route`가 "요청이 요구한 레이어(route.layers) vs 구현된 레이어" 요약
  마크다운 배너를 만들어 모델이 낸 `final` 앞에 prepend한 뒤 `final_report.md`로 쓴다.
- 게이트를 통과했으면 100%(모든 레이어 구현됨) 배너, 아니면 애초에 게이트에서 정지했으므로
  final 단계에 도달하지 않는다.
- dry-run에서도 prepend가 적용되어 `final_report.md`에 배너가 보인다(검증 가능).

## 데이터 흐름

```
route_task(request)
  → scores 계산(기존) → chosen(주 레이어, 기존)
  → build_layers(chosen, scores, db_score, high_risk_score) → route["layers"]
route.json 저장 (layers 포함)
  → preamble(context/architect/validation, 1회 — 레이어 공통)
  → 승인 게이트(기존)
  → run_implementation_route:
       for layer in layers: impl→review⇄fix
       coverage_gate(기대 vs 구현)   ── 미구현 있으면 forced 정지 ──▶ 종료
       eval/report(합산 1회) + 커버리지 표기
```

## 에러 처리

- **라우팅 과대검출**(실은 backend-only인데 frontend 오검출): frontend 임계 ≥2로 보수적
  차단. 그래도 오검출되면 커버리지 게이트가 forced 정지 → 사람이 판단(조용한 누락보다 안전).
- **레이어 프롬프트 부재**: backend/frontend 프롬프트는 모두 실재(`prompts/routed/{layer}/`).
  build_layers는 이 둘만 낸다 → 렌더 실패 없음.
- **budget 소진**: 기존 `AgentCallBudgetStopped` graceful 종료 유지(루프 중간 정지 허용).
- **비코드 요청**(docs/review): `layers == []` → 구현 라우트 진입 안 함(기존 docs 경로).

## 테스트 전략

- **`build_layers` 단위테스트(신규·결정론)** — routed는 원래 유닛테스트가 없지만 이 순수
  함수는 research의 "결정론 코드조각" 철학대로 pytest로 못박는다:
  1. 단일 backend 요청 → `["backend"]` (기존 동형)
  2. 단일 frontend 요청(frontend≥2) → `["frontend"]`
  3. backend+frontend 요청 → `["backend","frontend"]`(순서 고정)
  4. high-risk + backend+frontend → 집합 보존 & backend.risk=high & frontend.risk=medium
  5. frontend 단발 키워드(≥1, <2) → 집합에 frontend 미추가(오검출 방지)
  6. docs/review chosen → `[]`
- **dry-run 회로 검증** — `python run.py --dry-run --workflow routed --task-type auto
  --request "<백+프론트 요청>"` 로 두 레이어의 impl/review/fix 프롬프트·command 아티팩트가
  모두 렌더되고, 레이어 누락 시 커버리지 게이트가 forced 정지로 분기하는지 확인.
- **라이브 모델 런은 사용자 인계**(기존 관례, routed 라이브 미실증).

## 하위호환 체크리스트

- [ ] 단일 backend 요청: `layers==["backend"]`, 기존과 동일하게 1회 구현.
- [ ] 단일 frontend 요청: `layers==["frontend"]`, 동일.
- [ ] docs/review/read-only: `layers==[]`, 기존 docs 경로 그대로.
- [ ] 승인 게이트: 주 `task_type` 기준 판정 불변.
- [ ] resume: checkpoint의 route(layers 포함)로 구현 단계 재진입 — 코드 변경 없이 동작.

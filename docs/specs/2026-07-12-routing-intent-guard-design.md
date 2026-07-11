# 라우팅 의도 가드 설계

> 작성일: 2026-07-12 · 상태: 설계(승인 대기)

## 목표

`auto` 라우팅이 **구현 요청을 docs로 오분류**하는 문제를 없앤다. 구현 의도
동사가 있으면 명사 키워드가 docs를 가리켜도 backend/frontend 구현 라우트로
보낸다.

## 배경 — 현재 오분류

`autoagent/routing.py`의 `route_task` auto 분기는 **순수 명사 키워드 점수**다.

```python
docs_terms = ["readme","docs","document","spec","architecture","risk","review","planning","plan"]
scores = {"backend":.., "frontend":.., "docs":..}
chosen = max(scores, key=scores.get)
if scores[chosen] == 0:
    chosen = "docs"                      # 신호 0 → 무조건 docs(read-only no-op)
```

- 실제 사례: 요청 `"docs/specs/....md 설계대로 구현하라"` → 문자열에 `docs`·`spec`이
  박혀 docs=2로 최고점. **"구현하라"는 점수에 안 잡힘** → docs 라우트(구현 스텝
  없음) → **코드 0줄 변경, 그런데 exit 0 + 보고서까지** 만들어 성공처럼 보임.
- 근본: 라우터에 **동사/의도 인식이 없다.** 구현은 동사(구현/implement/추가/수정)로
  특징지어지는데 명사만 센다.

## 핵심 결정 (승인됨)

- 스코프 = **(A) 타깃 의도 가드만.** cheap 키워드 라우팅 철학은 유지하고 의도
  레이어만 얹는다. 라우팅 재구조화·LLM 라우팅·`/aa` 경고(B)는 범위 밖.

## 설계

### 바꾸는 곳

`autoagent/routing.py`의 `route_task` **auto 분기만.** 명시 지정
(`task_type != "auto"`, 예: `--task-type backend`)은 그대로 — 탈출구 유지.

### 의도 감지 (`IMPL_INTENT`)

현재 라우터의 취약점인 **무분별 substring 매칭을 답습하지 않는다**("add"가
"address"에, "fix"가 "prefix"에 걸리는 오염 방지).

- **한국어 동사** — substring 매칭(조사가 붙어도 안전: "구현하라/구현해줘"의 "구현").
  시작 세트: `구현, 수정, 추가, 삭제, 제거, 교체, 리팩터, 리팩토링, 반영, 만들, 고쳐, 바꿔, 통합`
- **영어 동사** — `\b` 단어경계 정규식(오염 방지). 시작 세트:
  `implement, refactor, rewrite, integrate, fix, add, remove, build, create, rename, wire`
- **의도적으로 제외**(docs 쪽으로 남겨야 하는 것): `update/업데이트`, `작성/write`,
  `리뷰/review`, `문서/document`. (이들은 문서·리뷰 요청의 신호라 override 트리거로
  쓰면 안 됨.)
- 구현: `import re` 추가. `ko = [t for t in KO_INTENT if t in lowered]`,
  `en = re.findall(EN_INTENT_PATTERN, lowered)`, `impl_intent = ko + en`.

### 오버라이드 규칙

점수/`chosen` 계산 직후, **db_score·high_risk 오버라이드보다 앞에서**:

```python
if chosen == "docs" and impl_intent:
    # 명사가 docs를 가리켜도 구현 의도가 있으면 구현 라우트로 되돌린다.
    # 파일명 속 'design'(-design.md) 같은 단발 프론트 키워드가 backend=0을 이겨
    # 도메인을 뒤집지 않도록, frontend는 신호 2개 이상일 때만 택한다.
    if scores["frontend"] >= 2 and scores["frontend"] > scores["backend"]:
        chosen = "frontend"
    else:
        chosen = "backend"
    confidence = 0.6
    reason = f"Implementation intent overrode docs routing ({len(impl_intent)} intent keyword(s)); scores {scores}."
```

- `chosen`이 이미 backend/frontend면 건드리지 않는다(오분류는 docs일 때만).
- 이후 기존 `db_score>0 → backend+db+high`, `high_risk_score>0 → high` 블록이 그대로
  이어져 강화한다.
- backend/frontend 결정: frontend 신호가 **2개 이상**이고 backend보다 높을 때만
  frontend, 그 외에는 backend(구현 기본). 이유: `...-design.md` 스펙을 참조하는 구현
  요청에서 파일명의 `design` 하나가 frontend=1을 만들어 backend=0을 이기는 오분류를
  막기 위함(검증에서 실제 발견해 규칙을 보강함).

### 안전 비대칭 (설계 근거)

- **과소분류(구현→docs)** = read-only no-op → **출력 없음**(치명적: `/aa`의 존재 이유
  위반).
- **과대분류(문서편집→backend)** = 무거운 파이프라인이지만 **출력은 맞음**(README를
  backend 라우트로 편집 — 코드 리뷰어가 붙는 정도의 비효율).

→ 애매하면 **구현 라우트 쪽으로 편향**이 안전하다. 그래서 `추가/add` 같은 광의
동사도 포함한다.

## 데이터 흐름 / 예시

| 요청 | 현재 | 개선 후 |
|---|---|---|
| `"docs/specs/x.md 설계대로 구현하라"` | docs (no-op) | **backend** |
| `"docs/specs/...-design.md 설계대로 구현하라"` | docs (no-op) | **backend** (파일명 `design`에도 불구) |
| `"config.py에 --project 인자 추가"` | docs (신호0 기본) | **backend** |
| `"UI 컴포넌트 구현"` | frontend | frontend (불변) |
| `"README 업데이트"` | docs | docs (불변 — update 제외) |
| `"이 PR 리뷰해줘"` | docs | docs (불변 — review 제외) |
| `"API 엔드포인트 추가"` | backend | backend (불변) |

## 하위호환 / 회귀

- 명시 `--task-type`은 전혀 영향 없음(auto 분기 밖).
- `chosen`이 backend/frontend/`docs(의도 없음)`인 경우 결과 불변.
- `db_score`/`high_risk_score` 오버라이드 순서·결과 불변.
- route dict의 필드 구성 불변(값만 달라질 수 있음). `reason`으로 override 추적 가능.

## 에러 / 엣지 (잔여 모호성, 수용)

- **문서 편집 요청이 광의 동사를 쓰면** backend로 과대분류될 수 있음
  (예: `"README에 섹션 추가"` → `추가` 트리거). 출력은 맞고(README 편집됨) 경로만
  무거움. 필요 시 `--task-type docs`로 강제. (안전 비대칭상 허용.)
- `"구현 계획 문서를 작성해줘"`처럼 진짜 docs인데 `구현`이 섞인 경우도 backend로 갈
  수 있음. 드묾 + 탈출구 있음.

## 범위 밖 (YAGNI)

- (B) 조용한 no-op 방지용 `/aa` 경고·게이트.
- 라우팅 재구조화(동사-우선 2단계)·LLM 라우팅.
- 명사 키워드 리스트 확장(`config`, `.py` 등) — 이번엔 의도 레이어만.

## 검증 (테스트 스위트 없음 → 순수함수 표 체크)

`route_task`는 순수 함수라 풀 워크플로 없이 직접 호출해 확인한다.

```python
from autoagent.routing import route_task
cases = [
    ("docs/specs/x.md 설계대로 구현하라", "backend"),   # 교정 대상
    ("docs/specs/...-design.md 설계대로 구현하라", "backend"),  # 파일명 design에도 backend
    ("config.py에 --project 인자 추가", "backend"),
    ("UI 컴포넌트 구현", "frontend"),
    ("README 업데이트", "docs"),                          # 회귀 없음
    ("이 PR 리뷰해줘", "docs"),
    ("API 엔드포인트 추가", "backend"),
    ("그냥 인사", "docs"),                                 # 신호0+의도0 → 안전 기본
    # docs가 최고점이어도 frontend 신호 2+면 오버라이드로 frontend 도달
    ("readme spec docs architecture css layout page 구현", "frontend"),
]
for req, want in cases:
    got = route_task("auto", req)["task_type"]
    assert got == want, f"{req!r}: got {got}, want {want}"
```

전 케이스 통과 + 기존 backend/frontend 요청 회귀 없음을 확인한다.

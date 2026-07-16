# 역할별 model+effort 선언 티어 팔레트 (설계)

- 날짜: 2026-07-16
- 상태: 설계 승인됨(구현 계획 대기)
- 범위: 서브프로젝트 **A** (기반). B(난이도 자동선택)·C(역할 세분화·신규 에이전트)는 별도 사이클.
- 기준선 의존성: **PR #14(Codex effort 실주입) 병합 후 상태**를 "현행 동작"으로 삼는다.
  #14 이전(main)에는 codex effort가 CLI에 주입되지 않으므로, A는 #14를 전제로 한다.

## 배경 / 문제

지금 역할 레지스트리(`roles.default.json`)는 모델·강도를 두 개의 거친 추상으로만
표현한다: `model_tier`(standard/tiered), `effort`(none/standard/tiered). 실제 값은
`resolve_role`이 config 전역값(claude_model/high_risk_model/effort, codex_model/effort)에서
뽑는다. 즉 사실상 **"평상시 vs high-risk" 2단계**가 전부다.

그러나 모델 팔레트는 다층이 됐다 — Codex Sol/Terra/Luna, Claude Fable5/Opus/Sonnet/Haiku,
effort low~max. 현행 구조로는 이 팔레트를 거의 못 쓴다. 예를 들어 순수 요약 작업인
`report`(Claude)에 저비용 Haiku를, 게이트성 codex 역할에 Terra를 배정하는 식의
역할별 세밀 튜닝이 불가능하다.

## 목표 / 비목표

**목표**
- 각 역할이 **평상시/high-risk 슬롯마다 구체 모델+effort를 팔레트에서 선택**할 수 있게 한다.
- route 역할(implementer/reviewer/fix)은 해석된 agent(claude/codex)별로 자동 해결한다.
- 팔레트 값은 config에 두어 operator가 코드 수정 없이 튜닝하게 한다(구조=roles / 값=config 분리 유지).
- B·C가 얹힐 확장 지점(티어명 선택 정책, 신규 역할의 티어 참조)을 명확히 남긴다.

**비목표 (A에서 하지 않음)**
- 실제 재튜닝(모델 배치 변경). A는 **메커니즘만**, 동작은 현행과 바이트 동일하게 보존한다.
- 난이도 기반 자동 티어 선택(B).
- 역할 세분화·신규 파이프라인 역할(C).
- `simple`/`decompose` 워크플로우 — 역할 레지스트리를 쓰지 않으므로 스코프 밖.

## 설계

### 1. config: agent별 티어 팔레트

`Config`에 `tiers: dict[str, dict[str, dict[str, Any]]]` 추가 (agent → 티어명 → {model, effort}).

기본 팔레트는 **기존 전역값에서 합성**하고, config의 `tiers`가 있으면 그 위에 필드 단위로
덮는다(deep-merge). → config를 안 바꿔도 현행과 동일하고, 기존 `claude_model` 등 노브도
계속 유효하다.

```python
# load_config 내부, 기존 globals 로드 뒤
default_tiers = {
    "claude": {
        "standard": {"model": claude_model,          "effort": claude_effort},          # sonnet / high
        "deep":     {"model": claude_high_risk_model, "effort": claude_high_risk_effort}, # opus / xhigh
        "light":    {"model": claude_model,           "effort": None},                    # sonnet / (effort 플래그 없음)
        # 아래는 재튜닝/B 대비 "정의만" — A default 매핑에서는 아무 역할도 참조하지 않음.
        "cheap":    {"model": "haiku",                "effort": None},
    },
    "codex": {
        "standard": {"model": codex_model, "effort": codex_reasoning_effort},  # gpt-5.6-sol / medium
        "deep":     {"model": codex_model, "effort": codex_high_risk_effort},  # gpt-5.6-sol / high
        # 정의만:
        "cheap":    {"model": "gpt-5.6-terra", "effort": "low"},
    },
}
tiers = _merge_tiers(default_tiers, raw.get("tiers") or {})
```

`_merge_tiers`는 (agent, 티어) 단위로 순회하며, config가 제공한 티어 dict의 필드만
기본값 위에 덮는다(부분 override 허용: effort만 바꾸는 것도 가능). config에만 있는
새 티어(예: 사용자 정의 `luna`)는 그대로 추가한다.

`effort: None`은 "effort 플래그를 붙이지 않음"을 뜻한다(현행 claude `effort:"none"` 재현).
codex는 #14 이후 effort가 항상 주입되므로 codex 티어는 항상 effort 문자열을 갖는다.

### 2. roles.default.json: 티어명 참조

각 역할의 `model_tier`/`effort`를 제거하고 **`tier`(필수) + `high_risk_tier`(선택)**로 교체한다.
`high_risk_condition`·`agent`·`mutating`·`permission`/`sandbox`는 그대로 둔다.

| 역할 | agent | tier | high_risk_tier | high_risk_condition |
|---|---|---|---|---|
| context | claude | `light` | — | none |
| architect | claude | `standard` | `deep` | any_high_risk |
| validation | codex | `standard` | — | none |
| implementer | route | `standard` | `deep` | backend_high_risk_mutating |
| reviewer | route | `standard` | — | none |
| fix | route | `standard` | `deep` | backend_high_risk_mutating |
| final-review | codex | `standard` | — | none |
| evaluation | codex | `standard` | — | none |
| report | claude | `light` | — | none |

### 3. resolve_role: 티어 조회로 model+effort 결정

`escalate` 판정(현행 `high_risk_condition` 로직)은 **불변**. 모델·effort 결정만 교체한다:

```python
tier_name = entry.get("high_risk_tier") if (escalate and entry.get("high_risk_tier")) else entry["tier"]
tier = config.tiers[agent][tier_name]           # agent는 이미 해석된 claude/codex
model = tier.get("model")
effort = tier.get("effort")
```

route 역할은 호출부가 넘긴 구체 agent로 `tiers[agent][tier_name]`을 찾으므로 자연히
agent별로 갈린다. `ResolvedRole`(model/effort 필드)·`command_for_agent`·runner는 변경 없음.

### 4. validate_roles: 티어 존재 검사

시작 시(현행 `validate_roles`) 각 역할이 참조하는 `tier`/`high_risk_tier`가 그 역할이 될 수
있는 **모든 agent** 아래 팔레트에 존재하는지 검사한다. `agent:"route"`면 claude·codex 둘 다,
아니면 해당 agent만. 없으면 `SystemExit`로 조기 종료(오타 방지). `high_risk_condition`이
`none`이 아닌데 `high_risk_tier`가 없으면 경고(동작은 tier로 폴백).

## 동작 보존 (핵심)

§2 표의 티어 매핑은 #14 이후 현행 `resolve_role` 출력과 **바이트 동일**하도록 설계했다:

- claude `light`={sonnet, None} → context/report의 현행 {sonnet, effort 없음}
- claude `standard`={sonnet, high} / `deep`={opus, xhigh} → architect·route(claude) 현행
- codex `standard`={gpt-5.6-sol, medium} / `deep`={gpt-5.6-sol, high} → codex 역할·route(codex) 현행
- `cheap` 티어는 **정의만** 존재하고 어떤 역할도 참조하지 않으므로 resolved 명령에 영향 없음

## 검증 계획

1. **바이트 동일성(주 검증)**: 변경 전(#14 병합본)·후로 각각 dry-run을 돌려 `runs/*/*_command.json`
   (+ `*_prompt.md`)을 비교한다. 아래 매트릭스에서 **완전 동일**해야 한다.
   - `--task-type backend`(일반) / backend DB(예: "DB migration ... unique constraint")
   - `--task-type frontend`
   - `--task-type docs --read-only`
   - 각 케이스 `--implementer claude`와 `--implementer codex`
2. **단위**: `resolve_role`를 role×(agent)×(escalate T/F)로 호출해 (model, effort)가
   §2 표와 일치하는지 확인. `validate_roles`가 없는 티어명에 대해 `SystemExit`를 내는지 확인.
3. **back-compat**: `tiers` 키가 없는 기존 config, `claude_model` 등 전역만 바꾼 config,
   `tiers` 부분 override가 있는 config 3종에서 합성 결과가 기대와 같은지 단위 확인.
4. `compileall` 통과.

## 후속 (A 이후)

- **A.1** — `report.tier`를 `light`→`cheap`(Haiku)로 변경. 동작이 바뀌므로 실런 1회로
  보고서 품질 확인(대용량 런은 Haiku 200K 초과 가능 → sonnet-light 폴백 여지 명시).
- **B** — 라우팅이 난이도/종류 신호로 티어명을 고르는 정책. 이 팔레트의 티어 슬롯을 그대로 소비.
- **C** — 구현 역할 세분화(fe/be/db)·신규 파이프라인 역할(보안검토·테스트작성·문서화). 각 역할이
  팔레트에서 자기 티어 참조.

## 리스크 / 완화

- **티어명 오타** → `validate_roles`가 시작 시 차단.
- **codex effort 미주입 환경(#14 미병합)에서 구현** → 스펙 전제로 #14 선행을 명시. #14 이전 상태를
  보존하려면 codex 티어 effort를 `None`으로 두는 변형이 필요(비권장).
- **operator config의 부분 override 오해** → deep-merge 규칙을 README 모델 정책 절에 문서화.

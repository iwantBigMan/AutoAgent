# 선언형 역할 레지스트리 설계 (Phase 1)

- 날짜: 2026-07-09
- 상태: 설계 승인 대기
- 관련: `docs/specs/2026-07-09-review-loop-and-approval-resume-design.md`, `docs/specs/2026-07-09-command-approval-resume-procedure-design.md`

## 배경 / 문제

지금 routed 워크플로우가 띄우는 9개 서브프로세스 역할(context, architect, validation, implementer, reviewer, fix, final-review, evaluation, report)은 **1급 객체가 아니다.** "역할"이란 (a) `{agent}_{task_type}_{step}.md` f-string 규칙으로 만든 프롬프트 이름, (b) `model_for_agent`/`architecture_model_for` 등이 고른 모델, (c) effort, (d) `command_for_agent`가 `mutating` 리터럴로 유도하는 권한/샌드박스, (e) Python 제어흐름상 하드코딩된 위치가 뭉쳐진 결과물이다. 모델 선택 로직이 5곳에 흩어져 있고(`routed_impl.py:224-268`, `routed_common.py:177-186`), high-risk 승격 규칙은 architect(any-high)와 impl(backend+high)에서 **중복·비대칭**이며, `config.codex_reasoning_effort`는 선언만 되고 `codex_exec_command`가 방출하지 않는다(README:206 결정에 따라 의도적으로 CLI 미주입 — 죽은 knob이 아니라 config 전용).

사용자는 하네스를 "성숙"시키고자 한다: **서브에이전트 역할을 코드 수정 없이 정의**하고, **언제 나타날지(트리거)를 상황(route/risk/키워드)에 따라 자동 결정**하며, 새 역할(security-reviewer 등)을 손쉽게 추가하고 싶다.

## 목표 / 비목표

목표
- `roles.json`(데이터)로 역할 **속성**(agent/model/effort/샌드박스/프롬프트/mutating/트리거)을 선언. 코드에 흩어진 5개 리졸버를 `resolve_role()` 하나로 통합.
- 조건 기반(자동) 트리거로 신규 8개 역할을 파이프라인의 정해진 스테이지에서 발동.
- review와 finish를 **선언된 역할 목록을 순회하는 확장 스테이지**로 일반화.
- 교차검증 불변식(리뷰어=구현자 반대 모델)을 시작 시 검증으로 보장.
- high-risk 승격 중복을 부수적으로 해소. (`codex_reasoning_effort`는 README:206 결정대로 CLI 미주입 유지 — config 저장 전용, high는 `~/.codex/config.toml`.)

비목표 (후속 단계)
- **파이프라인 재배치/삽입/새 워크플로 저작(스펙 엔진)** = Phase 2. 이번엔 스테이지 순서를 코드에 유지한다.
- **task_graph 실행기와의 통합** = Phase 2 이후.
- 결정적 오라클의 완전한 확장(모든 스테이지 continue_when) = 여기선 test-runner용 최소 형태만.

## 설계

### 1. 구조 개요

- 신설 `roles.default.json`(체크인) + `roles.json`(override, gitignore) — `autoagent.config.json`과 동일한 우선순위 규칙. 역할이 정의되지 않으면 현행 동작과 동일하게 폴백.
- `resolve_role(role_id, route, request, config)` → `ResolvedRole{agent, model, effort, prompt, mutating, sandbox_or_permission}`. 기존 `run_role_step`의 `if args.dry_run` 분기 **이전**에 계산 → dry-run과 실제 경로가 동일 command list를 얻어 `*_command.json` 동일성 보장.
- 파이프라인 뼈대는 코드 유지. 단 두 스테이지를 목록 순회로 확장: **review[]**, **finish[]**. 그리고 **preplan**(선택), **verify**(오라클) 스테이지를 추가.

### 2. 역할 엔트리 데이터 모델

```json
{
  "id": "security-reviewer",
  "stage": "review",
  "agent": "opposite-of-implementer",
  "model_tier": "standard",
  "effort": null,
  "prompt": "routed/roles/security_review.md",
  "mutating": false,
  "sandbox": "read-only",
  "trigger": { "keywords": ["auth", "secret", "token", "password", "permission", "crypto"] }
}
```

필드 의미:
- `stage`: `preplan | plan | review | verify | finish` 중 하나. 코드의 확장 스테이지가 이 값으로 역할을 모은다.
- `agent`: `claude | codex | route-implementer | opposite-of-implementer | fixed-codex`. 교차검증 관계를 값으로 선언.
- `model_tier`: `standard | high_risk` (조건은 `is_high_risk`가 판정). codex는 항상 `codex_model`.
- `effort`: 명시 값 또는 `null`(플래그 없음). high_risk 시 `claude_high_risk_effort`로 승격되는지 여부는 tier 규칙이 정함.
- `mutating`: 쓰기 여부. claude는 `mutating=false → permission-mode plan`, codex는 `sandbox` 필드 사용.
- `trigger`: 조건 객체(§6). 없으면 해당 스테이지에서 항상 발동.

### 3. 셀렉터 / 교차검증

- 구현자는 기존 `choose_implementer`(routing.py)가 그대로 결정하고 route에 `implementation_agent`/`review_agent`를 남긴다. 레지스트리는 `agent: opposite-of-implementer` 로 **관계만 참조**한다.
- 시작 시 `validate_roles()`: (a) 각 (agent×task_type) 프롬프트 파일 존재, (b) review 스테이지 역할이 구현자와 **같은 모델로 귀결되지 않는지**(교차검증 위반 거부), (c) `model_tier`/effort 토큰이 Config 필드로 해석되는지 확인.

### 4. 8개 역할 정의

| id | stage | agent | model | 트리거(자동) | mutating |
|---|---|---|---|---|---|
| research-explore | preplan | claude | standard(ro) | routing confidence 낮음 OR 요청에 조사/investigate/research 키워드 | 아니오 |
| context | plan | claude | standard | 항상 | 아니오 |
| architect | plan | claude | high_risk_when(is_high_risk) | 항상 (설계문서=승인 대상) | 아니오 |
| validation | plan | codex | standard | 항상 | 아니오 |
| implementer | (impl) | route-implementer | high_risk_when(backend&high) | 게이트 통과 후 항상(backend/frontend) | 예 |
| db-migration-specialist | (impl) | claude | high_risk(opus·xhigh) | `subtype==db` (구현자를 대체) | 예 |
| main-reviewer | review | opposite-of-implementer | (반대) | 항상 | 아니오 |
| security-reviewer | review | opposite-of-implementer | (반대) | 키워드 auth·secret·token·permission·crypto | 아니오 |
| perf-reviewer | review | opposite-of-implementer | (반대) | 키워드 query·index·loop·N+1·성능·hot-path | 아니오 |
| test-writer | finish | codex | standard | backend/service 코드 변경 & 테스트 관련 | 예(테스트) |
| test-runner | verify | codex | standard | 워크스페이스에 verify 명령 설정됨 | 명령 실행 |
| doc-updater | finish | claude | standard | 구현이 문서 있는 파일 변경(구현 후) | 예(문서) |
| refactorer-simplifier | finish | claude | standard | 리뷰가 중복/구조 지적 OR 요청 명시 | 예 |
| final-review | (final) | fixed-codex | standard | 항상 | 아니오 |
| evaluation | (final) | codex | standard | 항상 | 아니오 |
| report | (final) | claude | standard | 항상 | 아니오 |

(기존 역할 context/architect/validation/implementer/main-reviewer/final-review/evaluation/report은 역할로 "명명"만 하므로 동작 불변. 신규 8개 = research-explore, security-reviewer, perf-reviewer, test-writer, test-runner, doc-updater, refactorer-simplifier, db-migration-specialist.)

### 5. 파이프라인 순서

```
research-explore?(preplan) → context → architect(설계문서) ⇄ validation
   → [승인 게이트]
   → implementer (subtype==db 이면 db-migration-specialist가 대체)
   → review[] { main-reviewer + security-reviewer? + perf-reviewer? } ⇄ fix   (라운드 = max_review_rounds, 기본 1)
   → verify { test-runner? }        (오라클, opt-in)
   → final-review → evaluation
   → finish[] { test-writer?, doc-updater?, refactorer-simplifier? }   (구현 후)
   → report
```

- "문서작성 → 승인 → 구현" 요구 충족: 설계문서(architect)가 게이트 전, 구현은 게이트 후. doc-updater는 구현 후 finish에서 변경 반영.
- 발동된 역할만 실행 → 매 run이 무겁지 않음. review-fix 루프는 기존 예산·라운드로 보수적 유지(토큰 절약).

### 6. 트리거 모델 (조건 기반 = 자동)

- 트리거 vocabulary(작게 고정): `task_type`, `subtype`, `risk_level`, `confidence`(routing.py 산출), `read_only`, 그리고 `keywords`(요청 텍스트 부분일치, 기존 DB_TERMS/HIGH_RISK_TERMS와 동일 방식).
- 평가는 **화이트리스트 방식의 순수 함수**로만(절대 `eval()` 금지 — 주입/데이터-아님 위반).
- 지원하지 않는 조건이 필요하면 vocabulary에 코드로 추가해야 함(의도적으로 작게 유지).

### 7. 검증 오라클 (test-runner용, opt-in)

- `safety.review_needs_changes`를 named 오라클로 승격: `marker`(현행 로직 그대로, 기본·폴백) + `command`(사용자 선언 shell 명령의 exit code == 통과).
- verify 명령은 워크스페이스별 config에 선언(decompose의 미사용 `validation_commands` 재활용). 명령 없으면 `marker`로 폴백 → docs/review 라우트 안전.
- 오라클도 실제 서브프로세스이므로 `budget.before_call`을 거치고 `--dry-run`에선 placeholder.
- 실행 CLI는 명령 실행이 가능한 쪽(codex `--sandbox workspace-write`, 또는 non-plan permission의 claude)이어야 하며, read-only/plan 리뷰 역할은 오라클을 호스팅하지 않는다.

### 8. 확정 결정

1. **config 포맷 = JSON.** `roles.default.json` 체크인 + `roles.json`(gitignore) override. 신규 의존성 없음.
2. **final-review = Codex 고정 감사자(`fixed-codex`) 유지.** 교차검증 쌍과 별개의 독립 최종점검. 단 현재 `codex_sandbox_for`를 무시해 `--read-only`에서도 쓰기 가능하던 버그를 **수정**(read-only 존중).
3. **codex_reasoning_effort는 CLI에 주입하지 않음(README:206 결정 존중).** config 저장 전용이며, Codex를 high로 돌리려면 `~/.codex/config.toml`의 `model_reasoning_effort`로 설정한다. `resolve_role`는 codex effort=None을 유지(동작 불변).
4. **high-risk 승격 통일.** architect(any-high)와 impl(backend+high)의 비대칭을 tier 규칙 한 곳으로 정리하되 **각자의 기존 조건을 그대로 재현**(동작 불변).

## 검증 전략

- 프로젝트에 테스트 스위트 없음(CLAUDE.md) → **`--dry-run`의 `*_command.json` / `*_prompt.md` 바이트 동일성**이 수용 기준.
- 역할을 하나도 정의/발동하지 않은 상태에서, 변경 전후 dry-run 산출물이 task_type(backend/frontend/docs) × risk × read-only 조합에서 **완전히 동일**해야 한다(회귀 없음 증명).
- 신규 역할을 켠 뒤엔 해당 스테이지에서만 산출물이 늘어나는지 확인.
- 캐노니컬 파일명(`01_`/`02_`/`03_`, `approval_required.md`, `checkpoint.json`)은 resume/게이트가 리터럴로 참조하므로 **반드시 보존**.

## 마이그레이션 자세

- 빅뱅 교체 없음. `resolve_role`/레지스트리를 도입하되, `roles.default.json`이 **현행 동작을 그대로 인코딩**하도록 작성 → 기본 상태에서 하네스는 변하지 않는다.
- 신규 스테이지(preplan/review[]/verify/finish[])는 역할이 트리거되지 않으면 no-op.

## 후속 (별도)

- Phase 2: 파이프라인=스펙 엔진(재배치/새 워크플로 저작, `--engine` 플래그 + 골든파일 parity).
- Phase 3: 오라클을 모든 continue_when으로 확장, task_graph 실행기 통합.

## 가정 / 미해결

- research-explore의 confidence 임계값 구체값은 계획 단계에서 결정.
- perf-reviewer/refactorer 트리거 키워드 목록은 계획 단계에서 확정(작게 시작).

# AutoAgent 설계 spec: 리뷰-수정 반복 루프 + 인간검수 승인 후 재개

작성일: 2026-07-09
대상: AutoAgent `routed` 워크플로우 (`autoagent/workflows/`)
상태: 설계 확정용 초안

## 1. 배경

현재 `routed` 워크플로우는 의도했던 "3사이클 반복 + 인간검수 후 재개" 구조와 다르게 동작합니다. 코드 실측 결과는 다음과 같습니다.

- **계획 단계(`routed_preamble.py`)**: `01 context(claude) -> 02 architecture(claude) -> 03 validation(codex)` 로 **선형 1회** 실행됩니다. 리뷰->수정 반복 루프가 없습니다.
- **인간검수 게이트(`routed.py:50-51`)**: `approval_required`가 참이면 `block_for_human_approval`이 산출물을 쓰고 **프로세스를 종료**합니다. 승인 후 구현으로 **이어지는 연결(재개)이 없습니다**. 다시 실행해도 `approval_required`가 다시 참이 되어 또 멈춥니다.
- **구현 단계(`routed_impl.py`)**: `04 구현 -> 05 리뷰 -> 06 수정 -> 07 최종리뷰 -> 08 평가 -> 09 보고` 인데, **`06 수정`은 반복 루프가 아니라 조건부 1회**입니다(`routed_impl.py:64` `if args.max_review_rounds > 0 and review_needs_changes(review):`). 즉 `--max-review-rounds`는 `> 0` 여부(켜짐/꺼짐)로만 쓰여 `1`과 `3`의 동작이 같습니다.

이 spec은 두 가지를 코드에 실제로 반영하기 위한 청사진입니다.

## 2. 목표 / 비목표

**목표**
- (A) 리뷰-수정을 `max_review_rounds`만큼 **실제로 반복**하는 루프로 전환한다. 구현 단계와 계획 단계 **양쪽**에 적용한다.
- (B) 인간검수 게이트를 **체크포인트 + 재개(`--resume`)** 모델로 바꿔, 사람이 계획 산출물을 검토·승인한 뒤 **계획을 다시 돌리지 않고 구현 단계부터 이어가게** 한다.

**비목표**
- `simple`/`decompose` 워크플로우 변경(이번 범위는 `routed`만).
- 라우팅/리스크 판정 로직(`routing.py`) 변경.
- 인간검수 자체의 제거(사람은 여전히 게이트에서 검토한다). 정식 승인 경로는 **`--resume` 하나로 일원화**한다. 게이트를 건너뛰는 블랭킷 스위치(`--approve` 류)는 두지 않는다 — 그 스위치의 유일한 효과가 "반드시 승인해야 한다고 정한 high-risk/db 케이스의 승인을 생략"하는 것이라 핵심 철학과 정면으로 충돌하기 때문이다. 무인/CI 실행이 필요해지면 별도의 "정책 기반 게이트 완화"로 신중히 설계한다(블랭킷 우회 아님).

## 3. Feature A — 리뷰-수정 반복 루프

### 3.1 공통 규칙
- 루프 종료 조건: 리뷰 결과가 `review_needs_changes(review) == False`(통과)면 **조기 종료**. 아니면 `max_review_rounds` 소진 시 종료.
- 종료 시 미해결(마지막 리뷰가 여전히 "수정 필요")이면 그 상태를 산출물/보고에 명시하고 다음 단계로 진행한다(무한 반복 금지).
- 각 에이전트 호출은 기존 `AgentCallBudget.before_call(...)`를 그대로 통과한다. 예산 소진 시 `AgentCallBudgetStopped`로 안전 종료(현행 동작 유지).
- 산출물 파일명은 라운드 접미사 `_rN`을 붙여 라운드별로 남긴다(덮어쓰기 금지).

### 3.2 구현 단계(`routed_impl.py::run_implementation_route`)
현행 `05 리뷰` + 조건부 `06 수정`을 다음 루프로 대체한다.

```
implementation = run_role_step(... "04_impl" ..., mutating=True)
approved = False
for r in range(1, max(args.max_review_rounds, 1) + 1):
    review = run_role_step(... f"05_review_r{r}" ..., mutating=False)
    if not review_needs_changes(review):
        approved = True
        break
    if r == args.max_review_rounds:      # 마지막 라운드면 수정만 하고 끝(또는 정책상 중단)
        # 정책: 마지막 라운드에서도 수정을 1회 반영할지 여부는 아래 '결정' 참조
        ...
    fix = run_role_step(... f"06_fix_r{r}" ..., mutating=True)
    # 다음 루프의 리뷰가 fix 반영본을 다시 본다
# 루프 종료 후 최신 implementation/review/fix 결과로 07 최종리뷰 -> 08 평가 -> 09 보고
```

- `--max-review-rounds 0`이면 리뷰/수정 루프를 건너뛰고 곧장 최종리뷰로(기존 "수정 안 함"과 동치).
- `08 평가`, `09 보고`는 종단 **1회** 유지. 평가는 루프에 넣지 않는다.

### 3.3 계획 단계(`routed_preamble.py::run_preamble`)
현행 `02 architecture(claude)` + `03 validation(codex)` 선형을 다음 루프로 확장한다.

```
architecture = run(02_architecture, claude)
for r in range(1, max(args.max_review_rounds, 1) + 1):
    validation = run(f"03_validation_r{r}", codex)   # codex가 계획을 리뷰
    if not review_needs_changes(validation):
        break
    if r == args.max_review_rounds:
        break
    architecture = run(f"02_architecture_r{r}", claude, 이전 validation 피드백 반영)  # claude가 계획 수정
```

- 리뷰어=codex validation, 수정 주체=claude architecture 재작성. 통과 또는 소진까지 반복.
- `01 context`는 루프 밖(1회) 유지.
- 반환값은 최신 `context, architecture, validation`.

### 3.4 결정 사항(구현 시 확정)
- **마지막 라운드 수정 반영 여부**: 마지막 라운드에서 "수정 필요"가 나오면 (a) 수정 1회 더 하고 검증 없이 종료 vs (b) 수정 없이 종료. 기본안 = (a) 마지막에도 수정은 반영하되, 그 수정본은 재검증 없이 다음 단계로 넘긴다. 미해결 표시는 보고서에 남긴다.
- 프롬프트 템플릿(`*_fix.md`, `codex_validation.md`)에 "이전 리뷰 피드백"을 라운드마다 주입한다.

## 4. Feature B — 인간검수 승인 후 재개(체크포인트 + `--resume`)

### 4.1 흐름
- **1차 실행(계획)**: `run_routed_workflow`가 preamble(3.3 루프 포함)까지 실행한 뒤 게이트에서:
  - `checkpoint.json`을 `run_dir`에 저장한다(§4.2).
  - `approval_required.md` / `approval_status.json`을 쓴다(현행 유지).
  - 종료(exit 0). 구현은 실행하지 않는다.
- **사람 검토**: `01/02/03` 산출물을 검토한다.
- **재개(승인)**: `python run.py --resume <run_dir>` 실행.
  - `<run_dir>/checkpoint.json`을 읽어 `route`, `request`, `base_values`, `common`(context/architecture/validation)을 복원한다. **preamble을 다시 실행하지 않는다.**
  - 구현 직전 `git_baseline_status`를 재확인한다(현행과 동일 안전 검사).
  - `run_implementation_route(...)`를 호출해 구현 단계부터 이어간다(Feature A 루프 포함).
  - `approval_status.json`을 `approved: true`, `resumed_at`(값은 실행 측에서 주입)로 갱신한다.

### 4.2 `checkpoint.json` 스키마(`run_dir`에 저장)
큰 텍스트(context/architecture/validation)는 기존 `01/02/03_*.md` 파일을 재활용하고, 체크포인트에는 재개에 필요한 메타만 저장한다.

```json
{
  "version": 1,
  "stage": "awaiting_approval",
  "request": "...",
  "workspace": "C:/.../LanguageDetection",
  "config_path": "...",
  "route": { ...route.json 동일... },
  "artifacts": {
    "context": "01_claude_context.md",
    "architecture": "02_claude_architecture.md",
    "validation": "03_codex_validation.md"
  },
  "max_review_rounds": 3,
  "max_agent_calls": 25
}
```

- 재개 시 `artifacts` 경로에서 텍스트를 `read_text`로 읽어 `common`을 재구성한다.
- 계획 루프(3.3)로 파일명이 `_rN`이 된 경우, **최신 라운드 파일 경로**를 체크포인트에 기록한다.

### 4.3 CLI 변경(`cli.py`)
- `--resume RUN_DIR` 추가: 지정 시 `make_run_dir()`/신규 워크플로우 대신 재개 경로로 분기. **정식 승인 경로는 이것 하나뿐이다.**
- 게이트를 건너뛰는 블랭킷 플래그(`--approve` 류)는 두지 않는다(§2 비목표 참조). 사람의 승인 = 계획 산출물을 검토한 뒤 `--resume`를 실행하는 행위.
- `--resume`와 `--request`/`--request-file`은 상호배타(재개는 체크포인트에서 요청을 읽음). 동시 지정 시 오류.

### 4.4 게이트 코드 변경(`routed.py`, `routed_common.py`)
- `routed.py`: `block_for_human_approval` 직전 `checkpoint.json` 저장 단계 추가.
- `routed_common.py::approval_required`: 현행 유지(`--approve`면 False). 재개 경로는 게이트를 아예 거치지 않고 구현으로 진입.
- 재개 진입 함수 신설(예: `resume_routed_workflow(args, config)`): 체크포인트 로드 -> `common` 복원 -> 베이스라인 검사 -> `run_implementation_route`.

## 5. 영향 파일
- `autoagent/cli.py` — `--resume` 인자, 재개 분기, 상호배타 검증.
- `autoagent/workflows/routed.py` — 게이트 전 체크포인트 저장, 재개 진입점 연결.
- `autoagent/workflows/routed_common.py` — 체크포인트 write/load 헬퍼, (필요 시) 승인 상태 갱신.
- `autoagent/workflows/routed_preamble.py` — 계획 단계 리뷰-수정 루프.
- `autoagent/workflows/routed_impl.py` — 구현 단계 리뷰-수정 루프, 라운드별 산출물 명명.
- 프롬프트 템플릿 — `*_fix.md` / `codex_validation.md`에 라운드별 리뷰 피드백 주입.

## 6. 엣지 케이스 / 리스크
- **예산 소진 중 루프 중단**: `AgentCallBudgetStopped`로 안전 종료(현행 유지). 몇 라운드까지 돌았는지 산출물로 확인 가능.
- **재개 시 워크스페이스 변동**: 1차 계획 이후 코드가 바뀌면 계획 산출물이 낡을 수 있음. 재개는 최신 워크스페이스에 구현하므로, 계획-구현 사이 간극은 사용자 책임. 보고서에 계획 시점 기록.
- **미해결 종료**: N라운드 후에도 리뷰가 "수정 필요"면 강제 진행하되 보고서에 명시(무한 루프 방지).
- **체크포인트 손상/부재**: `--resume` 대상에 `checkpoint.json`이 없으면 명확한 오류로 중단.
- `--max-review-rounds` 의미 변경(불리언 -> 실제 반복 횟수)은 **하위호환 주의**: 기존 스크립트가 `1`을 넘겼다면 동작 동일(1라운드), `3`은 이제 실제 3라운드.

## 7. 테스트 계획
- **드라이런(`--dry-run`)**: 프롬프트/명령 산출물 시퀀스가 라운드 수만큼(예: `05_review_r1..r3`, `06_fix_r1..r3`) 생성되는지 확인(실제 에이전트 호출 없이).
- **루프 단위 검증**: `review_needs_changes`를 참/거짓으로 유도하는 스텁 리뷰 텍스트로 (a) 통과 시 조기 종료, (b) 계속 "수정 필요" 시 N라운드 후 종료를 확인.
- **재개 검증**: 1차 실행으로 `checkpoint.json` 생성 -> `--resume <run_dir>`가 preamble을 재실행하지 않고 `04_*` 구현부터 시작하는지, 승인 상태가 갱신되는지 확인.
- **회귀**: `--max-review-rounds 0`이 리뷰/수정을 건너뛰는지, `--approve` 빠른 경로가 여전히 단일 실행으로 구현까지 가는지 확인.

## 8. 롤아웃 순서(구현 단계에서 참조)
1. Feature A 구현 단계 루프(`routed_impl.py`) — 가장 체감 큼, 독립적.
2. Feature A 계획 단계 루프(`routed_preamble.py`).
3. Feature B 체크포인트 저장(`routed.py` + `routed_common.py`).
4. Feature B `--resume` CLI + 재개 진입점(`cli.py`).
5. 프롬프트 템플릿에 라운드 피드백 주입.
6. 드라이런/스텁 테스트로 검증.

# Command Approval→Resume Procedure Design

- Date: 2026-07-09
- Status: Approved (design); implementation pending
- Related: `docs/specs/2026-07-09-review-loop-and-approval-resume-design.md`, PR #2 (resume handoff)

## 배경 / 문제

AutoAgent는 Claude Code CLI 세션 안에서 슬래시 커맨드(`/aa-*`)로 구동된다. routed 워크플로우가 high-risk/db 요청에서 **승인 게이트**에 멈추면(`block_for_human_approval`), 지금은 사람이 산출물을 읽고 `python run.py --resume <run_dir>`를 **수동으로** 다시 쳐야 구현 단계로 넘어간다.

원하는 것: 사람이 **Claude CLI 안에서 승인**하면 구동 에이전트가 재개까지 이어주는 **한 흐름의 공통 절차**. 단,

- 승인을 *건너뛰는* 자동화가 아니라 *한 세션 안으로 합치는* 자동화여야 한다 (게이트 철학 유지: resume 실행 = 승인 행위, blanket `--approve` 없음).
- **모든 라우트에 공통**이어야 한다 — db 전용이 아니라 backend/frontend/auto 어디서든 동일 동작.

전제(이미 구현됨, PR #2): 게이트가 파싱 가능한 핸드오프를 방출한다 — stdout에 `ROUTED_STATUS` / `RUN_DIR` / `RESUME_COMMAND`, 그리고 `approval_status.json`에 `run_dir` + `resume_command`.

## 목표 / 비목표

목표
- 단일 범용 슬래시 커맨드 `/aa`가 실행→(게이트 시)승인 질의→재개→요약까지 한 흐름으로 처리.
- 게이트 판정은 하네스에 위임(타입 무관). 커맨드는 판단하지 않는다.
- 정본은 하네스 repo가 소유. 대상 워크스페이스 repo는 건드리지 않는다.

비목표 (이번 스코프 밖)
- 하네스 high-risk 커버리지 개선(키워드 확장 / frontend 등급 / `HIGH_RISK_REQUEST_TERMS` 중복 제거) → 별도 트랙.
- 기존 `LanguageDetection/.claude/commands/aa-*.md` 수정/삭제 → 외부 repo, 스코프 밖.
- 하네스 Python 코드 변경 (핸드오프는 PR #2로 충분). 이 스펙은 커맨드(에이전트 지시문)만 다룬다.

## 설계

### 1. 커맨드 형태 & 인자

- 정본 파일 1개: 하네스 repo `commands/aa.md`.
- 설치: `~/.claude/commands/aa.md`로 복사(글로벌). 모든 프로젝트 세션에서 `/aa` 사용 가능.
- 문법: `/aa [type] <request>`
  - 첫 공백 구분 토큰이 `{auto, backend, frontend, docs, review}` 중 하나면 → `--task-type <type>`, 나머지가 요청.
  - 아니면 → `--task-type auto`, `$ARGUMENTS` 전체가 요청.

### 2. 절차 흐름

구동 Claude 에이전트가 다음을 순서대로 수행한다.

Phase-1 (실행):
```
python <AutoAgent>/run.py --workflow routed --task-type <type> \
       --max-review-rounds 1 --max-agent-calls <N> --workspace . --request "<request>"
```
- `<N>` = type이 `docs`/`review`로 명시된 경우 5, 그 외(`auto` 포함) 9. (max 상한이라 docs/review가 auto로 잡혀도 남는 예산은 무해.)
- `--workspace .` = 현재 세션 프로젝트를 대상으로.
- `--require-human-approval`는 붙이지 않는다 (게이트는 하네스의 `is_high_risk` 판정에 위임).

Phase-1 stdout / `approval_status.json`을 파싱해 분기:

- (a) `ROUTED_STATUS: waiting_for_human_approval` → **게이트**
  1. run_dir에서 `01_claude_context.md`, `02_claude_architecture.md`, `03_codex_validation.md`, `route.json`, `approval_required.md`를 읽는다.
  2. 계획 요약 + 위험(route의 `risk_level`/`subtype`) + 영향 파일/비목표를 사람에게 제시한다.
  3. Claude CLI에서 승인 질의(기본: 대화형 텍스트).
     - 승인 → `approval_status.json`의 `resume_command`(= 방출된 `RESUME_COMMAND`)를 그대로 실행 = Phase-2.
     - 거부 → 정지. run_dir가 보존되므로 나중에 동일 `resume_command`로 이어갈 수 있음을 안내.
- (b) 게이트 없이 완료 (저위험 구현 or docs/review) → `final_report.md` / `final_evaluation.md` 요약. 승인 단계 없음(하네스가 위험하지 않다고 판정).
- (c) 차단 → `implementation_blocked.md`(git baseline) 또는 `stopped_by_budget.md`(예산 소진)의 사유를 그대로 surface.

Phase-2 (재개, 승인 시에만):
```
<resume_command>     # 예: python "<AutoAgent>/run.py" --resume "<run_dir>"
```
- `resume_routed_workflow` → `run_implementation_route` 진입(preamble 재실행 없음). 재개 실행 자체가 승인 행위이며 하네스가 `approval_status.json`을 approved로 갱신한다.
- 완료 후 에이전트는 구현 산출물(`04_*_impl`, `05_*_review_r*`, `07_codex_final_review`, `08_codex_evaluation`, `09_claude_final_report`)과 `git diff --stat`(대상 워크스페이스)을 요약한다.
- 하네스는 자동 커밋/푸시하지 않는다 → 사람이 diff를 검토한다.

### 3. 게이트 감지 (PR #2 의존)

- 1차 신호: Phase-1 stdout의 고정 라인 `ROUTED_STATUS: waiting_for_human_approval` + `RESUME_COMMAND: ...`.
- 견고성 보강: run_dir의 `approval_status.json`을 읽어 `status == "waiting_for_human_approval"` 및 `resume_command`를 확인(파싱 실패 대비).
- run_dir는 `RUN_DIR:` 라인 또는 `runs/`의 최신 폴더로 확보.

### 4. 승인 인터랙션

- 기본: **대화형 텍스트** — 계획+위험 요약을 보여주고 승인/거부를 묻는다. (사용자 선호: 위젯보다 프로즈.)
- 대안: AskUserQuestion(승인/거부/조건부) — 원하면 커맨드 문구만 바꿔 전환.
- 불변식: 자동 승인 없음. `resume_command` 실행이 유일한 승인 경로.

### 5. 스코프 & 산출물

- 신규 파일: 하네스 repo `commands/aa.md` (in-scope).
- 글로벌 설치는 사용자가 복사(또는 별도 승인 시 `~/.claude/commands/`에 배치). 하네스 `README.md`에 설치·사용법 추가.
- 대상 repo의 기존 `/aa-*`는 건드리지 않는다.

### 6. 에러 / 엣지 처리

- docs/review/read-only 라우트: 게이트/구현 없음 → 리뷰·리포트 요약만.
- git baseline 불안전: 하네스가 `implementation_blocked.md` 작성 → 에이전트가 사유 전달, 재개 시도 안 함.
- 예산 소진: `stopped_by_budget.md` → 사유 전달.
- Phase-1이 아무 마커 없이 종료(비routed/에러): 종료코드·stderr 요약.

## 검증

- Phase-1 게이트 핸드오프 파싱: PR #2에서 `--dry-run`으로 backend/frontend 검증 완료.
- 커맨드는 에이전트 지시문이므로, 저위험 요청으로 실제 1회 워크스루하여 (a)/(b)/(c) 분기와 승인→재개 흐름을 확인. 절차와 테스트 방법을 `commands/aa.md` 및 README에 명시.

## 향후 (별도 트랙)

- 하네스 high-risk 커버리지 개선.
- 기존 `/aa-*` 정리 또는 `/aa`로의 이관.

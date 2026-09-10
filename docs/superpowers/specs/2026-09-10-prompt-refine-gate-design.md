# 프롬프트 정제 게이트(refine stage) 설계

날짜: 2026-09-10
상태: 설계 확정(구현 전)
브랜치: feature/prompt-refine-gate

## 목적

사용자가 넣은 원문 요청을 그대로 파이프라인에 흘리지 않고, 하네스가 **가벼운
정제 콜 1회**로 요청을 구조화한 뒤 **승인 게이트에서 정지**한다. 사용자가
정제본을 확인·수정·승인(`--resume`)하면 그 텍스트가 이후 전 단계의 REQUEST가
된다.

기대 효과:

- 모호한 요청이 비싼 context/architect 콜로 흘러가기 전에 다듬어진다.
- high-risk 키워드 substring 오발(요청이 자기 코드를 설명하며 `auth` 등을
  스치는 경우)을 정제 단계에서 재표현으로 해소한다.
- 라우팅(`choose_implementer`)·게이트 판정이 정제된 텍스트 기준으로 돌아
  일관성이 높아진다.

## 확정된 결정 사항

| 결정 | 선택 |
| --- | --- |
| 위치 | 하네스 정식 스테이지(cli.py, 워크플로 분기 **전**) — 전 워크플로(simple/routed/decompose/research) 공통 |
| 정제 깊이 | 가벼운 정제 — 텍스트 구조화만, 파일 탐색·설계 금지(그건 context/architect의 일) |
| 정제 주체 | Claude, light 티어(역할 분업: 계획 계열은 Claude). solo 모드는 기존 resolve_role 스왑을 그대로 탄다 |
| 게이트 방식 | 정지 → `--resume`이 곧 승인(기존 관례). 정제본 파일을 직접 편집 후 resume하면 편집본으로 진행 |
| 기본값 | 기본 켜짐. `--no-refine`으로 스테이지 전체 스킵(기존 동작과 동일) |
| 예산 | 정제 콜 1회는 `--max-agent-calls`와 **별도 계정**(워크플로 진입 전이므로). /aa의 N 불변 |

## 데이터 흐름

```
load_request → 00_request_raw.md 저장
→ [--no-refine이면 스킵: 00_request.md=원문, 기존 흐름 그대로]
→ refine 에이전트 콜(Claude, light) → 00_refined_request.md
→ refine_status.json + refine_required.md 기록, 게이트 정지(exit 0)
   stdout 고정 라인: AA_STATUS: waiting_for_prompt_approval / RUN_DIR: / RESUME_COMMAND:
→ (사용자·구동 세션이 00_refined_request.md 확인, 필요시 편집)
→ python run.py --resume <RUN_DIR>
→ 00_refined_request.md를 request로 읽어 00_request.md로 기록,
   refine_status.json을 approved로 갱신, 저장된 args 스냅샷으로
   원래 워크플로를 같은 run_dir에서 시작
```

## 구성 요소

### 1. `autoagent/refine.py` (신규)

단일 목적 모듈. 공개 함수(안):

- `run_refine_stage(args, config, request, run_dir) -> str | None` — 정제 콜
  실행, 게이트 파일 기록, `AA_STATUS` 핸드오프 출력. `None`=게이트 정지(호출부
  exit 0), `str`=dry-run 통과 텍스트(원문 그대로 다음 스테이지로).
- `refine_gate_pending(run_dir) -> bool` — `refine_status.json`이 존재하고
  `status == "waiting_for_prompt_approval"`인지 판정(resume 분기용).
- `resume_from_refine_gate(args, run_dir) -> tuple[str, dict]` — 정제본(편집
  포함)을 읽고 approved 갱신, args 스냅샷 복원. 재디스패치는 호출부(cli.py)
  담당.

`cli.py`는 (a) 신규 런에서 `--no-refine`이 아니면 `run_refine_stage` 호출,
(b) `--resume` 분기 **맨 앞**에 `refine_gate_pending` 체크 — 이렇게 분기
몇 줄만 추가한다. 체크 순서: **refine → research_state.json → checkpoint.json**
(refine 게이트 시점엔 뒤 둘이 아직 없으므로 충돌 없음).

### 2. `prompts/refine/claude_refine.md` (신규)

정제 지침 프롬프트. 핵심 규칙:

- 출력은 **정제된 요청 markdown 그 자체**(메타 발화·머리말 금지) — 이 출력이
  그대로 다음 단계 REQUEST가 된다.
- 구조: 목표 / 범위 / 제약 / 완료 조건 / 비목표. 모호한 부분은 "확인 필요"
  항목으로 명시(추측으로 메꾸지 않는다).
- 파일 탐색·설계·구현 계획 금지. 워크스페이스는 용어 확인 수준만.
- **high-risk 키워드 규칙**: `migration` / `auth` / `payment` / `production` /
  `backfill` / `rollback`은 작업이 실제로 해당할 때만 남기고, 스치는 언급은
  다른 표현으로 바꾼다(게이트 오발 방지).
- 원문의 의도를 바꾸지 않는다 — 재구조화·명확화만.

플레이스홀더: `{REQUEST}`, `{WORKFLOW}`, `{TASK_TYPE}`, `{WORKSPACE}`.

### 3. `roles.default.json` — `refine` 역할 추가

```json
{ "id": "refine", "agent": "claude", "tier": "light",
  "high_risk_condition": "none", "mutating": false, "permission": "plan" }
```

resolve_role 경유로 티어·solo 스왑·permission이 기존 기제대로 적용된다.

### 4. 게이트 산출물(run_dir)

- `00_request_raw.md` — 원문 보존(`--no-refine` 시 미생성).
- `00_refined_request.md` — 정제본. **resume 전에 편집하면 편집본이 채택된다.**
- `refine_status.json` — `status`(waiting_for_prompt_approval → approved),
  `run_dir`, `resume_command`, `workspace`(최상위 필드), 그리고 재디스패치용
  **args 스냅샷**(`args` 키 아래: workflow, task_type, implementer, read_only,
  max_review_rounds, max_agent_calls, stop_after, skip_verification,
  require_human_approval, plan_only, skip_review, auto_approve_nonbranch,
  project, `config`(설정 파일 경로), no_refine).
- `refine_required.md` — 사람용 안내(정제본 검토 → 편집(선택) → resume 명령).
- `00_request.md` — **resume 승인 시점에 정제본으로 기록**. 다운스트림 전
  단계가 이 이름을 참조하므로 이후 흐름 무변경.

### 5. CLI 플래그

- `--no-refine` (신규, action="store_true") — 정제 스테이지 스킵.
- `--resume` + `--request` 동시 사용 금지는 기존 규칙 유지.

### 6. /aa·/aar 커맨드 연동

섹션 3(분기)에 게이트 분기 하나 추가:

- stdout에 `AA_STATUS: waiting_for_prompt_approval`이 보이면
  `00_refined_request.md`(+원문 대비 요지)를 사용자에게 보여주고
  "이 프롬프트로 진행? (승인 / 수정 / 거부)"를 묻는다.
- 승인 → `RESUME_COMMAND` 그대로 실행. 수정 의견 → 세션이
  `00_refined_request.md`를 편집한 뒤 resume. 거부 → 런 보존 안내 후 종료.
- 이후 high-risk 계획 게이트가 또 서면 기존 분기 로직이 그대로 처리한다
  (게이트 2회는 서로 다른 것을 승인하므로 병합하지 않는다).

## 에러 처리

- 정제 콜 실패·빈 출력: 런을 죽이지 않는다. `00_refined_request.md`에
  **원문을 그대로** 담고 `refine_status.json`에 실패 사유(`fallback: true`)를
  기록한 뒤 정상 게이트 정지 — 사용자가 원문 승인 또는 직접 편집으로 진행.
- resume 시 `00_refined_request.md`가 없거나 빈 파일: 명확한 메시지로 종료
  (SystemExit), 게이트 상태는 유지.

## dry-run 동작

정제 프롬프트(`00_refine_prompt.md`)와 command 아티팩트(`00_refine_command.json`)를
렌더하고, 게이트 정지 **없이 원문을 그대로** 다음 스테이지에 넘긴다(플레이스홀더가
아니라 원문 통과 — auto 라우팅·high-risk 판정이 요청 텍스트 기반이라 플레이스홀더면
dry-run의 검증 목적이 깨진다). 기존 dry-run 관례(CLI 미호출, 예산 미차감, exit 0) 유지.

## 검증 계획

- dry-run: `--workflow routed`(및 research)로 정제 프롬프트+command json 렌더
  확인, `--no-refine`으로 기존 산출물과 동일함 확인.
- refine_status 기반 resume 분기(결정적 로직)는 소규모 pytest 추가 가능
  (연구 서브시스템 테스트 관례 준용) — 게이트 파일 기록/판정/재디스패치 인자
  복원.
- **라이브 정제 콜은 미실증으로 사용자 인계**(기존 관례) — "구현됨"을
  "실전 검증됨"으로 단정하지 않는다.

## 비목표

- 깊은 정제(워크스페이스 탐색 기반 요청서 작성) — context/architect와 중복.
- 정제 게이트와 계획 승인 게이트의 병합.
- 대화형 재정제 루프(하네스 안에서 "다시 다듬어줘") — 파일 편집 + resume로
  대체.

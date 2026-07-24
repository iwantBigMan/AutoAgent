# review 라우트를 실제 리뷰 + 실행근거 라우트로 (설계)

- 날짜: 2026-07-22
- 상태: 설계 승인 대기 → 플랜 착수 예정
- 범위: `--task-type review` 라우트 한정 개선(3개 목표 동시). backend/frontend/docs 라우트는 동작 불변.

## 배경 / 문제

LanguageDetection에서 `--task-type review`(전체 아키텍처·결합도·디자인패턴 점검)를
`--max-agent-calls 5 --max-review-rounds 0`으로 돌린 런(`runs/20260722_095324`)이
codex 평가에서 `needs_changes / score 0.3`으로 자체 판정됐다. 원인 조사 결과 **예산(call 수)
문제가 아니라 라우트 구조 문제**였다:

1. **리뷰 산출물이 없다.** `review`는 `routed_docs.run_docs_route`로 매핑되는데, 이 라우트엔
   "리뷰를 실제로 수행·산출하는 단계"가 없다. preamble(context→architecture⇄validation)이
   *계획*을 만들고 곧장 evaluation으로 넘어가, 평가자는 "계획과 중간 검증뿐"이라며 정당하게
   미완 판정한다. `run_docs_route`는 평가자에게 `review="Read-only or docs/review route."`를
   그대로 넘긴다.
2. **실행 근거가 없다.** review 라우트는 검증 스테이지(`_maybe_run_verification`)를 부르지
   않는다(그건 `routed_impl`에만 있음). codex는 자기 샌드박스(`workspace-write`, 네트워크
   차단)에서 워크스페이스의 python을 직접 돌리기 어려워 "Python 환경이 끊어져 compileall/
   pytest/pip check/Alembic 검증 불가"라고 적는다. **하네스 자체 검증은 정상 동작한다**
   (impl 런 `runs/20260720_141619`의 `04b_verification.md` = overall PASS, venv311 실재).
3. **검증 커맨드가 LD 전용 하드코딩.** `verification.default_commands`는 venv311 + npm
   frontend가 박혀 있어, 다른 프로젝트에선 exe "missing"으로 실패한다. per-project 레지스트리
   철학과 어긋난다.

## 목표 (3개 동시)

1. review 라우트가 **파일·라인 근거 + 중요도 + 영향 + 최소권고**를 담은 실제 아키텍처 리뷰를
   산출한다.
2. review 라우트가 **하네스가 직접 실행한 검증 결과**를 리뷰 본문의 근거로 인용한다
   (codex가 python을 돌릴 필요 자체를 없앤다).
3. 검증 커맨드를 **per-project 설정**으로 빼고, 미설정 프로젝트는 조용히·정직하게 스킵한다.

## 비목표

- backend/frontend/docs 라우트 동작 변경(byte-equality로 보존 증명).
- codex 샌드박스 자체를 완화하거나 codex가 python을 직접 실행하게 만드는 것(대신 하네스가 실행).
- 2단계(실 PostgreSQL/Alembic 왕복) 검증 도입 — 여전히 DB-free 1단계만.
- venv 자동탐지(범위 초과, 오탐 위험으로 기각).

## 설계 결정 (브레인스토밍 확정)

- **Q1 = C**: 새 단계를 만들지 않고, review 서브타입일 때 preamble의 architecture/validation
  프롬프트를 "리뷰 산출/리뷰 검증"용으로 분기한다. 기존 claude(architect)⇄codex(validation)
  루프가 곧 크로스모델 리뷰 구조. + review 라우트에 검증 스테이지 부착.
- **Q2 = A**: 검증을 리뷰 **앞단**(context 직후, review 분석 직전)에서 실행해 리뷰 본문이
  실측(PASS/FAIL, 어느 모듈 import 깨짐 등)을 인용하게 한다. 읽기전용이라 앞에 돌려도 부작용 없음.
- **Q3 = A**: `verification_commands` 미설정 → `default_commands` LD-하드코딩으로 폴백하지 말고
  **명시적 스킵**. LD의 커맨드는 `projects/LanguageDetection/config.json`으로 이전. 스킵/실행
  여부를 리뷰·평가 프롬프트에 명시.

## 데이터 흐름 (review 라우트)

```
route(review)
 → context (claude, 01)
 → [NEW] 검증 스테이지 (하네스가 config.verification_commands 실행, 04b_verification) ── Q2-A
 → review 분석 (claude, 02_claude_architecture)   ← VERIFICATION_SUMMARY 인용, 리뷰 프롬프트
 → cross-check (codex, 03_codex_validation)        ← 리뷰의 완결성/정확성 반박·검증
 → (max_review_rounds만큼 review⇄cross-check 심화 반복; preamble 기존 루프)
 → evaluation (codex, 04_codex_evaluation)         ← 실제 리뷰 + 검증결과 근거로 채점
 → final_report (claude, 05_claude_final_report)   ← 통합 리뷰를 최종 산출물로 제시
```

- 검증 스테이지는 **agent call이 아니라 하네스 subprocess**라 `--max-agent-calls` 예산을
  소모하지 않는다(기존 impl 라우트와 동일).
- 크로스모델 불변식(리뷰어=반대 모델)은 claude(review)⇄codex(cross-check) 루프가 유지.

## 컴포넌트 변경 (4곳)

### 1) `autoagent/workflows/routed_preamble.py`
- `route["task_type"] == "review"`일 때:
  - (a) `run_architecture`가 `claude_architect.md` 대신 `routed/review/claude_review.md`를,
    `run_validation`이 `codex_validation.md` 대신 `routed/review/codex_review.md`를 렌더.
  - (b) context 단계 직후·첫 architecture(review) 직전에 검증 스테이지를 1회 실행하고 요약을
    얻는다(dry-run이면 실행하지 않고 빈 요약).
  - (c) 요약을 `VERIFICATION_SUMMARY` 템플릿 변수로 review·cross-check 프롬프트에 주입.
- docs/backend/frontend는 분기에 걸리지 않아 기존 프롬프트·순서 그대로(불변).
- 캐노니컬 파일명(`02_claude_architecture.md`, `03_codex_validation.md`)은 유지해 다운스트림/
  체크포인트 참조를 깨지 않는다(내용만 리뷰/리뷰검증으로 바뀜).

### 2) `autoagent/verification.py`
- 폴백 변경: 호출부가 `config.verification_commands`가 비면 **스킵**하도록 한다.
  `default_commands` LD-하드코딩 자동 폴백 제거(함수는 LD config 시드 용도로 남길 수 있음).
- 스킵 시 요약 문자열은 "이 프로젝트는 검증 커맨드 미설정(실행 근거 없음)"을 명시.
- 실패(exit≠0)/timeout/missing은 기존대로 런을 죽이지 않고 요약에 담는다.

### 3) `autoagent/workflows/routed_docs.py`
- `run_docs_route`가 review 라우트일 때 evaluation·final_report에 `"No ... step"` 대신
  **실제 리뷰(common의 CLAUDE_ARCHITECTURE) + 검증 요약**을 넘겨 평가자·보고가 진짜 산출물을
  다루게 한다. docs(문서) 서브타입은 기존 문자열 유지.

**검증 요약의 데이터 경로(명확화):** `common`은 `run_preamble` **반환 후** routed.py에서
조립되므로, 검증 요약이 evaluation·report까지 흐르려면 `run_preamble`이 요약을 반환해야 한다.
→ `run_preamble` 반환 튜플에 검증 요약을 1개 원소로 추가(review가 아니면 `""`). routed.py가
이를 `common["VERIFICATION_SUMMARY"]`로 넣는다. preamble 내부의 review/cross-check 프롬프트는
로컬 요약 변수를 직접 주입하므로 반환 전에도 인용 가능. 반환 튜플 시그니처 변경은 호출부(routed.py)
1곳뿐이라 국소적.

### 4) `projects/LanguageDetection/config.json`
- `verification_commands`에 LD 검증 3종을 명시(폴백 제거로 인한 회귀 방지):
  - compileall: `venv311/Scripts/python.exe -m compileall -q src/lang_detect`
  - pytest: `venv311/Scripts/python.exe -m pytest tests tests_legacy -q`
  - frontend_build: `npm --prefix frontend run build`
- (선택) 전역 `autoagent.config.json`에도 동일 커맨드를 두어 `--project` 없이 직접 run.py로
  LD를 돌릴 때의 회귀를 막는다. gitignored라 커밋 대상 아님.

## 신규 프롬프트 (2개, `prompts/routed/review/`)

### `claude_review.md`
- 산출: "계획"이 아니라 **최종 아키텍처 리뷰**. 요청 범위(전체 결합도/계층·포트어댑터 방향/
  디자인패턴 적용·중복/의존성 선언 vs 배포 드리프트/프론트 구조·문서 정합성)를 커버.
- 각 발견: 파일·라인 근거 + 중요도(양호/경미/중요) + 영향 + 최소 권고.
- `{VERIFICATION_SUMMARY}`를 근거로 인용(예: pytest 통과/실패, import 깨짐). 미설정이면
  "실행 근거 없음"을 리뷰에 명시.
- `{CLAUDE_CONTEXT}`, `{PRIOR_VALIDATION}`(cross-check 피드백) 소비 — 기존 architect 변수 형태 유지.

### `codex_review.md`
- 리뷰를 **반박·검증**: 범위 누락 항목? 근거 부실/오탐? 중요도 과대·과소? 실행 근거와의 정합성?
- 기존 `codex_validation.md`의 "계획 검증" 역할을 "리뷰 검증"으로 치환한 형태.
- `review_needs_changes` 규약(needs_changes 마커)을 그대로 사용해 preamble 루프의 조기종료/반복을
  재활용.

## 에러 / 폴백 처리

- 검증 미설정 → 스킵 + 프롬프트에 "실행 근거 없음" 명시(평가자 감안).
- 검증 실패(exit≠0)/timeout → 런 유지, FAIL 요약을 리뷰 근거로 흘림.
- venv/exe 없음 → 해당 커맨드 "missing" 기록, 나머지 진행.
- dry-run → 검증 실행 안 함(빈 요약), 프롬프트/커맨드만 렌더.

## 검증 전략 (pytest 스위트 없음)

1. **byte-equality 회귀**: 변경 전/후 dry-run 매트릭스에서
   **backend(일반/DB)·frontend·docs × implementer(claude/codex)** 의 `*_command.json` +
   `*_prompt.md`가 **바이트 동일**해야 한다(review만 의도적으로 달라짐). 검증 스테이지는
   dry-run에서 안 도니 command 아티팩트에 영향 없음.
2. **review 라우트 라이브 검증**: LanguageDetection에서
   `--task-type review --max-review-rounds 2 --max-agent-calls 9`로 실제 실행 →
   - `04b_verification.md`가 PASS/FAIL 근거를 담는가
   - `02_claude_architecture.md`(리뷰)가 검증을 인용하고 파일·라인 근거를 갖는가
   - `08/04_codex_evaluation`이 "미완" 대신 실질 채점을 내는가
3. **미설정 프로젝트 스킵**: 검증 커맨드 없는 임시 프로젝트로 review 실행 → "미설정 스킵" 표기 확인.
4. **자기수정 크래시 회피**: 이 레포엔 `/aa`/routed 안 돌림. 인라인 편집 + **새 프로세스**로
   dry-run 검증.

## 회귀 위험 / 주의

- **폴백 제거로 인한 LD 회귀**: `default_commands` 자동 폴백을 없애므로, LD의
  `verification_commands`를 config에 반드시 채워야 impl 라우트 검증이 유지된다(비우면 조용히 스킵).
- **review dry-run 아티팩트 변경은 의도된 것**: 회귀 매트릭스에서 review는 비교 대상에서
  제외하거나 "달라짐이 기대값"으로 취급.
- 프롬프트 이름 분기는 review에만 걸리도록 좁게 — docs가 실수로 리뷰 프롬프트를 타지 않게 한다.

# 문서 2 · harness-test 분석 — 동료 하네스

> 대상: `harness-test/` (동료가 `.claude/`로 구성한 AI 코딩 하네스)
> 팀 토론용 분석 자료 · 작성일 2026-07-12 · 짝 문서: `2_동료하네스_harness-test.html`
> 근거: `.claude/`·`CLAUDE.md`·`.mcp.json`·`Makefile`·`.github/`·`docs/` 실파일

---

## 0. 한 줄 요약

> **정책 기반 접근제어(`policy.json` v3) + 훅 강제(`guard-paths`/`on-stop`) + 자율 JS
> 워크플로우 + 커맨드 오케스트레이션의 4층 하네스.** "한 작업 = 한 브랜치". 에이전트가
> **실수하지 못하도록 가드레일을 먼저 깔고** 그 안에서 자율 팬아웃한다.

프레임워크 두께 스펙트럼상 **두꺼운(thick) 하네스**. 앱(FastAPI+React+PostgreSQL 주식
서비스) 레포 **안에** 하네스가 함께 산다(`.claude/`가 코드와 동거).

## 1. 4층 구조

```
정책       policy.json (v3)   ← 진실원본: 에이전트·읽기도메인·쓰기범위·테스트·lint·worklog
  ▼
훅         hooks/*.py          ← 강제: PreToolUse 경로/시크릿 차단 → PostToolUse lint
                                       → Stop 테스트게이트 → worklog 자동기록
  ▼
워크플로우  workflows/*.js      ← 자율: agent()/parallel()/phase() 결정론 팬아웃
  ▼
커맨드     commands/*.md        ← 상호작용: 사용자 선택·확정·PR (재현불가 입력을 args로 주입)
```

## 2. 프레임워크 4+1축 매핑

### Context
- `CLAUDE.md`(전역 규칙: 삭제금지·쓰기범위·테스트게이트·worklog·음슴체 한국어),
  에이전트별 `.claude/agents/<name>.md`(model/effort/tools/skills 헤더).
- **핸드오프 전략**:
  - `docs/CODEMAPS/INDEX.md`(현황 진실원본, doc-updater 유지) — architect가 설계 전 필독.
  - `docs/design/{analysis,plan,decisions(ADR)}/<날짜>-<슬러그>.md` (같은 프리픽스로 묶임).
  - `docs/api/openapi.yaml`(backend 소유) = backend↔frontend 단일계약, frontend는 읽기만.
  - progress 파일 대신 `.active-work.json`(진행원장, 세션경계 추적) +
    `docs/worklog/<날짜>/<시각>-<에이전트>.md`(Stop 시점 자동기록) + 슬라이스 roadmap 체크박스.
- → 프레임워크의 **Ralph loop / Handoff** 사상을 거의 그대로 구현.

### Tool
- MCP(`.mcp.json`): `sequential-thinking` · `arxiv-mcp-server` · `exa` · `github`
  (대부분 `settings.local.json`에서 기본 off — 불필요 인증 스킵).
- 커스텀 스킬 7종: `api-design`, `architecture-decision-records`, `backend-python-developer`,
  `devops-engineer`, `frontend-engineer`, `senior-architect`, `solid-principles` — 에이전트
  시작 전 Skill 호출로 체크리스트·지침 주입(`.claude/agents/<name>.md` `skills:` 필드로 강제).

### Permission (이 하네스의 최강점)
- **격자형 접근제어** — `policy.json`:
  - `readBoundaries`: 도메인(backend/frontend/database/infra/arch/codemap)별 읽기 허용
    에이전트. 옵트인(등록 안 된 도메인은 누구나 읽음), `readAllow`로 폐쇄 도메인 예외.
  - `write`/`deny`: 에이전트별 쓰기 범위 + 명시 제외. 예) `backend.write =
    ["backend/**","docs/api/**","docs/database/**","database/**"]`.
- `guard-paths.py`(PreToolUse)가 **모든 Read/Write/Edit/Bash를 사전 검사**, 시크릿(`.env`,
  `*.pem`, `*.key`, `**/secrets/**`, `.git/**`) 전역 차단.
- `settings.json`(전역 deny) vs `settings.local.json`(개발자별 허용 화이트리스트, git 미추적).
- → 프레임워크의 **Permission harness / Tool gate**를 정적 정책으로 강하게 구현.
  단, "허용/승인필요/금지" 3분할 중 **승인필요(사람 승인 루프)는 사실상 없음** — 도구·MCP 자동.

### Verification (또 다른 최강점)
- **3중 게이트**:
  1. **유닛**(pytest/npm) — `on-stop.py`가 git diff로 변경영역 감지 → Stop 시점 **하드
     차단**(실패 시 exit 2 → 자동수정 최대 3회 → 에스컬레이트) → 통과 시 worklog 생성.
  2. **계약**(schemathesis, `backend-evaluator`) — 실행 API ↔ `openapi.yaml` 자동 대조. 자체 venv.
  3. **화면**(Playwright, `frontend-evaluator` + `census.mjs`) — 풀스택 UI E2E + 기능 전수 커버리지.
- **LLM 평가자 안 씀 — exit code·결정론 도구만 신뢰.** e2e는 라이브 서버 필요라 CI
  (`.github/workflows/e2e.yml`)에서 실행. 커버리지(`scripts/coverage.py`)는 보고용(게이트 아님).
- → 프레임워크의 "자기평가 편향 → 외부 결정론 검증"을 정확히 실천.

### Subagent & Orchestration
- **무한루프 방지**: MAX_ROUNDS(feature/build-verify/verify-fix=3, bugfix=2) + 무진전 감지
  (failure signature 동일 시 중단) + 토큰 예산 가드(<60k 중단) + 교차단계 회귀 시 사람 개입.
- **"한 작업=한 브랜치"**: `ensure-branch.sh <prefix> <slug>` 멱등 헬퍼. design=`feat/`,
  bugfix=`fix/`, research=`docs/`.
- **분할 판정**: planner가 한 `/feature` 사이클로 끝나는지 판단 → 크면 세로 슬라이스
  (`<date>-<slug>-01-<slice>.md` … `-roadmap.md`). 수평 아님(무의미 분할 방지).

## 3. 카탈로그

### 에이전트 15개
| 이름 | 모델 | 역할 |
|------|------|------|
| architect | opus | 구조분석·아키텍처 결정(ADR) |
| planner | opus | 실행계획·분할 판정 |
| backend | sonnet | FastAPI async 구현 |
| frontend | sonnet | React 구현 |
| backend-evaluator | sonnet | API 계약 검증(schemathesis) |
| frontend-evaluator | sonnet | UI E2E(Playwright) |
| devops-engineer | sonnet | Docker/배포 |
| doc-updater | haiku | 코드맵 자동 갱신 |
| research-coordinator | opus | 리서치 조율 |
| academic-researcher | opus | 학술(arxiv) |
| search-specialist | opus | 웹 조사(Exa) |
| technical-researcher | sonnet | 기술 조사 |
| research-synthesizer | opus | 결론 종합 |
| research-critic | opus | 적대반증 검증 |
| research-architect | opus | 리서치 구조검토 |

### 커맨드 8(+1) · 워크플로우 8
| 커맨드 | 워크플로우 | 동작 |
|--------|-----------|------|
| `/design` | design.js | architect 분석 → planner 계획 → 사용자 확정 |
| `/feature` | feature.js | 작업브랜치 → backend/frontend 구현 → doc-updater → 서버기동 |
| `/verify` | verify-fix.js | evaluator 먼저 → 실패영역만 executor 수정 → 재검 |
| `/review-flow` | review-flow.js | diff 감지 → 영역별 병렬 리뷰 → findings 종합 → PR |
| `/deploy` | deploy.js | Dockerfile/compose 작성 → 로컬 도커 up |
| `/bugfix` | bugfix.js | 로그 입력 → 분류(단순 vs 구조) → 수정 → 검증 |
| `/research-brainstorming` | (대화) | 목적·범위·성공기준 좁히기 |
| `/research-deep-dive` | research-deep-dive.js | coordinator→4리서처→적대반증→synthesizer→HTML |
| `/coverage` | — | 변경분 커버리지 리포트 |

### 훅 6
`sync-agent-frontmatter`(SessionStart, policy→에이전트 동기화) · `guard-paths`(PreToolUse,
경로·시크릿 차단) · `format-on-edit`(PostToolUse, ruff/prettier) · `on-stop`(Stop, 테스트게이트) ·
`worklog`(작업기록) · `activity-ledger`(진행원장 upsert).

## 4. 앱 스택
FastAPI async + asyncpg / React18 + TS + Tailwind + Vite / PostgreSQL + Alembic /
e2e: schemathesis(계약) + Playwright(화면) / infra: Docker Compose. (JWT 인증 + 주식목록 서비스)

## 5. 강점 & 빈틈

**강점**
1. 정책 일원화(`policy.json` 단일원본) — 에이전트·도메인·테스트·lint·worklog를 한 곳에서.
2. 격자형 읽기/쓰기 경계 + 시크릿 전역 차단.
3. 3중 결정론 검증(LLM 평가자 배제, exit code만 신뢰).
4. 무한루프 방지 다중장치(MAX_ROUNDS + 무진전 감지 + 예산 가드 + 교차단계 회귀 금지).
5. 작업 추적(active-work + worklog + ADR + codemaps)으로 세션 경계 보존.
6. 리서치 층(다중출처 + 적대반증 3렌즈, confidence 등급).
7. 자율 워크플로우와 상호작용 커맨드 분리 → 재현성·비용 추적.

**빈틈**
1. **크로스모델 없음** — 에이전트별 모델 정적 하드코드(architect=opus, backend=sonnet,
   doc-updater=haiku). 런타임 교차검증 불가.
2. **사람 승인 게이트 없음** — 되돌릴 수 없는 작업의 승인 루프 부재(도구·MCP 호출 자동).
3. MCP는 등록되면 전역 접근(에이전트별 도구 화이트리스트는 커맨드 allowed-tools 수준).
4. 비용은 사후 worklog 집계(실시간 예산 관리 아님).
5. 코드맵(INDEX.md) 수동 유지 → 대규모에서 싱크 리스크.

**적합**: 중규모(10~30 에이전트), 팀 내 single-session 협업, 정책 준수 필수 조직(금융/헬스케어).

---
*근거 파일: `CLAUDE.md`, `.claude/policy.json`, `.claude/settings.json`, `.mcp.json`,
`.claude/agents/*.md`(15), `.claude/commands/*.md`(8), `.claude/workflows/*.js`(8),
`.claude/hooks/*.py`(6), `Makefile`, `.github/workflows/e2e.yml`, `docs/workflows/README.md`.*

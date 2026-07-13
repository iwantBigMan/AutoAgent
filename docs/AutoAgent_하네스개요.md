# AutoAgent 하네스 개요

> 팀 토론·비교용 설명 자료 · 대상 독자: 자기 하네스를 만들어 온 동료 개발자
> 짝 문서: `docs/AutoAgent_하네스개요.html` (같은 내용의 프레젠테이션 버전)

---

## 0. 한 문장 정의

**AutoAgent는 Claude Code CLI(`claude.cmd`)와 Codex CLI(`codex.cmd`)를 서브프로세스로
구동해, 별도의 타깃 워크스페이스를 두 모델이 교대로 구현·리뷰하도록 조율하는
로컬 오케스트레이터다.** 사람이 승인 게이트를 통과시켜야 코드가 바뀐다.

핵심은 "에이전트에게 다 맡기는 자율 루프"가 아니라, **비결정적인 에이전트를
결정적인 Python 제어 흐름으로 감싸는 것**이다. 무엇을 할지는 프롬프트가, 언제·누가·어떤
권한으로 실행할지는 코드가 정한다.

---

## 1. 설계 철학 (토론의 핵심 — 우리가 건 5개의 베팅)

| # | 베팅 | 무엇을 | 왜 |
|---|------|--------|----|
| 1 | **크로스-모델 리뷰** | 리뷰어는 **항상 구현자의 반대 모델** (`routing.choose_implementer`) | 같은 모델의 자기검토는 같은 맹점을 공유한다. 모델을 교차시키면 적대적 다양성이 생긴다. |
| 2 | **코드가 조율, 프롬프트가 "무엇"** | 에이전트 행동은 `prompts/**/*.md`에, 오케스트레이션만 Python에 | Codex는 Claude 스킬을 못 읽는다. 두 CLI가 stdin으로 공통으로 읽는 중립 채널(프롬프트)에 공유 행동을 둔다. 행동 변경은 코드가 아니라 프롬프트에서. |
| 3 | **사람 승인 게이트** | high-risk/db 구현은 코드 변경 **전에** 정지, `--resume`로만 진행 | 위험한 변경은 사람이 계획을 본 뒤에만. "재개 실행 = 승인 행위". 게이트를 우회하는 블랭킷 플래그(`--approve`)는 **의도적으로 없다**. |
| 4 | **외부·비침투** | 타깃 레포는 안 건드림, 상태는 하네스 레포에만, 자동 커밋/푸시 없음 | 하네스가 여러 프로젝트에 재사용되는 외부 도구라서. 타깃은 오직 `cwd`로만 바라본다. |
| 5 | **결정적 제어 흐름** | 루프·분기·예산·게이트는 전부 Python | 재현 가능하고 감사 가능한 파이프라인. 에이전트가 "얼마나 도느냐"를 사람이 통제한다. |

---

## 2. 실행 모델 (가장 중요한 정신 모델)

```
run.py --workspace C:\...\TargetProject --request "..."
   │
   └─ 서브프로세스로 claude.cmd / codex.cmd 를 호출
        ├─ cwd     = config.workspace   ← 타깃 프로젝트 (이 하네스 레포 아님!)
        ├─ stdin   = render_template(prompt.md, values)  ← "무엇을 할지"
        └─ stdout  = runs/<stamp>/NN_*.md 로 캡처         ← 산출물
```

- 서브프로세스는 **타깃 워크스페이스**를 `cwd`로 실행된다. 이 하네스 레포는 편집
  대상이 아니라 **조율자**일 뿐이다.
- 프롬프트는 `{VAR}` 자리표시자를 가진 마크다운이고, `render_template`이 값으로
  치환해 stdin으로 넣는다.
- 모든 실행 산출물은 `runs/YYYYMMDD_HHMMSS/`(프로젝트 지정 시
  `projects/<name>/runs/<stamp>/`)에 남는다.

---

## 3. 모듈 지도

```
run.py                     진입점 → autoagent.cli.main
autoagent/
├─ cli.py                  argparse, config 로드, 시작 시 역할 레지스트리 정합성 검사, 워크플로우 분기/--resume
├─ config.py               Config 데이터클래스, load_config(path, project) — 레이어드 병합
├─ routing.py              route_task 키워드 점수 + 구현 의도 가드, db_term_count, choose_implementer(반대 모델)
├─ roles.py                load_roles(default+override), validate_roles, resolve_role → ResolvedRole
├─ worktree.py             decompose 병렬 실행기용 격리 git worktree 생성/정리, 레인 브랜치 관리
├─ runner.py               claude_command/codex_exec_command 빌더, run_process, AgentCallBudget
├─ safety.py               git_baseline_status, codex_sandbox_for, review_needs_changes
├─ artifacts.py            make_run_dir, render_template, write_*, validate_project_name
└─ workflows/
   ├─ simple.py            Claude plan → Codex execute → Claude review
   ├─ routed.py            라우팅 → preamble → 게이트 → 구현 라우트 (오케스트레이션 상위)
   ├─ decompose.py         대규모 요청을 task_graph.json으로 분해(구현 없음)
   ├─ task_exec.py         decompose 병렬 실행기 본체 — task_graph를 파도별 worktree 격리 병렬 실행(`--resume`로 진입)
   ├─ routed_preamble.py   context → architecture ⇄ validation (계획 단계)
   ├─ routed_impl.py       구현 → 리뷰⇄수정 → 최종리뷰 → 평가 → 보고 (구현 루프)
   ├─ routed_docs.py       읽기 전용 라우트(평가 + 보고만)
   └─ routed_common.py     승인 게이트, checkpoint, 예산 정지, 평가/보고 공용 헬퍼

prompts/**/*.md            에이전트 지시문(중립 채널) — 여기서 "무엇"을 바꾼다
roles.default.json         선언형 역할 레지스트리 (+ roles.json 로 override)
autoagent.config.json      전역 설정(gitignore, 로컬)
projects/<name>/           프로젝트별 config.json + runs/ (재사용 레지스트리)
```

---

## 4. 워크플로우

### 4.1 simple — 최소 루프
```
Claude plan → Codex execute → Claude review
```
기존 동작 보존용. `--plan-only`로 계획만.

### 4.2 routed — 역할 기반 파이프라인 (주력)

```
route_task ──► route.json
   │
   ▼
[preamble / 계획]
   context(claude) ──► architecture(claude) ⇄ validation(codex)
                            └───── 루프: 검증 통과 or max_review_rounds 소진까지 ─────┘
   │
   ▼
읽기전용 or docs/review?  ──yes──►  docs 라우트(평가 + 보고만, 코드 변경 없음)
   │ no
   ▼
[승인 게이트]  approval_required?  ──yes──►  checkpoint.json + 정지, --resume 대기
   │ no                                        (사람이 계획 산출물 검토 후 재개 = 승인)
   ▼
git 베이스라인 확인(비-dry-run)  ── 유효 HEAD 없으면 구현 차단
   │
   ▼
[구현 라우트]
   impl(04) ──► review(05) ⇄ fix(06)  ──► final-review(07,codex) ──► evaluation(08,codex) ──► report(09,claude)
                └── 루프: 리뷰 통과 or max_review_rounds 소진까지 ──┘
```

- **리뷰어는 항상 구현자의 반대 모델.** backend 기본 구현자=claude → 리뷰어=codex.
  frontend 기본 구현자=codex → 리뷰어=claude.
- 각 단계 산출물은 `NN_<agent>_<type>_<stage>.md`로 남고, 리뷰-수정 라운드는 `_rN` 접미사.

### 4.3 decompose — 분해 전용
```
Claude decomposition → Codex plan review → task_graph.json → 인간 승인 필요(정지)
```
**decompose 워크플로우 자체는 구현 단계를 절대 실행하지 않는다.** Claude는
`--permission-mode plan`, Codex는 `--sandbox read-only`로만 돈다. 승인된
`task_graph`는 사람이 `approval_brief.md`를 검토한 뒤 `--resume <run_dir>`
(checkpoint `mode:"task_graph"`)로 재개하면 아래 4.4의 **decompose 병렬 실행기**가
실행을 맡는다(재개 = 승인 행위, 게이트 우회 아님).

### 4.4 decompose 병렬 실행기 (`task_exec.py`)
- **흐름**: git 베이스라인(HEAD) 확인 → `task_graph`를 위상정렬(`topological_waves`,
  순환이면 halt) → 파도(wave)별로 `ThreadPoolExecutor(max_workers=max_parallel_lanes)`
  병렬 실행.
- **노드 격리**: 코드-생성 노드(backend/frontend)마다 타깃 레포의 격리 git
  worktree + 레인 브랜치에서 구현→**반대모델 리뷰**→수정 미니 루프를 돈다.
  비코드 노드(docs/review/test/infra)는 skip하고 리포트에 미실행 명시.
- **통합**: 완료 레인들을 통합 브랜치 `aa-integration/<stamp>`로 위상 순 순차
  병합(충돌 시 stop-and-report). **main은 미접촉**, 자동 push 없음.
- **동시성 상한**은 `config.max_parallel_lanes`(기본 2). `1`이면 순차.
- **검증 상태**: dry-run·단위·코드리뷰까지 확인됐고, **라이브 end-to-end 실행은
  아직 미검증**이다.

---

## 5. 선언형 역할 레지스트리

`roles.default.json`이 9개 역할을 데이터로 선언하고, `resolve_role`이 route/모델
정책을 적용해 실행 속성(`ResolvedRole`)으로 해석한다. 행동을 코드에 하드코딩하지
않고 한 곳에서 데이터로 본다.

| 역할 | agent | 모델 티어 | high-risk 조건 | mutating | 권한/샌드박스 |
|------|-------|-----------|----------------|----------|----------------|
| context | claude | standard | none | ✗ | plan |
| architect | claude | tiered | any_high_risk | ✗ | plan |
| validation | codex | standard | none | ✗ | from_read_only |
| implementer | route | tiered | backend_high_risk_mutating | ✓ | write |
| reviewer | route | standard | none | ✗ | plan |
| fix | route | tiered | backend_high_risk_mutating | ✓ | write |
| final-review | codex | standard | none | ✗ | configured |
| evaluation | codex | standard | none | ✗ | from_read_only |
| report | claude | standard | none | ✗ | plan |

- `agent: "route"`는 라우팅이 정한 구체 에이전트(구현자/리뷰어)를 호출부가 주입.
- `tiered` + high-risk 조건 충족 → 모델을 opus로, effort를 xhigh로 승격.
- claude mutating 스텝 권한: 기본 `acceptEdits`(편집만 자동, bash/네트워크 차단),
  opt-in `bypassPermissions`(`--dangerously-skip-permissions`, 무샌드박스).
- 시작 시 `validate_roles`가 필수 역할 누락·잘못된 enum 값을 검사해 즉시 종료.

---

## 6. 라우팅 & 위험도 (설계 논쟁 지점)

### 6.1 라우팅 (`route_task`)
- `--task-type` 명시 시 키워드 분류를 **우회**(탈출구). `auto`면 키워드 점수.
- backend/frontend/docs 명사 키워드 점수 → 최고점 선택, 0점이면 docs(읽기 전용) 기본.
- **구현 의도 가드**: 명사 점수가 docs를 가리켜도 구현 동사(`구현/수정/추가/…`,
  `implement/refactor/fix/…`)가 있으면 backend/frontend로 되돌린다. 영어 동사는
  `\b` 단어경계로 오염 방지(`prefix`→`fix` 오매칭 차단), 파일명 속 `design` 하나가
  도메인을 뒤집지 않도록 frontend는 신호 2개 이상일 때만 택함.
- **안전 비대칭**: 구현→docs 오분류(=출력 없음)가 docs→backend 오분류(=경로만
  무거움, 출력은 맞음)보다 나쁘다 → 애매하면 구현 라우트로 편향.

### 6.2 위험도 — 두 층 (⚠️ db만이 아니다)

**1층 · 라우팅의 `risk_level`**: `db_score>0`(DB_TERMS 20개) → `subtype=db`/high,
`high_risk_score>0`(`migration/auth/payment/production/backfill/rollback`) → high,
그 외 backend/frontend medium, docs/review low. (frontend는 **내용 무관 항상 medium**)

**2층 · 승인 게이트** (`routed_common.approval_required` / `is_high_risk`):
```
approval_required = task_type∈{backend,frontend} and (--require-human-approval or is_high_risk)
is_high_risk      = risk_level=="high"  or  subtype=="db"  or  any(HIGH_RISK_REQUEST_TERMS)
```

- **알려진 잔여(토론거리)**:
  - *중복* — 1층 `HIGH_RISK_TERMS` ≡ 2층 `HIGH_RISK_REQUEST_TERMS`(동일 6단어),
    `subtype=="db"`도 db면 이미 high라 사실상 중복 → 한 곳만 고치면 어긋날 표면.
  - *공백* — frontend는 절대 자동 high가 안 됨(플래그 없으면 게이트 미발동).
    판정이 전부 키워드 substring이라 리스트 밖 위험(`drop`/`truncate` 등)은 놓칠 수 있음.
  - *교훈* — 키워드 substring 매칭은 요청문이 하네스 자체 코드를 설명하면 오작동한다
    (`docs/specs` 경로 → docs 오분류, `db_score` 심볼 → db 게이트 오발). 둘 다 교정됨.

---

## 7. 모델 정책

```
Claude 기본     : sonnet          Codex          : gpt-5.5
Claude high-risk: opus            Codex effort   : high (재현용 저장, CLI 주입 안 함)
Claude effort   : high
Claude hi effort: xhigh   ← high-risk(opus)에 주는 "ultracode급" 추론 강도
```

**구현자 선택 매트릭스** (`choose_implementer`, 리뷰어는 자동으로 반대):

| 조건 | 구현자 | 리뷰어 |
|------|--------|--------|
| `--implementer claude` | claude | codex |
| `--implementer codex` | codex | claude |
| auto · frontend | codex | claude |
| auto · backend (일반) | claude | codex |
| auto · backend + test/build/lint/diff-fix 성격 | codex | claude |
| docs / review / read-only | (구현 없음) | — |

---

## 8. 안전 & 검증

**안전 장치**
- `--read-only`: Codex 샌드박스 `read-only` 강제 + 구현 단계 스킵.
- 타깃 워크스페이스에 유효한 Git HEAD 베이스라인이 없으면 구현 라우트 **차단**.
- decompose는 구현 절대 안 함. simple은 레거시 동작 보존.
- 하네스는 **자동 커밋/푸시/업로드하지 않는다** — diff는 사람이 검토.
- `--max-agent-calls`: 서브프로세스 총 호출 예산. 소진 시 `stopped_by_budget.md` + exit 0.
- `--stop-after <stage>`: 지정 단계 후 정지(`stopped_after.md`).

**검증 (테스트 스위트 없음)**
- `--dry-run`: 모든 프롬프트 + `*_command.json`을 CLI 호출 없이 렌더링. 리팩터
  전/후 산출물의 **바이트 동일성(SHA-256)** 으로 회귀를 잡는다. dry-run은 예산에
  포함 안 됨.
- **도그푸딩**: 이 하네스로 이 하네스를 구현·리뷰했고, 크로스-모델 리뷰가 실제
  안전 회귀(DB 게이트 매칭)와 설계 갭(resume 경로)을 잡아냈다.

---

## 9. 프로젝트별 워크스페이스 레지스트리

하네스가 여러 타깃에 재사용되므로 프로젝트 단위로 상태를 분리한다.

```
--project <name>  →  projects/<name>/config.json  (workspace + 선택 override)
                     projects/<name>/runs/<stamp>/ (그 프로젝트 실행 이력만)
```
설정 우선순위(한 겹 확장, 얕은 병합):
```
per-project config  >  global config  >  AUTOAGENT_WORKSPACE(env)  >  하드코딩 default
```
`--project` 미지정 시 동작·산출물 경로는 **오늘과 100% 동일**(하위호환).

---

## 10. 하네스 비교 관점 (토론 프레임)

동료 하네스를 이 표의 각 축에 나란히 놓고 비교해 보자.

| 비교 축 | AutoAgent의 선택 | 물어볼 질문 |
|---------|------------------|-------------|
| **오케스트레이션** | 결정적 Python 스크립트 (루프·분기 코드) | 에이전트 자율 루프인가, 고정 파이프라인인가? |
| **모델 다양성** | 크로스-모델 (구현자 ↔ 반대 모델 리뷰) | 단일 모델인가, 교차 검증인가? |
| **휴먼 인 더 루프** | high-risk/db 승인 게이트 + resume | 언제, 무엇을 기준으로 사람이 개입하나? |
| **행동의 소재** | 프롬프트(중립 md) vs 코드 | 새 행동을 코드로 짜나, 프롬프트로 바꾸나? |
| **상태·감사성** | run 디렉터리에 단계별 산출물 전부 | 실행이 재현·추적 가능한가? |
| **타깃과의 관계** | 외부·비침투(cwd로만 접근, 자동 커밋 없음) | 타깃 레포에 침투하나, 분리돼 있나? |
| **위험 판정** | 키워드 substring + 고정 리스트 | 규칙 기반인가, 모델 판단인가? 오탐/미탐은? |
| **검증** | 테스트 없음 → dry-run 바이트 패리티 + 도그푸딩 | 하네스 자체의 회귀를 어떻게 막나? |

### 열어둔 논쟁거리
1. 키워드 라우팅은 싸지만 취약하다 — 모델 기반 라우팅으로 갈 가치가 있나?
2. 위험 리스트 중복·frontend 게이트 공백을 통합/보강할까, 단순함을 지킬까?
3. 크로스-모델의 비용(두 모델 호출) 대비 리뷰 품질 이득은 충분한가?
4. task_graph 실행은 승인 게이트를 유지한 채 `--resume`로 병렬 실행기가 담당하는
   쪽으로 구현됐다(라이브 미검증) — 이 경계를 더 밀어 게이트 자체를 줄일 가치가 있나?

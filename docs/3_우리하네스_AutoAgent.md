# 문서 3 · AutoAgent 분석 — 우리 하네스

> 대상: AutoAgent (크로스모델·승인게이트 오케스트레이터, 이 레포)
> 팀 토론용 분석 자료 · 작성일 2026-07-12 · 짝 문서: `3_우리하네스_AutoAgent.html`
> 정본 레퍼런스: `README.md` · 상세 개요: `docs/AutoAgent_하네스개요.md`

---

## 0. 한 줄 요약

> **AutoAgent는 Claude Code CLI와 Codex CLI를 서브프로세스로 구동해, 별도의 타깃
> 워크스페이스를 두 모델이 교대로 구현·리뷰하게 조율하는 로컬 오케스트레이터다.**
> 비결정적 에이전트를 결정적 Python 제어 흐름으로 감싸고, 위험한 변경은 사람이 승인
> 게이트를 통과시켜야 코드가 바뀐다.

프레임워크 두께 스펙트럼상 **중간(medium)**. 하네스가 타깃 레포 **밖**에 있는
외부·비침투 오케스트레이터.

## 1. 5개의 설계 베팅

| # | 베팅 | 무엇을 | 왜 |
|---|------|--------|----|
| 1 | **크로스-모델 리뷰** | 리뷰어는 항상 구현자의 반대 모델 | 같은 모델의 자기검토는 같은 맹점 공유 → 모델 교차로 적대적 다양성 |
| 2 | **코드가 조율, 프롬프트가 "무엇"** | 행동은 `prompts/**/*.md`, 오케스트레이션만 Python | Codex는 Claude 스킬을 못 읽음 → 두 CLI가 stdin으로 공유하는 중립 채널에 행동을 둔다 |
| 3 | **사람 승인 게이트** | high-risk/db는 코드 변경 전 정지, `--resume`으로만 진행 | 위험 변경은 사람이 계획을 본 뒤에만. "재개 실행 = 승인 행위" |
| 4 | **외부·비침투** | 타깃 안 건드림, 상태는 하네스 레포에만, 자동 커밋 없음 | 여러 프로젝트에 재사용되는 외부 도구 |
| 5 | **결정적 제어 흐름** | 루프·분기·예산·게이트 전부 Python | 재현·감사 가능, "얼마나 도느냐"를 사람이 통제 |

## 2. 실행 모델

```
run.py --workspace C:\...\TargetProject --request "..."
   └─ 서브프로세스로 claude.cmd / codex.cmd 호출
        ├─ cwd    = config.workspace   ← 타깃 프로젝트 (이 하네스 레포 아님!)
        ├─ stdin  = render_template(prompt.md, values)  ← "무엇을 할지"
        └─ stdout = runs/<stamp>/NN_*.md 로 캡처         ← 산출물
```

서브프로세스는 **타깃 워크스페이스**를 cwd로 실행된다. 이 하네스 레포는 편집 대상이
아니라 조율자다. 프롬프트는 `{VAR}` 자리표시자를 `render_template`이 치환해 stdin으로 주입.

## 3. routed 파이프라인 (주력 워크플로우)

```
route_task ──► route.json
   │
   ▼ [preamble / 계획]
   context(claude) ──► architecture(claude) ⇄ validation(codex)  ← 검증 루프
   │
   ▼ 읽기전용 or docs/review? ── yes ──► docs 라우트(평가+보고, 코드변경 없음)
   │ no
   ▼ [승인 게이트] approval_required? ── yes ──► checkpoint + 정지, --resume 대기
   │ no
   ▼ git 베이스라인 확인 ── 유효 HEAD 없으면 구현 차단
   │
   ▼ [구현 라우트]
   impl(04) ──► review(05) ⇄ fix(06) ──► final-review(07,codex) ──► eval(08,codex) ──► report(09,claude)
```

- **리뷰어는 항상 구현자의 반대 모델.** backend 기본 구현자=claude → 리뷰어=codex,
  frontend 기본 구현자=codex → 리뷰어=claude.
- 워크플로우 3종: `simple`(Claude plan→Codex execute→Claude review) / `routed`(위) /
  `decompose`(대규모 요청 → task_graph.json, 구현 절대 안 함).

## 4. 선언형 역할 레지스트리

`roles.default.json`이 9개 역할을 데이터로 선언하고, `resolve_role`이 route/모델 정책을
적용해 실행 속성(`ResolvedRole`)으로 해석한다. 시작 시 `validate_roles`가 정합성 검사.

| 역할 | agent | 티어 | high-risk 조건 | 변경 | 권한/샌드박스 |
|------|-------|------|----------------|------|----------------|
| context | claude | standard | none | ✗ | plan |
| architect | claude | tiered | any_high_risk | ✗ | plan |
| validation | codex | standard | none | ✗ | from_read_only |
| implementer | route | tiered | backend_high_risk_mutating | ✓ | write |
| reviewer | route | standard | none | ✗ | plan |
| fix | route | tiered | backend_high_risk_mutating | ✓ | write |
| final-review | codex | standard | none | ✗ | configured |
| evaluation | codex | standard | none | ✗ | from_read_only |
| report | claude | standard | none | ✗ | plan |

`tiered` + high-risk 충족 → 모델 opus, effort xhigh 승격. claude mutating 권한: 기본
`acceptEdits`(편집만 자동, bash·네트워크 차단), opt-in `bypassPermissions`(무샌드박스).

## 5. 라우팅 & 위험도

- **라우팅**(`route_task`): `--task-type` 명시 시 키워드 분류 우회. auto면 명사 키워드
  점수 → 최고점, **구현 의도 가드**(구현 동사가 있으면 docs 오분류를 backend/frontend로 교정).
- **위험도 두 층**: ①라우팅 `risk_level`(db_score → subtype=db/high, HIGH_RISK_TERMS →
  high) ②승인 게이트 `is_high_risk`(risk_level=="high" or subtype=="db" or
  HIGH_RISK_REQUEST_TERMS). frontend는 내용 무관 항상 medium.
- **알려진 잔여**: 두 고위험 리스트 중복, frontend 게이트 공백, 키워드 substring 취약
  (자기 코드 심볼이 오발 유발 — 라우팅·DB 게이트 모두 교정됨).

## 6. 프레임워크 4축 자기평가

| 축 | AutoAgent의 상태 | 평 |
|----|------------------|----|
| **Context** | run 디렉터리 단계별 산출물 + checkpoint(핸드오프는 resume 한정). CODEMAPS·worklog 같은 상시 상태 외부화는 약함 | ★★ |
| **Tool** | MCP 거의 없음. 행동을 프롬프트 중립채널로 전달(Codex가 Claude 스킬 못 읽어서) | ★ |
| **Permission** | **사람 승인 게이트(high-risk/db)** + claude 권한모드 + codex 샌드박스 + git 베이스라인. 경로 격자가드는 없음 | ★★ |
| **Verification** | 테스트 스위트 없음 → dry-run 바이트 패리티 + **크로스모델 상호 리뷰**. 실행 테스트 게이트는 없음 | ★ |
| **Orchestration** | 9역할 고정 파이프라인 + 예산(--max-agent-calls) + --stop-after. decompose 제한적 병렬 실행기(worktree, 라이브 미검증) | ★★ |

**덱 패턴 대응**: Plan→Execute(preamble) ✅ / Generate↔Review(크로스모델) ✅ /
Router(route_task) ✅ / **Human Approval(게이트+resume) ✅** / Parallel 제한적(미검증) /
Verification Loop(실테스트 없음) ⚠️.

## 7. 안전 & 검증

- `--read-only`(Codex read-only + 구현 스킵) / 유효 Git 베이스라인 없으면 구현 차단 /
  decompose 구현 안 함 / **자동 커밋·푸시 없음**(diff는 사람) / `--max-agent-calls` 예산 /
  `--stop-after` 단계 정지.
- **검증**: 테스트 없음 → `--dry-run`이 프롬프트 + `*_command.json`을 CLI 호출 없이 렌더,
  리팩터 전/후 **바이트 동일성(SHA-256)** 으로 회귀 감지.
- **도그푸딩**: 이 하네스로 이 하네스를 구현·리뷰했고, 크로스모델 리뷰가 실제 안전
  회귀(DB 게이트 매칭)와 설계 갭(resume 경로)을 잡아냈다.

## 8. 프로젝트별 워크스페이스 레지스트리

`--project <name>` → `projects/<name>/config.json`(workspace + 선택 override) +
`projects/<name>/runs/<stamp>/`. 설정 우선순위(얕은 병합):
`per-project > global > AUTOAGENT_WORKSPACE(env) > 하드코딩 default`.
`--project` 미지정 시 동작·산출물 경로는 오늘과 100% 동일(하위호환).

## 9. 강점 & 빈틈 (덱 렌즈)

**강점**: ①크로스모델 적대적 리뷰(덱의 "자기평가 편향" 대응) ②high-risk 사람 승인
게이트(덱의 Permission 3분할 중 "승인" 구현) ③외부·비침투(재사용성) ④high-risk opus/xhigh
승격 ⑤결정적 파이프라인 + resume 핸드오프.

**빈틈**: ①실행 테스트 게이트 없음(덱의 "계산적 검증"이 약함 — dry-run·리뷰로 대체)
②병렬은 제한적(decompose 실행기, 라이브 미검증) ③경로 격자가드·시크릿 차단 없음
④상시 상태 외부화(worklog류) 약함 ⑤MCP·도구층 미미 ⑥키워드 라우팅 취약.

> **한 줄 평**: harness-test가 "가드레일·결정론 검증"으로 강하다면, AutoAgent는
> "크로스모델 + 사람 승인 + 외부 분리"로 강하다. 덱의 이상형에서 **Permission의 '승인
> 루프'와 자기평가 편향 대응**을 정면으로 구현했고, **Verification(테스트)·Tool**이
> 비어 있고, 병렬은 제한적 구현(미검증)이다.

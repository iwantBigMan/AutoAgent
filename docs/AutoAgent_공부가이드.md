# AutoAgent 공부 가이드 — 프로젝트를 밑바닥부터 이해하기

> 이 문서는 **AutoAgent 코드베이스 자체를 이해하기 위한 공부용**이다. 발표 대본이 아니라,
> "이게 무슨 문제를 푸는가 → 어떻게 도는가 → 어느 파일이 무엇을 하는가"를 순서대로 익히도록
> 썼다. 각 항목은 실제 파일·함수 이름을 달아, 읽다가 바로 코드로 건너뛸 수 있게 했다.
>
> 더 깊은 레퍼런스: [`README.md`](../README.md), [`docs/AutoAgent_하네스개요.md`](AutoAgent_하네스개요.md).

---

## 목차
1. [한 문장 정의와 멘탈 모델](#1-한-문장-정의와-멘탈-모델)
2. [절대 안 흔들리는 4가지 원칙(불변식)](#2-절대-안-흔들리는-4가지-원칙불변식)
3. [전체 지도 — 디렉터리와 모듈](#3-전체-지도--디렉터리와-모듈)
4. [실행 한 번의 흐름 (routed 워크플로우)](#4-실행-한-번의-흐름-routed-워크플로우)
5. [3가지 워크플로우](#5-3가지-워크플로우)
6. [핵심 서브시스템 심화](#6-핵심-서브시스템-심화)
7. [산출물(artifacts) — `runs/` 읽는 법](#7-산출물artifacts--runs-읽는-법)
8. [설정(config)](#8-설정config)
9. [검증 방법 — 테스트가 없다](#9-검증-방법--테스트가-없다)
10. [알아둘 함정과 리스크](#10-알아둘-함정과-리스크)
11. [용어집](#11-용어집)
12. [공부 순서 추천](#12-공부-순서-추천)

---

## 1. 한 문장 정의와 멘탈 모델

**AutoAgent = Claude Code CLI(`claude.cmd`)와 Codex CLI(`codex.cmd`)를 서브프로세스로 오케스트레이션해서,
별도의 대상 프로젝트를 교차-모델로 구현/리뷰하게 만드는 로컬 하네스.**

멘탈 모델 3개로 잡으면 쉽다:

- **하네스는 "지휘자"이지 "연주자"가 아니다.** AutoAgent 자체에는 LLM이 없다. 두 CLI(연주자)를
  프롬프트로 지휘하고, 그 출력을 파일로 모으고, 다음 단계로 넘긴다.
- **코드는 오케스트레이션, 프롬프트가 "무엇을 할지"를 담는다.** 에이전트의 행동을 바꾸려면
  Python이 아니라 `prompts/**/*.md`를 바꾼다. Python은 "언제 누구를 어떤 순서로 부르는가"만 결정.
- **작업 대상은 이 레포가 아니다.** 서브프로세스는 `cwd = config.workspace`(예:
  `C:\Users\systran\Desktop\LanguageDetection`)에서 돈다. 이 레포는 "지휘 코드"일 뿐, 편집 대상은
  항상 대상 프로젝트다.

---

## 2. 절대 안 흔들리는 4가지 원칙(불변식)

이 넷은 코드 곳곳에 박혀 있어서, 먼저 외우면 나머지가 다 설명된다.

1. **리뷰어 = 구현자의 반대 모델.** backend면 Claude가 구현하고 Codex가 리뷰, frontend면 반대.
   → `autoagent/routing.py`의 `choose_implementer()`. *왜?* 같은 모델을 두 번 돌리면 사각지대가
   상관되지만, 다른 프론티어 모델은 실패 모드가 달라 독립적으로 결함을 잡는다.
2. **작업 디렉터리는 대상 워크스페이스.** 모든 CLI 호출은 `cwd=config.workspace`.
3. **행동은 프롬프트, 순서는 코드.** 공통 지침은 `prompts/*.md`(중립 채널)에 둔다.
   Codex는 Claude 스킬을 못 읽기 때문에, 코드/스킬에 박으면 한쪽에만 먹힌다.
4. **되돌리기 힘든 일 앞에는 사람 게이트.** 고위험/DB 라우팅은 사람 승인 전 구현을 시작하지 않는다.
   → `autoagent/workflows/routed_common.py`의 `approval_required()`.

---

## 3. 전체 지도 — 디렉터리와 모듈

```
AutoAgent/
├─ run.py                       진입점(얇음). cli.py로 위임
├─ autoagent/
│  ├─ cli.py                    인자 파싱 → config 로드 → 워크플로우 디스패치, 00_request/metadata 기록
│  ├─ config.py                 Config 데이터클래스 + 로드(파일>env>기본). MCP·모델·샌드박스 필드
│  ├─ routing.py                route_task()(위험·subtype 판정) · choose_implementer()(반대모델)
│  ├─ roles.py                  역할 레지스트리 로드 + resolve_role() → ResolvedRole
│  ├─ runner.py                 ★ run_process()(유일한 서브프로세스 실행점) + 명령 조립 헬퍼
│  ├─ safety.py                 git_baseline_status · codex_sandbox_for · review_needs_changes
│  ├─ artifacts.py              write_text/write_json/write_metadata + 프롬프트 별칭
│  ├─ mcp.py                    write_claude_mcp_config · check_mcp_symmetry (opt-in)
│  ├─ worktree.py               git worktree/통합 브랜치 헬퍼 (decompose용)
│  └─ workflows/
│     ├─ simple.py              plan → 구현 → 리뷰 (최소)
│     ├─ routed.py              ★ routed 오케스트레이터 + resume 진입점
│     ├─ routed_preamble.py     context → architecture ⇄ validation (계획 단계)
│     ├─ routed_impl.py         implement → review ⇄ fix → final-review (구현 루프)
│     ├─ routed_docs.py         읽기 전용 라우트(docs/review)
│     ├─ routed_common.py       승인 게이트·체크포인트·eval·report 등 공용
│     ├─ decompose.py           작업을 task_graph로 분해
│     └─ task_exec.py           ★ task_graph를 워크트리 병렬 실행(wavefront)
├─ prompts/**/*.md              에이전트에게 주는 "무엇을 할지"(render_template로 {VAR} 치환)
├─ roles.default.json           역할 정의(기본). roles.json으로 프로젝트별 오버레이
└─ runs/YYYYMMDD_HHMMSS/        실행 산출물(gitignored)
```

`★`가 붙은 4개(`runner.run_process`, `workflows/routed.py`, `workflows/routed_impl.py`,
`workflows/task_exec.py`)가 이 프로젝트의 심장이다.

---

## 4. 실행 한 번의 흐름 (routed 워크플로우)

`python run.py --workflow routed --task-type backend --request "..."`를 돌리면 벌어지는 일:

```
run.py → cli.py
  1. config 로드 · run_dir 생성(runs/타임스탬프/) · 00_request.md, metadata.json 기록
  2. routing.route_task() 로 route.json 산출 (task_type, risk_level, subtype, impl/review 에이전트)
  3. workflows/routed.run_routed_workflow() 진입
      │
      ├─ [계획] routed_preamble.run_preamble()
      │     context()      → 01_claude_context.md      (Claude, plan)
      │     architecture() → 02_claude_architecture.md  (Claude, plan)
      │     validation()   → 03_codex_validation.md     (Codex, read-only)   ⇄ 필요시 반복
      │
      ├─ [게이트] routed_common.approval_required()?
      │     예(고위험/DB) → write_checkpoint() + block_for_human_approval()
      │                     → approval_status.json = waiting_for_human_approval → 정지
      │                     (사람이 검토 후 `python run.py --resume <run_dir>`)
      │     아니오 → 계속
      │
      └─ [구현] routed_impl.run_impl_review_fix()   ← resume도 여기로 재진입
            implement → 04_{agent}_{task}_impl.md
            for r in 1..max_review_rounds:
                review → 05_..._review_r{r}.md
                review_needs_changes()?  아니오 → break
                fix    → 06_..._fix_r{r}.md
            final-review → 07_codex_final_review.md
            evaluation  → 08_codex_evaluation.md   (routed_common.run_evaluation)
            report      → 09_claude_final_report.md (routed_common.run_final_report)
```

**핵심 관찰**: 모든 단계는 결국 `runner.run_process()` 한 함수를 통해 CLI를 부른다. 단계마다
다른 건 (a) 어떤 프롬프트를 넣는가, (b) 어떤 명령을 조립하는가(claude vs codex, 권한/샌드박스)뿐이다.

---

## 5. 3가지 워크플로우

`--workflow simple|routed|decompose`로 고른다.

| 워크플로우 | 파일 | 성격 |
|---|---|---|
| **simple** | `workflows/simple.py` | plan(Claude) → 구현(Codex) → 리뷰. 가장 얇음. 빠른 확인용 |
| **routed** | `workflows/routed.py` | 위 §4의 9역할 풀 파이프라인. 위험 판정·승인 게이트 포함. **기본 주력** |
| **decompose** | `workflows/decompose.py` + `task_exec.py` | 큰 작업을 task_graph로 쪼개 **워크트리 병렬** 실행 |

**decompose의 특징(§6.6에서 상세)**: 승인 게이트 후 `--resume`이 task_graph를 wavefront로
병렬 실행 — 각 노드가 자기 워크트리에서 격리 구현·반대모델 리뷰 후, 통합 브랜치
`aa-integration/<stamp>`로 머지된다. 동시성은 `config.max_parallel_lanes`(기본 2, 1이면 순차).

---

## 6. 핵심 서브시스템 심화

### 6.1 라우팅 — `autoagent/routing.py`
- `route_task(task_type, request, requested_implementer)` → dict(`task_type`, `risk_level`,
  `subtype`, `impl/review agent`). `task_type != auto`면 명시값을 쓰고, `auto`면 요청 텍스트를
  키워드 스코어링한다.
- 위험 판정: DB 관련 토큰 → `subtype="db"` → `risk_level="high"`. HIGH_RISK_TERMS
  (migration/auth/payment/production/backfill/rollback 등) 매칭 시 `high`.
- `choose_implementer(...)` → `(impl_agent, review_agent, reason)`. **리뷰어는 항상 구현자의 반대.**
  frontend 기본 Codex 구현, backend는 test/build/diff-fix 키워드면 Codex, 아니면 Claude.

### 6.2 역할 레지스트리 — `autoagent/roles.py` + `roles.default.json`
- 역할(context/architect/validation/implementer/reviewer/fix/final-review/evaluation/report)을
  **JSON으로 선언**한다. `roles.json`을 두면 프로젝트별로 오버레이(코드 수정 없이 속성 교체).
- 각 역할 필드: `agent`(claude/codex/route), `model_tier`, `high_risk_condition`, `effort`,
  `mutating`(쓰기 여부), `permission`(claude용) / `sandbox`(codex용).
- `resolve_role(entry, *, config, route, request, agent, read_only)` → **ResolvedRole**
  (`agent, model, effort, mutating, permission_mode, skip_permissions, sandbox`). 이게 dry-run과
  실제 실행이 **같은 명령**을 만들도록 하는 단일 계산 지점이다(바이트 패리티의 핵심).

### 6.3 서브프로세스 실행 — `autoagent/runner.py` ★
- `run_process(*, name, command, prompt, cwd, out_dir, timeout_seconds)` → stdout 문자열.
  **모든 에이전트 호출이 지나는 유일한 관문.** 프롬프트를 stdin으로 주입하고
  prompt/command/stdout/stderr/exit_code를 `out_dir`에 파일로 남긴다.
- 명령 조립:
  - `claude_command(...)` → `["claude","-p","--model",...,"--permission-mode",...,"--effort",...]`.
    필요 시 `--mcp-config`/`--strict-mcp-config`, `--allowedTools`도 붙인다.
    비-mutating=`plan`, mutating=`acceptEdits` 또는 opt-in `--dangerously-skip-permissions`.
  - `codex_exec_command(...)` → `["codex","-c",'model_reasoning_effort="<강도>"',
    "--ask-for-approval",<never>,"exec","-m",모델,"-C",워크스페이스,"--sandbox",<모드>,...]`.
    강도는 medium 기본, high-risk 구현/수정일 때만 high로 승격(`-c`는 exec 앞 전역 플래그).

### 6.4 안전 가드 — `autoagent/safety.py`
- `git_baseline_status(workspace)` → 커밋된 HEAD가 있는 git 저장소인지 확인. 구현 라우트는 True일 때만 진행.
- `codex_sandbox_for(read_only, configured)` → read_only면 `"read-only"`, 아니면 설정값.
- `review_needs_changes(review)` → 리뷰 텍스트가 "수정 필요"인지 판정(`REVIEW_STATUS:` 마커 우선).
  이게 review⇄fix 루프의 종료 조건.

### 6.5 승인 게이트 & resume — `routed_common.py` / `routed.py`
- `approval_required(args, route, request)`: `task_type in {backend,frontend}`이고
  (`--require-human-approval` 또는 `is_high_risk(...)`)면 True.
- `block_for_human_approval(run_dir, route)`: `approval_status.json`(waiting) + `approval_required.md`를
  쓰고 `ROUTED_STATUS: waiting_for_human_approval`를 stdout에 찍고 정지.
- `write_checkpoint(...)`: `checkpoint.json`에 재개에 필요한 상태(request/workspace/route/…)를 저장.
- `resume_routed_workflow(args, config)`: checkpoint를 읽어 계획 산출물을 복원 → `approval_status.json`을
  approved로 갱신 → 구현 라우트로 직행. **`--resume`을 돌리는 행위 자체가 승인**이다.

### 6.6 decompose 병렬 실행 — `task_exec.py` + `worktree.py` ★
- `topological_waves(tasks)`: 의존성 순서대로 배치(wave)로 나눈다(사이클이면 에러).
- `run_node(...)`: 노드마다 (1) `_GIT_LOCK` 아래 워크트리 생성(baseline SHA에서 분기),
  (2) `run_impl_review_fix()`로 구현·반대모델 리뷰·수정, (3) `git add -A` + `aa: node {id}` 커밋.
- `_integrate_and_cleanup(...)`: 통합 브랜치 `aa-integration/<stamp>`를 baseline에서 만들고, 각 lane
  브랜치를 `--no-ff` 머지. 충돌 시 정지하고 워크트리 보존.
- worktree 헬퍼: `add_worktree`/`remove_worktree`/`delete_branch`/`create_integration_branch`/
  `merge_branch`/`scope_violations`(변경 파일이 허용 범위를 벗어났는지 soft 경고).

### 6.7 MCP — `autoagent/mcp.py` (opt-in)
- `write_claude_mcp_config(config, out_dir)`: `config.mcp_servers`가 있으면 run_dir에
  `.aa_mcp.json`을 쓰고 경로를 반환 → `claude_command`가 `--mcp-config`로 넘김. 비면 None(기존과 동일).
- `check_mcp_symmetry(config)`: Claude 서버 목록 vs Codex `~/.codex/config.toml [mcp_servers.*]`를
  비교해 **soft 경고**(차단 아님). 비대칭이면 크로스검증이 한쪽만 도구 보게 되므로.
- **주의**: 헤드리스 `plan` 모드는 allowlist 없이는 MCP 툴을 거부한다 → Claude는
  `config.mcp_allowed_tools`(→`--allowedTools`)가 있어야 검증 역할에서 MCP를 쓴다. 네트워크 MCP는
  Codex 샌드박스가 원천 차단 → 크로스검증엔 **로컬 stdio 서버만** 대칭 성립.

---

## 7. 산출물(artifacts) — `runs/` 읽는 법

한 번 실행하면 `runs/YYYYMMDD_HHMMSS/`에 번호순으로 쌓인다. 번호가 곧 파이프라인 순서다.

| 파일 | 의미 |
|---|---|
| `00_request.md` / `metadata.json` | 원 요청 / 실행 메타(워크플로우·모델·플래그) |
| `route.json` | 라우팅 결정(task_type, risk_level, subtype, impl/review 에이전트) |
| `01_claude_context.md` | 맥락 분석(Claude) |
| `02_claude_architecture.md` | 아키텍처 계획(Claude) |
| `03_codex_validation.md` | 계획 검증(Codex, 반대편) |
| `approval_status.json` / `approval_required.md` / `checkpoint.json` | 승인 게이트 상태·재개 정보 |
| `04_{agent}_{task}_impl.md` | 구현 결과 |
| `05_..._review_r{r}.md` / `06_..._fix_r{r}.md` | 리뷰 / 수정(라운드별) |
| `07_codex_final_review.md` | 최종 감사(Codex 고정) |
| `08_codex_evaluation.md` / `09_claude_final_report.md` | 평가 / 최종 보고 |
| `{name}_prompt.md` / `{name}_command.json` / `{name}_stdout.md` / `..._stderr.txt` | 단계별 입력·명령·출력 |
| `task_graph.json` (decompose) / `integration_report.md` | 분해 그래프 / 통합 결과 |

**공부 팁**: 어떤 단계가 이상하면 그 단계의 `*_command.json`(실제로 어떤 CLI 인자가 갔나)과
`*_stderr.txt`(에러)를 먼저 봐라. dry-run이면 `*_prompt.md`와 `*_command.json`만 생기고 CLI는 안 불린다.

---

## 8. 설정(config)

- 파일: `autoagent.config.json` (**gitignored**). 우선순위: **config 파일 > `AUTOAGENT_WORKSPACE` env > 하드코딩 기본**.
- 로드: `autoagent/config.py`의 `Config` 데이터클래스.
- 자주 보는 필드:
  - `workspace` — 대상 프로젝트 경로(필수급).
  - `claude_command`/`codex_command`, `claude_model`/`claude_high_risk_model`, `codex_model`.
  - `claude_effort`/`claude_high_risk_effort`, `claude_impl_permission`(acceptEdits/bypassPermissions).
  - `codex_sandbox`(workspace-write/read-only), `codex_approval`(never/ask).
  - `timeout_seconds`, `max_parallel_lanes`, `default_max_agent_calls_review|implementation`.
  - `mcp_servers`(dict), `mcp_allowed_tools`(list) — **둘 다 opt-in, 기본 비어 있음**.

---

## 9. 검증 방법 — 테스트가 없다

- **테스트 스위트 없음.** 대신 **dry-run 바이트 패리티**로 회귀를 막는다:
  ```
  python .\run.py --dry-run --workflow routed --task-type backend --request "..."
  ```
  CLI를 부르지 않고 모든 프롬프트 + `*_command.json`을 렌더한다. dry-run은 `--max-agent-calls`에
  안 잡힌다.
- 원리: `resolve_role()`가 dry-run/실제 경로 **이전**에 명령을 계산하므로, dry-run에서 본 명령이
  실제 실행 명령과 동일하다. 그래서 "명령이 맞게 조립되는지"는 dry-run으로 확신할 수 있다.
- 한계: dry-run은 **CLI 내부 동작(실제 편집·MCP 호출·병렬 머지)은 검증하지 못한다.** 그건 라이브 실행이 필요.

---

## 10. 알아둘 함정과 리스크

전체 경험 로그는 바탕화면 `autoagent-retrospective.html` 참고. 핵심만:

- **병렬 실행기 라이브 미검증** `[HIGH]` — `task_exec.py`/`worktree.py`는 dry-run·단위·코드리뷰까지만.
  실전 e2e는 아직 확인 안 됨.
- **MCP는 opt-in + plan 모드 제약** — 안 켜면(=config 비어 있으면) 모든 MCP가 안 붙는다. Claude 검증
  역할은 `--allowedTools`가 있어야 MCP를 쓴다. 네트워크 MCP는 Codex 불가.
- **라우팅 키워드 오발** — substring 매칭이라 `comfortable→table`류 무해한 과다발동 잔존. `/aa` 등에서는
  `--task-type`을 명시하면 안전.
- **환경** — Windows + Git Bash. `LF will be replaced by CRLF` 경고는 무해. **`main` 직접 push 차단** →
  기능 브랜치 + PR. 커밋/푸시는 사용자가 요청할 때만.

---

## 11. 용어집

- **하네스(harness)**: 모델을 감싸는 4축(Context/Tool/Permission/Verification)의 총체. 여기선
  "지휘 코드 + 프롬프트 + 게이트 + 검증".
- **routed / preamble / impl**: routed는 풀 워크플로우, preamble은 계획 단계(context~validation),
  impl은 구현 루프(implement~report).
- **route / risk_level / subtype**: 라우팅 결정. risk_level ∈ {low, medium, high}, subtype ∈
  {db, api, service, infra, ui, docs, review, …}.
- **ResolvedRole**: 역할 선언(JSON)을 실제 실행 파라미터(모델·권한·샌드박스)로 해석한 결과.
- **승인 게이트 / resume**: 고위험 계획에서 멈추는 지점, `--resume`으로 이어감.
- **wavefront / lane / 통합 브랜치**: decompose 병렬 실행의 배치 단위 / 개별 노드 브랜치 /
  `aa-integration/<stamp>`.
- **바이트 패리티**: dry-run과 실제 실행이 동일한 CLI 명령을 만든다는 성질.

---

## 12. 공부 순서 추천

1. **이 문서 §1~§2** — 정의와 4대 불변식을 먼저 머릿속에 박는다.
2. **dry-run 1회** — `python run.py --dry-run --workflow routed --task-type backend --request "테스트"`
   를 돌리고, 생성된 `runs/.../` 폴더의 `route.json` → `01~09_*` → `*_command.json`을 순서대로 열어본다.
   (§4·§7과 대조하며 읽으면 흐름이 손에 잡힌다.)
3. **코드 진입점 따라가기** — `run.py` → `cli.py` → `workflows/routed.py` → `routed_preamble.py` →
   `routed_impl.py`. 각 파일의 한국어 docstring부터 읽는다.
4. **단일 관문 확인** — `runner.py`의 `run_process()`와 `claude_command()`/`codex_exec_command()`를
   읽어 "모든 게 여기로 모인다"를 눈으로 확인.
5. **심화** — 라우팅(`routing.py`) → 역할(`roles.py` + `roles.default.json`) → 승인/재개
   (`routed_common.py`) → 병렬(`task_exec.py`) 순으로 §6을 따라간다.
6. **README.md** — 전체 CLI 옵션과 설계 배경을 마지막에 정독.

> 한 줄 요약: **AutoAgent은 "두 프론티어 모델을 밖에서 지휘하며, 반대 모델로 교차검증하고,
> 위험한 지점엔 사람 게이트를 두는" 순수 오케스트레이션 하네스다.**

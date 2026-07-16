# AutoAgent

Claude Code CLI와 Codex CLI를 함께 구동하는 로컬 하네스입니다.

기본 워크플로우:

```text
Claude plan -> Codex execute -> Claude review
```

routed 워크플로우는 역할 기반 라우팅을 더합니다:

```text
Claude context -> Claude architecture -> Codex validation -> route -> implementation/review/evaluation/final report
```

decompose 워크플로우는 대규모 요청을 구현 없이 리뷰된 task graph로 분해합니다:

```text
Claude decomposition -> Codex plan review -> task_graph.json -> 인간 승인 필요
```

routed 역할:

- Context Agent: 요청과 경계를 명확히 함
- Architect: Claude가 파일·계층·계약·비목표·위험 통제를 정의
- Implementer: `--implementer auto|claude|codex`로 선택
- Reviewer: 구현자와 반대 모델
- Evaluator: Codex가 요청 완료 여부를 판단
- Reporter: Claude가 최종 보고서 작성

## 요구사항

- PATH에 `claude.cmd`
- PATH에 `codex.cmd`
- Python 3

기본 작업공간:

```text
C:\Users\systran\Desktop\LanguageDetection
```

## 구조

```text
AutoAgent/
+-- run.py
+-- autoagent/
|   +-- config.py
|   +-- cli.py
|   +-- runner.py
|   +-- routing.py
|   +-- safety.py
|   +-- artifacts.py
|   +-- roles.py
|   +-- worktree.py
|   +-- workflows/
|       +-- simple.py
|       +-- routed.py
|       +-- routed_preamble.py
|       +-- routed_impl.py
|       +-- routed_docs.py
|       +-- routed_common.py
|       +-- decompose.py
|       +-- task_exec.py
+-- prompts/
|   +-- simple/
|   +-- decompose/
|   +-- routed/
|   |   +-- context/
|   |   +-- backend/
|   |   +-- frontend/
|   |   +-- final/
|   +-- README.md
+-- roles.default.json
+-- projects/
|   +-- <name>/
|       +-- config.json
|       +-- runs/
+-- runs/
+-- autoagent.config.json
+-- README.md
```

## Simple 워크플로우

계획만:

```powershell
python .\run.py --plan-only --request "현재 구조를 리뷰하고 위험만 나열하세요."
```

전체 simple 루프:

```powershell
python .\run.py --request "파일을 수정하지 말고 프로젝트를 리뷰하세요."
```

Dry run:

```powershell
python .\run.py --dry-run --request "프롬프트 렌더링 테스트"
```

## Routed 워크플로우

Backend 라우트:

```powershell
python .\run.py --workflow routed --task-type backend --request "backend 변경을 구현하세요."
```

Frontend 라우트:

```powershell
python .\run.py --workflow routed --task-type frontend --request "frontend 변경을 구현하세요."
```

읽기 전용 docs/review 라우트:

```powershell
python .\run.py --workflow routed --task-type docs --read-only --request "파일을 수정하지 마세요. 위험만 리뷰하세요."
```

Auto 라우트:

```powershell
python .\run.py --workflow routed --task-type auto --request "FastAPI 마이그레이션 위험을 리뷰하세요."
```

DB subtype 라우트:

```powershell
python .\run.py --dry-run --workflow routed --task-type backend --request "DB migration으로 translation_pairs에 unique constraint를 추가해줘"
```

DB 관련 요청도 여전히 `backend`이지만, `route.json`에 다음이 추가됩니다:

```json
{
  "task_type": "backend",
  "subtype": "db",
  "risk_level": "high",
  "architect_agent": "claude",
  "evaluator_agent": "codex"
}
```

DB subtype 프롬프트는 데이터 손실, 호환성, 마이그레이션 upgrade/downgrade, 롤백, 트랜잭션, 잠금, nullable/default/index/constraint, Alembic, repository/API 계약, 검증 관련 사항을 포함합니다.

## Routed 옵션

- `--workflow simple|routed`
- `--workflow decompose`
- `--task-type auto|backend|frontend|docs|review`
- `--implementer auto|claude|codex`
- `--project <name>` (프로젝트 레지스트리: `projects/<name>/config.json` + `projects/<name>/runs/` 사용)
- `--read-only`
- `--max-review-rounds 1`
- `--max-agent-calls 0`
- `--stop-after none|context|architecture|validation|implementation|review|final-review|evaluation|report`
- `--require-human-approval`
- `--resume <run_dir>` (게이트에서 정지한 run을 사람이 검토·승인한 뒤 재개. `checkpoint.json`의 `mode`로 분기 —
  `mode: task_graph`면 decompose 병렬 실행기로, 그 외에는 routed 구현 단계 재개로 이어짐)

기본값:

- `--workflow simple`
- `--task-type auto`
- `--implementer auto`
- `--max-review-rounds 1`
- `--max-agent-calls 0`은 무제한
- `--stop-after none`

## 모델 정책

기본 모델 배치:

```text
Claude 기본: sonnet
Claude high-risk: opus
Claude effort 기본: high
Claude high-risk effort: xhigh
Codex: gpt-5.6-sol
Codex reasoning effort: medium
Codex high-risk effort: high
```

역할 배치:

```text
Context Agent: Claude sonnet
Architect: Claude sonnet
DB/high-risk Architect: Claude opus (effort xhigh)
Implementer: --implementer auto|claude|codex로 선택
DB/high-risk Implementer(claude): Claude opus (effort xhigh)
Reviewer: 구현자와 반대 모델
Evaluator: Codex gpt-5.6-sol
Reporter: Claude sonnet
```

`--effort`(headless `claude -p`)는 low/medium/high/xhigh/max만 받습니다("ultracode"는 대화형 전용이라 무시됨). high-risk에서 opus에 xhigh를 부여하는 것이 "ultracode"의 추론 강도에 해당합니다. effort 값은 config의 `claude_effort`/`claude_high_risk_effort`로 조정합니다.

Implementer 선택:

```text
--implementer claude
  Claude가 구현하고 Codex가 리뷰.

--implementer codex
  Codex가 구현하고 Claude가 리뷰.

--implementer auto
  Frontend는 기본 Codex.
  Backend는 기본 Claude.
  Backend의 test/build/lint/diff-fix 작업은 Codex로 라우팅될 수 있음.
  Docs/review/read-only 라우트는 구현하지 않음.
```

Codex 추론 강도는 하네스가 `codex -c model_reasoning_effort="..."` 전역 오버라이드로 **실제 주입**합니다(값은 minimal/low/medium/high/xhigh). 기본은 `codex_reasoning_effort`(medium)이고, high-risk 조건(주로 backend·mutating을 codex가 구현/수정)일 때만 `codex_high_risk_effort`(high)로 승격합니다. config에서 두 값을 조정하며, 빈 문자열로 두면 주입을 생략합니다(`~/.codex/config.toml` 기본값 사용).

## MCP 도구 (선택)

config의 `mcp_allowed_tools`(기본 `[]`)에 MCP 툴 패턴을 넣으면, 하네스가 **Claude** 서브프로세스 명령에 `--allowedTools`로 주입합니다. 헤드리스 `claude -p`의 읽기 역할(`--permission-mode plan`)은 승인 TTY가 없어 allowlist 없이는 MCP 툴 호출이 거부되기 때문입니다(설계: `docs/specs/2026-07-13-mcp-integration-design.md`).

```json
{ "mcp_allowed_tools": ["mcp__serena", "mcp__context7"] }
```

- 서버 **발견**은 타깃 레포의 `.mcp.json`(Claude 자동 로드)에 맡깁니다. 이 키는 allowlist 주입만 담당합니다.
- **Codex**는 `--ask-for-approval never`라 allowlist 없이도 MCP 툴을 쓰므로 이 키의 영향을 받지 않습니다. Codex용 서버는 `~/.codex/config.toml`의 `[mcp_servers.*]`에 둡니다. 크로스모델 대칭을 위해 **로컬(무네트워크) 서버**(예: Serena)는 같은 것을 양쪽에 등록하세요.
- **네트워크 MCP 주의**: 웹 fetch·Context7 등 네트워크가 필요한 MCP는 **Codex `exec`에서 차단**됩니다(read-only·workspace-write·`network_access=true` 모두, 실측). 따라서 네트워크 MCP는 Claude 전용으로만 쓰고, Codex 쪽엔 등록하지 마세요(등록해도 동작 안 함). 설계: `docs/specs/2026-07-13-mcp-integration-design.md` §6.3.
- 비어 있으면(기본) `--allowedTools`를 붙이지 않아 기존 명령과 바이트 동일합니다(opt-in).

## 루프 제한

권장 review/docs 실행:

```powershell
python .\run.py `
  --workflow routed `
  --task-type review `
  --read-only `
  --max-review-rounds 0 `
  --max-agent-calls 5 `
  --request "프로젝트 구조와 위험만 리뷰하세요."
```

권장 구현 실행:

```powershell
python .\run.py `
  --workflow routed `
  --task-type backend `
  --implementer claude `
  --max-review-rounds 1 `
  --max-agent-calls 9 `
  --request "backend 기능을 구현하세요."
```

Codex에 위임하는 backend 구현:

```powershell
python .\run.py `
  --workflow routed `
  --task-type backend `
  --implementer codex `
  --max-review-rounds 1 `
  --max-agent-calls 9 `
  --request "실패한 pytest 출력을 기반으로 backend 코드를 고치세요."
```

자동 implementer 선택:

```powershell
python .\run.py `
  --workflow routed `
  --task-type auto `
  --implementer auto `
  --max-review-rounds 1 `
  --max-agent-calls 9 `
  --request "요청 텍스트"
```

`--max-review-rounds`는 리뷰-수정 반복 횟수입니다(리뷰가 통과하면 조기 종료). `0`이면 리뷰/수정을 건너뜁니다.

`--max-agent-calls`는 Claude/Codex 서브프로세스 총 호출 수를 제한합니다. Dry run은 호출 수에 포함되지 않습니다. 다음 호출 전에 예산이 소진되면 `stopped_by_budget.md`를 쓰고 종료 코드 0으로 끝납니다.

`--stop-after`는 지정한 단계 완료 후 정지하며 `stopped_after.md`를 씁니다.

## 승인 게이트

구현 실행은 다음 중 하나라도 참이면 코드 변경 전에 정지합니다:

- `--require-human-approval`이 설정됨
- `route.json`의 `"risk_level": "high"`
- `route.json`의 `"subtype": "db"`
- 요청이 `migration`, `auth`, `payment`, `production`, `backfill`, `rollback` 같은 high-risk 용어를 강하게 언급함

게이트는 다음을 씁니다:

```text
checkpoint.json
approval_required.md
approval_status.json
final_report.md
```

사람이 계획 산출물(`01_claude_context.md`·`02_claude_architecture.md`·`03_codex_validation.md`)을 검토한 뒤, `--resume <run_dir>`로 재개하면 preamble을 다시 돌리지 않고 구현 단계부터 이어갑니다. 재개 실행 자체가 사람의 승인 행위입니다. 게이트를 건너뛰는 블랭킷 플래그(예: `--approve`)는 두지 않습니다 — 그런 스위치의 유일한 효과가 "반드시 승인해야 하는 high-risk/db 케이스의 승인을 생략"하는 것이라 이 하네스의 철학과 충돌하기 때문입니다.

## 안전

- `--workflow simple`은 기존 동작을 유지합니다.
- `--workflow routed`는 새 역할 기반 흐름을 사용합니다.
- `--workflow decompose`는 구현 단계를 절대 실행하지 않습니다.
- `--read-only`는 Codex 샌드박스를 `read-only`로 강제하고 구현 단계를 건너뜁니다.
- Decompose는 Claude를 `--permission-mode plan`으로, Codex를 `--sandbox read-only`로 실행합니다.
- 대상 작업공간에 유효한 Git HEAD 베이스라인이 없으면 구현 라우트가 차단됩니다.
- 하네스는 자동으로 커밋/푸시/업로드하지 않습니다.

## Decompose 워크플로우

직접 구현하면 안 되는 대규모 요청에 decompose를 사용하세요.

```powershell
python .\run.py `
  --workflow decompose `
  --request "src-layout 마이그레이션을 안전한 task graph로 분해하세요."
```

Decompose는 다음을 씁니다:

```text
00_request.md
01_claude_decomposition.md
02_codex_plan_review.md
task_graph.json
approval_required.md
final_report.md
```

Task graph 스키마(필드명·enum값은 코드가 파싱하므로 영문 유지):

```json
{
  "version": 1,
  "goal": "string",
  "risk_level": "low|medium|high",
  "requires_human_approval": true,
  "tasks": [
    {
      "id": "001",
      "title": "string",
      "type": "backend|frontend|docs|review|test|db|infra",
      "description": "string",
      "rationale": "string",
      "allowed_paths": [],
      "blocked_paths": [],
      "expected_files": [],
      "validation_commands": [],
      "dependencies": [],
      "risk_level": "low|medium|high",
      "approval_required": true,
      "status": "pending"
    }
  ]
}
```

현재 버전은 task graph 승인 후 정지합니다. 이제 그래프로부터의 task 실행이 구현되어 있으며,
decompose 승인 게이트를 사람이 검토한 뒤 `--resume`로 병렬 실행됩니다(설계:
`docs/specs/2026-07-12-decompose-parallel-executor-design.md`).

### Decompose 병렬 실행기

`decompose`가 승인 게이트에서 정지하면, 사람이 `approval_brief.md`를 검토한 뒤
`--resume <run_dir>`(checkpoint `mode: task_graph`)로 실행기(`task_exec.py`)를 기동합니다.
task graph를 위상정렬해 파도(wave) 단위로 진행하며, 같은 파도 안의 노드는 각각 격리된
git worktree(레인 브랜치)에서 구현→반대모델 리뷰→수정을 거칩니다. 완료된 레인들은
통합 브랜치 `aa-integration/<stamp>`로 순차 병합되고, main은 건드리지 않습니다.
동시성 상한은 config의 `max_parallel_lanes`(기본 2)이며, `1`이면 사실상 순차 실행입니다.
다만 이 실행기는 dry-run·단위·코드리뷰까지만 검증되었고, 라이브 end-to-end 실행은
아직 확인되지 않았습니다.

## 출력

각 실행은 다음 아래에 산출물을 씁니다:

```text
runs/YYYYMMDD_HHMMSS/
```

주요 routed 산출물:

```text
00_request.md
01_claude_context.md
02_claude_architecture.md
03_codex_validation.md
route.json
final_evaluation.md
final_report.md
```

리뷰-수정 반복 라운드는 `_rN` 접미사로 남습니다(예: `05_codex_backend_review_r1.md`). Backend 라우트는 다음도 생성할 수 있습니다:

```text
04_claude_backend_impl.md
05_codex_backend_review_r1.md
06_claude_backend_fix_r1.md
07_codex_final_review.md
08_codex_evaluation.md
```

Frontend 라우트는 다음도 생성할 수 있습니다:

```text
04_codex_frontend_impl.md
05_claude_frontend_review_r1.md
06_codex_frontend_fix_r1.md
07_codex_final_review.md
08_codex_evaluation.md
```

Docs/review/read-only 라우트는 다음도 생성할 수 있습니다:

```text
04_codex_evaluation.md
05_claude_final_report.md
```

## `/aa` 커맨드 (Claude CLI)

Claude Code 세션 안에서 AutoAgent를 돌리고, 게이트에 걸리면 CLI에서 승인해 바로 구현까지 이어가는 단일 커맨드입니다.

설치 (글로벌, 모든 프로젝트에서 사용):

```powershell
Copy-Item C:\Users\systran\Desktop\AutoAgent\commands\aa.md $HOME\.claude\commands\aa.md
```

사용:

```text
/aa <요청>                 # auto 라우팅
/aa backend <요청>         # 타입 강제 (auto|backend|frontend|docs|review)
```

흐름: 현재 프로젝트(`--workspace .`)에 routed 워크플로우 실행 → high-risk/db면 게이트에서 계획·위험을 요약해 CLI에서 승인 질의 → 승인 시 `--resume`로 구현 단계 진입 → 구현 산출물 + `git diff --stat` 요약. 하네스는 자동 커밋/푸시하지 않으므로 diff는 사람이 검토합니다. 저위험 변경은 게이트 없이 바로 진행됩니다.

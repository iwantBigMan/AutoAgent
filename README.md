# AutoAgent

Local harness for running Claude Code CLI and Codex CLI together.

The default workflow remains:

```text
Claude plan -> Codex execute -> Claude review
```

The routed workflow adds role-based routing:

```text
Claude context -> Claude architecture -> Codex validation -> route -> implementation/review/evaluation/final report
```

The decompose workflow splits large requests into a reviewed task graph without implementation:

```text
Claude decomposition -> Codex plan review -> task_graph.json -> human approval required
```

Routed roles:

- Context Agent: clarifies the request and boundaries
- Architect: Claude defines files, layers, contracts, non-goals, and risk controls
- Implementer: selected by `--implementer auto|claude|codex`
- Reviewer: the opposite model from the implementer
- Evaluator: Codex decides whether the request is complete
- Reporter: Claude writes the final report

## Requirements

- `claude.cmd` available on PATH
- `codex.cmd` available on PATH
- Python 3

Default workspace:

```text
C:\Users\systran\Desktop\LanguageDetection
```

## Layout

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
|   +-- workflows/
|       +-- simple.py
|       +-- routed.py
|       +-- decompose.py
+-- prompts/
|   +-- simple/
|   +-- decompose/
|   +-- routed/
|   |   +-- context/
|   |   +-- backend/
|   |   +-- frontend/
|   |   +-- final/
|   +-- README.md
+-- runs/
+-- autoagent.config.json
+-- README.md
```

## Simple Workflow

Plan only:

```powershell
python .\run.py --plan-only --request "Review the current structure and list risks only."
```

Full simple loop:

```powershell
python .\run.py --request "Review the project without modifying files."
```

Dry run:

```powershell
python .\run.py --dry-run --request "Prompt rendering test"
```

## Routed Workflow

Backend route:

```powershell
python .\run.py --workflow routed --task-type backend --request "Implement the backend change."
```

Frontend route:

```powershell
python .\run.py --workflow routed --task-type frontend --request "Implement the frontend change."
```

Read-only docs/review route:

```powershell
python .\run.py --workflow routed --task-type docs --read-only --request "Do not modify files. Review risks only."
```

Auto route:

```powershell
python .\run.py --workflow routed --task-type auto --request "Review FastAPI migration risks."
```

DB subtype route:

```powershell
python .\run.py --dry-run --workflow routed --task-type backend --request "DB migration?쇰줈 translation_pairs??unique constraint瑜?異붽??댁쨾"
```

DB-related requests are still `backend`, but `route.json` adds:

```json
{
  "task_type": "backend",
  "subtype": "db",
  "risk_level": "high",
  "architect_agent": "claude",
  "evaluator_agent": "codex"
}
```

DB subtype prompts include data loss, compatibility, migration upgrade/downgrade, rollback, transaction, locking, nullable/default/index/constraint, Alembic, repository/API contract, and validation concerns.

## Routed Options

- `--workflow simple|routed`
- `--workflow decompose`
- `--task-type auto|backend|frontend|docs|review`
- `--implementer auto|claude|codex`
- `--read-only`
- `--max-review-rounds 1`
- `--max-agent-calls 0`
- `--stop-after none|context|architecture|validation|implementation|review|final-review|evaluation|report`
- `--require-human-approval`

Defaults:

- `--workflow simple`
- `--task-type auto`
- `--implementer auto`
- `--max-review-rounds 1`
- `--max-agent-calls 0` means unlimited
- `--stop-after none`

## Model Policy

Default model placement:

```text
Claude default: sonnet
Claude high-risk: opus
Codex: gpt-5.5
Codex reasoning effort: high
```

Role placement:

```text
Context Agent: Claude sonnet
Architect: Claude sonnet
DB/high-risk Architect: Claude opus
Implementer: selected by --implementer auto|claude|codex
Reviewer: opposite model from the implementer
Evaluator: Codex gpt-5.5
Reporter: Claude sonnet
```

Implementer selection:

```text
--implementer claude
  Claude implements and Codex reviews.

--implementer codex
  Codex implements and Claude reviews.

--implementer auto
  Frontend defaults to Codex.
  Backend defaults to Claude.
  Backend test/build/lint/diff-fix work can route to Codex.
  Docs/review/read-only routes do not implement.
```

`codex_reasoning_effort` is stored in config for reproducibility. The harness does not inject it as a `codex exec -c` override because CLI compatibility can vary; set it in `~/.codex/config.toml` when needed.

## Loop Limits

Recommended review/docs run:

```powershell
python .\run.py `
  --workflow routed `
  --task-type review `
  --read-only `
  --max-review-rounds 0 `
  --max-agent-calls 5 `
  --request "Review project structure and risks only."
```

Recommended implementation run:

```powershell
python .\run.py `
  --workflow routed `
  --task-type backend `
  --implementer claude `
  --max-review-rounds 1 `
  --max-agent-calls 9 `
  --request "Implement the backend feature."
```

Backend implementation delegated to Codex:

```powershell
python .\run.py `
  --workflow routed `
  --task-type backend `
  --implementer codex `
  --max-review-rounds 1 `
  --max-agent-calls 9 `
  --request "Fix the backend code based on failing pytest output."
```

Automatic implementer selection:

```powershell
python .\run.py `
  --workflow routed `
  --task-type auto `
  --implementer auto `
  --max-review-rounds 1 `
  --max-agent-calls 9 `
  --request "Request text"
```

`--max-agent-calls` limits the total number of Claude/Codex subprocess calls. Dry runs do not count as agent calls. If the budget is exhausted before the next call, the run writes `stopped_by_budget.md` and exits with code 0.

`--stop-after` stops after a named stage completes and writes `stopped_after.md`.

## Approval Gate

Implementation runs stop before code changes when any of these are true:

- `--require-human-approval` is set
- `route.json` has `"risk_level": "high"`
- `route.json` has `"subtype": "db"`
- the request strongly mentions high-risk terms such as `migration`, `auth`, `payment`, `production`, `backfill`, or `rollback`

The gate writes:

```text
approval_required.md
approval_status.json
final_report.md
```

This version only implements the safe stop. Resuming an approved run is intentionally left for a later change.

## Safety

- `--workflow simple` preserves the previous behavior.
- `--workflow routed` uses the new role-based flow.
- `--workflow decompose` never runs implementation steps.
- `--read-only` forces Codex sandbox to `read-only` and skips implementation steps.
- Decompose runs Claude with `--permission-mode plan` and Codex with `--sandbox read-only`.
- Implementation routes are blocked if the target workspace does not have a valid Git HEAD baseline.
- The harness never commits, pushes, or uploads automatically.

## Decompose Workflow

Use decompose for large requests that should not be implemented directly.

```powershell
python .\run.py `
  --workflow decompose `
  --request "Split the src-layout migration into a safe task graph."
```

Decompose writes:

```text
00_request.md
01_claude_decomposition.md
02_codex_plan_review.md
task_graph.json
approval_required.md
final_report.md
```

Task graph schema:

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

This version stops after task graph approval. Task execution from a graph is a later workflow.

## Output

Each run writes artifacts under:

```text
runs/YYYYMMDD_HHMMSS/
```

Important routed artifacts:

```text
00_request.md
01_claude_context.md
02_claude_architecture.md
03_codex_validation.md
route.json
final_evaluation.md
final_report.md
```

Backend routes may also create:

```text
04_claude_backend_impl.md
05_codex_backend_review.md
06_claude_backend_fix.md
04_codex_backend_impl.md
05_claude_backend_review.md
06_codex_backend_fix.md
07_codex_final_review.md
08_codex_evaluation.md
```

Frontend routes may also create:

```text
04_codex_frontend_impl.md
05_claude_frontend_review.md
06_codex_frontend_fix.md
04_claude_frontend_impl.md
05_codex_frontend_review.md
06_claude_frontend_fix.md
07_codex_final_review.md
07_claude_final_review.md
08_codex_evaluation.md
```

Docs/review/read-only routes may also create:

```text
04_codex_evaluation.md
05_claude_final_report.md
```

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

Routed roles:

- Context Agent: clarifies the request and boundaries
- Architect: Claude defines files, layers, contracts, non-goals, and risk controls
- Implementer: Claude for backend, Codex for frontend
- Reviewer: Codex for backend, Claude for frontend
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
├─ run.py
├─ autoagent/
│  ├─ config.py
│  ├─ cli.py
│  ├─ runner.py
│  ├─ routing.py
│  ├─ safety.py
│  ├─ artifacts.py
│  └─ workflows/
│     ├─ simple.py
│     └─ routed.py
├─ prompts/
├─ runs/
├─ autoagent.config.json
└─ README.md
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
python .\run.py --dry-run --workflow routed --task-type backend --request "DB migration으로 translation_pairs에 unique constraint를 추가해줘"
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
- `--task-type auto|backend|frontend|docs|review`
- `--read-only`
- `--max-review-rounds 1`

Defaults:

- `--workflow simple`
- `--task-type auto`
- `--max-review-rounds 1`

## Safety

- `--workflow simple` preserves the previous behavior.
- `--workflow routed` uses the new role-based flow.
- `--read-only` forces Codex sandbox to `read-only` and skips implementation steps.
- Implementation routes are blocked if the target workspace does not have a valid Git HEAD baseline.
- The harness never commits, pushes, or uploads automatically.

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
07_codex_final_review.md
08_codex_evaluation.md
```

Frontend routes may also create:

```text
04_codex_frontend_impl.md
05_claude_frontend_review.md
06_codex_frontend_fix.md
07_claude_final_review.md
08_codex_evaluation.md
```

Docs/review/read-only routes may also create:

```text
04_codex_evaluation.md
05_claude_final_report.md
```

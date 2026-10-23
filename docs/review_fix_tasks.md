# Code Review Remediation Tasks

Review: `runs/code_review_20260928_132735.md`

| Task | Risk | Findings | Scope | Status |
| --- | --- | --- | --- | --- |
| T1 | P1 | R01/R03/R04/R07 | Permissions, completion, scope, review intent | Complete: 51 focused tests passed |
| T2 | P1 | R05/R06 | Preview isolation, resume contract, durable budget | Complete: 46 focused tests passed |
| T3 | P1/P2 | R02/R13 | Dependency code state, resumable integration | Complete: 32 focused tests, including real temporary Git integration |
| T4 | P1 | R08/R10/R11 | Renderer termination, fail-closed verification | Complete: 29 focused tests passed |
| T5 | P1/P2 | R09/R12/R14/R15/R16 | Research gates/passes, solo dispatch, report inputs | Complete: full suite and CLI previews passed |

Each task requires focused regression tests before completion. Final checks cover the
entire test suite, CLI dry-runs, and integration tests with isolated temporary Git
repositories (including fixture commits). No production workspace, paid model call, production commit, push, or deployment
is part of this remediation run.

## Final Verification (2026-09-28)

- `python -m pytest tests/ -q`: 314 passed (baseline: 255).
- `python -m compileall -q run.py autoagent tests`: passed.
- `git diff --check`: passed.
- Six CLI previews passed with `--dry-run --no-refine --workspace .`:
  simple, routed backend, routed frontend, routed docs/read-only, decompose, research.
- Preview artifacts: `runs/20260928_175006` through `runs/20260928_175010`,
  including decompose `runs/20260928_175009_01`.
- `python run.py --resume runs/20260928_175009_01 --dry-run`: passed.
  All original top-level artifact hashes remained unchanged; outputs went to
  `previews/preview_000`.
- Temporary Git integration verified A -> B dependency changes and resumption after
  integration budget exhaustion. No model subprocess was used in tests.
- Prompt contracts now include completion markers for both providers and both code layers.

## Remaining Boundaries

- Paid CLI end-to-end execution and Windows subprocess timeout cleanup were not tested.
- Existing MCP asymmetry warning remains (Claude: serena, Codex: node_repl).
- Scope enforcement is post-change Git inspection, not per-file OS access control.
- Concurrent processes resuming the same run are unsupported.
- Existing refine/OpenAPI planning and decompose planning calls remain outside the
  counted agent-call budget. This limit is not a total token or monetary cap.
- CSV-free research currently blocks at stage c rather than claiming validation passed.
- User-owned `docs/발표준비_공부노트.md` was not changed.

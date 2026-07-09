# AutoAgent

Local harness that orchestrates Claude Code CLI (`claude.cmd`) and Codex CLI
(`codex.cmd`) as subprocesses to collaborate on a **separate target workspace** —
cross-model implement/review with human approval gates. Full reference: `README.md`.

## Critical model
- Subprocesses run with `cwd = config.workspace` (the **target project**, default
  `C:\Users\systran\Desktop\LanguageDetection`) — **not this repo**. This repo is only
  the orchestrator you edit.
- Agent instructions live in `prompts/**/*.md`, rendered by `render_template` with
  `{VAR}` placeholders. **Code orchestrates; prompts carry the "what to do"** — change
  behavior in prompts, not by hardcoding Python.
- Reviewer is always the **opposite model** of the implementer (`routing.choose_implementer`).
  Codex does **not** load Claude skills — keep shared agent behavior in `prompts/*.md`,
  the neutral channel both CLIs read via stdin.

## Workflows & layout
- `--workflow simple|routed|decompose`; routed = context→architecture⇄validation→
  approval gate→implement→review⇄fix→eval→report.
- `autoagent/workflows/routed_*.py` split by phase: `routed_preamble` (plan),
  `routed_impl` (implement/review loop), `routed_docs` (read-only), `routed_common` (gates).
- Run artifacts land in `runs/YYYYMMDD_HHMMSS/` (gitignored except `.gitkeep`).

## Testing / verification
- **No test suite.** Verify with dry-run:
  `python .\run.py --dry-run --workflow routed --task-type backend --request "..."`
  — renders every prompt + `*_command.json` without invoking any CLI. Dry-run never
  counts against `--max-agent-calls`.

## Conventions
- Every module opens with a **Korean docstring**; functions carry Korean inline comments.
  Match this style.
- `from __future__ import annotations`; PEP 604 types (`str | None`); dataclasses for
  config/state. Keep modules small and single-purpose.

## Environment / gotchas
- Windows + Git Bash; `LF will be replaced by CRLF` warnings on git ops are harmless.
- **Pushing to `main` is blocked** (default-branch protection) — use a feature branch + PR.
- `autoagent.config.json` is **gitignored**; precedence: config file >
  `AUTOAGENT_WORKSPACE` env > hardcoded default.

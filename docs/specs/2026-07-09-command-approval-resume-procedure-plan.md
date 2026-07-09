# /aa Command Approval→Resume Procedure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single generic `/aa` slash command that runs the AutoAgent routed workflow against the current project, and — when the harness gate fires — gets the human's approval in the Claude CLI and resumes into implementation, all in one flow.

**Architecture:** The deliverable is agent instructions (a Claude Code slash command), not Python. The command drives `run.py` as a subprocess, parses the PR#2 approval-gate handoff (`ROUTED_STATUS` / `RUN_DIR` / `RESUME_COMMAND` + `approval_status.json`), asks the human, then runs the emitted `resume_command`. Gating is delegated entirely to the harness (`is_high_risk`), so the command is type-agnostic.

**Tech Stack:** Claude Code slash command (Markdown + frontmatter); AutoAgent Python harness invoked via `python run.py`; Windows / PowerShell.

## Global Constraints

- No test suite exists; verification is `--dry-run` (CLAUDE.md). Tasks use dry-run walkthroughs, not unit tests.
- The command must NOT pass `--require-human-approval` — gating is the harness's job (design decision B).
- Canonical file lives in the harness repo (`commands/aa.md`); it is installed by copying to `~/.claude/commands/aa.md`. Do NOT create or edit files in the target workspace repo.
- Do NOT change harness Python code — the PR#2 handoff is sufficient.
- AutoAgent install path (hardcode, matching existing `/aa-*`): `C:\Users\systran\Desktop\AutoAgent`.
- The harness never auto-commits/pushes; the report step must remind the human to review the diff.

## File Structure

- Create: `commands/aa.md` — the `/aa` slash command (argument parsing → phase-1 run → gate branch → approve → resume → report). One responsibility: orchestrate the approve→resume flow.
- Modify: `README.md` — add a "`/aa` command" section (install + usage + flow).

---

### Task 1: Author the `/aa` command

**Files:**
- Create: `commands/aa.md`
- Verify with: `python run.py --dry-run ...` (no test file — dry-run walkthrough)

**Interfaces:**
- Consumes (from the harness, already implemented): phase-1 stdout lines `ROUTED_STATUS: waiting_for_human_approval`, `RUN_DIR: <abs>`, `RESUME_COMMAND: <cmd>`; and `<RUN_DIR>/approval_status.json` fields `status`, `run_dir`, `resume_command`.
- Produces: a `/aa` command discoverable once copied to `~/.claude/commands/aa.md`.

- [ ] **Step 1: Create `commands/aa.md` with this exact content**

````markdown
---
description: Run AutoAgent (routed) against the current project; approve at the gate in-CLI, then resume into implementation
argument-hint: "[auto|backend|frontend|docs|review] <request>"
allowed-tools: Bash(python:*), Bash(git:*), Read, Glob
---

You are driving the AutoAgent harness against the CURRENT project (the one this
session is in). The harness lives at `C:\Users\systran\Desktop\AutoAgent`.

## 1. Parse arguments

Raw arguments: `$ARGUMENTS`

- If the first whitespace-delimited token is one of `auto`, `backend`, `frontend`,
  `docs`, `review`: use it as TYPE and the rest as REQUEST.
- Otherwise: TYPE = `auto`, REQUEST = the whole of `$ARGUMENTS`.
- Budget N = `5` if TYPE is `docs` or `review`, else `9`.
- If REQUEST is empty, stop and ask the user what they want done.

## 2. Phase 1 — run the routed workflow

Run (do NOT add `--require-human-approval`; the harness gates high-risk/db itself):

```
python "C:\Users\systran\Desktop\AutoAgent\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --workspace . --request "REQUEST"
```

Substitute TYPE, N, REQUEST. From the output, capture the run directory from the
`RUN_DIR:` line. If there is no `RUN_DIR:` line, summarize stderr + exit code and stop.

## 3. Branch on the outcome

Read `<RUN_DIR>/approval_status.json` if it exists.

- **Gate** — stdout contains `ROUTED_STATUS: waiting_for_human_approval` (or
  `approval_status.json.status == "waiting_for_human_approval"`):
  1. Read `<RUN_DIR>/01_claude_context.md`, `02_claude_architecture.md`,
     `03_codex_validation.md`, and `route.json`.
  2. Present a concise summary to the user: the plan (files / layers / contracts),
     the route's `risk_level` and `subtype`, and the non-goals / risks.
  3. Ask plainly: "이 계획으로 구현을 진행할까요? (승인 / 거부)".
  4. If approved: run the `resume_command` value from `approval_status.json`
     verbatim (it is `python "...\run.py" --resume "<RUN_DIR>"`). Then go to section 4.
  5. If rejected: stop. Tell the user the run is preserved and can be resumed later
     with that same command. Run nothing else.
- **Completed without a gate** — no `waiting_for_human_approval` marker and the run
  finished (low-risk implementation, or a docs/review route): go to section 4.
- **Blocked** — `<RUN_DIR>/implementation_blocked.md` or `<RUN_DIR>/stopped_by_budget.md`
  exists: show that file's reason and stop.

## 4. Report

Read and summarize whichever of these exist in `<RUN_DIR>`: `04_*_impl*`,
`05_*_review*`, `06_*_fix*`, `07_codex_final_review*`, `08_codex_evaluation*`,
`09_claude_final_report*`, `final_report.md`, `final_evaluation.md`.

Then run `git -C . diff --stat` to show what changed in the project.

Remind the user: AutoAgent does NOT commit or push — review the diff yourself.
````

- [ ] **Step 2: Verify low-risk request does NOT gate (delegation to harness, option B)**

Run:
```
python run.py --dry-run --workflow routed --task-type backend --max-review-rounds 1 --max-agent-calls 9 --workspace . --request "add a health check endpoint"
```
Expected: run reaches implementation/report (NOT the gate) — stdout has `Routed run complete` (or renders 04+ artifacts), and NO `ROUTED_STATUS: waiting_for_human_approval` line. This proves that without `--require-human-approval`, a low-risk request flows straight through.

- [ ] **Step 3: Verify high-risk request DOES gate without the flag**

Run:
```
python run.py --dry-run --workflow routed --task-type backend --max-review-rounds 1 --max-agent-calls 9 --workspace . --request "add auth token migration"
```
Expected: stdout contains `ROUTED_STATUS: waiting_for_human_approval`, `RUN_DIR:`, and `RESUME_COMMAND:`. This proves the harness gates high-risk on its own (keywords `auth`/`migration`), so the command need not force approval.

- [ ] **Step 4: Verify the gate run dir carries the machine-readable resume fields**

Run (substitute the RUN_DIR printed in Step 3):
```
python -c "import json;d=json.load(open(r'<RUN_DIR>/approval_status.json',encoding='utf-8'));print(d['status']);print(d['resume_command'])"
```
Expected: prints `waiting_for_human_approval` and a `python "...run.py" --resume "..."` line. This is the exact value the command runs on approval.

- [ ] **Step 5: Commit**

```bash
git add commands/aa.md
git commit -m "feat: add generic /aa approve-and-resume command"
```

---

### Task 2: Document install + usage in README

**Files:**
- Modify: `README.md` (append a new section)

**Interfaces:**
- Consumes: `commands/aa.md` from Task 1.
- Produces: user-facing install + usage docs.

- [ ] **Step 1: Append this section to `README.md`**

```markdown
## `/aa` 커맨드 (Claude CLI)

Claude Code 세션 안에서 AutoAgent를 돌리고, 게이트에 걸리면 CLI에서 승인해 바로
구현까지 이어가는 단일 커맨드입니다.

설치 (글로벌, 모든 프로젝트에서 사용):

```powershell
Copy-Item C:\Users\systran\Desktop\AutoAgent\commands\aa.md $HOME\.claude\commands\aa.md
```

사용:

```text
/aa <요청>                 # auto 라우팅
/aa backend <요청>         # 타입 강제 (auto|backend|frontend|docs|review)
```

흐름: 현재 프로젝트(`--workspace .`)에 routed 워크플로우 실행 → high-risk/db면
게이트에서 계획·위험을 요약해 CLI에서 승인 질의 → 승인 시 `--resume`로 구현 단계
진입 → 구현 산출물 + `git diff --stat` 요약. 하네스는 자동 커밋/푸시하지 않으므로
diff는 사람이 검토합니다. 저위험 변경은 게이트 없이 바로 진행됩니다.
```

- [ ] **Step 2: Verify the README renders and the install path is correct**

Run:
```
python -c "import pathlib;print(pathlib.Path(r'commands/aa.md').exists())"
```
Expected: `True` (the file the README tells users to copy exists).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document /aa command install and usage"
```

---

## Self-Review

**1. Spec coverage:**
- Command shape & arg grammar → Task 1 Step 1 (section 1). ✓
- Procedure flow (phase-1 → branch → phase-2) → Task 1 Step 1 (sections 2–4). ✓
- Gate detection via PR#2 handoff → Task 1 Step 1 (section 3) + verified Steps 3–4. ✓
- Approval interaction (conversational default) → Task 1 Step 1 (section 3.3). ✓
- Default flags & safety (no `--require-human-approval`, no auto-commit) → Task 1 Step 1 + Steps 2–3 verify delegation. ✓
- Placement & install (harness `commands/aa.md` → global) → Task 1 + Task 2. ✓
- Edge/error handling (completed / blocked / docs-review / no marker) → Task 1 Step 1 (section 3 + section 2 fallback). ✓
- Testing via dry-run → Task 1 Steps 2–4. ✓

**2. Placeholder scan:** No TBD/TODO. TYPE/N/REQUEST/`<RUN_DIR>` are runtime parameters the command resolves, defined in section 1. No vague "handle errors" — the branches are explicit.

**3. Type consistency:** The command reads `status`, `run_dir`, `resume_command` from `approval_status.json` — matching the fields written by `block_for_human_approval` (PR#2). The stdout markers `ROUTED_STATUS` / `RUN_DIR` / `RESUME_COMMAND` match the exact strings printed by the harness.

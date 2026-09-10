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
- PROJECT = 현재 작업 디렉터리의 basename(예: `.../LanguageDetection` → `LanguageDetection`).
  이 이름으로 런을 `projects/<PROJECT>/`에 격리한다. config가 없으면 하네스가 현재 workspace로
  자동 생성한다.

## 2. Phase 1 — run the routed workflow

Run (do NOT add `--require-human-approval`; the harness gates high-risk/db itself):

```
python "C:\Users\systran\Desktop\AutoAgent\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --project "PROJECT" --workspace . --request "REQUEST"
```

Substitute TYPE, N, PROJECT, REQUEST. From the output, capture the run directory from the
`RUN_DIR:` line. If there is no `RUN_DIR:` line, summarize stderr + exit code and stop.

`--project "PROJECT"`로 런이 `projects/PROJECT/runs/<stamp>`에 격리된다. `RUN_DIR:`와
`resume_command`는 하네스가 절대경로로 출력·임베드하므로 아래 섹션 로직은 그대로 동작한다.

## 3. Branch on the outcome

Read `<RUN_DIR>/approval_status.json` if it exists.

- **Prompt gate** — stdout contains `AA_STATUS: waiting_for_prompt_approval` (or
  `refine_status.json.status == "waiting_for_prompt_approval"`):
  1. Read `<RUN_DIR>/00_refined_request.md` and `<RUN_DIR>/00_request_raw.md`.
  2. 정제본 전문과 원문 대비 달라진 요지를 사용자에게 보여준다.
     `refine_status.json.fallback`이 true면 "정제 콜 실패로 원문이 그대로
     담겨 있음"을 함께 알린다.
  3. Ask plainly: "이 프롬프트로 진행할까요? (승인 / 수정 / 거부)".
  4. 승인: run the `RESUME_COMMAND` line verbatim. 수정: 사용자의 피드백대로
     `<RUN_DIR>/00_refined_request.md`를 편집한 뒤 `RESUME_COMMAND`를 실행한다.
     어느 쪽이든 그 출력에 대해 **이 섹션 3의 분기를 처음부터 다시 적용**한다
     (정제 승인 뒤 high-risk 계획 게이트가 또 설 수 있다).
  5. 거부: stop. 런은 보존되며 나중에 같은 명령으로 재개 가능함을 알린다.
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
`05_*_review*`, `06_*_fix*`, `07_*_final_review*`, `08_codex_evaluation*`,
`09_claude_final_report*`, `final_report.md`, `final_evaluation.md`.

Then run `git -C . diff --stat` to show what changed in the project.

Remind the user: AutoAgent does NOT commit or push — review the diff yourself.

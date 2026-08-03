---
description: Run the AutoAgent research workflow against the current project; approve at gates in-CLI, resume, then summarize the cited HTML report
argument-hint: "[dry] <research request>"
allowed-tools: Bash(python:*), Read, Glob
---

You are driving the AutoAgent **research** workflow against the CURRENT project (the
one this session is in). The harness lives at `C:\Users\systran\Desktop\AutoAgent`.
It researches company / market / CSV / web-fact topics through a nested deepen loop
with cross-model adversarial verification, and emits a cited standalone-HTML report.
Free sources only (web + local CSV). The harness gates high-cost/contradiction/blocked
points itself.

## 1. Parse arguments

Raw arguments: `$ARGUMENTS`

- If the first whitespace-delimited token is `dry`: DRY = true, REQUEST = the rest.
  Otherwise DRY = false, REQUEST = the whole of `$ARGUMENTS`.
- If REQUEST is empty, stop and ask the user what to research.
- PROJECT = 현재 작업 디렉터리의 basename(예: `.../LanguageDetection` → `LanguageDetection`).
  이 이름으로 런을 `projects/<PROJECT>/runs/<stamp>`에 격리한다. config가 없으면 하네스가
  현재 workspace로 자동 생성한다.
- Budget N = `40`(전역 호출 상한; 하네스가 스테이지별/outer별 상한도 별도로 건다).

## 2. Run the research workflow

Run (live unless DRY — DRY면 아래 명령 끝에 `--dry-run`을 붙여 프롬프트/리포트만 렌더하고
실제 claude/codex 호출을 0으로 만든다):

```
python "C:\Users\systran\Desktop\AutoAgent\run.py" --workflow research --request "REQUEST" --project "PROJECT" --workspace . --max-agent-calls N
```

Substitute REQUEST, PROJECT, N. Capture the run directory `<RUN_DIR>` from whichever
of these stdout lines appears (하네스는 절대경로로 출력한다):
- `RUN_DIR: <path>` — 게이트에서 정지한 경우,
- `Research run complete: <path>` — 정상 완료한 경우,
- `Research dry run written to <path>` — dry-run인 경우.

If none of those lines appears, summarize stderr + exit code and stop.

**주의**: 리서치는 모델 호출이 많아 오래 걸릴 수 있다. 그대로 기다리되, 첫 라이브 런이면
작은 요청으로 시작하도록 사용자에게 권한다(라이브 경로는 실증 초기 단계 — dry-run과 결정론
테스트까지만 검증됨).

## 3. Branch on the outcome

Read `<RUN_DIR>/gate_status.json` if it exists.

- **Gate** — stdout contains `RESEARCH_STATUS: waiting_for_human_approval` (or
  `gate_status.json.status == "waiting_for_human_approval"`):
  1. Read `<RUN_DIR>/gate_required.md` and `<RUN_DIR>/gate_status.json`
     (fields: `gate_kind`, `forced`, `reason`, `resume_command`, `state`).
  2. Present a concise summary — **왜 멈췄나**(`gate_kind` + `reason`), 어느 pass/stage
     (`state`), forced 여부. `gate_kind` 의미:
     - `high_cost_deepen` — 고비용 심화 pass 진입(forced),
     - `contradiction` — pass간 검증된 claim이 뒤집힘/seed drift(forced),
     - `blocked` — 스테이지 검증 불가(forced),
     - `exhausted_unverified_many` — 미검증(exhausted) 스테이지 다수(비-forced).
  3. Ask plainly: "이 지점에서 리서치를 재개할까요? (승인 / 거부)".
  4. If approved: run the `resume_command` value from `gate_status.json` verbatim
     (그것은 `python "...\run.py" --resume "<RUN_DIR>"` 형태다 — 실행 자체가 승인이다).
     그 다음 **새 stdout을 다시 읽어 이 §3을 재실행**한다 — 한 런이 pass·stage마다
     여러 게이트에 걸릴 수 있다.
  5. If rejected: stop. 런은 보존되며 같은 `resume_command`로 나중에 재개 가능하다고 알린다.
- **Completed** — `waiting_for_human_approval` 마커가 없고 stdout에 `Research run complete:`
  (또는 dry-run 라인)이 있다: go to section 4.
- **Blocked** — `gate_status.json.gate_kind == "blocked"`: `gate_required.md`의 사유를 보이고
  정지한다(스테이지 검증 자체가 불가 — 그냥 재개하면 같은 지점에 다시 멈춘다. 입력·요청을
  손봐야 한다).

## 4. Report

- **산출물 위치**: 라이브면 바탕화면 `research_report_<stamp>.html`이 브라우저로 자동 열린다.
  감사추적 사본은 `<RUN_DIR>/final_report.html`. dry-run이면 `<RUN_DIR>`에 렌더된 프롬프트·
  `final_report.html` 스켈레톤만 있다(실제 리서치 없음).
- **커버리지 요약**: `<RUN_DIR>/research_state.json`을 Read해 스테이지별 `stage_status`
  (resolved / exhausted_unverified / blocked / missing)와 `outer_decision`(action + reason)을
  간단한 표로 요약한다. `exhausted_unverified`·`blocked` 스테이지의 주장은 도출·신뢰도 계산에서
  제외됐음을 명시한다(UNVERIFIED 격리).
- Remind the user: 무료 소스(웹 + 로컬 CSV)만 사용하며, 리포트의 사실 주장엔 인용이 붙는다.
  커버리지 100% 미만이면 리포트 상단에 경고 배너가 그 사실을 표시한다. 하네스는 커밋/푸시를
  하지 않는다.

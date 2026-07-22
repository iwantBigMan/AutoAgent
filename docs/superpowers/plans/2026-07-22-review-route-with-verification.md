# review 라우트 실제 리뷰 + 실행근거 + per-project 검증 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `--task-type review` 라우트가 하네스 실행 검증을 근거로 실제 아키텍처 리뷰를 산출하도록 고치고, 검증 커맨드를 per-project 설정으로 뺀다.

**Architecture:** review 서브타입일 때만 preamble의 architecture/validation 프롬프트를 "리뷰 산출/리뷰 검증"용으로 분기하고(Q1-C), context 직후·리뷰 직전에 하네스가 직접 검증을 실행해(Q2-A) 그 요약을 리뷰·평가 프롬프트에 흘린다. 검증 커맨드 미설정 프로젝트는 LD 하드코딩 폴백 대신 명시적으로 스킵한다(Q3-A). backend/frontend/docs 라우트는 동작 불변(byte-equality로 증명).

**Tech Stack:** Python 3.11, 표준 라이브러리만. 프롬프트는 `prompts/**/*.md`(`{{VAR}}` 치환, `render_template`+`PROMPT_ALIASES`). 테스트 스위트 없음 — dry-run byte-equality + 애드혹 python 검증 + 라이브 런.

## Global Constraints

- 모든 모듈은 **한국어 docstring**으로 시작, 함수엔 한국어 인라인 주석. 식별자만 영문.
- `from __future__ import annotations`; PEP 604 타입(`str | None`).
- **backend/frontend/docs 라우트의 dry-run `*_command.json`+`*_prompt.md`는 변경 전후 바이트 동일**해야 함. review만 의도적으로 달라짐.
- **이 레포엔 `/aa`/routed를 돌리지 말 것**(자기수정 크래시). 인라인 편집 + **새 `python` 프로세스**로 검증.
- main push 차단 — 작업은 `feature/review-route-verification` 브랜치에서. 커밋 메시지는 한국어, 말미에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- 검증 커맨드는 **DB-free 1단계**만(실 PostgreSQL/Alembic 왕복 금지).

---

## 파일 구조

- Create: `prompts/routed/review/claude_review.md` — 리뷰 산출 프롬프트(claude, architect 역할 재활용).
- Create: `prompts/routed/review/codex_review.md` — 리뷰 검증 프롬프트(codex, validation 역할 재활용).
- Modify: `autoagent/artifacts.py` — `PROMPT_ALIASES`에 위 2개 별칭 등록.
- Modify: `autoagent/verification.py` — `run_verification_or_skip` 추가(미설정 스킵).
- Modify: `autoagent/workflows/routed_impl.py` — `_maybe_run_verification`가 새 헬퍼 사용(default_commands 폴백 제거).
- Modify: `autoagent/workflows/routed_preamble.py` — review 프롬프트 분기 + 검증 앞단 실행 + 요약 반환.
- Modify: `autoagent/workflows/routed.py` — 새 preamble 반환 언패킹 + `common["VERIFICATION_SUMMARY"]` 주입.
- Modify: `autoagent/workflows/routed_docs.py` — review일 때 실제 리뷰+검증을 평가/보고로 전달.
- Modify/Create: `projects/LanguageDetection/config.json` — `verification_commands` 3종.

---

## Task 1: 회귀 베이스라인 캡처 (코드 변경 없음, 반드시 최초)

**Files:**
- Create: `<scratchpad>/regress/capture.sh` (스크래치패드, 커밋 안 함)

**Interfaces:**
- Produces: `<scratchpad>/regress/before/<label>/` 아래 각 dry-run의 `*_command.json`+`*_prompt.md`. Task 7이 `after/`와 diff.

- [ ] **Step 1: 캡처 스크립트 작성**

`<scratchpad>`는 세션 스크래치패드 절대경로. 스크립트:

```bash
#!/usr/bin/env bash
# 회귀 매트릭스 dry-run을 돌려 command/prompt 아티팩트만 뽑아 <phase> 폴더에 모은다.
set -u
REPO="C:/Users/systran/Desktop/AutoAgent"
PHASE="${1:?usage: capture.sh before|after}"
OUT="$(dirname "$0")/$PHASE"
rm -rf "$OUT"; mkdir -p "$OUT"

run() {  # <label> <task-type> <implementer> <request>
  local label="$1" tt="$2" impl="$3" req="$4"
  local log; log="$(python "$REPO/run.py" --dry-run --workflow routed \
      --task-type "$tt" --implementer "$impl" --request "$req" 2>&1)"
  # 마지막에 찍히는 run_dir 경로를 뽑는다(gate면 RUN_DIR:, 완주면 complete:).
  local rd; rd="$(printf '%s\n' "$log" | grep -oE '(RUN_DIR: |complete: ).*' | tail -1 | sed -E 's/^(RUN_DIR: |complete: )//')"
  if [ -z "$rd" ]; then echo "!! $label: run_dir 못 찾음"; printf '%s\n' "$log" | tail -5; return; fi
  local dst="$OUT/$label"; mkdir -p "$dst"
  cp "$rd"/*_command.json "$dst"/ 2>/dev/null
  cp "$rd"/*_prompt.md "$dst"/ 2>/dev/null
  echo "ok $label -> $rd"
}

run backend_general_claude backend claude "add a health check endpoint"
run backend_general_codex  backend codex  "add a health check endpoint"
run backend_db_claude      backend claude "add an alembic migration for the users table"
run backend_db_codex       backend codex  "add an alembic migration for the users table"
run frontend_claude        frontend claude "add a dark mode toggle"
run frontend_codex         frontend codex  "add a dark mode toggle"
run docs_claude            docs claude "document the public API endpoints"
run docs_codex             docs codex  "document the public API endpoints"
```

- [ ] **Step 2: 베이스라인 캡처 실행**

Run: `bash <scratchpad>/regress/capture.sh before`
Expected: 8줄 모두 `ok ...`. `!!`가 있으면 멈추고 원인 확인(코드 변경 전이므로 스크립트/환경 문제).

- [ ] **Step 3: 캡처물 확인**

Run: `ls <scratchpad>/regress/before/backend_general_claude/`
Expected: `01_claude_context_command.json`, `01_claude_context_prompt.md`, `02_...`, `03_...` 등이 보임(backend_db_*는 게이트라 01~03 + 없을 수도). 커밋 없음(스크래치패드).

---

## Task 2: 검증 스킵 헬퍼 + impl 폴백 제거 + LD config (Q3-A)

**Files:**
- Modify: `autoagent/verification.py` (함수 추가; `default_commands`는 남겨두되 자동 폴백에서 분리)
- Modify: `autoagent/workflows/routed_impl.py:198-215` (`_maybe_run_verification`)
- Modify: `autoagent/workflows/routed_impl.py:18` (import 교체)
- Modify/Create: `projects/LanguageDetection/config.json`

**Interfaces:**
- Produces: `run_verification_or_skip(*, run_dir: Path, config: Config, name: str = "04b_verification") -> tuple[str, bool]` — `config.verification_commands`가 비면 SKIPPED 요약+`(summary, True)`, 있으면 `run_verification` 위임.

- [ ] **Step 1: `run_verification_or_skip` 추가**

`autoagent/verification.py` 끝(파일 맨 아래)에 추가:

```python
def run_verification_or_skip(
    *, run_dir: Path, config: Any, name: str = "04b_verification"
) -> tuple[str, bool]:
    """config.verification_commands가 있으면 실행, 없으면 명시적 스킵 요약을 남긴다.

    미설정 프로젝트를 LD 하드코딩(default_commands)으로 폴백하지 않는다. 대신 '검증
    커맨드 미설정(실행 근거 없음)'을 기록해, 리뷰/평가 프롬프트가 근거 부재를 알게 한다.
    (요약 markdown, overall_ok)를 반환한다.
    """
    # 미설정: 조용히 스킵하되 그 사실을 산출물로 남긴다(정직한 스킵 > 남의 경로로 실패).
    if not config.verification_commands:
        summary = (
            "# 자동 검증 결과 (하네스 1단계, DB-free)\n\n"
            "**overall: SKIPPED**\n\n"
            "이 프로젝트는 verification_commands가 미설정이라 검증을 실행하지 않았습니다"
            "(실행 근거 없음). projects/<name>/config.json에 커맨드를 추가하면 활성화됩니다.\n"
        )
        write_text(run_dir / f"{name}.md", summary)
        write_json(run_dir / f"{name}.json", {"overall_ok": True, "skipped": True, "results": []})
        return summary, True
    # 설정됨: 기존 실행기에 위임(폴백 없이 config 값만 사용).
    return run_verification(
        run_dir=run_dir,
        workspace=config.workspace,
        commands=config.verification_commands,
        timeout_seconds=config.verification_timeout_seconds,
        name=name,
    )
```

(`write_text`/`write_json`은 이 모듈이 이미 import 중. `Any`는 `typing`에서 이미 import 중.)

- [ ] **Step 2: 애드혹 검증 — 미설정 스킵 / 설정 실행 분기**

`<scratchpad>/t2_check.py` 작성 후 새 프로세스로 실행:

```python
import sys; sys.path.insert(0, r"C:\Users\systran\Desktop\AutoAgent")
from pathlib import Path
from types import SimpleNamespace
from autoagent.verification import run_verification_or_skip

rd = Path(r"C:\Users\systran\AppData\Local\Temp\claude_t2"); rd.mkdir(parents=True, exist_ok=True)
# 미설정 → SKIPPED
cfg_empty = SimpleNamespace(verification_commands=[], workspace=rd, verification_timeout_seconds=60)
s, ok = run_verification_or_skip(run_dir=rd, config=cfg_empty)
assert ok is True and "SKIPPED" in s, ("skip 분기 실패", s)
# 설정됨 → 실제 실행(간단한 python -c)
py = sys.executable
cfg_run = SimpleNamespace(
    verification_commands=[{"name": "echo", "command": [py, "-c", "print('hi')"]}],
    workspace=rd, verification_timeout_seconds=60)
s2, ok2 = run_verification_or_skip(run_dir=rd, config=cfg_run)
assert ok2 is True and "PASS" in s2, ("run 분기 실패", s2)
print("T2 OK")
```

Run: `python <scratchpad>/t2_check.py`
Expected: `T2 OK`

- [ ] **Step 3: `_maybe_run_verification` 폴백 교체**

`autoagent/workflows/routed_impl.py:18` import를 교체:

```python
from autoagent.verification import run_verification_or_skip
```

(기존 `from autoagent.verification import default_commands, run_verification` 줄을 위로 대체.)

`_maybe_run_verification`(198~215) 본문의 실행부를 교체:

```python
    if args.dry_run or getattr(args, "skip_verification", False) or not config.verification_enabled:
        return implementation
    # 미설정이면 default_commands로 폴백하지 않고 스킵(Q3-A). LD는 자기 config로 커맨드를 갖는다.
    summary, ok = run_verification_or_skip(run_dir=run_dir, config=config)
    print(f"Verification stage: {'PASS' if ok else 'FAIL'} ({run_dir})")
    return f"{implementation}\n\n---\n{summary}"
```

- [ ] **Step 4: LD config에 verification_commands 추가**

`projects/LanguageDetection/config.json`을 읽어(없으면 `{}`) `verification_commands`를 병합해 저장. `workspace` 키는 기존 값 보존(없으면 `C:\\Users\\systran\\Desktop\\LanguageDetection`). 최종 내용:

```json
{
  "workspace": "C:\\Users\\systran\\Desktop\\LanguageDetection",
  "verification_commands": [
    {"name": "compileall", "command": ["venv311/Scripts/python.exe", "-m", "compileall", "-q", "src/lang_detect"]},
    {"name": "pytest", "command": ["venv311/Scripts/python.exe", "-m", "pytest", "tests", "tests_legacy", "-q"]},
    {"name": "frontend_build", "command": ["npm", "--prefix", "frontend", "run", "build"]}
  ]
}
```

주의: `command[0]`의 상대경로(`venv311/Scripts/python.exe`)는 `verification._resolve`가 workspace 기준으로 붙인다(이미 그렇게 동작). 절대경로로 박지 말 것 — per-project 이식성.

- [ ] **Step 5: 애드혹 검증 — LD config 로드**

```python
import sys; sys.path.insert(0, r"C:\Users\systran\Desktop\AutoAgent")
from pathlib import Path
from autoagent.config import load_config
c = load_config(Path(r"C:\Users\systran\Desktop\AutoAgent\autoagent.config.json"), project="LanguageDetection")
names = [x["name"] for x in c.verification_commands]
assert names == ["compileall", "pytest", "frontend_build"], names
print("T2-LD OK", names)
```

Run: `python <scratchpad>/t2_ld_check.py`
Expected: `T2-LD OK ['compileall', 'pytest', 'frontend_build']`

- [ ] **Step 6: 커밋**

```bash
git add autoagent/verification.py autoagent/workflows/routed_impl.py projects/LanguageDetection/config.json
git commit -m "feat: 검증 미설정 시 명시적 스킵(default_commands 폴백 제거), LD 커맨드를 per-project config로 이전"
```
(`projects/*/config.json`은 gitignored라 `git add`가 무시할 수 있음 — `git add -f projects/LanguageDetection/config.json` 필요 여부를 `git status`로 확인. gitignore면 커밋에서 빠지고 로컬에만 존재; 그래도 무방.)

---

## Task 3: 신규 review 프롬프트 2개 + 별칭 등록

**Files:**
- Create: `prompts/routed/review/claude_review.md`
- Create: `prompts/routed/review/codex_review.md`
- Modify: `autoagent/artifacts.py:19-43` (`PROMPT_ALIASES`)

**Interfaces:**
- Produces: 별칭 `claude_review_route.md` → `routed/review/claude_review.md`, `codex_review_route.md` → `routed/review/codex_review.md`. Task 4가 `render_template`로 소비.
- Consumes: 템플릿 변수 `{{REQUEST}} {{WORKSPACE}} {{TASK_TYPE}} {{CLAUDE_CONTEXT}} {{VERIFICATION_SUMMARY}} {{PRIOR_VALIDATION}}`(claude_review), `{{...}} {{CLAUDE_CONTEXT}} {{CLAUDE_ARCHITECTURE}} {{VERIFICATION_SUMMARY}}`(codex_review). 기존 architect/validation 변수 형태를 그대로 따른다.

- [ ] **Step 1: `prompts/routed/review/claude_review.md` 작성**

```markdown
당신은 시니어 소프트웨어 아키텍트입니다. 아래 프로젝트를 **읽기 전용으로** 점검해 최종
아키텍처 리뷰 보고서를 작성하세요. 파일을 수정하지 마세요.

## 대상
- 작업 유형: {{TASK_TYPE}}
- 워크스페이스: {{WORKSPACE}}
- 요청: {{REQUEST}}

## 사전 컨텍스트(코드 탐색 결과)
{{CLAUDE_CONTEXT}}

## 하네스 실행 검증 결과(실측 근거)
{{VERIFICATION_SUMMARY}}

## 직전 검증자 피드백(있으면 반영)
{{PRIOR_VALIDATION}}

## 요구 산출물
"무엇을 점검할 계획"이 아니라 **완료된 리뷰**를 내세요. 다음 범위를 빠짐없이 다룹니다:
1. 전체 의존 결합도(계층/포트-어댑터 의존 방향, 양방향 결합, 계층 위반).
2. 디자인패턴 적용/중복(composition root 중복, 공통 runner/Template Method 적정성 등).
3. 의존성 선언 대 실행·배포 환경 드리프트(pyproject/requirements/Dockerfile/lock 불일치).
4. 프론트엔드 구조와 문서 정합성(feature 교차 import, CLAUDE.md 구조 목록 드리프트).

각 발견은 반드시:
- **파일·라인 근거**(`경로:라인` 형식),
- **중요도**: 양호 / 경미 / 중요,
- **영향**과 **최소 권고**를 포함합니다.

위 "실행 검증 결과"가 SKIPPED이면 "실행 근거 없음"을 명시하고, 정적 분석 기반임을 밝히세요.
리뷰는 근거 없는 단정 대신 관찰→근거→중요도→권고 순으로 적으세요.
```

- [ ] **Step 2: `prompts/routed/review/codex_review.md` 작성**

```markdown
당신은 깐깐한 리뷰 검증자입니다. 아래 **아키텍처 리뷰**가 요청을 충족하는지 반박적으로
검증하세요. 코드를 수정하지 마세요.

## 요청
{{REQUEST}}

## 사전 컨텍스트
{{CLAUDE_CONTEXT}}

## 하네스 실행 검증 결과(실측 근거)
{{VERIFICATION_SUMMARY}}

## 검증 대상 리뷰
{{CLAUDE_ARCHITECTURE}}

## 검증 기준
- 범위 누락: 결합도/디자인패턴/의존성 드리프트/프론트·문서 정합성 중 빠진 축이 있는가?
- 근거 부실: 파일·라인 근거 없이 단정한 항목이 있는가?
- 오탐/과대·과소 중요도: 문서화된 유예를 신규 결함으로 오분류하지 않았는가?
- 실측 정합성: 위 실행 검증 결과와 모순되는 주장이 있는가?

수정이 필요하면 응답에 정확히 `NEEDS_CHANGES`를 포함하고, 무엇을 어떻게 보완할지
구체적으로 지시하세요. 충분하면 통과로 판정하고 남은 잔여 리스크만 짧게 남기세요.
```

주의: `NEEDS_CHANGES` 마커는 `autoagent/safety.py:review_needs_changes`가 인식하는 값과 일치해야 preamble 루프의 조기종료/반복이 동작한다. Step 4에서 실제 인식 문자열을 확인해 맞출 것.

- [ ] **Step 3: `review_needs_changes` 인식 문자열 확인 후 프롬프트 정합**

Run: `python -c "import sys; sys.path.insert(0,r'C:\Users\systran\Desktop\AutoAgent'); import inspect; from autoagent import safety; print(inspect.getsource(safety.review_needs_changes))"`
Expected: 인식하는 마커 문자열(예: `needs_changes` 대소문자 처리)을 확인. codex_review.md의 `NEEDS_CHANGES`가 그 규칙에 매치되도록(대소문자/구두점) 필요 시 수정.

- [ ] **Step 4: `PROMPT_ALIASES`에 별칭 추가**

`autoagent/artifacts.py`의 `PROMPT_ALIASES` 딕셔너리에 두 줄 추가(codex_validation.md 별칭 아래):

```python
    "claude_review_route.md": "routed/review/claude_review.md",
    "codex_review_route.md": "routed/review/codex_review.md",
```

- [ ] **Step 5: 애드혹 검증 — 별칭 해석 + 렌더**

```python
import sys; sys.path.insert(0, r"C:\Users\systran\Desktop\AutoAgent")
from autoagent.artifacts import prompt_path, render_template
assert prompt_path("claude_review_route.md").name == "claude_review.md"
assert prompt_path("codex_review_route.md").name == "codex_review.md"
out = render_template("claude_review_route.md", {"REQUEST": "X", "VERIFICATION_SUMMARY": "S", "CLAUDE_CONTEXT": "C", "PRIOR_VALIDATION": "", "TASK_TYPE": "review", "WORKSPACE": "W"})
assert "{{" not in out.replace("{{VERIFICATION", "OK") or "S" in out  # 치환 확인
assert "S" in out and "X" in out, out[:200]
print("T3 OK")
```

Run: `python <scratchpad>/t3_check.py`
Expected: `T3 OK`

- [ ] **Step 6: 커밋**

```bash
git add prompts/routed/review/claude_review.md prompts/routed/review/codex_review.md autoagent/artifacts.py
git commit -m "feat: review 라우트용 리뷰 산출/리뷰 검증 프롬프트 2개 + PROMPT_ALIASES 등록"
```

---

## Task 4: preamble에서 review 프롬프트 분기 (Q1-C)

**Files:**
- Modify: `autoagent/workflows/routed_preamble.py:66-116` (`run_architecture`/`run_validation` 내부 프롬프트 이름)

**Interfaces:**
- Consumes: 별칭 `claude_review_route.md`/`codex_review_route.md`(Task 3).
- Produces: review일 때 02/03가 리뷰/리뷰검증 프롬프트로 렌더됨. 파일명(`02_claude_architecture`, `03_codex_validation`)은 불변.

- [ ] **Step 1: review 분기 플래그 + 프롬프트 이름 선택**

`run_preamble` 안, `request = base_values["REQUEST"]`(34행) 아래에 추가:

```python
    # review 서브타입일 때만 리뷰 산출/검증 프롬프트로 분기한다(docs/backend/frontend 불변).
    is_review = route["task_type"] == "review"
    arch_prompt_name = "claude_review_route.md" if is_review else "claude_architect.md"
    val_prompt_name = "codex_review_route.md" if is_review else "codex_validation.md"
```

- [ ] **Step 2: `run_architecture`의 렌더 호출 교체**

`run_architecture`(66행) 내부 `render_template("claude_architect.md", {...})`를 아래로 교체:

```python
        prompt = render_template(
            arch_prompt_name,
            {
                **base_values,
                "CLAUDE_CONTEXT": context,
                "PRIOR_VALIDATION": prior_validation,
                "VERIFICATION_SUMMARY": verification_summary,
            },
        )
```

(`verification_summary`는 Task 5에서 정의. Task 4만 단독 적용 시 이 변수가 없어 NameError → Task 4·5는 연속 실행하고 하나로 커밋. 아래 Step 5에서 함께 검증.)

- [ ] **Step 3: `run_validation`의 렌더 호출 교체**

`run_validation`(92행) 내부 `render_template("codex_validation.md", {...})`를 아래로 교체:

```python
        prompt = render_template(
            val_prompt_name,
            {
                **base_values,
                "CLAUDE_CONTEXT": context,
                "CLAUDE_ARCHITECTURE": architecture,
                "VERIFICATION_SUMMARY": verification_summary,
            },
        )
```

- [ ] **Step 4: (Task 5와 합류) — 아래 Task 5를 이어서 구현**

Task 4의 `verification_summary` 참조는 Task 5가 정의한다. 두 태스크는 한 편집 세션에서 연속 수행하고 **Task 5 끝에서 함께 커밋**한다(중간 상태는 import는 되지만 review 실행 시 NameError이므로 커밋하지 않는다).

---

## Task 5: preamble 검증 앞단 실행 + 요약 반환 + routed.py 주입 (Q2-A)

**Files:**
- Modify: `autoagent/workflows/routed_preamble.py` (import, `verification_summary` 정의/실행, 모든 return 튜플)
- Modify: `autoagent/workflows/routed.py:44-53` (언패킹 + common 주입)

**Interfaces:**
- Produces: `run_preamble(...) -> tuple[str, str, str, str, bool]` = `(context, architecture, validation, verification_summary, stopped)`.
- Consumes: `run_verification_or_skip`(Task 2), `common["VERIFICATION_SUMMARY"]`를 Task 6이 소비.

- [ ] **Step 1: import 추가**

`autoagent/workflows/routed_preamble.py:18` 아래에 추가:

```python
from autoagent.verification import run_verification_or_skip
```

- [ ] **Step 2: `verification_summary` 초기화 + 검증 앞단 실행**

`is_review` 정의(Task 4 Step 1) 아래에 초기화 추가:

```python
    verification_summary = ""  # review가 아니거나 dry-run이면 빈 문자열로 남는다.
```

context 단계의 `if stop_after(args, run_dir, "context"): return context, "", "", True`를 아래로 교체(요약 원소 추가 + review면 검증 실행):

```python
    if stop_after(args, run_dir, "context"):
        return context, "", "", verification_summary, True

    # Q2-A: review 라우트는 리뷰 분석 앞단에서 하네스가 직접 검증을 돌려 실측 근거를 만든다.
    # 읽기전용이라 부작용 없음. dry-run/skip/비활성/미설정은 run_verification_or_skip이 처리.
    if is_review and not args.dry_run and not getattr(args, "skip_verification", False) and config.verification_enabled:
        verification_summary, _ok = run_verification_or_skip(run_dir=run_dir, config=config)
```

- [ ] **Step 3: 나머지 return 튜플에 요약 원소 추가**

`routed_preamble.py`의 남은 두 return을 각각 교체:

- 121행 근처 `return context, architecture, "", True` → `return context, architecture, "", verification_summary, True`
- 142행 근처 `return context, architecture, validation, stopped` → `return context, architecture, validation, verification_summary, stopped`

시그니처 주석도 갱신: 28행 `-> tuple[str, str, str, bool]:` → `-> tuple[str, str, str, str, bool]:`, docstring(29행)을 `(context, architecture, validation, verification_summary, stopped)`로.

- [ ] **Step 4: `routed.py` 언패킹 + common 주입**

`autoagent/workflows/routed.py:44`:

```python
        context, architecture, validation, verification_summary, stopped = run_preamble(args, config, base_values, route, budget, run_dir)
```

`common` 딕셔너리(48~53행)에 한 줄 추가:

```python
        common = {
            **base_values,
            "CLAUDE_CONTEXT": context,
            "CLAUDE_ARCHITECTURE": architecture,
            "CODEX_VALIDATION": validation,
            "VERIFICATION_SUMMARY": verification_summary,
        }
```

- [ ] **Step 5: 애드혹 검증 — review dry-run이 새 프롬프트로 렌더 + 시그니처**

```python
import sys; sys.path.insert(0, r"C:\Users\systran\Desktop\AutoAgent")
import inspect
from autoagent.workflows.routed_preamble import run_preamble
sig = str(inspect.signature(run_preamble))
print("sig", sig)  # 반환 주석은 런타임에 안 보이니 참고용
```

이어서 review dry-run을 실제로 돌려 02/03 프롬프트가 리뷰용인지 확인:

Run: `python C:/Users/systran/Desktop/AutoAgent/run.py --dry-run --workflow routed --task-type review --request "프로젝트 결합도 점검" 2>&1 | grep -oE 'complete: .*' | tail -1`
그 run_dir의 `02_claude_architecture_prompt.md`를 **Read 도구**로 열어 "최종 아키텍처 리뷰"/"파일·라인 근거" 문구가 있는지, `03_codex_validation_prompt.md`에 "리뷰 검증"/"NEEDS_CHANGES"가 있는지 확인.
Expected: review 프롬프트 내용이 렌더됨(계획 프롬프트가 아님). dry-run이라 `VERIFICATION_SUMMARY`는 빈 문자열로 치환.

- [ ] **Step 6: 커밋 (Task 4 편집 포함)**

```bash
git add autoagent/workflows/routed_preamble.py autoagent/workflows/routed.py
git commit -m "feat: review 라우트 프롬프트 분기 + 검증 앞단 실행·요약 주입(Q1-C, Q2-A)"
```

---

## Task 6: routed_docs가 실제 리뷰+검증을 평가/보고로 전달

**Files:**
- Modify: `autoagent/workflows/routed_docs.py:18-55` (`run_docs_route`)

**Interfaces:**
- Consumes: `common["TASK_TYPE"]`, `common["CLAUDE_ARCHITECTURE"]`(=리뷰 본문), `common["VERIFICATION_SUMMARY"]`.
- Produces: review일 때 evaluation/final_report가 실제 리뷰를 `REVIEW_RESULT`, 검증을 `FINAL_REVIEW_RESULT`로 받는다.

- [ ] **Step 1: review/docs 분기 값 구성**

`run_docs_route` 첫 부분(evaluation 호출 전)에 추가:

```python
    # review 라우트는 실제 리뷰(02)와 검증 요약을 평가/보고에 넘긴다. docs(문서)는 기존 문자열 유지.
    if common.get("TASK_TYPE") == "review":
        impl_arg = "No implementation step was run (read-only review route)."
        review_arg = common.get("CLAUDE_ARCHITECTURE") or "No review produced."
        final_review_arg = common.get("VERIFICATION_SUMMARY") or "No verification stage was run."
    else:
        impl_arg = "No implementation step was run."
        review_arg = "Read-only or docs/review route."
        final_review_arg = "No final code review step was run."
```

- [ ] **Step 2: evaluation/final_report 호출에서 분기 값 사용**

`run_evaluation(...)`의 `implementation=`, `review=`, `final_review=` 인자를 `impl_arg`/`review_arg`/`final_review_arg`로, `fix=`는 `"No fix step was run."` 유지. `run_final_report(...)`도 동일하게 교체. 예:

```python
    evaluation = run_evaluation(
        args, config, common, budget, run_dir,
        name="04_codex_evaluation",
        implementation=impl_arg,
        review=review_arg,
        fix="No fix step was run.",
        final_review=final_review_arg,
    )
    ...
    final = run_final_report(
        args, config, common, budget, run_dir,
        name="05_claude_final_report",
        implementation=impl_arg,
        review=review_arg,
        fix="No fix step was run.",
        final_review=final_review_arg,
        evaluation=evaluation,
    )
```

- [ ] **Step 3: 애드혹 검증 — review dry-run의 평가 프롬프트가 리뷰를 담는가**

Run: `python C:/Users/systran/Desktop/AutoAgent/run.py --dry-run --workflow routed --task-type review --request "프로젝트 결합도 점검" 2>&1 | grep -oE 'complete: .*' | tail -1`
그 run_dir의 `04_codex_evaluation_prompt.md`를 **Read 도구**로 열어 `REVIEW_RESULT` 자리에 리뷰 본문(dry-run이면 `[dry-run: Claude architecture output]`)이, `FINAL_REVIEW_RESULT` 자리에 검증 요약이 들어갔는지 확인.
그리고 **docs** dry-run은 기존 문자열("Read-only or docs/review route.")을 유지하는지 확인:
`python .../run.py --dry-run --workflow routed --task-type docs --request "document API" 2>&1 | ...` → `04_codex_evaluation_prompt.md`에 기존 문자열.
Expected: review는 리뷰/검증이 주입, docs는 불변.

- [ ] **Step 4: 커밋**

```bash
git add autoagent/workflows/routed_docs.py
git commit -m "feat: review 라우트가 실제 리뷰+검증 요약을 평가/보고로 전달"
```

---

## Task 7: 회귀 매트릭스 비교 + LD 라이브 review 검증

**Files:**
- Use: `<scratchpad>/regress/capture.sh` (Task 1)

**Interfaces:**
- Consumes: `before/`(Task 1). Produces: `after/` + diff 결과.

- [ ] **Step 1: 변경 후 매트릭스 재캡처**

Run: `bash <scratchpad>/regress/capture.sh after`
Expected: 8줄 모두 `ok`.

- [ ] **Step 2: byte-equality 비교(backend/frontend/docs = review 제외)**

Run: `diff -r <scratchpad>/regress/before <scratchpad>/regress/after`
Expected: **출력 없음(바이트 동일)**. 차이가 있으면 어떤 라벨/파일인지 확인 — backend/frontend/docs에서 차이가 나면 회귀이므로 원인 수정. (review는 매트릭스에 없으므로 비교 대상 아님.)

- [ ] **Step 3: LD 라이브 review 실행(예산 정상)**

Run:
```
python C:/Users/systran/Desktop/AutoAgent/run.py --workflow routed --task-type review --project LanguageDetection --workspace C:/Users/systran/Desktop/LanguageDetection --max-review-rounds 2 --max-agent-calls 9 --request "프로젝트 전체 의존 결합 및 아키텍처 패턴 디자인패턴 전체적으로 점검해"
```
run_dir을 stdout에서 확보.

- [ ] **Step 4: 라이브 산출물 확인(Read 도구, utf-8)**

`<run_dir>`에서 확인:
- `04b_verification.md` — overall PASS/FAIL(SKIPPED 아님). venv311 compileall/pytest/frontend_build 결과가 담김.
- `02_claude_architecture.md` — 계획이 아니라 파일·라인 근거+중요도 담은 리뷰. 검증 결과 인용 흔적.
- `04_codex_evaluation.md` / `final_evaluation.md` — `needs_changes/0.3` 대신 실질 채점(범위 커버 시 status 개선).

Expected: 검증이 실제로 돌고(SKIPPED 아님), 리뷰가 실측을 근거로 하며, 평가가 "산출물 없음"으로 미완 판정하지 않음. (내용상 needs_changes가 남을 수는 있으나, 그 사유가 "리뷰 없음"이 아니라 구체적 발견이어야 함.)

- [ ] **Step 5: 미설정 프로젝트 스킵 확인(선택)**

임시 프로젝트로 review 실행 시 `04b_verification.md`가 `SKIPPED`이고 리뷰 프롬프트가 "실행 근거 없음"을 명시하는지 확인(별도 워크스페이스가 있으면).

- [ ] **Step 6: PR**

```bash
git push -u origin feature/review-route-verification
gh pr create --title "review 라우트: 실제 리뷰 산출 + 하네스 실행근거 + per-project 검증" --body "<요약>"
```

---

## Self-Review (작성자 체크)

- **스펙 커버리지**: 목표1(실제 리뷰)=Task 3·4·6, 목표2(실행 근거)=Task 2·5, 목표3(per-project)=Task 2. Q1-C=Task4, Q2-A=Task5, Q3-A=Task2. 신규 프롬프트 2개=Task3. byte-equality=Task1·7. 라이브=Task7. 누락 없음.
- **플레이스홀더 스캔**: 모든 코드 스텝에 실제 코드/명령/기대출력 포함. TBD/TODO 없음.
- **타입 일관성**: `run_preamble` 4-튜플→5-튜플을 Task5에서 모든 return + routed.py 언패킹 동시 수정. `run_verification_or_skip` 시그니처가 Task2 정의와 Task5 호출에서 일치(`run_dir=`, `config=`). `verification_summary` 변수는 Task4에서 참조·Task5에서 정의 → 두 태스크 합류·단일 커밋으로 NameError 회피 명시.

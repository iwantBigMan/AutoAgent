# Codex 구현 전담 · Claude 리뷰/계획/문서 역할 분업 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** routed 워크플로에서 모든 구현(backend·frontend)을 Codex가, 모든 리뷰(라운드 + 최종)를 반대편 Claude가 맡도록 역할을 고정하고, Codex 구현자가 자기 diff를 자체 리뷰하도록 한다.

**Architecture:** 세 지점을 바꾼다 — (1) `routing.choose_implementer`의 backend 기본을 codex로, (2) `final-review` 역할을 codex 고정에서 구현자 반대편(`route["review_agent"]`)으로(신규 `claude_final_review.md` 프롬프트 포함), (3) `codex_impl` 프롬프트에 자체 리뷰 지시를 접는다. 나머지 고정 역할(context·architect=claude, validation·evaluation=codex, report=claude)은 불변.

**Tech Stack:** Python 3(순수 표준 라이브러리), 프롬프트 마크다운 템플릿(`{{VAR}}` 치환), JSON 역할 레지스트리. 서브프로세스로 `claude.cmd`/`codex.cmd` 오케스트레이션.

**Spec:** `docs/superpowers/specs/2026-07-23-codex-impl-claude-review-role-split.md`

## Global Constraints

- **테스트 스위트 없음.** 검증은 (a) `--dry-run` 렌더 산출물 확인, (b) 함수 직접 호출 python 어서션(**새 프로세스**), (c) 회귀 감시선은 docs·review 라우트 dry-run의 **byte-equality**. pytest를 새로 만들지 말 것.
- **하네스 자기수정 금지.** 이 레포 자신에 `/aa`나 실제 routed 런(비-dry-run)을 돌리지 말 것 — 실행 중 프로세스가 옛 모듈을 든 채 파일이 바뀌면 리뷰 단계에서 크래시. 검증은 **빠른 dry-run(새 프로세스마다 최신 코드 로드)** 과 python 어서션, 그리고 **타깃 워크스페이스(LanguageDetection) 라이브 런**만 사용.
- **리뷰어 = 구현자 반대 모델** 불변식을 절대 깨지 말 것. 이번 변경은 이 불변식을 최종리뷰(07)까지 확장하는 것이지 약화가 아니다.
- 모든 모듈은 **한국어 docstring**, 함수는 **한국어 인라인 주석**(식별자만 영문). 기존 스타일에 맞출 것.
- `from __future__ import annotations`, PEP 604 타입(`str | None`).
- 커밋 메시지 끝에 반드시: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **main 푸시 차단** — 작업 브랜치는 `feature/codex-impl-claude-review`(이미 최신 main 기반으로 생성됨).
- 한글 산출물은 Bash stdout에서 cp949 mojibake가 나므로 내용 확인은 **Read 도구(utf-8)** 로.
- `--dry-run`은 예산(`--max-agent-calls`)에 포함되지 않음. dry-run 검증은 기본 config(workspace=LanguageDetection, `--project` 미지정 → 산출물은 `runs/`)로 수행.

---

## 파일 구조 (변경 대상)

| 파일 | 책임 | 태스크 |
|---|---|---|
| `roles.default.json` | 역할 레지스트리 — `final-review` agent를 `route`로 | 1 |
| `autoagent/workflows/routed_impl.py` | `run_final_review`를 review_agent 기준 분기 + 산출명, 모듈 docstring | 1 |
| `prompts/routed/final/claude_final_review.md` | **신규** — Claude용 최종리뷰 프롬프트(`codex_final.md`의 대칭본) | 1 |
| `autoagent/artifacts.py` | `PROMPT_ALIASES`에 신규 프롬프트 등록 | 1 |
| `commands/aa.md` | resume/report용 07 glob 와일드카드화 | 1 |
| `autoagent/workflows/task_exec.py` | decompose 통합 최종리뷰 주석(코드 변경 없음) | 1 |
| `autoagent/routing.py` | `choose_implementer` backend 기본 codex + `CODEX_IMPLEMENTER_TERMS` 제거 | 2 |
| `prompts/routed/backend/codex_impl.md` | 자체 리뷰(SELF_REVIEW) 지시 추가 | 3 |
| `prompts/routed/frontend/codex_impl.md` | 자체 리뷰(SELF_REVIEW) 지시 추가 | 3 |
| `CLAUDE.md`, `README.md`, `docs/AutoAgent_공부가이드.md`, `docs/AutoAgent_하네스개요.md`, `docs/AutoAgent_하네스개요.html` | 서술/도식 정확성 갱신 | 4 |

---

## Pre-Flight: 회귀 감시선 베이스라인 캡처

**Task 1 시작 전에** 반드시 1회 수행(현재 브랜치 코드 == main, spec 커밋만 얹혀 있어 동작은 main과 동일):

- [ ] **docs·review 라우트 dry-run 베이스라인을 스크래치패드에 저장**

```bash
BASE="C:/Users/systran/AppData/Local/Temp/claude/C--Users-systran-Desktop-AutoAgent/43edc380-6605-4113-a31c-202eddc8fe13/scratchpad/baseline_dryrun"
mkdir -p "$BASE"
cd /c/Users/systran/Desktop/AutoAgent
for t in docs review; do
  for impl in claude codex; do
    python run.py --dry-run --workflow routed --task-type "$t" --request "sample $t request" --implementer "$impl"
  done
done
# 방금 생성된 docs/review 런 디렉터리들만 베이스라인으로 복사(가장 최근 4개)
ls -dt runs/*/ | head -4 | xargs -I{} cp -r {} "$BASE/"
ls "$BASE"
```

Expected: `$BASE` 아래에 최근 4개 런 폴더가 복사됨(각 폴더에 `*_command.json`, `*_prompt.md`). 이 스냅샷이 최종 회귀 비교의 기준이다. docs/review는 이번 변경과 무관하므로 **작업 후에도 byte-identical이어야 한다.**

---

## Task 1: 최종리뷰(07)를 구현자 반대편으로

`final-review` 역할을 codex 고정에서 구현자의 반대 모델로 바꾼다. codex 구현이면 07은 Claude가, claude 구현이면 07은 Codex가 맡는다. 이 태스크만으로도 **frontend**(이미 codex 구현) dry-run에서 07이 claude로 바뀌어 독립 검증된다.

**Files:**
- Modify: `roles.default.json:10`
- Modify: `autoagent/workflows/routed_impl.py:1-6`(docstring), `:213-257`(`run_final_review`)
- Create: `prompts/routed/final/claude_final_review.md`
- Modify: `autoagent/artifacts.py:43-44`(PROMPT_ALIASES)
- Modify: `commands/aa.md:60`
- Modify: `autoagent/workflows/task_exec.py:439`(주석)

**Interfaces:**
- Consumes: `route["review_agent"]`(구현자 반대 모델; `route_task`가 항상 채운다), `roles["final-review"]` 엔트리, `resolve_role(...)`, `command_for_agent(...)`, `render_template(...)`.
- Produces: `run_final_review(*, args, config, common, route, request, budget, run_dir, implementation, review, fix, name: str | None = None) -> str` — 산출 파일 `07_{review_agent}_final_review.md`(+dry-run 시 `_prompt.md`/`_command.json`).

- [ ] **Step 1: `roles.default.json`의 final-review agent를 `route`로**

`roles.default.json:10`을 아래로 교체(변경점: `"agent": "codex"` → `"agent": "route"`):

```json
    { "id": "final-review",  "agent": "route",   "tier": "standard",                          "high_risk_condition": "none",                       "mutating": false, "sandbox": "configured" },
```

- [ ] **Step 2: 신규 프롬프트 `prompts/routed/final/claude_final_review.md` 작성**

`codex_final.md`의 Claude 대칭본. 아래 내용 그대로 생성(맨 앞 `# 역할`부터 마지막 줄까지):

````markdown
# 역할

당신은 최종 검증 리뷰를 수행하는 Claude입니다. 구현은 Codex가 했고, 당신은 반대 모델로서 독립적으로 최종 점검합니다.

# 작업공간

{{WORKSPACE}}

# 원본 사용자 요청

{{REQUEST}}

# 라우트

```json
{{ROUTE_JSON}}
```

# Claude 컨텍스트

{{CLAUDE_CONTEXT}}

# Claude 아키텍처

{{CLAUDE_ARCHITECTURE}}

# Codex 검증

{{CODEX_VALIDATION}}

# 구현 결과

{{IMPLEMENTATION_RESULT}}

# 리뷰 결과

{{REVIEW_RESULT}}

# 수정 결과

{{FIX_RESULT}}

# 작업

최종 코드리뷰 방식의 검증을 수행하세요.

`subtype`이 `db`이면 데이터 안전성, 호환성, 마이그레이션 upgrade/downgrade, 롤백, 트랜잭션, 잠금, nullable/default/index/constraint 선언, Alembic 리비전 일관성, repository/API 계약, 검증 커버리지에 대한 최종 점검을 포함하세요.

다음으로 시작하세요:

`FINAL_STATUS: approved`

또는

`FINAL_STATUS: needs_changes`

또는

`FINAL_STATUS: blocked`

그다음 반환하세요:
- 블로킹 지적사항(있다면)
- 검증 충분성
- 남은 위험
- 간결한 다음 조치

규칙:
- 파일을 수정하지 마세요.
- 비밀정보를 포함하지 마세요.
````

- [ ] **Step 3: `PROMPT_ALIASES`에 신규 프롬프트 등록**

`autoagent/artifacts.py`의 `PROMPT_ALIASES`에서 `"codex_final.md"` 줄(43) 바로 뒤에 한 줄 추가:

```python
    "claude_final.md": "routed/final/claude_final.md",
    "codex_final.md": "routed/final/codex_final.md",
    "claude_final_review.md": "routed/final/claude_final_review.md",
    "codex_evaluator.md": "routed/final/codex_evaluator.md",
```

(위는 문맥 확인용. 실제 추가 줄은 `    "claude_final_review.md": "routed/final/claude_final_review.md",` 하나이며 `"codex_final.md"`와 `"codex_evaluator.md"` 사이에 넣는다.)

- [ ] **Step 4: `run_final_review`를 review_agent 기준으로 분기**

`autoagent/workflows/routed_impl.py:213-257`의 `run_final_review` 전체를 아래로 교체:

```python
def run_final_review(
    *,
    args: Namespace,
    config: Config,
    common: dict[str, Any],
    route: dict[str, Any],
    request: str,
    budget: AgentCallBudget,
    run_dir: Path,
    implementation: str,
    review: str,
    fix: str,
    name: str | None = None,
) -> str:
    """최종리뷰(07). 리뷰어는 구현자의 반대 모델(route["review_agent"])이다.

    codex 구현이면 claude가, claude 구현이면 codex가 최종리뷰를 맡는다. 산출 파일명도
    05/06처럼 에이전트를 반영한다. dry-run이면 프롬프트/커맨드만 렌더하고 [dry-run]
    문자열을 반환한다. routed_impl과 decompose 실행기(task_exec)가 공유한다.
    """
    # 리뷰어 = 구현자 반대편. 파일명은 05_{review_agent}/06_{impl}과 일관되게 review_agent 반영.
    review_agent = route["review_agent"]
    if name is None:
        name = f"07_{review_agent}_final_review"
    # final-review 역할은 sandbox="configured"라 read_only를 무시하고 config.codex_sandbox를
    # 그대로 쓴다(codex가 리뷰어일 때만 의미; 현행 동작 보존). claude 리뷰어면 mutating=false라
    # resolve_role이 permission_mode=plan을 부여한다.
    roles = load_roles(DEFAULT_CONFIG.parent)
    final_review_role = resolve_role(
        roles["final-review"], config=config, route=route, request=request, agent=review_agent, read_only=args.read_only
    )
    # claude 리뷰어면 대칭 프롬프트(claude_final_review.md)를, codex면 기존 codex_final.md를 쓴다.
    prompt_name = "claude_final_review.md" if review_agent == "claude" else "codex_final.md"
    final_review_prompt = render_template(
        prompt_name,
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review, "FIX_RESULT": fix},
    )
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", final_review_prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, final_review_role))
        return f"[dry-run: {review_agent} final review output]"
    command_name = require_command(config.claude_command if review_agent == "claude" else config.codex_command)
    budget.before_call(next_step="final-review", out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name,
        command=command_for_agent(config, final_review_role, resolved_command=command_name),
        prompt=final_review_prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / f"{name}.md", result)
    return result
```

- [ ] **Step 5: 모듈 docstring 갱신**

`autoagent/workflows/routed_impl.py:4-5`(docstring 본문 2줄)을 아래로 교체:

```python
최종리뷰(07) -> 평가(08) -> 최종보고(09). 리뷰어는 항상 구현자와 반대 모델이고(07 최종리뷰 포함),
high-risk backend 구현/수정은 codex의 deep 티어(effort high)로 수행한다.
```

- [ ] **Step 6: `commands/aa.md`의 07 glob 와일드카드화 + `task_exec.py` 주석**

`commands/aa.md:60`에서 `07_codex_final_review*`를 `07_*_final_review*`로 바꾼다(05/06과 일관; 08은 codex 고정 유지라 `08_codex_evaluation*` 그대로):

```text
`05_*_review*`, `06_*_fix*`, `07_*_final_review*`, `08_codex_evaluation*`,
```

`autoagent/workflows/task_exec.py:439`의 주석 한 줄을 갱신(코드 변경 없음 — run_route가 이미 review_agent를 담고 run_final_review가 그걸 소비):

```python
        # 통합 트리에 대해 run 레벨 1회: 최종리뷰(구현자 반대편, 07) → 평가(codex 08) → 최종보고(claude 09).
```

- [ ] **Step 7: 검증 — 하네스 부팅(validate_roles) + 신규 프롬프트 렌더 + frontend 07 flip**

`C:\Users\systran\Desktop\AutoAgent`에서(새 프로세스마다 최신 코드 로드):

```bash
cd /c/Users/systran/Desktop/AutoAgent
# (a) final-review="route"로도 validate_roles 통과 + 하네스 부팅 확인(docs 라우트는 이번 변경과 무관)
python run.py --dry-run --workflow routed --task-type docs --request "boot check"
echo "exit=$?"   # Expected: exit=0 (SystemExit 없음 = validate_roles 통과)

# (b) 신규 프롬프트 alias가 해석되고 Claude 최종리뷰 프롬프트가 렌더되는지
python -c "from autoagent.artifacts import render_template; v={k:k for k in ['WORKSPACE','REQUEST','ROUTE_JSON','CLAUDE_CONTEXT','CLAUDE_ARCHITECTURE','CODEX_VALIDATION','IMPLEMENTATION_RESULT','REVIEW_RESULT','FIX_RESULT']}; s=render_template('claude_final_review.md', v); print(s.splitlines()[2]); print('FINAL_STATUS' in s)"
# Expected 1st line: 당신은 최종 검증 리뷰를 수행하는 Claude입니다. 구현은 Codex가 했고, 당신은 반대 모델로서 독립적으로 최종 점검합니다.
# Expected 2nd line: True

# (c) frontend dry-run에서 07이 claude로 뒤집혔는지(파일명으로 확인)
python run.py --dry-run --workflow routed --task-type frontend --request "tweak the button hover color"
NEW=$(ls -dt runs/*/ | head -1)
ls "$NEW" | grep -E '^07_'
# Expected: 07_claude_final_review_command.json 및 07_claude_final_review_prompt.md
#           (07_codex_final_review* 는 없어야 함)
```

만약 (c)가 07을 아예 안 만들면(예: frontend가 gate에서 멈춤) `07_` 산출이 없을 수 있다 — 그 경우 `--task-type backend --request "add a health check GET endpoint"`(medium risk, gate 없음)로 재확인. 단 backend는 Task 2 전이므로 07=`07_codex_final_review`(backend가 아직 claude 구현)로 나오는 게 정상이다. **Task 1 단독 검증의 핵심은 frontend에서 07이 claude로 바뀌는 것.**

- [ ] **Step 8: 커밋**

```bash
git add roles.default.json autoagent/workflows/routed_impl.py prompts/routed/final/claude_final_review.md autoagent/artifacts.py commands/aa.md autoagent/workflows/task_exec.py
git commit -m "feat: 최종리뷰(07)를 구현자 반대편으로(codex 고정 해제) + claude_final_review 프롬프트

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 라우팅 — backend 기본 구현자를 Codex로

`choose_implementer`에서 auto 라우팅의 구현을 backend·frontend 모두 Codex로 통일한다. `CODEX_IMPLEMENTER_TERMS` 특례 분기는 결과가 codex로 수렴하므로 상수째 제거하고, 이제 쓰이지 않는 `subtype`/`request` 매개변수도 함수에서 뺀다(호출부는 같은 파일 1곳).

**Files:**
- Modify: `autoagent/routing.py:45-67`(상수 제거), `:239-244`(호출부), `:261-297`(`choose_implementer`)

**Interfaces:**
- Consumes: `requested_implementer`(명시 오버라이드), `task_type`.
- Produces: `choose_implementer(*, requested_implementer: str, task_type: str) -> tuple[str, str, str]`. auto·backend·frontend → `("codex", "claude", ...)`, docs/review → `("claude", "codex", ...)`, 명시 오버라이드 우선.

- [ ] **Step 1: `CODEX_IMPLEMENTER_TERMS` 상수 제거**

`autoagent/routing.py:45-67`의 `CODEX_IMPLEMENTER_TERMS = [ ... ]` 블록 전체(빈 줄 포함)를 삭제한다. (grep 확인 결과 이 상수의 유일한 사용처는 `choose_implementer` 하나이며 Step 3에서 함께 사라진다.)

- [ ] **Step 2: 호출부에서 불필요해진 인자 제거**

`autoagent/routing.py:239-244`를 아래로 교체:

```python
    implementation_agent, review_agent, implementer_reason = choose_implementer(
        requested_implementer=requested_implementer,
        task_type=chosen,
    )
```

- [ ] **Step 3: `choose_implementer` 본문 교체**

`autoagent/routing.py:261-297`의 `choose_implementer` 전체를 아래로 교체:

```python
def choose_implementer(
    *,
    requested_implementer: str,
    task_type: str,
) -> tuple[str, str, str]:
    """(구현자, 리뷰어, 사유)를 반환. 리뷰어는 항상 구현자와 반대 모델이다.

    명시 지정이 우선. auto면 모든 구현(backend·frontend)은 Codex가 맡고 리뷰는 반대편
    Claude가 맡는다. docs/review 라우트는 구현 스텝이 없어 claude를 구현자 자리에 둔다.
    """
    if requested_implementer == "claude":
        return "claude", "codex", "Implementer explicitly set to Claude."
    if requested_implementer == "codex":
        return "codex", "claude", "Implementer explicitly set to Codex."

    if task_type in {"backend", "frontend"}:
        return "codex", "claude", f"{task_type.capitalize()} implementation defaults to Codex."
    if task_type in {"docs", "review"}:
        return "claude", "codex", "Docs/review routes have no implementation step."

    return "claude", "codex", "Fallback implementer selection."
```

- [ ] **Step 4: `choose_implementer` 상단 docstring 문구 정정(있다면)**

`autoagent/routing.py:1-6` 모듈 docstring은 "구현자/리뷰어 모델(구현자와 반대)을 선택"이라 여전히 옳으므로 변경 불필요. (이 스텝은 확인만: 모듈 docstring에 "backend=claude" 취지의 문구가 없는지 Read로 확인하고, 있으면 새 규칙에 맞춰 정정한다. 없으면 그대로 둔다.)

- [ ] **Step 5: 검증 — 라우팅 매트릭스 python 어서션**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python -c "
from autoagent.routing import route_task
def impl(t, req='x', ov='auto'):
    r = route_task(t, req, ov)
    return r['implementation_agent'], r['review_agent']
assert impl('backend') == ('codex','claude'), impl('backend')
assert impl('frontend') == ('codex','claude'), impl('frontend')
assert impl('docs') == ('claude','codex'), impl('docs')
assert impl('review') == ('claude','codex'), impl('review')
# 이전에 codex로 넘어가던 test/build 성격 문구도 이제 동일하게 codex
assert impl('backend','fix the failing pytest build') == ('codex','claude')
# 명시 오버라이드 우선
assert impl('backend', ov='claude') == ('claude','codex')
assert impl('frontend', ov='codex') == ('codex','claude')
print('routing OK')
"
# Expected: routing OK
```

- [ ] **Step 6: 검증 — backend dry-run 전체 흐름 확인**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python run.py --dry-run --workflow routed --task-type backend --request "add a health check GET endpoint"
NEW=$(ls -dt runs/*/ | head -1)
ls "$NEW" | grep -E '^(04|05|06|07)_'
# Expected(핵심): 04_codex_backend_impl_*, 05_claude_backend_review_r1_*,
#                 06_codex_backend_fix_r1_*(리뷰가 needs_changes를 안 내면 06은 없을 수 있음),
#                 07_claude_final_review_*
```

- [ ] **Step 7: 커밋**

```bash
git add autoagent/routing.py
git commit -m "feat: backend 기본 구현자를 codex로(모든 구현=codex, 리뷰=claude), CODEX_IMPLEMENTER_TERMS 제거

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Codex 자체 리뷰를 구현 프롬프트에 접기

`codex_impl.md`(backend·frontend)에 구현 직후 자기 diff를 자체 리뷰하고 명백한 결함을 고친 뒤 `SELF_REVIEW:` 절로 보고하라는 지시를 넣는다. 새 에이전트 호출은 없다.

**Files:**
- Modify: `prompts/routed/backend/codex_impl.md`(규칙 블록과 상태 줄 사이에 삽입)
- Modify: `prompts/routed/frontend/codex_impl.md`(맨 끝에 추가)

- [ ] **Step 1: backend `codex_impl.md`에 자체 리뷰 절 삽입**

`prompts/routed/backend/codex_impl.md`에서 아래 블록을

```markdown
- 변경된 파일, 실행한 테스트, 실패, 남은 위험을 보고하세요.

결과의 첫 줄은 다음 중 하나로 시작하세요:
```

다음으로 교체(규칙 마지막 줄과 상태 줄 사이에 `# 자체 리뷰` 절 삽입):

```markdown
- 변경된 파일, 실행한 테스트, 실패, 남은 위험을 보고하세요.

# 자체 리뷰

구현을 마친 뒤 결과를 보고하기 전에, 당신의 변경(diff)을 스스로 리뷰하세요.
- 정확성, 회귀, 누락된 테스트, 요청 범위 초과, 기존 스타일 불일치를 점검하세요.
- 명백한 결함은 직접 수정하세요.
- 스스로 고치지 못했거나 판단을 유보한 우려는 결과에 `SELF_REVIEW:` 절로 명시하세요.

이 자체 리뷰는 당신 자신의 점검이며, 독립적인 교차 리뷰는 이후 반대 모델(Claude)이 수행합니다.

결과의 첫 줄은 다음 중 하나로 시작하세요:
```

- [ ] **Step 2: frontend `codex_impl.md` 끝에 자체 리뷰 절 추가**

`prompts/routed/frontend/codex_impl.md`의 마지막 줄

```markdown
- 변경된 파일, 실행한 테스트, 실패, 남은 위험을 보고하세요.
```

뒤에 아래를 이어 붙인다:

```markdown

# 자체 리뷰

구현을 마친 뒤 결과를 보고하기 전에, 당신의 변경(diff)을 스스로 리뷰하세요.
- 정확성, 회귀, 누락된 테스트, 요청 범위 초과, 기존 스타일 불일치를 점검하세요.
- 명백한 결함은 직접 수정하세요.
- 스스로 고치지 못했거나 판단을 유보한 우려는 결과에 `SELF_REVIEW:` 절로 명시하세요.

이 자체 리뷰는 당신 자신의 점검이며, 독립적인 교차 리뷰는 이후 반대 모델(Claude)이 수행합니다.
```

- [ ] **Step 3: 검증 — 두 프롬프트가 SELF_REVIEW 지시를 담는지**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python -c "
from autoagent.artifacts import render_template
bv={k:k for k in ['WORKSPACE','REQUEST','ROUTE_JSON','CLAUDE_CONTEXT','CLAUDE_ARCHITECTURE','CODEX_VALIDATION']}
be=render_template('codex_backend_impl.md', bv)
fe=render_template('codex_frontend_impl.md', bv)
assert '자체 리뷰' in be and 'SELF_REVIEW' in be, 'backend missing'
assert '자체 리뷰' in fe and 'SELF_REVIEW' in fe, 'frontend missing'
# backend는 상태 줄이 자체 리뷰 절 뒤에 와야 함(삽입 위치 확인)
assert be.index('자체 리뷰') < be.index('IMPLEMENTATION_STATUS'), 'backend order wrong'
print('self-review prompts OK')
"
# Expected: self-review prompts OK
```

- [ ] **Step 4: 커밋**

```bash
git add prompts/routed/backend/codex_impl.md prompts/routed/frontend/codex_impl.md
git commit -m "feat: codex 구현 프롬프트에 자체 리뷰(SELF_REVIEW) 단계 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 문서 정확성 갱신

기능 변경을 반영해 서술/도식 문서를 정정한다. 코드 동작에는 영향 없음.

**Files:**
- Modify: `CLAUDE.md`(Critical model 섹션에 역할 규칙 명시)
- Modify: `docs/AutoAgent_하네스개요.md:214-223`(구현자 선택 매트릭스), `:216-223` 표
- Modify: `README.md:413-431`(backend/frontend 산출물 예시의 04/06/07)
- Modify: `docs/AutoAgent_공부가이드.md:120, 227`(07 표기)
- Modify: `docs/AutoAgent_하네스개요.html:326`(final-review 배지)

- [ ] **Step 1: `CLAUDE.md`에 역할 규칙 한 줄 추가**

`CLAUDE.md`의 "## Critical model" 섹션에서 다음 줄

```markdown
- Reviewer is always the **opposite model** of the implementer (`routing.choose_implementer`).
```

뒤에 한 줄 추가:

```markdown
- **역할 분업(고정)**: auto 라우팅에서 모든 구현(backend·frontend)은 **Codex**, 모든 리뷰(라운드 05 + 최종 07)는 반대편 **Claude**가 맡는다. 계획(context·architect)·최종보고는 Claude, 계획검증·평가(08)는 Codex. high-risk backend 구현은 codex `deep` 티어(effort high). Codex 구현자는 결과 전 자기 diff를 자체 리뷰(`SELF_REVIEW`)한다.
```

- [ ] **Step 2: `docs/AutoAgent_하네스개요.md` 구현자 매트릭스 정정**

`docs/AutoAgent_하네스개요.md:216-223`의 표를 아래로 교체:

```markdown
| 조건 | 구현자 | 리뷰어 |
|------|--------|--------|
| `--implementer claude` | claude | codex |
| `--implementer codex` | codex | claude |
| auto · backend | codex | claude |
| auto · frontend | codex | claude |
| docs / review / read-only | (구현 없음) | — |
```

- [ ] **Step 3: `README.md` 산출물 예시 정정**

`README.md:415-421`(backend 예시 블록) 내용을 아래로 교체:

```text
04_codex_backend_impl.md
05_claude_backend_review_r1.md
06_codex_backend_fix_r1.md
07_claude_final_review.md
08_codex_evaluation.md
```

`README.md:425-431`(frontend 예시 블록)에서 `07_codex_final_review.md`를 `07_claude_final_review.md`로 바꾼다(04/05/06은 이미 옳음):

```text
04_codex_frontend_impl.md
05_claude_frontend_review_r1.md
06_codex_frontend_fix_r1.md
07_claude_final_review.md
08_codex_evaluation.md
```

또한 `README.md:413`의 리뷰-수정 예시 문구 `05_codex_backend_review_r1.md`를 `05_claude_backend_review_r1.md`로 정정한다.

- [ ] **Step 4: `docs/AutoAgent_공부가이드.md` 07 표기 정정**

`docs/AutoAgent_공부가이드.md:120`의 `final-review → 07_codex_final_review.md`를 아래로:

```text
            final-review → 07_{리뷰어}_final_review.md (구현자 반대편)
```

`docs/AutoAgent_공부가이드.md:227`의 표 행 `| \`07_codex_final_review.md\` | 최종 감사(Codex 고정) |`을 아래로:

```markdown
| `07_{리뷰어}_final_review.md` | 최종 감사(구현자 반대편; codex 구현이면 claude) |
```

- [ ] **Step 5: `docs/AutoAgent_하네스개요.html` 배지 정정**

`docs/AutoAgent_하네스개요.html:326`의 줄을 아래로 교체(배지 Codex→Claude, 파일명 갱신):

```html
          <div class="st-h"><span class="badge b-c">Claude</span><span class="name">final-review</span><span class="art">07_claude_final_review.md (구현자 반대편)</span></div>
```

(주의: 배지 클래스는 이 파일의 Claude용 클래스를 따를 것. 파일에서 다른 Claude 스텝의 `<span class="badge ...">`를 Read로 확인해 동일 클래스를 쓴다. `b-c`가 Claude가 아니면 그 파일의 Claude 배지 클래스로 맞춘다.)

- [ ] **Step 6: 검증 — stale 07 표기 잔존 여부**

```bash
cd /c/Users/systran/Desktop/AutoAgent
grep -rn "07_codex_final_review" README.md docs/AutoAgent_공부가이드.md docs/AutoAgent_하네스개요.md docs/AutoAgent_하네스개요.html || echo "no stale 07_codex_final_review refs"
# Expected: no stale 07_codex_final_review refs
grep -n "codex" docs/AutoAgent_하네스개요.md | grep -i "backend (일반)" || echo "matrix updated"
# Expected: matrix updated
```

(주의: `commands/aa.md`의 `08_codex_evaluation*`, `docs/specs/2026-07-09-*`의 이력성 언급은 08 고정/과거 기록이라 이 grep 대상에서 제외했다.)

- [ ] **Step 7: 커밋**

```bash
git add CLAUDE.md README.md docs/AutoAgent_공부가이드.md docs/AutoAgent_하네스개요.md docs/AutoAgent_하네스개요.html
git commit -m "docs: 역할 분업(codex 구현/claude 리뷰) + 07 구현자 반대편 반영

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 최종 검증 (전 태스크 완료 후, 컨트롤러 수행)

- [ ] **A. 회귀 감시선 — docs·review byte-equality**

```bash
cd /c/Users/systran/Desktop/AutoAgent
BASE="C:/Users/systran/AppData/Local/Temp/claude/C--Users-systran-Desktop-AutoAgent/43edc380-6605-4113-a31c-202eddc8fe13/scratchpad/baseline_dryrun"
AFTER="C:/Users/systran/AppData/Local/Temp/claude/C--Users-systran-Desktop-AutoAgent/43edc380-6605-4113-a31c-202eddc8fe13/scratchpad/after_dryrun"
mkdir -p "$AFTER"
for t in docs review; do for impl in claude codex; do
  python run.py --dry-run --workflow routed --task-type "$t" --request "sample $t request" --implementer "$impl"
done; done
ls -dt runs/*/ | head -4 | xargs -I{} cp -r {} "$AFTER/"
# *_command.json + *_prompt.md 만 비교(런 폴더명 타임스탬프 차이는 무시하도록 파일 내용만)
# 각 베이스라인 폴더의 command/prompt가 after의 대응 파일과 byte-identical인지 확인
python -c "
import pathlib, sys
base=pathlib.Path(r'$BASE'); after=pathlib.Path(r'$AFTER')
def sig(root):
    d={}
    for f in root.rglob('*'):
        if f.suffix in ('.json','.md') and ('command' in f.name or 'prompt' in f.name):
            d[f.name]=f.read_bytes()
    return d
b=sig(base); a=sig(after)
mism=[k for k in b if k not in a or a[k]!=b[k]]
print('docs/review 회귀:', 'IDENTICAL' if not mism else f'MISMATCH {mism}')
sys.exit(1 if mism else 0)
"
# Expected: docs/review 회귀: IDENTICAL
```

(파일명이 라우트에 따라 유일하므로 이름 기준 매칭으로 충분. MISMATCH가 나오면 docs/review 라우트가 의도치 않게 영향받은 것 → 회귀.)

- [ ] **B. 의도된 변경 육안 확인 — backend·frontend dry-run**

backend/frontend dry-run(Task 2 Step 6 / Task 1 Step 7 재사용)에서 `04_codex_*_impl`, `05_claude_*_review`, `07_claude_final_review`가 생성되고, `04_*_impl_prompt.md`에 `SELF_REVIEW` 문구가 있는지 Read로 확인.

- [ ] **C. 최종 whole-branch 리뷰**: `superpowers:requesting-code-review`의 code-reviewer로 `git merge-base main HEAD`..HEAD 전체 diff 리뷰(가장 강한 모델). Global Constraints(리뷰어=구현자 반대 불변식, 한국어 주석, byte-equality 회귀선) 기준.

- [ ] **D. 라이브 실증(백그라운드, 타깃=LanguageDetection)**: backend 요청 1건을 실제 routed로 실행해 `04 codex 구현+SELF_REVIEW → 05/07 claude 리뷰 → 08 codex 평가` 흐름을 산출물로 확인. **대상 워크스페이스 소스는 건드리지 않도록** 읽기 성격 요청 또는 일회성 격리로 수행하고 결과 파일만 검토. (자기수정 금지: 이 레포가 아니라 LD를 대상으로.)

---

## Self-Review (플랜 작성자 체크리스트)

1. **Spec 커버리지**: 변경 1(라우팅)=Task 2, 변경 2(07 flip)=Task 1, 변경 3(자체 리뷰)=Task 3, 파급(high-risk 티어/frontend 07/decompose 공유/문서)=Task 1·4 + 최종검증 D. 검증 전략(byte-equality 회귀선·라이브)=Pre-Flight + 최종검증 A·D. **갭 없음.**
2. **플레이스홀더**: 모든 코드/프롬프트/문서 편집은 실제 내용을 담았고, 검증은 정확한 명령 + 기대 출력을 명시. TBD/TODO 없음.
3. **타입 정합성**: `run_final_review`의 `name: str | None = None`(양 호출부 모두 name 미전달) — 내부에서 `07_{review_agent}_final_review` 생성. `choose_implementer(*, requested_implementer, task_type)` 2-인자 시그니처와 호출부(Task 2 Step 2) 일치. `route["review_agent"]` 키는 `route_task`가 항상 채움(routed_impl·task_exec 양쪽 route 모두 `route_task` 산출).

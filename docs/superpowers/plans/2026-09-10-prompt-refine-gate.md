# 프롬프트 정제 게이트(refine stage) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자 원문 요청을 하네스가 가벼운 Claude 콜 1회로 구조화하고, 승인 게이트(`--resume`=승인, 정제본 파일 편집 허용)를 거친 텍스트로 전 워크플로(simple/routed/decompose/research)를 돌린다.

**Architecture:** `cli.py`의 워크플로 분기 **전**에 신규 모듈 `autoagent/refine.py`가 정제 콜 → `00_refined_request.md` + `refine_status.json` 기록 → 정지(exit 0). `--resume`은 refine 게이트 체크를 기존 체크(research_state → checkpoint)보다 먼저 수행하고, 스냅샷된 args를 복원해 같은 run_dir에서 원래 워크플로를 시작한다. 스펙: `docs/superpowers/specs/2026-09-10-prompt-refine-gate-design.md`.

**Tech Stack:** Python 3(stdlib만), 기존 하네스 유틸(render_template/run_process/resolve_role), pytest.

## Global Constraints

- 모든 모듈 첫머리는 **한국어 docstring**, 함수엔 한국어 인라인 주석(기존 스타일 일치).
- `from __future__ import annotations`, PEP 604 타입(`str | None`), 파일은 LF(write_text가 보장).
- **stdout `print`에는 ASCII + 한글만** — em dash 등 비-cp949 문자는 Windows cp949에서 크래시(메모리: 비-cp949 문자 print 크래시). 파일 내용은 무관(UTF-8).
- 브랜치 `feature/prompt-refine-gate`에서 작업, main 직push 금지(브랜치 보호).
- 정제 콜은 `--max-agent-calls` 예산과 **별도 계정**(AgentCallBudget 미사용).
- dry-run은 CLI를 호출하지 않고 게이트도 세우지 않는다(아래 Task 3 참고).
- 커밋 메시지는 기존 관례(`feat(refine): ...`, `docs(refine): ...`) + Co-Authored-By 트레일러.

---

### Task 1: refine 역할 + 프롬프트 템플릿 + alias

**Files:**
- Modify: `roles.default.json`
- Modify: `autoagent/roles.py:53-54` (validate_roles의 required 집합)
- Modify: `autoagent/artifacts.py:19-57` (PROMPT_ALIASES)
- Create: `prompts/refine/claude_refine.md`
- Test: `tests/test_refine_gate.py`

**Interfaces:**
- Produces: 역할 id `"refine"`(agent=claude, tier=light, permission=plan) — Task 2의 `resolve_role(roles["refine"], ...)`가 소비. 프롬프트 alias `"claude_refine.md"` → `refine/claude_refine.md`, 플레이스홀더 `{{REQUEST}}`, `{{WORKFLOW}}`, `{{TASK_TYPE}}`, `{{WORKSPACE}}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_refine_gate.py` 생성:

```python
"""프롬프트 정제 게이트 단위테스트(역할/템플릿/게이트 파일/재개)."""
from __future__ import annotations

from pathlib import Path

from autoagent.artifacts import ROOT
from autoagent.roles import load_roles


def test_refine_role_registered():
    # roles.default.json에 refine 역할이 Claude light/plan/비변이로 등록되어야 한다.
    roles = load_roles(ROOT)
    entry = roles["refine"]
    assert entry["agent"] == "claude"
    assert entry["tier"] == "light"
    assert entry["mutating"] is False
    assert entry["permission"] == "plan"
    assert entry["high_risk_condition"] == "none"


def test_refine_prompt_template_renders():
    # alias가 걸려 있고 4개 플레이스홀더가 모두 치환되어야 한다.
    from autoagent.artifacts import render_template
    rendered = render_template(
        "claude_refine.md",
        {
            "REQUEST": "로그인 화면 버그 고쳐줘",
            "WORKFLOW": "routed",
            "TASK_TYPE": "auto",
            "WORKSPACE": "C:/ws",
        },
    )
    assert "로그인 화면 버그 고쳐줘" in rendered
    assert "routed" in rendered
    assert "{{" not in rendered  # 치환 누락 없음
```

- [ ] **Step 2: 실패 확인**

```bash
python -m pytest tests/test_refine_gate.py -q
```

Expected: FAIL — `KeyError: 'refine'` 및 템플릿 파일 없음(FileNotFoundError).

- [ ] **Step 3: roles.default.json에 refine 추가**

`report` 행 뒤(researcher 앞)에 삽입:

```json
    { "id": "refine",        "agent": "claude",  "tier": "light",                             "high_risk_condition": "none",                       "mutating": false, "permission": "plan" },
```

- [ ] **Step 4: validate_roles required 집합에 refine 추가**

`autoagent/roles.py`의 `validate_roles`에서:

```python
    required = {"context", "architect", "validation", "implementer", "reviewer",
                "fix", "final-review", "evaluation", "report", "refine"}
```

- [ ] **Step 5: PROMPT_ALIASES에 alias 추가**

`autoagent/artifacts.py`의 `PROMPT_ALIASES` dict 마지막에:

```python
    "claude_refine.md": "refine/claude_refine.md",
```

- [ ] **Step 6: 프롬프트 템플릿 작성**

`prompts/refine/claude_refine.md` 생성(내용 그대로):

```markdown
# 역할

당신은 AutoAgent 하네스의 프롬프트 정제자(Claude)입니다. 사용자 원문 요청을
파이프라인에 넣기 전에 구조화된 요청서로 다듬습니다.

# 작업공간

{{WORKSPACE}}

# 워크플로우 / 작업 유형

{{WORKFLOW}} / {{TASK_TYPE}}

# 사용자 원문 요청

{{REQUEST}}

# 작업

원문 요청을 아래 구조의 markdown 요청서로 재작성하세요. 당신의 출력이 그대로
다음 단계 에이전트들의 REQUEST가 됩니다.

섹션 구조(제목 고정, 해당 없으면 "없음"으로 채움):

## 목표
## 범위
## 제약
## 완료 조건
## 비목표
## 확인 필요

규칙:
- 출력은 정제된 요청서 markdown **그 자체만** — 머리말·해설·전체를 감싸는
  코드펜스를 붙이지 마세요.
- 원문의 의도를 바꾸지 마세요. 재구조화와 명확화만 하세요.
- 모호한 부분은 추측으로 메꾸지 말고 "## 확인 필요"에 질문으로 남기세요.
- 파일 탐색·설계·구현 계획을 하지 마세요(그건 다음 단계의 일입니다).
  작업공간 확인은 용어를 맞추는 수준까지만.
- high-risk 키워드 규칙: 영문 단어 `migration`, `auth`, `payment`,
  `production`, `backfill`, `rollback`은 이번 작업이 **실제로 그 작업을
  수행할 때만** 사용하세요. 배경 설명으로 스치는 언급은 한국어 등 다른
  표현으로 바꾸세요(예: 설명 목적이면 "auth 모듈" 대신 "로그인 모듈").
  하네스가 이 단어들의 포함 여부로 high-risk 게이트를 판정합니다.
- 파일을 수정하지 마세요. 비밀정보를 포함하지 마세요.
```

- [ ] **Step 7: 테스트 통과 확인**

```bash
python -m pytest tests/test_refine_gate.py -q
```

Expected: 2 passed. 회귀 확인:

```bash
python -m pytest tests/ -q
```

Expected: 전부 passed(기존 ~171 + 2).

- [ ] **Step 8: 커밋**

```bash
git add roles.default.json autoagent/roles.py autoagent/artifacts.py prompts/refine/claude_refine.md tests/test_refine_gate.py
git commit -m "feat(refine): refine 역할·프롬프트 템플릿·alias 등록"
```

---

### Task 2: autoagent/refine.py — 스테이지·게이트 파일·재개 헬퍼

**Files:**
- Create: `autoagent/refine.py`
- Test: `tests/test_refine_gate.py` (추가)

**Interfaces:**
- Consumes: Task 1의 역할 `refine`·alias `claude_refine.md`; 기존 `render_template`, `run_process`, `resolve_role`, `command_for_agent`.
- Produces (Task 3의 cli.py가 소비):
  - `run_refine_stage(args: Namespace, config: Config, request: str, run_dir: Path) -> str | None` — `None`=게이트 정지(호출부는 exit 0), `str`=그 텍스트로 계속(dry-run 통과).
  - `refine_gate_pending(run_dir: Path) -> bool`
  - `resume_from_refine_gate(args: Namespace, run_dir: Path) -> tuple[str, dict]` — (정제본 request, status dict). args를 스냅샷으로 in-place 복원하고 status를 approved로 갱신, `00_request.md` 기록. config 재로딩은 호출부 몫.
  - 상수: `REFINE_STATUS = "refine_status.json"`, `REFINED_REQUEST = "00_refined_request.md"`, `RAW_REQUEST = "00_request_raw.md"`, `STAGE_NAME = "00_refine"`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_refine_gate.py`에 추가:

```python
import json
from argparse import Namespace

from autoagent.artifacts import read_text


def _args(**overrides):
    # cli.build_parser의 기본값과 같은 모양의 Namespace(스냅샷 대상 필드 전부 포함).
    base = dict(
        workflow="routed", task_type="auto", implementer="auto", read_only=False,
        max_review_rounds=1, max_agent_calls=9, stop_after="none",
        skip_verification=False, require_human_approval=False, plan_only=False,
        skip_review=False, project="LangDet", config="C:/cfg.json", no_refine=False,
        dry_run=False, workspace=None, resume=None,
    )
    base.update(overrides)
    return Namespace(**base)


def test_write_refine_gate_and_pending(tmp_path):
    from autoagent.refine import write_refine_gate, refine_gate_pending, REFINED_REQUEST, REFINE_STATUS
    config = Namespace(workspace=tmp_path / "ws")
    write_refine_gate(tmp_path, args=_args(), config=config, refined="## 목표\n정제본")
    assert refine_gate_pending(tmp_path)
    status = json.loads(read_text(tmp_path / REFINE_STATUS))
    assert status["status"] == "waiting_for_prompt_approval"
    assert status["fallback"] is False
    assert status["args"]["workflow"] == "routed"
    assert status["args"]["max_agent_calls"] == 9
    assert "--resume" in status["resume_command"]
    assert read_text(tmp_path / REFINED_REQUEST) == "## 목표\n정제본"
    assert (tmp_path / "refine_required.md").exists()


def test_refine_gate_pending_false_cases(tmp_path):
    from autoagent.refine import refine_gate_pending, REFINE_STATUS
    assert not refine_gate_pending(tmp_path)  # 파일 없음
    (tmp_path / REFINE_STATUS).write_text('{"status": "approved"}', encoding="utf-8")
    assert not refine_gate_pending(tmp_path)  # 이미 승인됨


def test_write_refine_gate_fallback_records_reason(tmp_path):
    from autoagent.refine import write_refine_gate, REFINE_STATUS
    config = Namespace(workspace=tmp_path / "ws")
    write_refine_gate(tmp_path, args=_args(), config=config, refined="원문 그대로",
                      fallback_reason="empty refine output")
    status = json.loads(read_text(tmp_path / REFINE_STATUS))
    assert status["fallback"] is True
    assert status["fallback_reason"] == "empty refine output"


def test_resume_from_refine_gate_adopts_edited_file_and_restores_args(tmp_path):
    from autoagent.refine import write_refine_gate, resume_from_refine_gate, REFINED_REQUEST, REFINE_STATUS
    config = Namespace(workspace=tmp_path / "ws")
    original = _args(workflow="research", max_agent_calls=7)
    write_refine_gate(tmp_path, args=original, config=config, refined="정제본 v1")
    # 사용자가 정제본을 편집했다고 가정
    (tmp_path / REFINED_REQUEST).write_text("편집된 정제본", encoding="utf-8")
    # resume 시점의 args는 CLI 기본값(workflow=routed 등)
    resumed = _args()
    request, status = resume_from_refine_gate(resumed, tmp_path)
    assert request == "편집된 정제본"
    assert resumed.workflow == "research"       # 스냅샷 복원
    assert resumed.max_agent_calls == 7
    assert json.loads(read_text(tmp_path / REFINE_STATUS))["status"] == "approved"
    assert read_text(tmp_path / "00_request.md") == "편집된 정제본"


def test_resume_from_refine_gate_rejects_empty_refined(tmp_path):
    import pytest
    from autoagent.refine import write_refine_gate, resume_from_refine_gate, REFINED_REQUEST
    config = Namespace(workspace=tmp_path / "ws")
    write_refine_gate(tmp_path, args=_args(), config=config, refined="정제본")
    (tmp_path / REFINED_REQUEST).write_text("   \n", encoding="utf-8")
    with pytest.raises(SystemExit):
        resume_from_refine_gate(_args(), tmp_path)
```

- [ ] **Step 2: 실패 확인**

```bash
python -m pytest tests/test_refine_gate.py -q
```

Expected: FAIL — `ModuleNotFoundError: autoagent.refine` (신규 5개 테스트).

- [ ] **Step 3: autoagent/refine.py 구현**

```python
"""프롬프트 정제(refine) 스테이지.

사용자 원문 요청을 가벼운 정제 콜 1회(역할 refine: Claude light, solo면 스왑)로
구조화하고 승인 게이트에서 정지한다. --resume이 곧 승인이며,
00_refined_request.md를 편집한 뒤 재개하면 편집본이 채택된다.
cli.py의 워크플로 분기 전에 호출되어 전 워크플로 공통으로 적용된다.
설계: docs/superpowers/specs/2026-09-10-prompt-refine-gate-design.md
"""
from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any

from autoagent.artifacts import DEFAULT_CONFIG, read_text, render_template, write_json, write_text
from autoagent.config import Config
from autoagent.roles import load_roles, resolve_role
from autoagent.runner import require_command, run_process, write_command_artifact

REFINE_STATUS = "refine_status.json"
REFINED_REQUEST = "00_refined_request.md"
RAW_REQUEST = "00_request_raw.md"
STAGE_NAME = "00_refine"

# refine_status.json에 저장해 --resume 때 그대로 복원할 args 필드들.
# 게이트 정지 후 재개 명령엔 플래그가 없으므로 원 런의 인자를 여기 스냅샷한다.
SNAPSHOT_KEYS = [
    "workflow", "task_type", "implementer", "read_only", "max_review_rounds",
    "max_agent_calls", "stop_after", "skip_verification", "require_human_approval",
    "plan_only", "skip_review", "project", "config", "no_refine",
]


def args_snapshot(args: Namespace) -> dict[str, Any]:
    """재개 재디스패치에 필요한 args 값들을 dict로 뜬다."""
    return {key: getattr(args, key) for key in SNAPSHOT_KEYS}


def resume_command_for(run_dir: Path) -> str:
    """어느 cwd에서든 붙여넣어 실행 가능한 표준 재개 명령(routed_common과 동일 규약)."""
    run_py = DEFAULT_CONFIG.parent / "run.py"
    return f'python "{run_py}" --resume "{run_dir}"'


def write_refine_gate(
    run_dir: Path,
    *,
    args: Namespace,
    config: Any,
    refined: str,
    fallback_reason: str | None = None,
) -> None:
    """게이트 산출물(정제본/status/안내문)을 기록하고 stdout 핸드오프를 찍는다.

    fallback_reason이 있으면 정제 콜 실패로 refined에 원문이 담긴 경우다.
    config는 workspace 속성만 읽으므로 테스트에선 Namespace로 대체 가능하다.
    """
    resume_command = resume_command_for(run_dir)
    write_text(run_dir / REFINED_REQUEST, refined)
    status = {
        "status": "waiting_for_prompt_approval",
        "run_dir": str(run_dir),
        "resume_command": resume_command,
        "workspace": str(config.workspace),
        "fallback": bool(fallback_reason),
        "fallback_reason": fallback_reason,
        "args": args_snapshot(args),
    }
    write_json(run_dir / REFINE_STATUS, status)
    fallback_note = (
        f"\n> 정제 콜 실패로 **원문이 그대로** 담겨 있습니다(사유: {fallback_reason}).\n"
        if fallback_reason
        else ""
    )
    write_text(
        run_dir / "refine_required.md",
        "# Prompt Refine Gate\n\n"
        "정제된 요청이 준비되었습니다. 진행 전에 확인하세요.\n"
        f"{fallback_note}\n"
        f"- 원문: {RAW_REQUEST}\n"
        f"- 정제본: {REFINED_REQUEST} (필요하면 이 파일을 직접 수정하세요 - 수정본이 채택됩니다)\n\n"
        "승인(=진행)하려면:\n\n"
        f"```powershell\n{resume_command}\n```\n\n"
        "Running that resume command IS the act of approval.\n",
    )
    # 파싱 가능한 핸드오프(비-cp949 문자 금지: ASCII+한글만).
    print("AA_STATUS: waiting_for_prompt_approval")
    print(f"RUN_DIR: {run_dir}")
    print(f"RESUME_COMMAND: {resume_command}")
    print(f"Refine gate waiting for prompt approval: {run_dir}")


def run_refine_stage(args: Namespace, config: Config, request: str, run_dir: Path) -> str | None:
    """정제 스테이지 실행. None=게이트 정지(호출부 exit 0), str=그 텍스트로 계속.

    dry-run은 프롬프트/커맨드 아티팩트만 렌더하고 원문을 그대로 반환한다 -
    다운스트림 렌더가 실제 요청을 담아야 라우팅·프롬프트 검증이라는 dry-run의
    목적이 유지되기 때문(플레이스홀더면 auto 라우팅이 오판한다).
    정제 콜은 워크플로 진입 전이라 AgentCallBudget과 별도 계정이다.
    """
    # 지연 import: routed_impl -> routed_common -> runner 체인과의 순환 방지(관례).
    from autoagent.workflows.routed_impl import command_for_agent

    write_text(run_dir / RAW_REQUEST, request)
    prompt = render_template(
        "claude_refine.md",
        {
            "REQUEST": request,
            "WORKFLOW": args.workflow,
            "TASK_TYPE": args.task_type,
            "WORKSPACE": str(config.workspace),
        },
    )
    roles = load_roles(DEFAULT_CONFIG.parent)
    # route는 high_risk_condition="none"이라 판정에 안 쓰이지만 시그니처상 필요({} 전달).
    resolved = resolve_role(
        roles["refine"], config=config, route={}, request=request, agent="claude", read_only=True
    )

    if args.dry_run:
        write_text(run_dir / f"{STAGE_NAME}_prompt.md", prompt)
        write_command_artifact(run_dir, STAGE_NAME, command_for_agent(config, resolved))
        return request

    cli = require_command(config.claude_command if resolved.agent == "claude" else config.codex_command)
    try:
        refined = run_process(
            name=STAGE_NAME,
            command=command_for_agent(config, resolved, resolved_command=cli),
            prompt=prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        ).strip()
    except (SystemExit, subprocess.TimeoutExpired) as exc:
        # 콜 실패로 런을 죽이지 않는다 - 원문 폴백으로 게이트를 세워 사용자가
        # 원문 승인 또는 직접 편집으로 진행할 수 있게 한다.
        write_refine_gate(run_dir, args=args, config=config, refined=request, fallback_reason=str(exc))
        return None

    if not refined:
        write_refine_gate(run_dir, args=args, config=config, refined=request, fallback_reason="empty refine output")
        return None

    write_refine_gate(run_dir, args=args, config=config, refined=refined)
    return None


def refine_gate_pending(run_dir: Path) -> bool:
    """refine 게이트에서 정지한 run인지 판정한다(--resume 분기 최우선 체크)."""
    path = run_dir / REFINE_STATUS
    if not path.exists():
        return False
    return json.loads(read_text(path)).get("status") == "waiting_for_prompt_approval"


def resume_from_refine_gate(args: Namespace, run_dir: Path) -> tuple[str, dict[str, Any]]:
    """refine 게이트 재개: 정제본(편집 포함)을 읽고 approved 갱신, args 복원.

    반환 (request, status). --resume 실행 자체가 승인 행위다.
    project 반영 config 재로딩과 workspace 복원은 호출부(cli.main)가 담당한다.
    """
    status = json.loads(read_text(run_dir / REFINE_STATUS))
    refined_path = run_dir / REFINED_REQUEST
    if not refined_path.exists():
        raise SystemExit(f"No {REFINED_REQUEST} in {run_dir}; cannot resume the refine gate.")
    request = read_text(refined_path).strip()
    if not request:
        raise SystemExit(f"{REFINED_REQUEST} is empty in {run_dir}; edit it and resume again.")
    # 원 런의 인자를 복원한다(재개 명령엔 플래그가 없으므로 CLI 기본값을 덮는다).
    for key, value in (status.get("args") or {}).items():
        setattr(args, key, value)
    status["status"] = "approved"
    write_json(run_dir / REFINE_STATUS, status)
    # 다운스트림 전 단계가 참조하는 정본 요청 파일을 이 시점에 기록한다.
    write_text(run_dir / "00_request.md", request)
    return request, status
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
python -m pytest tests/test_refine_gate.py -q
```

Expected: 7 passed.

- [ ] **Step 5: 커밋**

```bash
git add autoagent/refine.py tests/test_refine_gate.py
git commit -m "feat(refine): 정제 스테이지·게이트 파일·재개 헬퍼 모듈"
```

---

### Task 3: cli.py 배선 — --no-refine, 스테이지 호출, 재개 분기

**Files:**
- Modify: `autoagent/cli.py` (parser, main의 resume 분기 및 fresh-run 경로, dispatch 헬퍼 추출)
- Modify: `docs/superpowers/specs/2026-09-10-prompt-refine-gate-design.md` (dry-run 절 정정)

**Interfaces:**
- Consumes: Task 2의 `run_refine_stage` / `refine_gate_pending` / `resume_from_refine_gate`.
- Produces: CLI 플래그 `--no-refine`(dest `no_refine`); `dispatch_workflow(args, config, request, run_dir) -> int` 헬퍼(신규·재개 공용); metadata에 `"no_refine"` 필드.

- [ ] **Step 1: parser에 --no-refine 추가**

`build_parser()`의 `--auto-approve-nonbranch` 인자 뒤에:

```python
    parser.add_argument(
        "--no-refine", action="store_true",
        help="Skip the prompt refine gate and run with the raw request",
    )
```

- [ ] **Step 2: 워크플로 분기를 dispatch_workflow 헬퍼로 추출**

`main()` 끝의 4-분기(routed/decompose/research/simple)를 모듈 레벨 함수로 옮긴다:

```python
def dispatch_workflow(args: argparse.Namespace, config, request: str, run_dir: Path) -> int:
    """요청 텍스트로 워크플로를 시작한다(신규 런과 refine 게이트 재개가 공용)."""
    if args.workflow == "routed":
        return run_routed_workflow(args, config, request, run_dir)
    if args.workflow == "decompose":
        return run_decompose_workflow(args, config, request, run_dir)
    if args.workflow == "research":
        return run_research_workflow(args, config, request, run_dir)
    return run_simple_workflow(args, config, request, run_dir)
```

`main()`의 기존 4-분기 자리는 `return dispatch_workflow(args, config, request, run_dir)`로 대체.

- [ ] **Step 3: --resume 분기 맨 앞에 refine 게이트 체크 추가**

`main()`의 `if args.resume:` 블록에서 `run_dir = Path(args.resume)` 직후,
기존 `config.mcp_config_path = write_claude_mcp_config(...)` **앞에** 삽입:

```python
        from autoagent.refine import refine_gate_pending, resume_from_refine_gate
        # refine 게이트 재개는 다른 재개 체크(research_state/checkpoint)보다 먼저 -
        # 이 시점엔 그 파일들이 아직 없다. --resume 실행 자체가 프롬프트 승인이다.
        if refine_gate_pending(run_dir):
            request, refine_status = resume_from_refine_gate(args, run_dir)
            # 원 런의 project/config로 재로딩(티어·solo 등 반영) 후 workspace 복원.
            config = load_config(Path(args.config), project=args.project)
            config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)
            if args.workspace:
                config.workspace = Path(args.workspace)
            else:
                config.workspace = Path(refine_status["workspace"])
            if not config.workspace.exists():
                raise SystemExit(f"Workspace does not exist: {config.workspace}")
            return dispatch_workflow(args, config, request, run_dir)
```

(참고: `resume_from_refine_gate`가 args.project/args.config를 스냅샷 값으로
복원한 뒤이므로 `load_config`가 원 런의 프로젝트 config를 읽는다.)

- [ ] **Step 4: fresh-run 경로에 refine 스테이지 삽입**

`main()`에서 기존 `write_text(run_dir / "00_request.md", request)` 라인을 **삭제**하고,
`write_metadata(...)` 호출의 dict에 `"no_refine": args.no_refine,` 필드를 추가
(`"dry_run"` 항목 옆). 그 다음 `write_metadata(...)` 호출 **뒤**를 이렇게 배선:

```python
    if not args.no_refine:
        from autoagent.refine import run_refine_stage
        refined = run_refine_stage(args, config, request, run_dir)
        if refined is None:
            # 게이트 정지 - 정제본 검토 후 --resume으로 승인·계속한다.
            return 0
        request = refined  # dry-run 통과 경로(원문 그대로)
    write_text(run_dir / "00_request.md", request)
    return dispatch_workflow(args, config, request, run_dir)
```

- [ ] **Step 5: 전체 테스트 회귀 확인**

```bash
python -m pytest tests/ -q
```

Expected: 전부 passed.

- [ ] **Step 6: dry-run으로 배선 검증(관례 검증법)**

```bash
python run.py --dry-run --workflow routed --task-type backend --workspace . --request "리프레시 토큰 저장 로직 정리"
```

Expected: exit 0, `RUN_DIR` 출력. 그 run_dir에 다음이 **모두** 존재:
`00_request_raw.md`, `00_refine_prompt.md`, `00_refine_command.json`,
`00_request.md`(원문과 동일), 이후 기존 dry-run 산출물(`01_*`…, `*_command.json`).
`refine_status.json`은 **없어야** 한다(dry-run은 게이트를 안 세움).

```bash
python run.py --dry-run --no-refine --workflow routed --task-type backend --workspace . --request "리프레시 토큰 저장 로직 정리"
```

Expected: refine 산출물(`00_request_raw.md`/`00_refine_*`) 없음, 나머지는 기존과 동일.

```bash
python run.py --dry-run --workflow research --request "AI 번역 시장 경쟁사 조사"
```

Expected: exit 0, refine 아티팩트 + research 전 스테이지 렌더(기존 dry-run 흐름 유지).

- [ ] **Step 7: 스펙 dry-run 절 정정**

`docs/superpowers/specs/2026-09-10-prompt-refine-gate-design.md`의 "## dry-run 동작" 본문을 아래로 교체(구현과 문서 일치):

```markdown
정제 프롬프트(`00_refine_prompt.md`)와 command 아티팩트(`00_refine_command.json`)를
렌더하고, 게이트 정지 **없이 원문을 그대로** 다음 스테이지에 넘긴다(플레이스홀더가
아니라 원문 통과 — auto 라우팅·high-risk 판정이 요청 텍스트 기반이라 플레이스홀더면
dry-run의 검증 목적이 깨진다). 기존 dry-run 관례(CLI 미호출, 예산 미차감, exit 0) 유지.
```

- [ ] **Step 8: 커밋**

```bash
git add autoagent/cli.py docs/superpowers/specs/2026-09-10-prompt-refine-gate-design.md
git commit -m "feat(refine): cli 배선 - --no-refine, 전 워크플로 정제 게이트, 재개 분기"
```

---

### Task 4: /aa·/aar 커맨드에 프롬프트 게이트 분기 추가

**Files:**
- Modify: `commands/aa.md` (섹션 3 분기)
- Modify: `commands/aar.md` (게이트 분기 섹션)

주의: `~/.claude/commands/aa.md`는 레포 파일로의 심링크이므로 **레포 파일만** 편집한다(메모리 관례).

**Interfaces:**
- Consumes: Task 3이 출력하는 `AA_STATUS: waiting_for_prompt_approval` / `RUN_DIR:` / `RESUME_COMMAND:` stdout 라인, `<RUN_DIR>/00_refined_request.md`, `00_request_raw.md`, `refine_status.json`.

- [ ] **Step 1: aa.md 섹션 3 맨 앞에 분기 추가**

`## 3. Branch on the outcome`의 기존 첫 불릿(**Gate**) 앞에 삽입:

```markdown
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
```

- [ ] **Step 2: aar.md 게이트 분기 섹션에 같은 분기 추가**

`commands/aar.md`의 게이트 분기(51행 부근 `- **Gate** — stdout contains
`RESEARCH_STATUS: ...`` 불릿) **앞에**, aa.md와 동일한 **Prompt gate** 불릿을
삽입한다(위 Step 1 블록을 그대로 사용하되, 재분기 문구는 "이 섹션의 분기를
처음부터 다시 적용한다(정제 승인 뒤 research 게이트가 이어질 수 있다)"로 맞춘다).

- [ ] **Step 3: 커밋**

```bash
git add commands/aa.md commands/aar.md
git commit -m "feat(refine): /aa·/aar에 프롬프트 정제 게이트 분기 추가"
```

---

### Task 5: 문서 반영 + 최종 검증

**Files:**
- Modify: `CLAUDE.md` (Workflows & layout 절)
- Test: 전체 pytest + dry-run 3종(최종 확인)

- [ ] **Step 1: CLAUDE.md에 refine 게이트 한 줄 추가**

`## Workflows & layout` 절의 첫 불릿(`--workflow simple|routed|...`) 바로 아래에 삽입:

```markdown
- **프롬프트 정제 게이트(전 워크플로 공통)**: 요청은 기본적으로 refine 콜(Claude light)로
  구조화된 뒤 `waiting_for_prompt_approval` 게이트에서 정지한다 — `00_refined_request.md`를
  확인·편집 후 `--resume`이 곧 승인. `--no-refine`으로 스킵. 정제 콜은 `--max-agent-calls`와
  별도 계정. 코어 `autoagent/refine.py`, 프롬프트 `prompts/refine/claude_refine.md`.
  **라이브 정제 콜은 미실증(사용자 인계)** — pytest+dry-run까지만 검증됨.
```

- [ ] **Step 2: 최종 검증(전체 스위트 + dry-run)**

```bash
python -m pytest tests/ -q
```

Expected: 전부 passed.

```bash
python run.py --dry-run --workflow routed --task-type backend --workspace . --request "리프레시 토큰 저장 로직 정리"
```

Expected: Task 3 Step 6과 동일(refine 아티팩트 + 전 스테이지 렌더, exit 0).

```bash
python run.py --dry-run --workflow decompose --workspace . --request "테스트 요청"
```

Expected: exit 0, refine 아티팩트 존재(decompose에도 공통 적용 확인).

- [ ] **Step 3: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs(refine): CLAUDE.md에 프롬프트 정제 게이트 항목 추가"
```

- [ ] **Step 4: 라이브 미실증 표기**

구현 완료 보고 시 반드시 포함: **라이브 정제 콜(실모델)과 게이트 재개의 실전
경로는 미실증** — pytest와 dry-run까지만 검증되었고 실전 검증은 사용자 인계
(메모리·CLAUDE.md 관례). "구현됨"을 "실전 검증됨"으로 단정하지 않는다.

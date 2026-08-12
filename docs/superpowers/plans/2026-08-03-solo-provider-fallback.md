# solo_provider 폴백 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한쪽 프로바이더 토큰이 없을 때 `config.solo_provider`(null/claude/codex) 1회 선언으로 살아 있는 한 프로바이더가 전 역할을 겸직하되 리뷰/검증은 적대적으로 수행한다.

**Architecture:** 주 chokepoint인 `resolve_role`에서 agent를 solo_provider로 덮어(정상=null이면 no-op) routed·research·decompose-실행 전 역할을 균일하게 접고, `resolve_role`를 우회하는 5곳(decompose 01/02·simple 01/02/03)은 정상 경로를 건드리지 않는 `solo_command` 스왑으로 커버하며, 중립적인 routed 리뷰 프롬프트엔 solo일 때만 적대 프리앰블을 prepend한다(research 검증 프롬프트는 이미 적대적).

**Tech Stack:** Python 3.x, dataclass Config, pytest(결정론), dry-run(배선).

## Global Constraints

- 모든 모듈/함수는 **한국어 docstring·주석**(식별자만 영문).
- `from __future__ import annotations`; PEP 604 타입.
- **정상(solo_provider=null) 경로는 바이트 동형** — 모든 오버라이드/스왑이 null이면 no-op. 폴백이 교차모델 동작을 흔들면 안 된다.
- solo_provider 유효값: `null` / `"claude"` / `"codex"`만. 그 외는 `load_config`에서 `SystemExit`.
- research 검증 프롬프트(crossmodel_verifier·b_market_verifier·d_grounding_verify)는 **이미 적대적 → 무변경**. 적대 프리앰블은 routed 리뷰 역할에만.
- routed/decompose 오케스트레이션은 유닛테스트가 없다 → 순수/헬퍼 함수는 pytest, 배선은 dry-run. 라이브 모델 런은 사용자 인계.
- 스펙: `docs/superpowers/specs/2026-08-03-solo-provider-fallback-design.md`.

---

### Task 1: `config.solo_provider` 필드 + 검증 + codex `light` tier 대칭 + 배너 + metadata

**Files:**
- Modify: `autoagent/config.py` (Config 필드, codex default_tiers에 light, load_config 파싱·검증·전달)
- Modify: `autoagent/cli.py` (시작 배너, metadata 기록)
- Test: `tests/test_solo_provider.py` (신규)

**Interfaces:**
- Produces: `Config.solo_provider: str | None`. `load_config`가 이를 채우고 잘못된 값은 `SystemExit`.
- Produces: `config.tiers["codex"]["light"]` 존재(대칭화).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_solo_provider.py`

```python
"""solo_provider 폴백 단위테스트."""
from __future__ import annotations

import json

import pytest

from autoagent.config import load_config


def _write_cfg(tmp_path, extra):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"workspace": str(tmp_path), **extra}), encoding="utf-8")
    return p


def test_solo_provider_default_none(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert cfg.solo_provider is None


def test_solo_provider_valid(tmp_path):
    assert load_config(_write_cfg(tmp_path, {"solo_provider": "claude"})).solo_provider == "claude"
    assert load_config(_write_cfg(tmp_path, {"solo_provider": "codex"})).solo_provider == "codex"


def test_solo_provider_invalid_rejected(tmp_path):
    with pytest.raises(SystemExit):
        load_config(_write_cfg(tmp_path, {"solo_provider": "gpt4"}))


def test_codex_light_tier_symmetric(tmp_path):
    # solo=codex에서 light tier 역할(context/report)이 KeyError 안 나도록 codex 팔레트에 light 존재.
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert "light" in cfg.tiers["codex"]
    assert cfg.tiers["codex"]["light"]["model"] == cfg.codex_model
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'solo_provider'` (및 codex light KeyError).

- [ ] **Step 3: Config 필드 추가** — `autoagent/config.py`, `research_max_capture_chars` 필드(약 line 90) 바로 뒤에 추가

```python
    research_max_capture_chars: int = 12000
    # solo 폴백: 설정되면 살아 있는 이 프로바이더가 전 역할을 겸직한다(null=교차모델 현행).
    solo_provider: str | None = None
```

- [ ] **Step 4: codex 팔레트에 light tier 추가(대칭화)** — `autoagent/config.py` default_tiers의 codex 블록(약 line 138-142)

```python
        "codex": {
            "standard": {"model": codex_model, "effort": codex_reasoning_effort},
            "deep": {"model": codex_model, "effort": codex_high_risk_effort},
            "light": {"model": codex_model, "effort": None},
            "cheap": {"model": "gpt-5.6-terra", "effort": "low"},
        },
```

- [ ] **Step 5: load_config에서 파싱·검증·전달** — `autoagent/config.py`, `tiers = _merge_tiers(...)`(약 line 144) 뒤, `return Config(` 앞에 검증을 넣고 생성자에 전달

```python
    tiers = _merge_tiers(default_tiers, raw.get("tiers") or {})

    # solo 폴백 값 검증: null/claude/codex만 허용.
    solo_provider = raw.get("solo_provider") or None
    if solo_provider is not None and solo_provider not in {"claude", "codex"}:
        raise SystemExit(
            f"solo_provider must be 'claude', 'codex', or null; got {solo_provider!r}"
        )

    return Config(
```

그리고 `Config(...)` 생성자 인자 목록 끝(약 line 177 `tiers=tiers,` 뒤)에 추가:

```python
        tiers=tiers,
        solo_provider=solo_provider,
    )
```

- [ ] **Step 6: cli.py 시작 배너 + metadata** — `autoagent/cli.py`

MCP 경고 루프(약 line 123-124) **바로 뒤**에 배너 추가:

```python
    for _mcp_warning in check_mcp_symmetry(config):
        print(f"[mcp] {_mcp_warning}")
    if config.solo_provider:
        print(
            f"[solo] SOLO MODE: {config.solo_provider} 단독 — 교차모델 대신 "
            "단일 프로바이더 적대검증(엄격도 감소)."
        )
```

`write_metadata(...)` dict(약 line 178 `"codex_high_risk_effort": ...` 뒤)에 추가:

```python
            "codex_high_risk_effort": config.codex_high_risk_effort,
            "solo_provider": config.solo_provider,
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: PASS (4 passed).
Run(회귀): `python -m pytest tests/ -q`
Expected: 기존 190 + 4 신규, 회귀 0.

- [ ] **Step 8: 커밋**

```bash
git add autoagent/config.py autoagent/cli.py tests/test_solo_provider.py
git commit -m "feat(solo): config.solo_provider 필드+검증+codex light 대칭+배너/metadata"
```

---

### Task 2: `resolve_role` solo 오버라이드 (주 chokepoint)

**Files:**
- Modify: `autoagent/roles.py` (resolve_role 맨 앞 오버라이드)
- Test: `tests/test_solo_provider.py` (append)

**Interfaces:**
- Consumes: Task 1의 `config.solo_provider`, `config.tiers["codex"]["light"]`.
- Produces: solo 설정 시 `resolve_role(...).agent == config.solo_provider`, tier는 solo 팔레트에서 조회. null이면 기존 agent 보존.

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_solo_provider.py` 하단에 append

```python
from autoagent.roles import load_roles, resolve_role
from autoagent.artifacts import DEFAULT_CONFIG

_ROUTE = {"task_type": "backend", "risk_level": "medium", "subtype": "general"}


def _roles():
    return load_roles(DEFAULT_CONFIG.parent)


def test_resolve_role_null_is_noop(tmp_path):
    # solo_provider=null이면 agent 인자 그대로(교차모델 동형).
    cfg = load_config(_write_cfg(tmp_path, {}))
    roles = _roles()
    r = resolve_role(roles["implementer"], config=cfg, route=_ROUTE, request="add api", agent="codex", read_only=False)
    assert r.agent == "codex"


def test_resolve_role_solo_claude_overrides(tmp_path):
    # solo=claude면 상류 agent가 codex여도 claude로 덮이고 tier는 claude 팔레트에서.
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    roles = _roles()
    r = resolve_role(roles["implementer"], config=cfg, route=_ROUTE, request="add api", agent="codex", read_only=False)
    assert r.agent == "claude"
    assert r.model == cfg.tiers["claude"]["standard"]["model"]


def test_resolve_role_solo_codex_light_role_no_keyerror(tmp_path):
    # solo=codex + light tier 역할(report)이 codex 팔레트 light를 조회해 KeyError 없이 resolve.
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "codex"}))
    roles = _roles()
    r = resolve_role(roles["report"], config=cfg, route=_ROUTE, request="write report", agent="claude", read_only=False)
    assert r.agent == "codex"
    assert r.model == cfg.tiers["codex"]["light"]["model"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: FAIL — solo 오버라이드가 없어 `test_resolve_role_solo_claude_overrides`가 `r.agent == "codex"`로 실패.

- [ ] **Step 3: resolve_role 오버라이드 구현** — `autoagent/roles.py`, resolve_role 본문 맨 앞(docstring 뒤, `mutating = bool(entry["mutating"])` 앞)

```python
    """..."""  # 기존 docstring 유지
    # solo 폴백: solo_provider가 설정되면 모든 역할을 그 프로바이더가 겸직한다.
    # 상류에서 배정된 agent(architect="claude"/evaluator="codex"/반대모델 등)를 여기서 덮는다.
    # 정상(null)이면 no-op이라 교차모델 경로는 바이트 동형.
    if getattr(config, "solo_provider", None):
        agent = config.solo_provider

    mutating = bool(entry["mutating"])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: PASS (7 passed).
Run(회귀): `python -m pytest tests/ -q`
Expected: 회귀 0.

- [ ] **Step 5: 커밋**

```bash
git add autoagent/roles.py tests/test_solo_provider.py
git commit -m "feat(solo): resolve_role solo 오버라이드(주 chokepoint) + 단위테스트"
```

---

### Task 3: `solo_command` 헬퍼 + 우회 5곳(decompose 01/02, simple 01/02/03)

**Files:**
- Modify: `autoagent/runner.py` (solo_command + solo_cli 헬퍼)
- Modify: `autoagent/workflows/decompose.py` (01 claude decomposition, 02 codex plan-review)
- Modify: `autoagent/workflows/simple.py` (01 plan, 02 execute, 03 review)
- Test: `tests/test_solo_provider.py` (append) + dry-run 스모크

**Interfaces:**
- Consumes: Task 1의 `config.solo_provider`, `runner.claude_command`/`codex_exec_command`/`require_command`.
- Produces:
  - `solo_command(config, *, intent: str, resolved_command: str) -> list[str]` — intent∈{plan,review,execute}.
  - `solo_cli(config) -> str` — solo 프로바이더의 CLI 명령 문자열.

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_solo_provider.py` 하단에 append

```python
from autoagent.runner import solo_command


def test_solo_command_claude_plan(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    cmd = solo_command(cfg, intent="plan", resolved_command="claude.cmd")
    assert cmd[0] == "claude.cmd"
    assert "--permission-mode" in cmd and "plan" in cmd


def test_solo_command_claude_execute_acceptedits(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    cmd = solo_command(cfg, intent="execute", resolved_command="claude.cmd")
    assert "acceptEdits" in cmd  # 기본 claude_impl_permission


def test_solo_command_codex_review_readonly(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "codex"}))
    cmd = solo_command(cfg, intent="review", resolved_command="codex.cmd")
    assert cmd[0] == "codex.cmd"
    assert "--sandbox" in cmd and "read-only" in cmd


def test_solo_command_codex_execute_uses_config_sandbox(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "codex", "codex_sandbox": "workspace-write"}))
    cmd = solo_command(cfg, intent="execute", resolved_command="codex.cmd")
    assert "workspace-write" in cmd
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: FAIL — `ImportError: cannot import name 'solo_command'`.

- [ ] **Step 3: solo_command + solo_cli 구현** — `autoagent/runner.py`, `codex_exec_command` 정의(약 line 139) 뒤

```python
def solo_cli(config: Config) -> str:
    """solo 프로바이더의 CLI 명령 문자열(claude_command/codex_command)."""
    return config.claude_command if config.solo_provider == "claude" else config.codex_command


def solo_command(config: Config, *, intent: str, resolved_command: str) -> list[str]:
    """solo 프로바이더로 intent(plan|review|execute)에 맞는 커맨드를 조립한다.

    plan/review=읽기전용, execute=변이. claude=permission_mode/skip_permissions,
    codex=sandbox로 매핑해 resolve_role의 posture와 정합. 우회 사이트(decompose/simple)
    전용 — resolve_role 경유 역할은 여기 오지 않는다.
    """
    if config.solo_provider == "claude":
        if intent == "execute":
            if config.claude_impl_permission == "bypassPermissions":
                return claude_command(
                    resolved_command, config.claude_model, None, skip_permissions=True,
                    allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path,
                )
            return claude_command(
                resolved_command, config.claude_model, "acceptEdits",
                allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path,
            )
        return claude_command(
            resolved_command, config.claude_model, "plan",
            allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path,
        )
    # codex: 읽기전용(plan/review) 또는 config 샌드박스(execute).
    sandbox = config.codex_sandbox if intent == "execute" else "read-only"
    return codex_exec_command(config, resolved_command, sandbox)
```

- [ ] **Step 4: decompose.py 01/02 우회 스왑** — `autoagent/workflows/decompose.py`

먼저 import에 solo_command·solo_cli 추가(약 line 16):

```python
from autoagent.runner import claude_command, codex_exec_command, require_command, run_process, solo_command, write_command_artifact, solo_cli
```

**01 claude decomposition**(약 line 27-47)을 아래로 교체 — 정상 경로 빌더는 그대로, solo 분기만 추가:

```python
    decomposition_prompt = render_template("claude_decompose.md", values)
    solo = config.solo_provider
    if args.dry_run:
        write_text(run_dir / "01_claude_decomposition_prompt.md", decomposition_prompt)
        cmd = (
            solo_command(config, intent="plan", resolved_command=solo_cli(config))
            if solo else
            claude_command(config.claude_command, config.claude_model, "plan", allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
        )
        write_command_artifact(run_dir, "01_claude_decomposition", cmd)
        decomposition = dry_run_task_graph(request)
        write_text(run_dir / "01_claude_decomposition.md", decomposition)
    else:
        cmd = (
            solo_command(config, intent="plan", resolved_command=require_command(solo_cli(config)))
            if solo else
            claude_command(require_command(config.claude_command), config.claude_model, "plan", allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
        )
        decomposition = run_process(
            name="01_claude_decomposition",
            command=cmd,
            prompt=decomposition_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "01_claude_decomposition.md", decomposition)
```

**02 codex plan-review**(약 line 59-78)을 아래로 교체:

```python
    if args.dry_run:
        write_text(run_dir / "02_codex_plan_review_prompt.md", review_prompt)
        cmd = (
            solo_command(config, intent="review", resolved_command=solo_cli(config))
            if solo else
            codex_exec_command(config, config.codex_command, "read-only")
        )
        write_command_artifact(run_dir, "02_codex_plan_review", cmd)
        plan_review = "PLAN_REVIEW_STATUS: approved\n\n[dry-run: Codex plan review output]"
        write_text(run_dir / "02_codex_plan_review.md", plan_review)
    else:
        cmd = (
            solo_command(config, intent="review", resolved_command=require_command(solo_cli(config)))
            if solo else
            codex_exec_command(config, require_command(config.codex_command), "read-only")
        )
        plan_review = run_process(
            name="02_codex_plan_review",
            command=cmd,
            prompt=review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
        write_text(run_dir / "02_codex_plan_review.md", plan_review)
```

- [ ] **Step 5: simple.py 01/02/03 우회 스왑** — `autoagent/workflows/simple.py`

import에 solo_command·solo_cli 추가(약 line 13-21의 from autoagent.runner import 블록):

```python
from autoagent.runner import (
    AgentCallBudget,
    AgentCallBudgetStopped,
    claude_command,
    codex_exec_command,
    require_command,
    run_process,
    solo_command,
    write_command_artifact,
    solo_cli,
)
```

dry-run 분기(약 line 32-36)를 교체 — 01 아티팩트에 solo 반영:

```python
    if args.dry_run:
        write_text(run_dir / "01_plan_prompt.md", plan_prompt)
        cmd01 = (
            solo_command(config, intent="plan", resolved_command=solo_cli(config))
            if config.solo_provider else
            claude_command(config.claude_command, config.claude_model, allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
        )
        write_command_artifact(run_dir, "01_claude_plan", cmd01)
        print(f"Dry run written to {run_dir}")
        return 0
```

라이브 경로에서 `claude = require_command(...)`/`codex = require_command(...)`(약 line 38-39)는 그대로 두되, 각 스텝의 command 인자를 solo 분기로 바꾼다.

**01 plan**(약 line 43-50)의 `command=`:

```python
        plan = run_process(
            name="01_claude_plan",
            command=(
                solo_command(config, intent="plan", resolved_command=require_command(solo_cli(config)))
                if config.solo_provider else
                claude_command(claude, config.claude_model, allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
            ),
            prompt=plan_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
```

**02 execute**(약 line 66-73)의 `command=`:

```python
        codex_result = run_process(
            name="02_codex_execute",
            command=(
                solo_command(config, intent="execute", resolved_command=require_command(solo_cli(config)))
                if config.solo_provider else
                codex_exec_command(config, codex, config.codex_sandbox)
            ),
            prompt=execute_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
```

**03 review**(약 line 90-97)의 `command=`:

```python
        review = run_process(
            name="03_claude_review",
            command=(
                solo_command(config, intent="review", resolved_command=require_command(solo_cli(config)))
                if config.solo_provider else
                claude_command(claude, config.claude_model, allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
            ),
            prompt=review_prompt,
            cwd=config.workspace,
            out_dir=run_dir,
            timeout_seconds=config.timeout_seconds,
        )
```

- [ ] **Step 6: 단위 테스트 통과 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: PASS (11 passed).

- [ ] **Step 7: dry-run 스모크(우회 사이트 solo 렌더)**

임시 solo config를 만들어 --config로 넘긴다(사용자의 gitignored config를 건드리지 않음):

```bash
python -c "import json,pathlib; pathlib.Path('_solo_tmp.json').write_text(json.dumps({'workspace':'.','solo_provider':'claude'}))"
python .\run.py --dry-run --workflow decompose --config _solo_tmp.json --request "split this into tasks"
python .\run.py --dry-run --workflow simple --config _solo_tmp.json --request "do a small change"
```

Expected: 둘 다 exit 0. 각 run_dir의 `02_codex_plan_review_command.json`(decompose)과 `02_codex_execute` 상당 아티팩트가 **claude 명령**(solo=claude)으로 렌더됨 — 즉 codex 스텝이 claude로 스왑. 확인 후 `_solo_tmp.json` 삭제:

```bash
rm _solo_tmp.json
```

- [ ] **Step 8: 커밋**

```bash
git add autoagent/runner.py autoagent/workflows/decompose.py autoagent/workflows/simple.py tests/test_solo_provider.py
git commit -m "feat(solo): solo_command 헬퍼 + 우회 5곳(decompose/simple) solo 스왑"
```

---

### Task 4: 적대 프리앰블 + routed 리뷰 역할 주입

**Files:**
- Create: `prompts/routed/_solo_adversarial_preamble.md`
- Modify: `autoagent/artifacts.py` (PROMPT_ALIASES 항목 추가)
- Modify: `autoagent/workflows/routed_impl.py` (maybe_prepend_adversarial + reviewer/final-review 주입)
- Test: `tests/test_solo_provider.py` (append) + dry-run 스모크

**Interfaces:**
- Consumes: Task 1의 `config.solo_provider`, `artifacts.render_template`.
- Produces: `maybe_prepend_adversarial(prompt: str, config, is_review: bool) -> str` — solo & is_review일 때만 프리앰블 prepend, 아니면 원본 반환.

- [ ] **Step 1: 적대 프리앰블 프롬프트 생성** — `prompts/routed/_solo_adversarial_preamble.md`

```markdown
## 적대적 리뷰 지침 (SOLO 모드)

당신은 지금 **자기 진영 모델이 만든 산출물을 검증**하고 있다. 교차모델 리뷰어가 없으므로,
같은 모델의 무른 통과(rubber-stamp)를 스스로 차단해야 한다. 아래를 강제한다:

1. **능동 공격**: 방어하지 말고 약점을 능동적으로 찾아라. 자기 진영 코드라도 봐주지 않는다.
2. **최소 3개 구체 지적**: 파일:라인과 함께 최소 3개의 구체적 결함/리스크를 반환하거나,
   결함이 없음을 증거로 증명하라(무결 자유선언 금지).
3. **major/critical 발견 시 approve 금지**: 하나라도 있으면 반드시 needs_changes로 판정한다.
4. **근거 한정**: 제공된 컨텍스트와 코드에만 근거하라. 모델 일반지식으로 없는 사실을 만들지 마라.
5. **충분성 우선**: "오버엔지니어링 회피"보다 검증 충분성을 우선한다.

---
```

- [ ] **Step 2: PROMPT_ALIASES 항목 추가** — `autoagent/artifacts.py`, PROMPT_ALIASES dict(약 line 19-)에 추가

```python
    "_solo_adversarial_preamble.md": "routed/_solo_adversarial_preamble.md",
```

- [ ] **Step 3: 실패하는 테스트 추가** — `tests/test_solo_provider.py` 하단에 append

```python
from autoagent.workflows.routed_impl import maybe_prepend_adversarial


def test_preamble_null_is_noop(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert maybe_prepend_adversarial("BODY", cfg, is_review=True) == "BODY"


def test_preamble_solo_non_review_is_noop(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    assert maybe_prepend_adversarial("BODY", cfg, is_review=False) == "BODY"


def test_preamble_solo_review_prepends(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"solo_provider": "claude"}))
    out = maybe_prepend_adversarial("BODY", cfg, is_review=True)
    assert out.endswith("BODY")
    assert "적대적 리뷰 지침" in out
    assert out != "BODY"
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: FAIL — `ImportError: cannot import name 'maybe_prepend_adversarial'`.

- [ ] **Step 5: maybe_prepend_adversarial 구현 + 주입** — `autoagent/workflows/routed_impl.py`

헬퍼 추가(파일 상단, run_impl_review_fix 앞 아무 곳). render_template는 이미 import되어 있다(routed_impl.py:13):

```python
def maybe_prepend_adversarial(prompt: str, config: Config, is_review: bool) -> str:
    """solo 모드의 리뷰 역할이면 적대 프리앰블을 프롬프트 앞에 붙인다(아니면 원본).

    교차모델 리뷰어 부재 시 같은 모델의 rubber-stamp를 막는다. research 검증 프롬프트는
    이미 적대적이라 여기 오지 않는다(routed/decompose 리뷰 역할 전용).
    """
    if not getattr(config, "solo_provider", None) or not is_review:
        return prompt
    preamble = render_template("_solo_adversarial_preamble.md", {})
    return preamble + "\n" + prompt
```

**reviewer(05) 주입** — `run_impl_review_fix`의 리뷰 스텝(routed_impl.py:70-85, `review = run_role_step(... role_id="reviewer" ...)`). `run_role_step`은 내부에서 프롬프트를 렌더하므로, 프리앰블은 **run_role_step 안**에서 role_id 기반으로 주입한다. `run_role_step`(routed_impl.py:268-314)의 프롬프트 렌더 직후에 추가:

routed_impl.py의 `run_role_step` 내부, `prompt = render_template(prompt_name, prompt_values)`(약 line 297) 바로 뒤에:

```python
    prompt = render_template(prompt_name, prompt_values)
    # solo 모드: 리뷰 역할이면 적대 프리앰블을 붙여 자기검증 rubber-stamp를 막는다.
    prompt = maybe_prepend_adversarial(prompt, config, is_review=(role_id == "reviewer"))
```

**final-review(07) 주입** — `run_final_review`(routed_impl.py:213-265)의 `final_review_prompt = render_template(...)`(약 line 246-249) 바로 뒤에:

```python
    final_review_prompt = render_template(
        prompt_name,
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review, "FIX_RESULT": fix},
    )
    # solo 모드: final-review도 적대 프리앰블 주입.
    final_review_prompt = maybe_prepend_adversarial(final_review_prompt, config, is_review=True)
```

(decompose 실행단계는 `run_impl_review_fix`→`run_role_step`(reviewer)+`run_final_review`를 재사용하므로 자동 커버. implementer/fix 역할과 research 경로엔 주입되지 않는다.)

- [ ] **Step 6: 단위 테스트 통과 확인**

Run: `python -m pytest tests/test_solo_provider.py -q`
Expected: PASS (14 passed).

- [ ] **Step 7: dry-run 스모크(리뷰 프롬프트에 프리앰블)**

```bash
python -c "import json,pathlib; pathlib.Path('_solo_tmp.json').write_text(json.dumps({'workspace':'.','solo_provider':'claude'}))"
python .\run.py --dry-run --workflow routed --task-type backend --config _solo_tmp.json --request "add an api endpoint" --max-review-rounds 1
rm _solo_tmp.json
```

Expected: exit 0. run_dir의 `05_*_review_r1_prompt.md`에 "적대적 리뷰 지침 (SOLO 모드)" 문구가 상단에 있고, `04_*_impl_prompt.md`(구현)에는 **없음**(리뷰 역할에만 주입).

- [ ] **Step 8: 커밋**

```bash
git add prompts/routed/_solo_adversarial_preamble.md autoagent/artifacts.py autoagent/workflows/routed_impl.py tests/test_solo_provider.py
git commit -m "feat(solo): 적대 프리앰블 + routed 리뷰/final-review 주입"
```

---

## Self-Review (플랜 작성자 수행 완료)

**1. Spec coverage:**
- §4.1 config 필드/검증/precedence/metadata → Task 1. §4.2 resolve_role 오버라이드 + codex light 대칭 → Task 1(대칭) + Task 2(오버라이드). §4.3 우회 5곳 solo 스왑 → Task 3. §4.4 적대 프리앰블(routed 리뷰) → Task 4. §4.5 시작 배너 + metadata → Task 1.
- 워크플로별 동작: routed(resolve_role+프리앰블=Task2,4), research(resolve_role만=Task2, 코드수정 0), decompose(resolve_role+우회=Task2,3), simple(우회=Task3) 모두 커버.
- 하위호환 체크리스트(null no-op): 각 Task의 null 테스트(resolve_role null, 프리앰블 null)와 dry-run으로 커버.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드. "적절히" 류 없음. 프리앰블의 "최소 3개"는 구체값.

**3. Type consistency:** `solo_provider: str | None` 일관. `solo_command(config, *, intent, resolved_command)`/`solo_cli(config)`/`maybe_prepend_adversarial(prompt, config, is_review)` 시그니처가 정의(Task3/4)와 호출·테스트에서 일치. `config.tiers["codex"]["light"]` 생산(Task1)·소비(Task2 테스트) 일치.

**주의(구현자 유의):** 정상(null) 경로는 반드시 기존 빌더/프롬프트 그대로여야 한다(바이트 동형). solo 분기만 추가한다. `solo_cli`는 runner.py의 공개 헬퍼로, decompose/simple에서 명시적으로 import한다.

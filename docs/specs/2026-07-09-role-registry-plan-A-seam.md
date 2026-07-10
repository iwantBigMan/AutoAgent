# 역할 레지스트리 Plan A — 레지스트리 seam (동작 보존) 구현 계획

> **에이전트 작업자용:** 필수 서브스킬 — superpowers:subagent-driven-development(권장) 또는 superpowers:executing-plans로 태스크 단위 실행. 스텝은 체크박스(`- [ ]`)로 추적.

**목표:** 흩어진 5개 모델/effort/샌드박스 리졸버를 데이터 기반 `roles.json` + `resolve_role()` 하나로 통합하되, **동작을 완전히 보존한다**(dry-run 산출물 바이트 동일).

**아키텍처:** 신설 `autoagent/roles.py`(ResolvedRole + 로더 + resolve_role + validate_roles)와 체크인된 `roles.default.json`(현행 역할 규칙을 그대로 인코딩)을 도입한다. `routed_impl.py`/`routed_preamble.py`/`routed_common.py`의 인라인 리졸버 호출을 `resolve_role`로 교체한다. 파이프라인 순서·게이트·루프·신규 역할은 건드리지 않는다(그건 Plan B).

**기술 스택:** Python 3, dataclass, JSON(신규 의존성 없음), Windows.

## Global Constraints

- 테스트 스위트 없음(CLAUDE.md). 검증은 **`--dry-run`의 `*_command.json` / `*_prompt.md` 바이트 동일성**.
- 이 계획은 **동작 보존 리팩터**: 모든 (task_type ∈ backend/frontend/docs) × (risk high/일반) × (read_only on/off) 조합에서 명령줄 결과가 변경 전과 **완전히 동일**해야 한다.
- high-risk 비대칭을 그대로 재현: architect는 `is_high_risk`(any), implementer/fix는 `mutating & task_type=='backend' & is_high_risk`.
- **보존 baseline은 권한 픽스(PR #5) 병합 후 상태**다: mutating Claude는 `config.claude_impl_permission` posture(기본 `acceptEdits`, opt-in `bypassPermissions`)를 따른다. resolve_role은 이 config-gating을 그대로 재현하고, command_for_agent의 내부 gating을 resolve_role로 이전한다.
- final-review가 `codex_sandbox_for`를 우회해 `--read-only`에서도 쓰기 가능한 현재 동작은 **Plan A에서 그대로 보존**(버그 수정은 Plan B).
- 캐노니컬 파일명(`01_`/`02_`/`03_`, `approval_required.md`, `checkpoint.json`) 보존.
- 모든 신규 코드 주석·docstring은 **한국어**(프로젝트 관례 + 사용자 지시).
- `roles.default.json` 체크인 + `roles.json`(gitignore) override, `autoagent.config.json`과 동일 우선순위.

## File Structure

- 생성 `autoagent/roles.py` — 역할 레지스트리의 단일 책임: 로드·해석·검증.
- 생성 `roles.default.json` — 현행 9개 역할 규칙을 데이터로 인코딩(동작 불변의 근거).
- 수정 `autoagent/workflows/routed_impl.py` — `command_for_agent`/`model_for_agent`/`effort_for_agent` 호출을 `resolve_role`로 교체.
- 수정 `autoagent/workflows/routed_preamble.py` — context/architect/validation 명령 조립을 `resolve_role` 경유로.
- 수정 `autoagent/workflows/routed_common.py` — `architecture_model_for`/`architecture_effort_for` 제거 후 `resolve_role`로 대체, final-review/evaluation/report도 경유.
- 수정 `autoagent/cli.py` — 시작 시 `validate_roles()` 호출.
- 수정 `.gitignore` — `roles.json` 추가.

---

### Task 1: 역할 데이터 모델 + 로더 + roles.default.json

**Files:**
- Create: `autoagent/roles.py`
- Create: `roles.default.json`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ResolvedRole` 데이터클래스; `load_roles(config_dir: Path) -> dict[str, dict]`(role_id→엔트리); 상수 `HIGH_RISK_CONDITIONS = {"none","any_high_risk","backend_high_risk_mutating"}`.

- [ ] **Step 1: `autoagent/roles.py` 생성 (모델 + 로더)**

```python
"""역할 레지스트리.

roles.default.json(+roles.json override)에서 역할 엔트리를 읽어들이고,
route/모델 정책을 적용해 실행 가능한 ResolvedRole로 해석한다(resolve_role, Task 2).
Plan A는 동작 보존이 목표라 default 엔트리는 현행 규칙을 그대로 인코딩한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ResolvedRole:
    """한 스텝 실행에 필요한 최종 실행 속성(command_for_agent가 소비)."""

    agent: str            # "claude" | "codex"
    model: str | None
    effort: str | None
    mutating: bool
    permission_mode: str | None  # claude 전용(plan/acceptEdits/None)
    skip_permissions: bool       # claude 전용(--dangerously-skip-permissions; bypass posture)
    sandbox: str | None          # codex 전용


def load_roles(config_dir: Path) -> dict[str, dict[str, Any]]:
    """roles.default.json을 읽고 roles.json(있으면)으로 얕게 override한다."""
    default_path = config_dir / "roles.default.json"
    base: dict[str, Any] = json.loads(default_path.read_text(encoding="utf-8-sig"))
    roles: dict[str, dict[str, Any]] = {r["id"]: r for r in base["roles"]}
    override_path = config_dir / "roles.json"
    if override_path.exists():
        extra = json.loads(override_path.read_text(encoding="utf-8-sig"))
        for r in extra.get("roles", []):
            roles[r["id"]] = {**roles.get(r["id"], {}), **r}
    return roles
```

- [ ] **Step 2: `roles.default.json` 생성 (현행 9개 역할 인코딩)**

```json
{
  "version": 1,
  "roles": [
    { "id": "context",       "agent": "claude",  "model_tier": "standard", "high_risk_condition": "none",                       "effort": "none",     "mutating": false, "permission": "plan" },
    { "id": "architect",     "agent": "claude",  "model_tier": "tiered",   "high_risk_condition": "any_high_risk",               "effort": "tiered",   "mutating": false, "permission": "plan" },
    { "id": "validation",    "agent": "codex",   "model_tier": "standard", "high_risk_condition": "none",                       "effort": "none",     "mutating": false, "sandbox": "from_read_only" },
    { "id": "implementer",   "agent": "route",   "model_tier": "tiered",   "high_risk_condition": "backend_high_risk_mutating", "effort": "tiered",   "mutating": true,  "permission": "write" },
    { "id": "reviewer",      "agent": "route",   "model_tier": "standard", "high_risk_condition": "none",                       "effort": "standard", "mutating": false, "permission": "plan" },
    { "id": "fix",           "agent": "route",   "model_tier": "tiered",   "high_risk_condition": "backend_high_risk_mutating", "effort": "tiered",   "mutating": true,  "permission": "write" },
    { "id": "final-review",  "agent": "codex",   "model_tier": "standard", "high_risk_condition": "none",                       "effort": "none",     "mutating": false, "sandbox": "configured" },
    { "id": "evaluation",    "agent": "codex",   "model_tier": "standard", "high_risk_condition": "none",                       "effort": "none",     "mutating": false, "sandbox": "from_read_only" },
    { "id": "report",        "agent": "claude",  "model_tier": "standard", "high_risk_condition": "none",                       "effort": "none",     "mutating": false, "permission": "plan" }
  ]
}
```

주의(현행 재현 근거):
- `context`/`report`: effort=none(현행 `claude_command`에 effort 미전달).
- `architect`: model·effort 모두 `any_high_risk`로 승격(`architecture_model_for`/`architecture_effort_for`).
- `implementer`/`fix`: `backend_high_risk_mutating`일 때만 승격(`model_for_agent`/`effort_for_agent`).
- `reviewer`: mutating=false라 effort=standard(=`claude_effort`), 승격 없음(현행 effort_for_agent의 else).
- `validation`/`evaluation`: `sandbox=from_read_only`(=`codex_sandbox_for`). `final-review`: `sandbox=configured`(=`config.codex_sandbox` 고정, read_only 무시 — 현행 버그 보존).
- `agent="route"`: 실제 claude/codex는 `route["implementation_agent"]`/`route["review_agent"]`가 정함(호출부에서 주입).

- [ ] **Step 3: `.gitignore`에 `roles.json` 추가**

```
roles.json
```

- [ ] **Step 4: 로드 스모크 체크**

Run: `python -c "from autoagent.roles import load_roles; from pathlib import Path; r=load_roles(Path('.')); print(sorted(r)); assert len(r)==9"`
Expected: 9개 역할 id 정렬 출력, assert 통과.

- [ ] **Step 5: 커밋**

```bash
git add autoagent/roles.py roles.default.json .gitignore
git commit -m "역할 레지스트리 데이터 모델 + 현행 규칙 인코딩"
```

---

### Task 2: resolve_role() — 현행 규칙 충실 재현

**Files:**
- Modify: `autoagent/roles.py`
- Create: `scripts/parity_check_roles.py` (일회성 대조 스크립트)

**Interfaces:**
- Consumes: `ResolvedRole`, `load_roles`(Task 1); `is_high_risk`(routed_common); `codex_sandbox_for`(safety); `Config`.
- Produces: `resolve_role(entry, *, config, route, request, agent, read_only) -> ResolvedRole`.

- [ ] **Step 1: `resolve_role` 구현 (roles.py에 추가)**

```python
from autoagent.config import Config
from autoagent.safety import codex_sandbox_for


def _is_high_risk(route, request) -> bool:
    # routed_common.is_high_risk와 동일 판정(순환 import 방지 위해 지연 import).
    from autoagent.workflows.routed_common import is_high_risk
    return is_high_risk(route, request)


def resolve_role(
    entry: dict[str, Any],
    *,
    config: Config,
    route: dict[str, Any],
    request: str,
    agent: str,
    read_only: bool,
) -> ResolvedRole:
    """레지스트리 엔트리를 route/모델 정책에 따라 실행 속성으로 해석한다.

    agent는 이미 결정된 구체 에이전트(claude/codex). entry["agent"]가 "route"면
    호출부가 route에서 뽑아 넘긴다. 동작은 현행 리졸버들과 바이트 단위로 일치해야 한다.
    """
    mutating = bool(entry["mutating"])

    # high-risk 조건 판정(역할별 비대칭 그대로).
    cond = entry["high_risk_condition"]
    if cond == "any_high_risk":
        escalate = _is_high_risk(route, request)
    elif cond == "backend_high_risk_mutating":
        escalate = mutating and route.get("task_type") == "backend" and _is_high_risk(route, request)
    else:
        escalate = False

    # 모델.
    if agent == "codex":
        model: str | None = config.codex_model
    elif agent == "claude":
        model = config.claude_high_risk_model if escalate else config.claude_model
    else:
        model = None

    # effort.
    effort_spec = entry["effort"]
    if agent != "claude" or effort_spec == "none":
        effort: str | None = None
    else:  # "standard" | "tiered"
        effort = config.claude_high_risk_effort if escalate else config.claude_effort

    # 권한/샌드박스 — 병합된 command_for_agent(config-gated posture)와 동일하게 재현.
    permission_mode = None
    skip_permissions = False
    sandbox = None
    if agent == "claude":
        if not mutating:
            permission_mode = "plan"
        elif config.claude_impl_permission == "bypassPermissions":
            skip_permissions = True          # --dangerously-skip-permissions (무샌드박스 opt-in)
        else:
            permission_mode = "acceptEdits"  # 기본: 편집만 자동, bash/네트워크 차단
    elif agent == "codex":
        sb = entry.get("sandbox", "configured")
        sandbox = codex_sandbox_for(read_only, config.codex_sandbox) if sb == "from_read_only" else config.codex_sandbox

    return ResolvedRole(agent=agent, model=model, effort=effort, mutating=mutating,
                        permission_mode=permission_mode, skip_permissions=skip_permissions, sandbox=sandbox)
```

- [ ] **Step 2: 대조 스크립트 작성 (`scripts/parity_check_roles.py`)**

```python
"""resolve_role 결과가 현행 인라인 리졸버와 동일한지 대조하는 일회성 검증."""
from pathlib import Path
from autoagent.config import load_config
from autoagent.roles import load_roles, resolve_role
from autoagent.workflows.routed_impl import model_for_agent, effort_for_agent

cfg = load_config(Path("autoagent.config.json"))
roles = load_roles(Path("."))
cases = []
for task_type in ("backend", "frontend"):
    for risk in ("high", "medium"):
        for agent in ("claude", "codex"):
            route = {"task_type": task_type, "subtype": "db" if risk == "high" else "api", "risk_level": risk}
            req = "migration auth" if risk == "high" else "add endpoint"
            # implementer(mutating=True)로 대조
            rr = resolve_role(roles["implementer"], config=cfg, route=route, request=req, agent=agent, read_only=False)
            assert rr.model == model_for_agent(cfg, agent, route, req, True), (task_type, risk, agent, "model")
            assert rr.effort == effort_for_agent(cfg, agent, route, req, True), (task_type, risk, agent, "effort")
            cases.append((task_type, risk, agent))
print(f"OK: {len(cases)} implementer cases match current resolvers")
```

- [ ] **Step 3: 대조 실행**

Run: `python scripts/parity_check_roles.py`
Expected: `OK: 8 implementer cases match current resolvers` (assert 실패 없음).

- [ ] **Step 4: 커밋**

```bash
git add autoagent/roles.py scripts/parity_check_roles.py
git commit -m "resolve_role: 현행 모델/effort/샌드박스 규칙 재현 + 대조 스크립트"
```

---

### Task 3: validate_roles() + cli 시작 훅

**Files:**
- Modify: `autoagent/roles.py`
- Modify: `autoagent/cli.py`

**Interfaces:**
- Produces: `validate_roles(roles, config_dir) -> None`(문제 시 SystemExit).

- [ ] **Step 1: `validate_roles` 구현 (roles.py)**

```python
def validate_roles(roles: dict[str, Any], config_dir: Path) -> None:
    """시작 시 레지스트리 정합성 검사. 문제가 있으면 즉시 종료한다."""
    required = {"context", "architect", "validation", "implementer", "reviewer",
                "fix", "final-review", "evaluation", "report"}
    missing = required - set(roles)
    if missing:
        raise SystemExit(f"roles.default.json에 필수 역할 누락: {sorted(missing)}")
    valid_cond = {"none", "any_high_risk", "backend_high_risk_mutating"}
    for rid, r in roles.items():
        if r.get("high_risk_condition") not in valid_cond:
            raise SystemExit(f"역할 {rid}: high_risk_condition 값 오류 {r.get('high_risk_condition')!r}")
        if r.get("agent") not in {"claude", "codex", "route"}:
            raise SystemExit(f"역할 {rid}: agent 값 오류 {r.get('agent')!r}")
```

- [ ] **Step 2: `cli.py`에서 config 로드 직후 호출**

`autoagent/cli.py`의 `main()`에서 `config = load_config(...)` 다음 줄에:

```python
from autoagent.roles import load_roles, validate_roles
validate_roles(load_roles(DEFAULT_CONFIG.parent), DEFAULT_CONFIG.parent)
```

- [ ] **Step 3: 검증 (정상 + 위반)**

Run: `python run.py --dry-run --workflow routed --task-type backend --request "add endpoint"`
Expected: 종료코드 0(검증 통과, 정상 진행).
Run(위반 재현): `roles.json`에 `{"roles":[{"id":"context","agent":"bogus"}]}` 임시 작성 후 위 명령 → `역할 context: agent 값 오류 'bogus'`로 종료. 확인 후 임시 파일 삭제.

- [ ] **Step 4: 커밋**

```bash
git add autoagent/roles.py autoagent/cli.py
git commit -m "validate_roles + 시작 시 레지스트리 정합성 검사"
```

---

### Task 4: routed_impl 통합 (구현/리뷰/수정 스텝)

**Files:**
- Modify: `autoagent/workflows/routed_impl.py`

**Interfaces:**
- Consumes: `resolve_role`, `load_roles`; `command_for_agent`(그대로 사용하되 ResolvedRole로 인자 공급).

- [ ] **Step 1: `run_role_step`이 resolve_role을 쓰도록 교체**

`run_role_step` 내부에서 `model_for_agent`/`effort_for_agent` 호출을 제거하고, 역할 id를 받아 다음처럼 해석:

```python
roles = load_roles(DEFAULT_CONFIG.parent)  # 모듈 상단 or 함수 진입 시
entry = roles[role_id]                      # role_id: "implementer"|"reviewer"|"fix"
resolved = resolve_role(entry, config=config, route=route, request=request,
                        agent=agent, read_only=args.read_only)
command = command_for_agent(config, resolved, resolved_command=command_name)
```

`command_for_agent`는 **ResolvedRole 하나를 받는 얇은 빌더**로 리팩터한다(내부 config-gating 제거 — resolve_role로 이전). claude는 `claude_command(cmd, resolved.model, resolved.permission_mode, resolved.effort, skip_permissions=resolved.skip_permissions)`, codex는 `codex_exec_command(config, cmd, resolved.sandbox, resolved.model)`로 조립. `run_implementation_route`가 `run_role_step`을 부를 때 `role_id`를 넘기도록 시그니처에 `role_id` 추가(impl→"implementer", review→"reviewer", fix→"fix").

- [ ] **Step 2: dry-run 바이트 동일성 (backend high-risk)**

Run:
```
git stash -- autoagent/  # 비교용 원본은 별도 클론/이전 커밋에서; 실제로는 아래 diff 방식 사용
```
실제 검증: 변경 전 커밋에서 `python run.py --dry-run --workflow routed --task-type backend --implementer claude --request "add auth token migration"` 산출 run 폴더의 `04_*_command.json`을 보관 → 변경 후 동일 명령 재실행 → 두 `04_*_command.json` **바이트 동일**.
Expected: `diff` 결과 없음.

- [ ] **Step 3: dry-run 바이트 동일성 (frontend, codex 구현자)**

Run: `python run.py --dry-run --workflow routed --task-type frontend --request "add settings toggle"`
Expected: 변경 전/후 `04_*_command.json`, `05_*_command.json` 바이트 동일.

- [ ] **Step 4: 커밋**

```bash
git add autoagent/workflows/routed_impl.py
git commit -m "routed_impl: 구현/리뷰/수정 스텝을 resolve_role로 통합"
```

---

### Task 5: preamble + common 통합 (context/architect/validation/final/eval/report)

**Files:**
- Modify: `autoagent/workflows/routed_preamble.py`
- Modify: `autoagent/workflows/routed_common.py`

**Interfaces:**
- Consumes: `resolve_role`, `load_roles`.

- [ ] **Step 1: preamble의 context/architect/validation 명령 조립을 resolve_role 경유로**

`run_preamble`에서 `claude_command(config.claude_command, config.claude_model, "plan")`(context)와 `architecture_model_for`/`architecture_effort_for`(architect), `codex_exec_command`(validation)을 각각 `resolve_role(roles["context"|"architect"|"validation"], agent=..., read_only=args.read_only)` 결과로 대체. context는 effort=None, architect는 tiered.

- [ ] **Step 2: common에서 architecture_model_for/architecture_effort_for 제거, final-review/evaluation/report 경유**

`routed_common.py`의 `architecture_model_for`/`architecture_effort_for` 삭제(호출부가 resolve_role 사용). `run_evaluation`(evaluation), `run_final_report`(report), 그리고 `routed_impl`의 07 final-review 명령을 `resolve_role(roles["evaluation"|"report"|"final-review"], ...)`로 조립. final-review는 `sandbox="configured"`라 read_only 무시(현행 보존).

- [ ] **Step 3: dry-run 바이트 동일성 (preamble + final 단계)**

Run: `python run.py --dry-run --workflow routed --task-type backend --request "add auth token migration"`
Expected: 변경 전/후 `01_*_command.json`(context), `02_*_command.json`(architect, opus+xhigh 유지), `03_*_command.json`(validation) 바이트 동일. 그리고 read-only 케이스: `--read-only`로 실행 시 validation/evaluation은 read-only, final-review는 workspace-write(현행 버그 보존) 유지 확인.

- [ ] **Step 4: 커밋**

```bash
git add autoagent/workflows/routed_preamble.py autoagent/workflows/routed_common.py
git commit -m "preamble/common: context/architect/validation/final/eval/report를 resolve_role로 통합"
```

---

### Task 6: codex effort 배선 (opt-in) + 전체 parity 스윕

**Files:**
- Modify: `autoagent/runner.py`
- Modify: `autoagent/roles.py`

**Interfaces:**
- Consumes: `resolve_role`가 codex 역할에 대해 `effort`를 낼 수 있게 확장.

- [ ] **Step 1: `codex_exec_command`에 effort 인자 추가(opt-in)**

`autoagent/runner.py`의 `codex_exec_command`에 `effort: str | None = None` 파라미터를 추가하고, 값이 있을 때만 `-c model_reasoning_effort=<effort>` 형태로 주입(정확한 플래그는 codex CLI 호환 확인). **기본 None이면 명령줄 불변.**

```python
def codex_exec_command(config, codex, sandbox, model=None, effort=None):
    command = [codex, "--ask-for-approval", config.codex_approval, "exec"]
    selected_model = model or config.codex_model
    if selected_model:
        command.extend(["-m", selected_model])
    if effort:
        command.extend(["-c", f"model_reasoning_effort={effort}"])
    command.extend(["-C", str(config.workspace), "--sandbox", sandbox, "--skip-git-repo-check", "-"])
    return command
```

- [ ] **Step 2: resolve_role이 codex effort를 처리하도록 확장**

`resolve_role`에서 `agent=="codex"`이고 엔트리 `effort`가 `"none"`이 아니면 `config.codex_reasoning_effort`를 반환(그 외 None). default 엔트리는 codex 역할 effort가 모두 `"none"`이라 **기본 동작 불변**.

- [ ] **Step 3: 전체 dry-run parity 스윕**

Run(각각 변경 전/후 산출물 비교):
```
python run.py --dry-run --workflow routed --task-type backend  --request "add auth token migration"
python run.py --dry-run --workflow routed --task-type frontend --request "add settings toggle"
python run.py --dry-run --workflow routed --task-type docs --read-only --request "review risks only"
python run.py --dry-run --workflow simple --request "review the project"
```
Expected: 네 실행 모두 변경 전/후 run 폴더의 모든 `*_command.json`이 **바이트 동일**(역할 override 없음 상태). simple 워크플로우는 레지스트리를 안 쓰므로 당연히 동일.

- [ ] **Step 4: 커밋**

```bash
git add autoagent/runner.py autoagent/roles.py
git commit -m "codex effort 배선(opt-in) + 전체 dry-run parity 확인"
```

---

## Self-Review

**1. 스펙 커버리지 (Plan A 범위):**
- roles.json + resolve_role 통합 → Task 1–2. ✓
- 시작 시 교차검증/정합성 검증 → Task 3(정합성). (교차모델 위반 거부는 신규 역할이 붙는 Plan B에서 강화 — Plan A엔 review agent=route라 현행과 동일.) ✓
- 5개 리졸버 통합 → Task 4–5. ✓
- high-risk 비대칭 보존 → Task 2(대조로 증명). ✓
- codex_reasoning_effort 배선(opt-in) → Task 6. ✓
- final-review read-only 버그 **보존**(수정은 Plan B) → Task 5 Step 2/3. ✓
- 신규 역할/스테이지/오라클 → **Plan B**(범위 밖, 의도적). ✓

**2. Placeholder 스캔:** "TBD/TODO" 없음. codex effort 플래그는 "codex CLI 호환 확인" 단서가 있으나 이는 실행 시 확인할 구체 행위(정확한 `-c` 키)이며 기본 None이라 미설정 시 무해 — 실행자가 dry-run으로 검증.

**3. 타입 일관성:** `ResolvedRole` 필드(agent/model/effort/mutating/permission_mode/sandbox)를 Task 4/5가 `command_for_agent(config, resolved.agent, resolved.model, effort=resolved.effort, mutating=resolved.mutating)`로 일관 소비. `resolve_role` 시그니처(entry, *, config, route, request, agent, read_only)를 Task 2에서 정의하고 Task 4–6에서 동일하게 호출.

## 후속 (Plan B)

review[]/finish[]/preplan/verify 스테이지 + 8개 신규 역할 + 조건 트리거 + 결정적 오라클 + final-review read-only 버그 수정.

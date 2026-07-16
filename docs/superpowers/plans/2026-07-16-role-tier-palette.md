# 역할별 model+effort 선언 티어 팔레트 (A) — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `roles.default.json`의 `model_tier`/`effort` 2단 추상을, config의 agent별 티어 팔레트(`tiers[agent][tier] → {model, effort}`) 참조로 교체한다. 동작은 현행과 바이트 동일하게 보존한다.

**Architecture:** config에 `tiers` 팔레트를 추가하되 기본값은 기존 전역값에서 합성해 동작을 보존한다(Task 1, 순수 가산). 그 뒤 `roles.default.json`·`resolve_role`·`validate_roles`를 팔레트 참조로 전환한다(Task 2). 검증의 핵심은 변경 전/후 dry-run 산출물의 **바이트 동일성**이다.

**Tech Stack:** Python 3.13, dataclasses, stdlib json. CLI 하네스(pytest 스위트 없음).

## Global Constraints

- **기준선(base)은 반드시 PR #14 병합본을 포함한다.** #14는 Codex effort 실주입을 도입한다:
  `codex_model` 기본 `gpt-5.6-sol`, `codex_reasoning_effort` 기본 `medium`, 신규
  `codex_high_risk_effort` 기본 `high`, `config._effort_default` 헬퍼,
  `codex_exec_command`의 `-c model_reasoning_effort="..."` 주입, `resolve_role`의 codex effort 분기.
  이 계획의 모든 "수정 전" 코드는 #14 반영 상태를 가리킨다. 미병합이면 먼저 rebase/merge 한다.
- **테스트 스위트 없음.** 검증은 (1) dry-run 산출물 바이트 동일성, (2) 인라인 python 단위
  스크립트로 한다. dry-run은 `--max-agent-calls`에 포함되지 않는다.
- 모든 모듈/함수는 **한국어 docstring·주석**(식별자만 영문).
- `from __future__ import annotations`; PEP 604 타입(`str | None`).
- **동작 보존**: 어떤 dry-run 산출물(`*_command.json`, `*_prompt.md`)도 변경 전과 동일해야 한다.
- Windows + Git Bash. `LF will be replaced by CRLF` 경고는 무해.
- **main push 금지** — feature 브랜치 + PR.

---

## Task 0: 브랜치 준비 + 바이트 동일성 베이스라인 캡처

**Files:** (코드 변경 없음)

- [ ] **Step 1: PR #14 포함 base에서 feature 브랜치 생성**

```bash
cd /c/Users/systran/Desktop/AutoAgent
# feature/codex-5.6-sol-effort(=PR #14) 또는 그것이 병합된 main에서 분기
git checkout feature/codex-5.6-sol-effort
git checkout -b feature/role-tier-palette
# 확인: codex effort 주입이 존재해야 함(#14 반영 증거)
grep -n "model_reasoning_effort" autoagent/runner.py
```
Expected: `runner.py`에 `model_reasoning_effort` 주입 라인이 보임.

- [ ] **Step 2: 변경 전 dry-run 산출물을 베이스라인으로 캡처**

```bash
cd /c/Users/systran/Desktop/AutoAgent
BASE="C:/Users/systran/AppData/Local/Temp/claude/role-tier-baseline"
rm -rf "$BASE"; mkdir -p "$BASE"
capture () {  # $1=케이스명, $2.. = run.py 인자
  local name="$1"; shift
  python ./run.py --dry-run "$@" >/dev/null 2>&1
  local rd=$(ls -td runs/*/ | head -1)
  mkdir -p "$BASE/$name"
  cp "$rd"*_command.json "$rd"*_prompt.md "$BASE/$name/" 2>/dev/null || true
}
capture backend_claude   --workflow routed --task-type backend  --implementer claude --request "리스트 유틸 함수 추가"
capture backend_codex    --workflow routed --task-type backend  --implementer codex  --request "리스트 유틸 함수 추가"
capture backenddb_claude --workflow routed --task-type backend  --implementer claude --request "DB migration으로 translation_pairs에 unique constraint 추가"
capture backenddb_codex  --workflow routed --task-type backend  --implementer codex  --request "DB migration으로 translation_pairs에 unique constraint 추가"
capture frontend_codex   --workflow routed --task-type frontend --implementer codex  --request "버튼 컴포넌트 추가"
capture docs_readonly    --workflow routed --task-type docs --read-only --request "구조와 위험만 리뷰"
echo "baseline files:"; find "$BASE" -type f | wc -l
```
Expected: 여러 `*_command.json`/`*_prompt.md`가 `$BASE/<케이스>/`에 복사됨(파일 수 > 0).
이 베이스라인은 Task 2 이후 비교 기준이다(Task 1은 가산 변경이라 산출물 불변).

---

## Task 1: config에 agent별 티어 팔레트 추가 (가산·동작 보존)

**Files:**
- Modify: `autoagent/config.py`

**Interfaces:**
- Produces: `Config.tiers: dict[str, dict[str, dict[str, Any]]]` — `tiers[agent][tier_name]`은
  `{"model": str, "effort": str | None}`. `config._merge_tiers(default, override) -> dict` 헬퍼.

- [ ] **Step 1: `Config` 데이터클래스에 `tiers` 필드 추가**

`autoagent/config.py`의 `Config`에서 `mcp_config_path` 아래에 추가:

```python
    # 역할↔모델 매핑 팔레트: tiers[agent][tier명] = {"model": str, "effort": str | None}.
    # load_config가 기존 전역값에서 기본 팔레트를 합성하고 config의 "tiers"로 덮는다.
    tiers: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
```

- [ ] **Step 2: `_merge_tiers` 헬퍼 추가**

`autoagent/config.py`의 `_effort_default` 아래(#14에서 추가된 헬퍼)에 추가:

```python
def _merge_tiers(
    default: dict[str, dict[str, dict[str, Any]]],
    override: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """기본 팔레트에 config override를 (agent, 티어) 단위로 필드 병합한다.

    override가 준 티어의 필드만 기본값 위에 덮는다(effort만 바꾸는 부분 override 허용).
    override에만 있는 agent/티어는 그대로 추가한다.
    """
    merged = {a: {t: dict(fields) for t, fields in tiers.items()} for a, tiers in default.items()}
    for agent, tiers in override.items():
        dst = merged.setdefault(agent, {})
        for tname, fields in tiers.items():
            base = dict(dst.get(tname, {}))
            base.update(fields or {})
            dst[tname] = base
    return merged
```

- [ ] **Step 3: `load_config`에서 모델/effort 기본값을 지역변수로 뽑고 팔레트 합성**

`load_config`의 `return Config(...)` 직전에, 현재 인라인으로 계산되던 값들을 지역변수로 올린 뒤
팔레트를 만든다. `return Config(...)`의 해당 인자들은 이 지역변수를 참조하도록 바꾼다:

```python
    # 모델/effort 기본값(팔레트 합성과 Config 양쪽에서 재사용).
    claude_model = raw.get("claude_model") or "sonnet"
    claude_high_risk_model = raw.get("claude_high_risk_model") or "opus"
    claude_effort = raw.get("claude_effort") or "high"
    claude_high_risk_effort = raw.get("claude_high_risk_effort") or "xhigh"
    codex_model = raw.get("codex_model") or "gpt-5.6-sol"
    codex_reasoning_effort = _effort_default(raw.get("codex_reasoning_effort"), "medium")
    codex_high_risk_effort = _effort_default(raw.get("codex_high_risk_effort"), "high")

    # 기본 팔레트 — 기존 전역값에서 합성해 동작을 보존한다. cheap 티어는 재튜닝/B 대비 "정의만".
    default_tiers = {
        "claude": {
            "standard": {"model": claude_model, "effort": claude_effort},
            "deep": {"model": claude_high_risk_model, "effort": claude_high_risk_effort},
            "light": {"model": claude_model, "effort": None},
            "cheap": {"model": "haiku", "effort": None},
        },
        "codex": {
            "standard": {"model": codex_model, "effort": codex_reasoning_effort},
            "deep": {"model": codex_model, "effort": codex_high_risk_effort},
            "cheap": {"model": "gpt-5.6-terra", "effort": "low"},
        },
    }
    tiers = _merge_tiers(default_tiers, raw.get("tiers") or {})
```

그리고 `return Config(...)`에서 다음 인자들을 지역변수 참조로 교체하고 `tiers=tiers`를 추가한다
(다른 인자는 그대로):

```python
        claude_model=claude_model,
        claude_high_risk_model=claude_high_risk_model,
        claude_effort=claude_effort,
        claude_high_risk_effort=claude_high_risk_effort,
        codex_model=codex_model,
        codex_reasoning_effort=codex_reasoning_effort,
        codex_high_risk_effort=codex_high_risk_effort,
        ...
        tiers=tiers,
```

- [ ] **Step 4: 단위 검증 스크립트 실행**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python - <<'PY'
from pathlib import Path
from autoagent.config import load_config, _merge_tiers

cfg = load_config(Path("autoagent.config.json"))
c, x = cfg.tiers["claude"], cfg.tiers["codex"]
assert c["standard"] == {"model": "sonnet", "effort": "high"}, c["standard"]
assert c["deep"] == {"model": "opus", "effort": "xhigh"}, c["deep"]
assert c["light"] == {"model": "sonnet", "effort": None}, c["light"]
assert c["cheap"]["model"] == "haiku"
assert x["standard"] == {"model": "gpt-5.6-sol", "effort": "medium"}, x["standard"]
assert x["deep"] == {"model": "gpt-5.6-sol", "effort": "high"}, x["deep"]

# 필드 단위 부분 override
m = _merge_tiers(
    {"claude": {"standard": {"model": "sonnet", "effort": "high"}}},
    {"claude": {"standard": {"effort": "medium"}}, "codex": {"luna": {"model": "gpt-5.6-luna", "effort": "low"}}},
)
assert m["claude"]["standard"] == {"model": "sonnet", "effort": "medium"}, m
assert m["codex"]["luna"]["model"] == "gpt-5.6-luna"

# back-compat: 전역값을 바꾼 config가 팔레트에 반영되는지(임시 파일로 로드)
import json, tempfile, os
raw = {"claude_model": "opus", "tiers": {"codex": {"standard": {"effort": "high"}}}}
fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd)
Path(p).write_text(json.dumps(raw), encoding="utf-8")
cfg2 = load_config(Path(p)); os.unlink(p)
assert cfg2.tiers["claude"]["standard"]["model"] == "opus", cfg2.tiers["claude"]["standard"]
assert cfg2.tiers["codex"]["standard"] == {"model": "gpt-5.6-sol", "effort": "high"}, cfg2.tiers["codex"]["standard"]
print("Task1 OK")
PY
```
Expected: `Task1 OK` (assert 통과).

- [ ] **Step 5: 산출물 불변 확인(선택, 빠른 스팟체크) + 커밋**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python ./run.py --dry-run --workflow routed --task-type backend --implementer codex --request "리스트 유틸 함수 추가" >/dev/null && echo "dry-run OK"
python -m compileall -q autoagent/config.py && echo "compile OK"
git add autoagent/config.py
git commit -m "feat: config에 agent별 티어 팔레트(tiers) 추가(가산·동작 보존)

기존 전역값에서 기본 팔레트를 합성하고 config의 tiers로 필드 단위 override.
아직 아무도 소비하지 않아 resolved 명령은 불변. cheap 티어는 정의만.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: `dry-run OK`, `compile OK`, 커밋 생성.

---

## Task 2: roles·resolve_role·validate_roles를 팔레트 참조로 전환 (동작 보존)

**Files:**
- Modify: `roles.default.json`
- Modify: `autoagent/roles.py` (`resolve_role`, `validate_roles`)
- Modify: `autoagent/cli.py` (`validate_roles` 호출부)
- Modify: `README.md` (모델 정책 절)

**Interfaces:**
- Consumes: `Config.tiers`(Task 1).
- Produces: `resolve_role(...)`는 시그니처 불변, model/effort를 `tiers[agent][tier]`에서 뽑음.
  `validate_roles(roles, config_dir, tiers)` — 티어 파라미터 추가.

- [ ] **Step 1: `roles.default.json`을 `tier`/`high_risk_tier`로 교체**

각 역할 엔트리에서 `model_tier`·`effort`를 제거하고 `tier`(+필요 시 `high_risk_tier`)를 넣는다.
전체 파일을 아래로 교체:

```json
{
  "version": 1,
  "roles": [
    { "id": "context",       "agent": "claude",  "tier": "light",                            "high_risk_condition": "none",                       "mutating": false, "permission": "plan" },
    { "id": "architect",     "agent": "claude",  "tier": "standard", "high_risk_tier": "deep", "high_risk_condition": "any_high_risk",               "mutating": false, "permission": "plan" },
    { "id": "validation",    "agent": "codex",   "tier": "standard",                          "high_risk_condition": "none",                       "mutating": false, "sandbox": "from_read_only" },
    { "id": "implementer",   "agent": "route",   "tier": "standard", "high_risk_tier": "deep", "high_risk_condition": "backend_high_risk_mutating", "mutating": true,  "permission": "write" },
    { "id": "reviewer",      "agent": "route",   "tier": "standard",                          "high_risk_condition": "none",                       "mutating": false, "permission": "plan" },
    { "id": "fix",           "agent": "route",   "tier": "standard", "high_risk_tier": "deep", "high_risk_condition": "backend_high_risk_mutating", "mutating": true,  "permission": "write" },
    { "id": "final-review",  "agent": "codex",   "tier": "standard",                          "high_risk_condition": "none",                       "mutating": false, "sandbox": "configured" },
    { "id": "evaluation",    "agent": "codex",   "tier": "standard",                          "high_risk_condition": "none",                       "mutating": false, "sandbox": "from_read_only" },
    { "id": "report",        "agent": "claude",  "tier": "light",                             "high_risk_condition": "none",                       "mutating": false, "permission": "plan" }
  ]
}
```

- [ ] **Step 2: `resolve_role`의 model+effort 블록을 티어 조회로 교체**

`autoagent/roles.py`의 `resolve_role`에서 현행(#14) 모델 블록과 effort 블록을 **모두** 아래로 교체한다.

수정 전(제거 대상):
```python
    # 모델.
    if agent == "codex":
        model: str | None = config.codex_model
    elif agent == "claude":
        model = config.claude_high_risk_model if escalate else config.claude_model
    else:
        model = None

    # effort.
    effort_spec = entry["effort"]
    if agent == "codex":
        effort: str | None = config.codex_high_risk_effort if escalate else config.codex_reasoning_effort
    elif agent == "claude" and effort_spec != "none":  # "standard" | "tiered"
        effort = config.claude_high_risk_effort if escalate else config.claude_effort
    else:
        effort = None
```

수정 후:
```python
    # 모델·effort — 팔레트 티어 조회로 결정한다. escalate면 high_risk_tier(있을 때), 아니면 tier.
    # agent는 이미 해석된 claude/codex라 route 역할도 agent별로 자동 해결된다.
    tier_name = entry["high_risk_tier"] if (escalate and entry.get("high_risk_tier")) else entry["tier"]
    tier = config.tiers[agent][tier_name]
    model: str | None = tier.get("model")
    effort: str | None = tier.get("effort")
```

- [ ] **Step 3: `validate_roles`에 티어 존재 검사 추가**

`autoagent/roles.py`의 `validate_roles` 전체를 아래로 교체한다(시그니처에 `tiers` 추가 +
기존 검사 유지 + 티어 존재 검사 추가):

```python
def validate_roles(
    roles: dict[str, Any],
    config_dir: Path,
    tiers: dict[str, dict[str, dict[str, Any]]],
) -> None:
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
        # 티어 참조 정합성: 역할이 참조하는 tier/high_risk_tier가 가능한 agent 팔레트에 있어야 한다.
        agents = ["claude", "codex"] if r.get("agent") == "route" else [r.get("agent")]
        names = [r.get("tier")] + ([r["high_risk_tier"]] if r.get("high_risk_tier") else [])
        for ag in agents:
            for tname in names:
                if tname not in tiers.get(ag, {}):
                    raise SystemExit(f"역할 {rid}: 티어 '{tname}'가 agent '{ag}' 팔레트에 없음")
        # high_risk_condition이 있는데 high_risk_tier가 없으면 경고(동작은 tier로 폴백).
        if r.get("high_risk_condition") not in (None, "none") and not r.get("high_risk_tier"):
            print(f"[roles] 경고: 역할 {rid}는 high_risk_condition이 있으나 high_risk_tier가 없어 tier로 폴백")
```

- [ ] **Step 4: `cli.py`의 `validate_roles` 호출에 팔레트 전달**

`autoagent/cli.py:104`을 교체(`config`는 이미 :102에서 로드됨):

수정 전:
```python
    validate_roles(load_roles(DEFAULT_CONFIG.parent), DEFAULT_CONFIG.parent)
```
수정 후:
```python
    validate_roles(load_roles(DEFAULT_CONFIG.parent), DEFAULT_CONFIG.parent, config.tiers)
```

- [ ] **Step 5: 단위 검증 — 보존 매핑표와 일치 + validate 실패 확인**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python - <<'PY'
from pathlib import Path
from autoagent.config import load_config
from autoagent.roles import load_roles, resolve_role, validate_roles

cfg = load_config(Path("autoagent.config.json"))
roles = load_roles(Path("."))

def eff_model(rid, agent, route, request):
    r = resolve_role(roles[rid], config=cfg, route=route, request=request, agent=agent, read_only=False)
    return (r.model, r.effort)

# 평상시(비 high-risk)
assert eff_model("context", "claude", {"task_type":"backend"}, "x") == ("sonnet", None)
assert eff_model("report", "claude", {"task_type":"backend"}, "x") == ("sonnet", None)
assert eff_model("architect", "claude", {"task_type":"backend"}, "일반") == ("sonnet", "high")
assert eff_model("validation", "codex", {"task_type":"backend"}, "x") == ("gpt-5.6-sol", "medium")
assert eff_model("reviewer", "claude", {"task_type":"backend"}, "x") == ("sonnet", "high")
assert eff_model("reviewer", "codex", {"task_type":"backend"}, "x") == ("gpt-5.6-sol", "medium")
assert eff_model("implementer", "codex", {"task_type":"backend"}, "일반") == ("gpt-5.6-sol", "medium")
# high-risk 승격(architect any_high_risk; implementer/fix backend_high_risk_mutating)
hr = {"task_type":"backend","risk_level":"high","subtype":"db"}
assert eff_model("architect", "claude", hr, "스키마 변경") == ("opus", "xhigh")
assert eff_model("implementer", "claude", hr, "스키마 변경") == ("opus", "xhigh")
assert eff_model("implementer", "codex", hr, "스키마 변경") == ("gpt-5.6-sol", "high")
assert eff_model("fix", "codex", hr, "스키마 변경") == ("gpt-5.6-sol", "high")
print("resolve_role 보존 매핑 OK")

# validate_roles: 없는 티어명 거부
bad = {"x": {"id":"x","agent":"claude","tier":"nope","high_risk_condition":"none","mutating":False,"permission":"plan"}}
try:
    validate_roles({**roles, **bad}, Path("."), cfg.tiers)
    raise AssertionError("없는 티어인데 통과함")
except SystemExit:
    print("validate_roles 티어 검사 OK")
PY
```
Expected: `resolve_role 보존 매핑 OK`, `validate_roles 티어 검사 OK`.

- [ ] **Step 6: README 모델 정책 절 갱신**

`README.md`의 "## 모델 정책" 절에서 역할별 배치가 이제 `roles.default.json`의 `tier`/`high_risk_tier`가
config `tiers` 팔레트를 참조해 결정됨을 설명하는 문단을 추가한다(기존 표는 유지하되 아래를 덧붙임):

```text
역할별 모델·effort는 roles.default.json의 `tier`/`high_risk_tier`가 config의
`tiers` 팔레트(agent→티어명→{model,effort})를 참조해 결정됩니다. 기본 팔레트는
기존 전역값(claude_model/effort, codex_model/effort 등)에서 합성되므로 config를
안 바꿔도 현행과 동일합니다. operator는 config `tiers`에 티어를 필드 단위로 override
하거나 새 티어를 추가할 수 있고, 역할의 tier 참조만 바꿔 재배치할 수 있습니다.
정의만 되어 있고 기본 매핑에서 미사용인 티어: claude `cheap`(haiku), codex `cheap`(gpt-5.6-terra).
```

- [ ] **Step 7: 바이트 동일성 매트릭스 검증(주 검증)**

```bash
cd /c/Users/systran/Desktop/AutoAgent
BASE="C:/Users/systran/AppData/Local/Temp/claude/role-tier-baseline"
AFTER="C:/Users/systran/AppData/Local/Temp/claude/role-tier-after"
rm -rf "$AFTER"; mkdir -p "$AFTER"
capture () { local name="$1"; shift; python ./run.py --dry-run "$@" >/dev/null 2>&1; local rd=$(ls -td runs/*/ | head -1); mkdir -p "$AFTER/$name"; cp "$rd"*_command.json "$rd"*_prompt.md "$AFTER/$name/" 2>/dev/null || true; }
capture backend_claude   --workflow routed --task-type backend  --implementer claude --request "리스트 유틸 함수 추가"
capture backend_codex    --workflow routed --task-type backend  --implementer codex  --request "리스트 유틸 함수 추가"
capture backenddb_claude --workflow routed --task-type backend  --implementer claude --request "DB migration으로 translation_pairs에 unique constraint 추가"
capture backenddb_codex  --workflow routed --task-type backend  --implementer codex  --request "DB migration으로 translation_pairs에 unique constraint 추가"
capture frontend_codex   --workflow routed --task-type frontend --implementer codex  --request "버튼 컴포넌트 추가"
capture docs_readonly    --workflow routed --task-type docs --read-only --request "구조와 위험만 리뷰"
diff -r "$BASE" "$AFTER" && echo "BYTE-IDENTICAL ✅" || echo "DIFF FOUND ❌ (조사 필요)"
python -m compileall -q autoagent/ && echo "compile OK"
```
Expected: `BYTE-IDENTICAL ✅`, `compile OK`. 차이가 나오면 §동작보존 매핑표와 실제 티어 값을 대조해 원인 교정(커밋 전).

- [ ] **Step 8: 커밋**

```bash
cd /c/Users/systran/Desktop/AutoAgent
git add roles.default.json autoagent/roles.py autoagent/cli.py README.md
git commit -m "feat: 역할 model/effort를 티어 팔레트 참조로 전환(동작 보존)

roles.default.json의 model_tier/effort를 tier/high_risk_tier로 교체하고
resolve_role이 config.tiers[agent][tier]에서 model+effort를 뽑도록 변경.
validate_roles가 티어명 존재를 검사. dry-run 산출물 바이트 동일성으로 검증.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: 커밋 생성.

---

## Task 3: PR 생성

- [ ] **Step 1: 푸시 + PR**

```bash
cd /c/Users/systran/Desktop/AutoAgent
git push -u origin feature/role-tier-palette
gh pr create --base main --title "feat: 역할별 model+effort 선언 티어 팔레트(A)" --body "설계: docs/superpowers/specs/2026-07-16-role-tier-palette-design.md
계획: docs/superpowers/plans/2026-07-16-role-tier-palette.md

무동작 리팩터. dry-run 산출물 바이트 동일성 매트릭스로 검증(backend 일반/DB·frontend·docs × claude/codex).
전제: PR #14(Codex effort 주입) 병합.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
Expected: PR URL 출력.

---

## 후속 (이 계획 밖)

- **A.1** — `report.tier`를 `light`→`cheap`(Haiku). 동작 변경이므로 실런 1회로 보고서 품질 확인.
- **B** — 난이도 기반 티어 자동선택(이 팔레트 소비).
- **C** — 구현 역할 세분화·신규 파이프라인 역할.

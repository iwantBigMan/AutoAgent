# 리서치 하네스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude/Codex 크로스모델 리서치 하네스를 AutoAgent에 신설한다 — 중첩 루프(바깥 심화 2회 × 안쪽 검증 3회)로 스테이지별 리서치를 적대적 검증하고, 코드가 verdict를 재계산하며, 검증 커버리지를 명시한 standalone HTML 리포트를 바탕화면에 전달한다.

**Architecture:** `--workflow research`가 `run_research_workflow`로 진입해 preamble에서 canonical seed를 확정·pin하고, 바깥 pass마다 스테이지 a(회사)·b(시장)·c(CSV정제)·d(팩트리포트)·derive(도출)를 안쪽 루프로 돌린다. 각 스테이지는 리서처(Claude/Codex) → 반대 모델 검증기(crossmodel/source_grounding) 또는 코드 검증기(data_quality)로 검증되고, **코드가 findings를 집계해 verdict status를 재계산**한다(모델 자유선언 불신). 순수 결정론 로직(타입·라우팅·어댑터·grounding·seed계약·수렴게이트·커버리지)은 pytest로, 모델 호출부(오케스트레이터·프롬프트·CLI)는 dry-run 렌더로 검증한다.

**Tech Stack:** Python 3.11+ (stdlib만 — `csv`/`hashlib`/`re`/`json`), pytest(신설 스위트), 기존 AutoAgent 하네스(`artifacts.render_template`·`runner.run_process`·`roles.resolve_role`·`routing`), Claude Code CLI(`claude.cmd`)·Codex CLI(`codex.cmd`) 서브프로세스.

## Global Constraints

- 무료 소스만 사용(유료 데이터·API 금지).
- 웹 fetch는 Claude(또는 하네스 코드)만 수행하고, Codex는 스냅샷만 읽는다(재fetch 금지).
- verdict status는 코드가 findings를 집계해 재계산한다(모델이 pass라 적어도 강등 가능).
- silent-pass 금지: 검증을 못 넘긴 채 소진되면 `exhausted_unverified`를 명시 반환·기록한다.
- 모든 모듈은 한국어 docstring + 한국어 인라인 주석(식별자만 영문).
- PEP 604 타입(`str | None`), `from __future__ import annotations`, config/state는 dataclass.
- CSV만 지원(XLSX 제외), stdlib `csv`만 사용(pandas/openpyxl 금지).
- 인코딩 폴백 고정: `utf-8 → utf-8-sig → cp949`, 전부 실패 시 조용한 skip 금지 — 정직한 ValueError.
- 입력 CSV는 바이트 sha256을 provenance로 기록.
- tolerance는 kind별 고정(합계·행수·카운트=정확일치 0.0, 비율·CAGR·평균=1% 0.01), config로만 조정.
- 산출물은 바탕화면 standalone HTML(인라인 CSS, 외부 리소스 0)로 전달(아티팩트 아님, PDF 없음).
- 프롬프트 placeholder는 전부 `{{KEY}}` 형식(render_template 실측), config JSON은 `utf-8-sig` 로딩.

---

## File Structure

**신설 — 순수 결정론 코어(`autoagent/research/`):**
- `autoagent/research/__init__.py` — 빈 패키지 마커.
- `autoagent/research/types.py` — 공유 dataclass(`StageId`/`Finding`/`Verdict`/`StageResult`). 모든 슬라이스가 import하는 고정 계약.
- `autoagent/research/adapters.py` — `verify(adapter, ...)` 디스패처 + `crossmodel` 어댑터(마커 파싱·status 재계산). `data_quality`/`source_grounding` 분기는 뒤 태스크가 추가.
- `autoagent/research/html_report.py` — markdown→인라인CSS standalone HTML 변환 + 바탕화면 저장.
- `autoagent/research/data_quality.py` — c 스테이지 코드 검증(행수보존·claim재계산·스키마·sanity) + `run_data_quality` 집계.
- `autoagent/research/snapshots.py` — 웹 fetch 원문을 `runs/sources/*.txt` 스냅샷 + 메타로 고정.
- `autoagent/research/grounding.py` — d 스테이지 결정론 grounding 검사(fabricated/dead/orphan/부분문자열).
- `autoagent/research/source_grounding.py` — d 스테이지 하이브리드 어댑터(GROUNDING_VERDICT 파싱 + 결정론 병합 + 강등).
- `autoagent/research/seed_contract.py` — canonical seed pin(read-only) + 계약 위반 검출.
- `autoagent/research/convergence.py` — pass간 claim delta·모순 검출 + 수렴/게이트 판정.
- `autoagent/research/state.py` — `research_state.json` 영속/재개(done 스킵·inner 이어감·seed pin).
- `autoagent/research/gates.py` — 분기점 전용 게이트 트리거 판정 + 정지 부수효과(`pause_at_gate`).
- `autoagent/research/coverage.py` — 커버리지 매트릭스 표 + 100%미만 경고배너 HTML 렌더.

**신설 — CSV 층(`autoagent/data/`):**
- `autoagent/data/__init__.py` — 빈 패키지 마커.
- `autoagent/data/csv_validator.py` — 인코딩 폴백 로더 + sha256 + `CSVQualityMetrics`/`validate_csv`.

**신설 — 오케스트레이터(`autoagent/workflows/`):**
- `autoagent/workflows/research.py` — `run_research_workflow`/`run_stage_loop`/`run_outer_loop`. 중첩 루프 엔진, 게이트·재개·커버리지 통합.

**신설 — 프롬프트(`prompts/research/`):** `seed_contract.md`, `a_researcher.md`, `crossmodel_verifier.md`, `b_market_researcher.md`, `b_market_verifier.md`, `c_codex_research.md`, `d_fact_report.md`, `d_grounding_verify.md`, `derive.md`, `final_html_report.md`.

**수정:**
- `autoagent/routing.py` — `choose_researcher(stage)` 신설(테이블 + 반대모델 verifier 계산).
- `roles.default.json` — `researcher`/`verifier` 역할 2종 추가.
- `autoagent/artifacts.py` — `PROMPT_ALIASES`에 리서치 프롬프트 별칭 등록.
- `autoagent/cli.py` — `--workflow research` choice/분기, `--auto-approve-nonbranch` 플래그, research 재개 경로.

**신설 — 테스트(`tests/`):** `tests/__init__.py`, `tests/research/__init__.py`, `tests/data/__init__.py`, `pytest.ini`, 그리고 각 코어 모듈에 대응하는 `test_*.py`.

**태스크→슬라이스 매핑(재번호):** Slice 1 = Task 1–7, Slice 2 = Task 8–14, Slice 3 = Task 15–18, Slice 4 = Task 19–23, Slice 5 = Task 24–29.

---

## Slice 1 — 엔진 + 최소경로 (a→crossmodel→derive→HTML)

최소경로 = preamble seed(Claude) → a 리서치(Claude) → crossmodel 검증(Codex) → derive(Claude) → crossmodel(Codex) → standalone HTML(바탕화면). 결정론 아키텍처 결정: `render_template`은 `{{KEY}}` 치환(실측 `artifacts.py:71`); config/roles JSON은 `utf-8-sig` 로딩; 리서치 스텝 실행은 `routed_impl.command_for_agent`를 지연 import로 재사용; crossmodel은 첫 줄 마커 `CROSSMODEL_VERDICT:` + fenced JSON을 파싱하고 코드가 status를 재계산한다.

---

### Task 1: 공유 타입 (`autoagent/research/types.py`)

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\__init__.py` (빈 패키지 마커)
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\types.py`
- Create: `C:\Users\systran\Desktop\AutoAgent\tests\__init__.py` (빈)
- Create: `C:\Users\systran\Desktop\AutoAgent\tests\research\__init__.py` (빈)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_types.py`

**Interfaces:**
- Consumes: 없음(순수 데이터 타입).
- Produces:
  - `StageId = Literal["a","b","c","d","derive"]`
  - `@dataclass Finding(severity: Literal["critical","major","minor"], category: str, detail: str, fix_directive: str, claim_id: str | None = None)`
  - `@dataclass Verdict(status: Literal["pass","needs_changes","blocked"], adapter: str, stage_id: str, findings: list[Finding], raw: dict)`
  - `@dataclass StageResult(stage_id: StageId, status: Literal["resolved","exhausted_unverified","blocked"], output_path: str, verdict: Verdict | None, inner_rounds: int)`

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_types.py`:
```python
"""research.types 공유 dataclass 스모크 테스트.

계약(고정 시그니처)이 필드·기본값·타입 그대로 존재하는지만 확인한다.
로직이 없는 순수 데이터 타입이므로 구조 검증에 한정한다.
"""
from __future__ import annotations

from dataclasses import fields

from autoagent.research.types import Finding, StageResult, Verdict


def test_finding_fields_and_default() -> None:
    f = Finding(severity="major", category="overreach", detail="추론이 사실을 넘음", fix_directive="근거 추가")
    assert f.claim_id is None
    assert f.severity == "major"
    names = [x.name for x in fields(Finding)]
    assert names == ["severity", "category", "detail", "fix_directive", "claim_id"]


def test_verdict_holds_findings_and_raw() -> None:
    f = Finding(severity="critical", category="unsupported", detail="d", fix_directive="fx")
    v = Verdict(status="needs_changes", adapter="crossmodel", stage_id="a", findings=[f], raw={"k": 1})
    assert v.findings[0] is f
    assert v.raw == {"k": 1}
    names = [x.name for x in fields(Verdict)]
    assert names == ["status", "adapter", "stage_id", "findings", "raw"]


def test_stage_result_fields() -> None:
    v = Verdict(status="pass", adapter="crossmodel", stage_id="a", findings=[], raw={})
    r = StageResult(stage_id="a", status="resolved", output_path="a/out.md", verdict=v, inner_rounds=2)
    assert r.verdict is v
    names = [x.name for x in fields(StageResult)]
    assert names == ["stage_id", "status", "output_path", "verdict", "inner_rounds"]
```

- [ ] **Step 2: 실패 확인** — Run: `cd C:\Users\systran\Desktop\AutoAgent; python -m pytest tests/research/test_types.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research'` (collection error, 3 errors).

- [ ] **Step 3: 최소 구현** — `autoagent/research/__init__.py`는 빈 파일. `autoagent/research/types.py`:
```python
"""리서치 워크플로 공유 타입.

모든 슬라이스가 이 이름·시그니처를 그대로 import한다(고정 인터페이스 계약).
로직 없는 순수 데이터 타입: StageId + Finding/Verdict/StageResult dataclass 3종.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


# 파이프라인 스테이지 식별자. derive는 도출 스테이지(최소경로 = a → derive).
StageId = Literal["a", "b", "c", "d", "derive"]


@dataclass
class Finding:
    """검증기(또는 코드)가 발견한 단일 약점. crossmodel/data_quality/source_grounding 공용."""

    severity: Literal["critical", "major", "minor"]
    category: str            # 예: unsupported/overreach/logic_gap/scope_miss 등(어댑터별 어휘)
    detail: str              # 사람이 읽는 약점 설명
    fix_directive: str       # 안쪽 루프 반송 시 리서처에게 줄 보정 지시
    claim_id: str | None = None  # 특정 claim에 걸린 finding이면 그 id, 축(axis) 단위면 None


@dataclass
class Verdict:
    """어댑터 검증 결과. status는 코드가 findings를 집계해 재계산한 최종 판정이다."""

    status: Literal["pass", "needs_changes", "blocked"]
    adapter: str             # "crossmodel" | "data_quality" | "source_grounding"
    stage_id: str            # 이 검증이 걸린 스테이지("a"/"b"/"derive" 등)
    findings: list[Finding]  # 집계 대상 약점 목록
    raw: dict[str, Any]      # 파싱한 원본 verdict JSON(감사추적·재개용)


@dataclass
class StageResult:
    """한 스테이지 안쪽 루프의 최종 결과(오케스트레이터가 스테이지 경계에서 소비)."""

    stage_id: StageId
    status: Literal["resolved", "exhausted_unverified", "blocked"]
    output_path: str                 # 스테이지 산출물 파일 경로(run_dir 기준 문자열)
    verdict: Verdict | None          # 마지막 검증 verdict(검증 없이 종료면 None)
    inner_rounds: int                # 실제로 돈 안쪽 라운드 수
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_types.py -q`
  Expected: `3 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/__init__.py autoagent/research/types.py tests/__init__.py tests/research/__init__.py tests/research/test_types.py
git commit -m "feat(research): 공유 타입 types.py(StageId/Finding/Verdict/StageResult)"
```

---

### Task 2: 리서치 라우팅 (`autoagent/routing.py` — `choose_researcher`)

`choose_implementer` 불변. 스테이지→리서처 테이블 + verifier(반대 모델) 기계 계산을 추가한다.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\routing.py` (파일 끝, `choose_implementer` 아래)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_routing_researcher.py`

**Interfaces:**
- Consumes: 없음(순수).
- Produces: `def choose_researcher(stage: str) -> tuple[str, str, str]` — `(researcher_agent, verifier_agent, reason)`; 테이블 `{a:claude, b:claude, c:codex, d:claude, derive:claude}`, verifier=반대 모델 기계 계산, 미지 스테이지는 `SystemExit`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_routing_researcher.py`:
```python
"""choose_researcher 라우팅 테이블·반대모델 계산 테스트.

리서처 배정(스펙 §3 테이블)과 verifier=반대모델 불변식을 고정한다.
choose_implementer는 건드리지 않음(쌍둥이 추가만).
"""
from __future__ import annotations

import pytest

from autoagent.routing import choose_researcher


@pytest.mark.parametrize(
    "stage,researcher,verifier",
    [
        ("a", "claude", "codex"),
        ("b", "claude", "codex"),
        ("c", "codex", "claude"),
        ("d", "claude", "codex"),
        ("derive", "claude", "codex"),
    ],
)
def test_researcher_table_and_opposite_verifier(stage, researcher, verifier) -> None:
    r, v, reason = choose_researcher(stage)
    assert (r, v) == (researcher, verifier)
    assert r != v
    assert stage in reason


def test_unknown_stage_rejected() -> None:
    with pytest.raises(SystemExit):
        choose_researcher("zz")
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_routing_researcher.py -q`
  Expected: `ImportError: cannot import name 'choose_researcher' from 'autoagent.routing'`.

- [ ] **Step 3: 최소 구현** — `autoagent/routing.py` 끝에 추가:
```python


# 스테이지별 리서처 배정(스펙 §3). 웹 리서치는 전부 Claude, CSV 정제(c)만 Codex.
# verifier는 항상 반대 모델을 코드가 기계 계산한다(구현자≠리뷰어 불변식과 동형).
RESEARCHER_BY_STAGE = {
    "a": "claude",
    "b": "claude",
    "c": "codex",
    "d": "claude",
    "derive": "claude",
}


def choose_researcher(stage: str) -> tuple[str, str, str]:
    """(리서처, 검증기, 사유)를 반환. 검증기는 항상 리서처와 반대 모델이다.

    choose_implementer와 동형 계약: 리서처를 테이블로 정하고 verifier는 반대 모델을
    코드가 기계 계산한다. 바깥 심화 2회 사이에도 이 쌍은 고정된다(계통 표류 차단).
    """
    researcher = RESEARCHER_BY_STAGE.get(stage)
    if researcher is None:
        raise SystemExit(f"Unknown research stage: {stage!r}")
    verifier = "codex" if researcher == "claude" else "claude"
    reason = f"Stage {stage} researcher={researcher}, verifier={verifier} (opposite model)."
    return researcher, verifier, reason
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_routing_researcher.py -q`
  Expected: `6 passed`. 회귀: `python run.py --dry-run --workflow routed --task-type backend --request "add health endpoint"` → exit 0.

- [ ] **Step 5: commit**
```bash
git add autoagent/routing.py tests/research/test_routing_researcher.py
git commit -m "feat(research): choose_researcher 라우팅(스테이지 테이블+반대모델 verifier)"
```

---

### Task 3: 역할 2종 + 리서치 프롬프트 5종 (`roles.default.json` + `prompts/research/*.md`)

`researcher`(tier standard)·`verifier`(mutating:false, permission plan) 역할을 추가하고, 최소경로가 렌더하는 프롬프트 5종을 신설한다. 프롬프트는 `{{KEY}}` placeholder를 쓴다.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\roles.default.json` (`roles` 배열에 2 엔트리)
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\seed_contract.md`
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\a_researcher.md`
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\crossmodel_verifier.md`
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\derive.md`
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\final_html_report.md`
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\artifacts.py` (`PROMPT_ALIASES`에 5 엔트리)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_roles_and_prompts.py`

**Interfaces:**
- Consumes: `load_roles(config_dir)`(utf-8-sig), `resolve_role(entry, ...)`, `validate_roles(roles, dir, tiers)`, `render_template(name, values)`.
- Produces (roles): `researcher`/`verifier` 엔트리 — `validate_roles`(agent∈{claude,codex,route}, high_risk_condition∈{none,any_high_risk,backend_high_risk_mutating}, tier가 팔레트에 존재) 통과. Produces (aliases): `seed_contract.md`→`research/seed_contract.md` 등 5종.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_roles_and_prompts.py`:
```python
"""researcher/verifier 역할 + 리서치 프롬프트 렌더 테스트.

역할이 validate_roles를 통과하고 resolve_role로 기대 posture(researcher=구현자류,
verifier=plan/mutating:false)로 풀리는지, 프롬프트 5종이 별칭으로 렌더되고 핵심
placeholder가 치환되는지 확인한다.
"""
from __future__ import annotations

from autoagent.artifacts import DEFAULT_CONFIG, render_template
from autoagent.config import load_config
from autoagent.roles import load_roles, resolve_role, validate_roles

CONFIG_DIR = DEFAULT_CONFIG.parent


def _config():
    return load_config(DEFAULT_CONFIG)


def test_roles_present_and_valid() -> None:
    roles = load_roles(CONFIG_DIR)
    assert "researcher" in roles and "verifier" in roles
    validate_roles(roles, CONFIG_DIR, _config().tiers)


def test_verifier_is_readonly_plan_posture() -> None:
    roles = load_roles(CONFIG_DIR)
    cfg = _config()
    route = {"task_type": "research", "risk_level": "medium", "subtype": "research"}
    resolved = resolve_role(roles["verifier"], config=cfg, route=route, request="x", agent="claude", read_only=False)
    assert resolved.mutating is False
    assert resolved.permission_mode == "plan"


def test_researcher_resolves_for_both_agents() -> None:
    roles = load_roles(CONFIG_DIR)
    cfg = _config()
    route = {"task_type": "research", "risk_level": "medium", "subtype": "research"}
    r_claude = resolve_role(roles["researcher"], config=cfg, route=route, request="x", agent="claude", read_only=False)
    r_codex = resolve_role(roles["researcher"], config=cfg, route=route, request="x", agent="codex", read_only=False)
    assert r_claude.agent == "claude" and r_claude.model is not None
    assert r_codex.agent == "codex" and r_codex.sandbox is not None


def test_prompts_render_with_placeholders() -> None:
    values = {
        "REQUEST": "삼성전자 회사 리서치",
        "WORKSPACE": "C:/tmp/ws",
        "SEED_CONTRACT": "회사=삼성전자; 통화=KRW",
        "STAGE_ID": "a",
        "OUTER_PASS": "1",
        "INNER_ROUND": "1",
        "PRIOR_FEEDBACK": "",
        "RESEARCHER_OUTPUT": "산출물 본문",
        "STAGE_A_OUTPUT": "a 산출물",
        "DERIVE_OUTPUT": "derive 산출물",
        "COVERAGE_MATRIX_MD": "| a | passed |",
        "REPORT_BODY_MD": "# 리포트",
    }
    for name in ["seed_contract.md", "a_researcher.md", "crossmodel_verifier.md", "derive.md", "final_html_report.md"]:
        text = render_template(name, values)
        assert "{{" not in text
        assert text.strip()
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_roles_and_prompts.py -q`
  Expected: `test_roles_present_and_valid`에서 `AssertionError`(researcher 없음) + `test_prompts_render_with_placeholders`에서 `FileNotFoundError`.

- [ ] **Step 3: 최소 구현 — roles** — `roles.default.json`의 `roles` 배열 끝(마지막 엔트리 뒤, `]` 앞)에 콤마+2줄 추가:
```json
    { "id": "researcher",    "agent": "route",   "tier": "standard", "high_risk_tier": "deep", "high_risk_condition": "any_high_risk",               "mutating": false, "permission": "plan" },
    { "id": "verifier",      "agent": "route",   "tier": "deep",                              "high_risk_condition": "none",                       "mutating": false, "permission": "plan" }
```
  근거: verifier는 crossmodel "backend high-risk 동급"이라 `tier:"deep"` 고정. researcher는 기본 standard, 고위험 스테이지만 deep 승격(`any_high_risk`). `mutating:false`라 resolve_role이 claude면 `permission_mode="plan"`, codex면 `sandbox=config.codex_sandbox`. `agent:"route"`라 호출부가 `choose_researcher`가 준 구체 에이전트를 넘긴다.

- [ ] **Step 4: 최소 구현 — PROMPT_ALIASES** — `artifacts.py`의 `PROMPT_ALIASES` dict 끝(마지막 엔트리 뒤)에 추가:
```python
    "seed_contract.md": "research/seed_contract.md",
    "a_researcher.md": "research/a_researcher.md",
    "crossmodel_verifier.md": "research/crossmodel_verifier.md",
    "derive.md": "research/derive.md",
    "final_html_report.md": "research/final_html_report.md",
```

- [ ] **Step 5: 최소 구현 — prompts** — 5개 파일 생성.

  `prompts/research/seed_contract.md`:
```markdown
# preamble: canonical seed 확정 (Claude)

당신은 리서치 파이프라인의 **불변식 seed**를 확정하는 계획자다. 아래 요청에서
바깥 루프 전체가 공유할 **canonical seed**를 뽑아 고정한다. 이후 pass는 이 seed를
바꿀 수 없고 심화만 허용된다(계통 표류 차단).

## 요청
{{REQUEST}}

## 작업공간
{{WORKSPACE}}

## 확정할 seed 필드(전부 채워라)
- **회사/대상 식별자**: 정확한 법인/제품/시장 대상명
- **시장 정의**: 분석 대상 시장의 범위·세그먼트 경계
- **기준통화**: 예 KRW/USD
- **기간**: 분석 대상 기간(예 2023–2025)
- **단위**: 매출·수량 등의 표기 단위

## 출력(엄격)
첫 줄에 마커, 이어서 fenced JSON 한 블록만 출력하라(자유서술 금지):

SEED_CONTRACT_JSON
```json
{"company": "...", "market": "...", "base_currency": "...", "period": "...", "unit": "..."}
```
```

  `prompts/research/a_researcher.md`:
```markdown
# 스테이지 a — 회사 리서치 (Claude, 웹 종합)

당신은 회사 리서치 담당이다. 아래 canonical seed를 **불변식**으로 삼아(바꾸지 말 것)
회사에 대한 사실·추론을 웹에서 종합한다. WebSearch/WebFetch로 근거를 모으고, 긴 페이지는
요지만 인용한다. **모델 지식으로 채운 주장은 금지** — 오직 fetch한 원문만 근거로 삼아라.

## canonical seed (불변식)
{{SEED_CONTRACT}}

## 원 요청
{{REQUEST}}

## 루프 컨텍스트
- outer_pass: {{OUTER_PASS}}
- inner_round: {{INNER_ROUND}}

## 직전 검증 피드백(있으면 이번 라운드에서 반드시 반영)
{{PRIOR_FEEDBACK}}

## 출력(엄격 — 코드가 파싱한다)
자유 서술 뒤에, 마지막에 마커 + fenced JSON 한 블록을 출력하라:

STAGE_OUTPUT_JSON
```json
{
  "stage_id": "a",
  "claims": [
    {"id": "a1", "text": "...", "kind": "fact|inference|recommendation", "source_refs": ["s1"], "confidence": 0.0}
  ],
  "narrative_md": "회사 리서치 요약(마크다운)",
  "evidence_bundle": {"sources": [
    {"ref_id": "s1", "url": "https://...", "fetched_text_excerpt": "원문에서 인용한 실제 텍스트", "fetch_ts": "2026-07-30T00:00:00Z"}
  ]}
}
```
```

  `prompts/research/crossmodel_verifier.md`:
```markdown
# 크로스모델 적대적 검증기 (반대 모델)

당신은 **깐깐한 반박 검증자**다. 방어하지 말고 **공격**하라. 아래 산출물과 원문
evidence_bundle을 대조해, 오직 첨부된 `fetched_text_excerpt`만을 근거로 판정한다.
**모델 지식으로 채운 주장은 unsupported로 간주**한다.

## 검증 축(최소 3개 약점 강제 — 없으면 소스 ref로 무결함을 증명)
1. **인용 소스가 실제로 그 주장을 지지하는가**(unsupported/hallucinated_source)
2. **추론이 사실을 넘어서는가**(overreach/logic_gap)
3. **누락된 축은 없는가**(scope_miss / stale / contradiction)

## 스테이지
{{STAGE_ID}}

## 검증 대상 산출물(원문 evidence 포함)
{{RESEARCHER_OUTPUT}}

## 출력(엄격 — 코드가 마커+JSON만 파싱, 나머지는 무시)
첫 줄에 마커, 이어서 fenced JSON 한 블록:

CROSSMODEL_VERDICT: pass|needs_changes|blocked
```json
{
  "schema_version": 1, "adapter": "crossmodel", "stage_id": "{{STAGE_ID}}",
  "verdict": "pass|needs_changes|blocked",
  "findings": [
    {"claim_id": "a1", "severity": "critical|major|minor", "category": "unsupported|overreach|logic_gap|scope_miss|stale|contradiction|hallucinated_source", "quote": "...", "rebuttal": "...", "fix_directive": "...", "evidence_pointer": "s1"}
  ],
  "coverage": {"axes_checked": ["support", "overreach", "omission"], "axes_missing": []},
  "unchallenged_but_weak": [], "reviewer_model": "codex", "tokens_seen": 0
}
```

참고: 코드가 severity를 집계해 최종 status를 **재계산**한다. 당신이 "pass"라 적어도
major/critical finding이 하나라도 있으면 needs_changes로 강등된다(자기모순 방지).
```

  `prompts/research/derive.md`:
```markdown
# 스테이지 derive — 도출 (Claude, 종합·논리)

당신은 앞선 검증된 스테이지 산출물에서 **도출**을 만든다. canonical seed를 벗어나지 말고,
검증된 claim만 토대로 결론·시사점을 합성한다. 과대추론(상관→인과, 추정→확정)을 스스로 배제하라.

## canonical seed (불변식)
{{SEED_CONTRACT}}

## 스테이지 a 산출물(검증 통과분)
{{STAGE_A_OUTPUT}}

## 직전 검증 피드백(있으면 반영)
{{PRIOR_FEEDBACK}}

## 출력(엄격)
자유 서술 뒤에 마커 + fenced JSON 한 블록:

STAGE_OUTPUT_JSON
```json
{
  "stage_id": "derive",
  "claims": [
    {"id": "d1", "text": "도출 결론", "kind": "inference|recommendation", "source_refs": ["a1"], "confidence": 0.0}
  ],
  "narrative_md": "도출 서술(마크다운)",
  "evidence_bundle": {"sources": []}
}
```
```

  `prompts/research/final_html_report.md`:
```markdown
# 리서치 리포트 (내부 검토용)

아래는 이 리서치 run의 최종 마크다운 리포트 본문이다. 코드가 이 마크다운을
standalone HTML로 변환해 바탕화면에 저장한다.

## 커버리지 매트릭스(상단 강제)
{{COVERAGE_MATRIX_MD}}

## 요청
{{REQUEST}}

## canonical seed
{{SEED_CONTRACT}}

## 회사 리서치(a)
{{STAGE_A_OUTPUT}}

## 도출(derive)
{{DERIVE_OUTPUT}}
```
  주: 이 슬라이스에서 `final_html_report.md`는 모델을 호출하지 않고 오케스트레이터가 직접 값으로 채워 markdown 본문을 만든 뒤 Task 5의 `render_report_html`에 넘긴다.

- [ ] **Step 6: 통과 확인** — Run: `python -m pytest tests/research/test_roles_and_prompts.py -q`
  Expected: `4 passed`. 회귀: `python run.py --dry-run --workflow routed --task-type backend --request "add endpoint"` → exit 0(startup `validate_roles`가 신설 역할 검증).

- [ ] **Step 7: commit**
```bash
git add roles.default.json prompts/research/ autoagent/artifacts.py tests/research/test_roles_and_prompts.py
git commit -m "feat(research): researcher/verifier 역할 2종 + 리서치 프롬프트 5종(별칭 등록)"
```

---

### Task 4: crossmodel 어댑터 + verify 디스패치 (`autoagent/research/adapters.py`)

verify 디스패치와 crossmodel 어댑터를 신설한다. crossmodel은 (1)마커 `CROSSMODEL_VERDICT:` 존재 확인 → (2)fenced JSON 파싱(`extract_json_block` 재사용) → (3)findings로 status **재계산**(critical/major 있으면 needs_changes로 강등) → (4)`axes_missing` 비어있어야 pass.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\adapters.py`
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\config.py` (`Config`에 `crossmodel_min_findings` 필드 추가)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_crossmodel_adapter.py`

**Interfaces:**
- Consumes: `artifacts.extract_json_block(text) -> dict`, `artifacts.write_json`, `research.types.{Finding,Verdict}`, `config.Config`.
- Produces:
  - `def parse_crossmodel_verdict(raw_text: str, stage_id: str, *, config=None) -> Verdict` — 텍스트→Verdict(코드 재계산). 순수·테스트 대상. config는 §4.1② 최소 findings 쿼터에만 쓰이며 default None이라 계약 확장(기존 호출 불변).
  - `def verify(adapter: str, stage_out: dict, run_dir: Path, *, verifier_agent: str, config) -> Verdict` — adapter∈{crossmodel,data_quality,source_grounding}; 이 태스크는 crossmodel만, 나머지는 `SystemExit`(다음 슬라이스). `stage_out["verifier_raw_text"]`(검증기 stdout)를 파싱한다. crossmodel 경로는 `config`를 `parse_crossmodel_verdict`로 전달(§4.1② tokens_seen 교차검사·쿼터).

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_crossmodel_adapter.py`:
```python
"""crossmodel verdict 파싱·재계산 테스트(결정론 코드 핵심).

핵심 불변식: 검증기가 'pass'라 적어도 major/critical finding이 있으면 코드가
needs_changes로 강등한다. axes_missing 비어있음 + critical/major 0건 + blocked 아님 → pass.
마커/JSON 없음 → blocked(판정 불가).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autoagent.research.adapters import parse_crossmodel_verdict, verify


def _verdict_text(status: str, findings_json: str, axes_missing: str = "[]") -> str:
    return (
        f"CROSSMODEL_VERDICT: {status}\n"
        "```json\n{\n"
        '  "schema_version": 1, "adapter": "crossmodel", "stage_id": "a",\n'
        f'  "verdict": "{status}",\n'
        f'  "findings": {findings_json},\n'
        f'  "coverage": {{"axes_checked": ["support"], "axes_missing": {axes_missing}}},\n'
        '  "unchallenged_but_weak": [], "reviewer_model": "codex", "tokens_seen": 10\n'
        "}\n```\n"
    )


def test_clean_pass() -> None:
    v = parse_crossmodel_verdict(_verdict_text("pass", "[]"), "a")
    assert v.status == "pass"
    assert v.adapter == "crossmodel" and v.stage_id == "a"
    assert v.findings == []


def test_major_finding_downgrades_declared_pass() -> None:
    findings = '[{"claim_id": "a1", "severity": "major", "category": "overreach", "rebuttal": "r", "fix_directive": "f"}]'
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a")
    assert v.status == "needs_changes"
    assert v.findings[0].severity == "major"
    assert v.findings[0].fix_directive == "f"


def test_minor_only_stays_pass_when_axes_complete() -> None:
    # evidence_pointer가 있는 minor finding: §4.1② tokens_seen 교차검사에 안 걸리고 pass 유지.
    findings = ('[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", '
                '"rebuttal": "r", "fix_directive": "f", "evidence_pointer": "s1"}]')
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a")
    assert v.status == "pass"


def test_tokens_seen_without_evidence_pointer_downgrades() -> None:
    # §4.1② anti-gaming: tokens_seen>0(번들 봤음)인데 어느 finding도 소스를 안 가리키면 강등.
    findings = '[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", "rebuttal": "r", "fix_directive": "f"}]'
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a")
    assert v.status == "needs_changes"


def test_below_min_findings_quota_downgrades_when_config_given() -> None:
    # §4.1② 쿼터: config crossmodel_min_findings=3, findings 1개 < 3, unchallenged_but_weak 비었으면 강등.
    from types import SimpleNamespace
    findings = ('[{"claim_id": "a1", "severity": "minor", "category": "scope_miss", '
                '"rebuttal": "r", "fix_directive": "f", "evidence_pointer": "s1"}]')
    cfg = SimpleNamespace(crossmodel_min_findings=3)
    v = parse_crossmodel_verdict(_verdict_text("pass", findings), "a", config=cfg)
    assert v.status == "needs_changes"


def test_axes_missing_forces_needs_changes() -> None:
    v = parse_crossmodel_verdict(_verdict_text("pass", "[]", axes_missing='["omission"]'), "a")
    assert v.status == "needs_changes"


def test_declared_blocked_stays_blocked() -> None:
    v = parse_crossmodel_verdict(_verdict_text("blocked", "[]"), "a")
    assert v.status == "blocked"


def test_missing_marker_is_blocked() -> None:
    v = parse_crossmodel_verdict("검증기가 마커 없이 자유서술만 했다.", "a")
    assert v.status == "blocked"
    assert v.adapter == "crossmodel"


def test_unparseable_json_is_blocked() -> None:
    v = parse_crossmodel_verdict("CROSSMODEL_VERDICT: pass\n(no json block here)", "a")
    assert v.status == "blocked"


def test_verify_dispatch_crossmodel(tmp_path: Path) -> None:
    stage_out = {"verifier_raw_text": _verdict_text("pass", "[]")}
    v = verify("crossmodel", stage_out, tmp_path, verifier_agent="codex", config=None)
    assert v.status == "pass"
    assert (tmp_path / "verdict_crossmodel_a.json").exists()


def test_verify_unknown_adapter_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        verify("weird", {"verifier_raw_text": ""}, tmp_path, verifier_agent="claude", config=None)
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_crossmodel_adapter.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.adapters'` (collection error, 11 errors).

- [ ] **Step 3: 최소 구현** — `autoagent/research/adapters.py`:
```python
"""검증 어댑터 디스패치 + crossmodel 어댑터.

verify(adapter, ...)가 어댑터별 검증기로 라우팅한다. 이 슬라이스는 crossmodel만
구현한다(data_quality/source_grounding은 다음 슬라이스). crossmodel은 검증기 원문에서
마커+fenced JSON을 파싱하고, **코드가 findings를 집계해 status를 재계산**한다
(검증기가 pass라 적어도 major/critical이 있으면 needs_changes로 강등 — 자기모순 방지).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autoagent.artifacts import extract_json_block, write_json
from autoagent.research.types import Finding, Verdict


CROSSMODEL_MARKER = "CROSSMODEL_VERDICT:"


def parse_crossmodel_verdict(raw_text: str, stage_id: str, *, config: Any = None) -> Verdict:
    """검증기 원문 → Verdict. status는 코드가 재계산한다(모델 자유선언 불신).

    판정 규칙:
    - 마커(CROSSMODEL_VERDICT:)가 없거나 JSON 파싱 실패 → blocked(판정 불가).
    - 검증기가 blocked라 선언 → blocked 유지.
    - findings에 severity∈{critical,major}가 하나라도 있으면 → needs_changes(강등).
    - coverage.axes_missing가 비어있지 않으면 → needs_changes(누락 축).
    - (§4.1② anti-gaming) tokens_seen>0인데 어느 finding도 evidence_pointer가 없으면 → needs_changes.
    - (§4.1② quota) config 있고 len(findings)<crossmodel_min_findings이며 unchallenged_but_weak가
      비었으면 → needs_changes(무결 증명 미흡 강등). config=None이면 이 쿼터는 건너뛴다.
    - 위에 걸리지 않고 검증기 verdict가 pass면 → pass.
    """
    # 1) 마커 부재는 판정 불가(blocked). 자유서술만 온 경우를 명확히 격리한다.
    if CROSSMODEL_MARKER not in raw_text:
        return Verdict(status="blocked", adapter="crossmodel", stage_id=stage_id, findings=[], raw={})
    # 2) fenced JSON 파싱(코드 하네스의 extract_json_block 재사용). 실패 시 blocked.
    try:
        data = extract_json_block(raw_text)
    except Exception:  # noqa: BLE001 - JSON 없음/깨짐 전부 판정 불가로 격리
        return Verdict(status="blocked", adapter="crossmodel", stage_id=stage_id, findings=[], raw={})

    raw_findings = data.get("findings") or []
    findings = _findings_from(raw_findings)
    declared = str(data.get("verdict") or "").strip().lower()
    coverage = data.get("coverage") or {}
    axes_missing = coverage.get("axes_missing") or []
    # §4.1②: tokens_seen 교차검사 + 최소 findings 쿼터에 필요한 원본 신호를 뽑는다.
    tokens_seen = int(data.get("tokens_seen") or 0)
    has_evidence_pointer = any((f.get("evidence_pointer") or "") for f in raw_findings)
    unchallenged_weak = data.get("unchallenged_but_weak") or []
    min_findings = int(getattr(config, "crossmodel_min_findings", 3)) if config is not None else None
    status = _recompute_status(
        declared=declared, findings=findings, axes_missing=axes_missing,
        tokens_seen=tokens_seen, has_evidence_pointer=has_evidence_pointer,
        unchallenged_weak_empty=not unchallenged_weak, min_findings=min_findings,
    )
    return Verdict(status=status, adapter="crossmodel", stage_id=stage_id, findings=findings, raw=data)


def _findings_from(items: list[dict[str, Any]]) -> list[Finding]:
    """검증기 JSON의 findings 배열을 공유 Finding 타입으로 정규화한다.

    crossmodel 스키마의 rebuttal을 detail로, fix_directive를 그대로 매핑한다.
    severity가 알 수 없는 값이면 안전 방향으로 major 취급(강등 유발).
    """
    out: list[Finding] = []
    for it in items:
        sev = str(it.get("severity") or "").strip().lower()
        if sev not in {"critical", "major", "minor"}:
            sev = "major"  # 미상 severity는 보수적으로 강등쪽
        out.append(
            Finding(
                severity=sev,  # type: ignore[arg-type]
                category=str(it.get("category") or "unspecified"),
                detail=str(it.get("rebuttal") or it.get("detail") or ""),
                fix_directive=str(it.get("fix_directive") or ""),
                claim_id=(it.get("claim_id") if it.get("claim_id") not in ("", None) else None),
            )
        )
    return out


def _recompute_status(
    *,
    declared: str,
    findings: list[Finding],
    axes_missing: list[Any],
    tokens_seen: int = 0,
    has_evidence_pointer: bool = False,
    unchallenged_weak_empty: bool = True,
    min_findings: int | None = None,
) -> str:
    """findings/axes로 최종 status를 코드가 재계산한다(스펙 §4.1 pass 기준 + anti-gaming §4.1②)."""
    if declared == "blocked":
        return "blocked"
    blocking = any(f.severity in {"critical", "major"} for f in findings)
    if blocking or axes_missing:
        return "needs_changes"
    # §4.1② tokens_seen 교차검사: 번들을 봤다(tokens_seen>0)면서 어느 finding도 소스를
    # 가리키지(evidence_pointer) 않으면, 근거 없는 무결 선언으로 보고 자동 강등한다.
    if tokens_seen > 0 and findings and not has_evidence_pointer:
        return "needs_changes"
    # §4.1② 최소 findings 쿼터: config가 주어졌을 때만 강제. 약점이 쿼터 미만인데
    # unchallenged_but_weak(약하지만 통과)도 비었으면 무결 증명이 부실하다고 보고 강등.
    if min_findings is not None and len(findings) < min_findings and unchallenged_weak_empty:
        return "needs_changes"
    if declared == "pass":
        return "pass"
    return "needs_changes" if declared == "needs_changes" else "pass"


def verify(
    adapter: str,
    stage_out: dict[str, Any],
    run_dir: Path,
    *,
    verifier_agent: str,
    config: Any,
) -> Verdict:
    """어댑터별 검증 디스패치. crossmodel만 이 슬라이스에서 구현한다.

    crossmodel: stage_out["verifier_raw_text"](검증기 stdout)를 파싱·재계산하고
    verdict를 run_dir/verdict_crossmodel_<stage>.json으로 남긴다(감사추적). 모델 호출
    자체는 오케스트레이터(research.py)가 수행해 결과 텍스트를 여기로 넘긴다.
    """
    if adapter == "crossmodel":
        stage_id = str(stage_out.get("stage_id") or "a")
        raw_text = str(stage_out.get("verifier_raw_text") or "")
        verdict = parse_crossmodel_verdict(raw_text, stage_id, config=config)
        write_json(
            run_dir / f"verdict_crossmodel_{stage_id}.json",
            {
                "status": verdict.status, "adapter": verdict.adapter, "stage_id": verdict.stage_id,
                "verifier_agent": verifier_agent,
                "findings": [f.__dict__ for f in verdict.findings], "raw": verdict.raw,
            },
        )
        return verdict
    if adapter in {"data_quality", "source_grounding"}:
        raise SystemExit(f"Adapter '{adapter}' not implemented in this slice (later slice).")
    raise SystemExit(f"Unknown verify adapter: {adapter!r}")
```

- [ ] **Step 3b: config 필드 추가** — `autoagent/config.py`의 `Config` dataclass 끝(`tiers` 필드 뒤)에 §4.1② 쿼터용 기본값을 더한다(기본 3, config JSON로만 조정):
```python
    # 크로스모델 검증기가 강제하는 최소 findings 쿼터(§4.1②). 미만이고 unchallenged_but_weak도
    # 비었으면 코드가 needs_changes로 강등한다(무결 자유선언 방지).
    crossmodel_min_findings: int = 3
```
  주: `getattr(config, "crossmodel_min_findings", 3)` fallback이 있어 필드 없이도 동작하지만, 명시 필드가 있어야 config JSON에서 조정 가능하고 discoverable하다.

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_crossmodel_adapter.py -q`
  Expected: `11 passed`(§4.1② anti-gaming 실패테스트 2건 `test_tokens_seen_without_evidence_pointer_downgrades`·`test_below_min_findings_quota_downgrades_when_config_given` 포함).

- [ ] **Step 5: commit**
```bash
git add autoagent/research/adapters.py autoagent/config.py tests/research/test_crossmodel_adapter.py
git commit -m "feat(research): crossmodel 어댑터+verify 디스패치(마커·fenced JSON 파싱, 코드 status 재계산 + §4.1② anti-gaming)"
```

---

### Task 5: HTML 렌더 (markdown → 인라인 CSS standalone HTML)

리포트 markdown 본문을 pandoc/외부 의존 없이 최소 markdown→HTML로 변환하고 인라인 CSS로 감싸 바탕화면에 저장하는 순수 함수를 신설한다(deliver-local-html 준수).

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\html_report.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_html_report.py`

**Interfaces:**
- Consumes: 없음(stdlib `html`/`re`/`pathlib`).
- Produces:
  - `def markdown_to_html(md: str) -> str` — 헤딩(#/##/###)·굵게(`**x**`)·표 파이프행·불릿(`- `)·문단을 HTML로. 순수.
  - `def render_report_html(*, title: str, body_md: str) -> str` — `<style>` 인라인 + 본문. `<!doctype>` 포함 완결 문서.
  - `def write_desktop_report(html: str, filename: str) -> Path` — `~/Desktop/<filename>`에 UTF-8 기록, 경로 반환.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_html_report.py`:
```python
"""markdown→HTML 변환·문서 조립 테스트(순수·결정론).

외부 의존 없이 헤딩/굵게/표/불릿/문단이 HTML로 변환되고, 완결 문서가 인라인 style을
품고 self-contained(외부 리소스 참조 없음)인지 고정한다.
"""
from __future__ import annotations

from autoagent.research.html_report import markdown_to_html, render_report_html


def test_heading_and_bold() -> None:
    html = markdown_to_html("# 제목\n\n본문 **강조** 끝")
    assert "<h1>제목</h1>" in html
    assert "<strong>강조</strong>" in html


def test_bullets_become_list() -> None:
    html = markdown_to_html("- 하나\n- 둘")
    assert "<ul>" in html and "<li>하나</li>" in html and "<li>둘</li>" in html


def test_table_rows() -> None:
    md = "| stage | status |\n| --- | --- |\n| a | passed |"
    html = markdown_to_html(md)
    assert "<table>" in html
    assert "<th>stage</th>" in html and "<th>status</th>" in html
    assert "<td>a</td>" in html and "<td>passed</td>" in html


def test_html_escaped() -> None:
    html = markdown_to_html("본문 <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_full_document_self_contained() -> None:
    doc = render_report_html(title="리서치 리포트", body_md="# 제목\n\n본문")
    assert doc.lstrip().lower().startswith("<!doctype html>")
    assert "<style>" in doc
    assert "<title>리서치 리포트</title>" in doc
    assert "http://" not in doc and "https://" not in doc.split("본문")[0]
    assert "<h1>제목</h1>" in doc
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_html_report.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.html_report'` (collection error, 5 errors).

- [ ] **Step 3: 최소 구현** — `autoagent/research/html_report.py`:
```python
"""리서치 리포트 HTML 렌더(외부 의존 0).

markdown 본문을 최소 파서로 HTML로 바꾸고 인라인 CSS로 감싼 standalone 문서를 만든다
(pandoc 회피). 산출물은 바탕화면 standalone HTML로 전달한다(deliver-local-html 준수,
아티팩트 아님). 지원 문법: #/##/### 헤딩, **굵게**, `- ` 불릿, GFM 표(파이프+구분행), 문단.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """인라인 마크업 처리(먼저 escape 후 굵게만 복원). XSS/깨짐 방지로 escape가 먼저다."""
    escaped = _html.escape(text)
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


def _is_table_sep(line: str) -> bool:
    # | --- | --- | 형태의 구분행(셀이 대시/콜론/공백뿐).
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(set(c) <= set("-: ") and c for c in cells)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_html(md: str) -> str:
    """지원 문법 한정 markdown → HTML 조각(문서 래퍼 없음)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # 헤딩
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        # 표: 헤더행 + 구분행 + 바디행들
        if stripped.startswith("|") and i + 1 < n and _is_table_sep(lines[i + 1]):
            header = _split_row(stripped)
            out.append("<table>")
            out.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead>")
            out.append("<tbody>")
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i].strip())
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        # 불릿 리스트
        if stripped.startswith("- "):
            out.append("<ul>")
            while i < n and lines[i].strip().startswith("- "):
                out.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        # 문단(연속 비어있지 않은 줄 합침)
        para: list[str] = []
        while i < n and lines[i].strip() and not lines[i].strip().startswith(("#", "|", "- ")):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(out)


_STYLE = """
body{font-family:-apple-system,Segoe UI,Roboto,'Malgun Gothic',sans-serif;max-width:860px;
margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a}
h1{border-bottom:2px solid #333;padding-bottom:.3rem}
h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{border:1px solid #ccc;padding:.4rem .6rem;text-align:left}
th{background:#f2f2f2}
code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}
.warn{background:#fff3cd;border:1px solid #ffe08a;padding:.6rem;border-radius:4px}
""".strip()


def render_report_html(*, title: str, body_md: str) -> str:
    """본문 markdown을 완결된 standalone HTML 문서로 만든다(인라인 CSS, 외부 리소스 0)."""
    body_html = markdown_to_html(body_md)
    safe_title = _html.escape(title)
    return (
        "<!doctype html>\n<html lang=\"ko\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{safe_title}</title>\n"
        f"<style>\n{_STYLE}\n</style>\n"
        "</head>\n<body>\n"
        f"{body_html}\n"
        "</body>\n</html>\n"
    )


def write_desktop_report(html: str, filename: str) -> Path:
    """바탕화면(~/Desktop)에 리포트 HTML을 UTF-8로 기록하고 경로를 반환한다.

    브라우저 오픈은 호출부가 결정한다(os.startfile). Desktop이 없으면 홈에 저장.
    """
    desktop = Path.home() / "Desktop"
    target_dir = desktop if desktop.exists() else Path.home()
    path = target_dir / filename
    path.write_text(html, encoding="utf-8", newline="\n")
    return path
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_html_report.py -q`
  Expected: `5 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/html_report.py tests/research/test_html_report.py
git commit -m "feat(research): standalone HTML 렌더(markdown→인라인CSS, 바탕화면 전달)"
```

---

### Task 6: 중첩 루프 오케스트레이터 (`autoagent/workflows/research.py`)

`run_research_workflow` + `run_stage_loop`를 신설한다. 최소경로 = seed(Claude) → a 리서치(Claude) → crossmodel 검증(Codex) → derive(Claude) → crossmodel(Codex) → HTML. 안쪽 루프 최대 3, **silent pass-through 금지**(소진 시 `exhausted_unverified` 반환·기록). `research_state.json`에 매 전이 영속. 이 슬라이스는 바깥 루프 1회 고정(심화 루프는 Slice 4).

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py`

**Interfaces:**
- Consumes: `routing.choose_researcher(stage)`, `roles.{load_roles,resolve_role}`, `workflows.routed_impl.command_for_agent`(지연 import), `runner.{require_command,run_process,write_command_artifact,AgentCallBudget,AgentCallBudgetStopped}`, `artifacts.{render_template,write_text,write_json,DEFAULT_CONFIG,extract_json_block}`, `research.adapters.verify`, `research.html_report.{render_report_html,write_desktop_report}`, `research.types.{StageId,StageResult,Verdict}`.
- Produces:
  - `def run_research_workflow(args, config, request, run_dir) -> int`
  - `def run_stage_loop(stage: StageId, outer_pass: int, ctx: ResearchContext) -> StageResult` — 스테이지별 값/검증기프롬프트 매핑(`STAGE_VERIFIER_PROMPT`), seed 5필드 분해(`_seed_fields`), c 코드검증 분기(`_run_stage_c_verify`), pass 시 claims 주입(`_inject_verified_claims`) 포함.
  - `@dataclass ResearchContext(args, config, request, run_dir, budget, seed_contract, stage_outputs, state)`
  - `MINIMAL_PATH: list[StageId] = ["a", "derive"]`; `STAGE_VERIFIER_PROMPT`; 헬퍼 `_seed_fields`/`_inject_verified_claims`/`_run_stage_c_verify`.

- [ ] **Step 1: 최소 구현** — `autoagent/workflows/research.py`:
```python
"""리서치 워크플로 오케스트레이터(중첩 루프 엔진, 최소경로 슬라이스).

최소경로: preamble seed(Claude) → a 회사리서치(Claude) → crossmodel 검증(Codex) →
derive 도출(Claude) → crossmodel 검증(Codex) → standalone HTML 리포트(바탕화면).

안쪽 루프는 리서치→검증→보정을 최대 3회 돌고, 통과하면 resolved, 소진되면
exhausted_unverified를 **명시 반환**한다(silent pass-through 금지, 스펙 §8 F1).
매 전이는 research_state.json에 영속한다(재개용 골격). 바깥 루프는 이 슬라이스에서
1회 고정이고, 2회 심화 루프·seed pin·수렴 게이트는 Slice 4가 이 파일을 확장한다.
"""
from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from autoagent.artifacts import (
    DEFAULT_CONFIG,
    extract_json_block,
    render_template,
    write_json,
    write_text,
)
from autoagent.config import Config
from autoagent.research.adapters import verify
from autoagent.research.html_report import render_report_html, write_desktop_report
from autoagent.research.types import StageId, StageResult, Verdict
from autoagent.roles import load_roles, resolve_role
from autoagent.routing import choose_researcher
from autoagent.runner import (
    AgentCallBudget,
    require_command,
    run_process,
    write_command_artifact,
)

# 이 슬라이스의 최소경로 스테이지 순서. b/c/d는 다음 슬라이스에서 채운다.
MINIMAL_PATH: list[StageId] = ["a", "derive"]
STAGE_ADAPTER = {"a": "crossmodel", "b": "crossmodel", "derive": "crossmodel"}
STAGE_PROMPT = {"a": "a_researcher.md", "derive": "derive.md"}
# 스테이지별 검증기 프롬프트. 기본은 crossmodel_verifier.md, b는 전용 프롬프트.
# c(코드검증)·d(source_grounding)는 crossmodel 프롬프트를 쓰지 않으므로 매핑에서 제외한다.
STAGE_VERIFIER_PROMPT = {"a": "crossmodel_verifier.md", "derive": "crossmodel_verifier.md"}
INNER_MAX = 3  # 안쪽 루프 상한(안전밸브)


@dataclass
class ResearchContext:
    """run_stage_loop가 소비하는 실행 컨텍스트(오케스트레이터가 채워 전달)."""

    args: Namespace
    config: Config
    request: str
    run_dir: Path
    budget: AgentCallBudget
    seed_contract: str
    stage_outputs: dict[str, str] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    # 계층 예산(§6.4, Task 29). Slice 1~5 최소경로에선 None 허용(전역 budget만 사용).
    tiered: "TieredCallCap | None" = None


def _persist_state(ctx: "ResearchContext") -> None:
    """research_state.json을 매 전이마다 갱신한다(재개 골격)."""
    write_json(ctx.run_dir / "research_state.json", ctx.state)


def _run_agent_step(
    ctx: "ResearchContext",
    *,
    agent: str,
    role_id: str,
    name: str,
    prompt_name: str,
    prompt_values: dict[str, str],
    next_step: str,
    dry_output: str,
) -> str:
    """리서치 스텝 1회 실행(dry-run이면 프롬프트/커맨드만 렌더). routed의 run_role_step 축약판.

    command_for_agent는 순환 import 방지를 위해 지연 import한다(레포 관례).
    """
    from autoagent.workflows.routed_impl import command_for_agent

    args = ctx.args
    config = ctx.config
    run_dir = ctx.run_dir
    roles = load_roles(DEFAULT_CONFIG.parent)
    route = {"task_type": "research", "risk_level": "medium", "subtype": "research"}
    resolved = resolve_role(
        roles[role_id], config=config, route=route, request=ctx.request, agent=agent, read_only=args.read_only
    )
    prompt = render_template(prompt_name, prompt_values)
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, resolved))
        return dry_output

    command_name = require_command(config.claude_command if agent == "claude" else config.codex_command)
    ctx.budget.before_call(next_step=next_step, out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name,
        command=command_for_agent(config, resolved, resolved_command=command_name),
        prompt=prompt,
        cwd=config.workspace,
        out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / f"{name}.md", result)
    return result


def _seed_fields(ctx: "ResearchContext") -> dict[str, str]:
    """seed_pin dict를 프롬프트가 쓰는 5+1 필드(SEED_COMPANY 등)로 분해한다.

    b/d 리서처·검증기 프롬프트가 개별 seed 필드 placeholder를 쓴다(SEED_CONTRACT 통짜 아님).
    seed_pin이 아직 없으면(dry-run 초기) 빈 문자열로 채워 미치환 잔존을 막는다.
    """
    pin = ctx.state.get("seed_pin") or {}
    return {
        "SEED_COMPANY": str(pin.get("company", "")),
        "SEED_MARKET": str(pin.get("market", "")),
        "SEED_CURRENCY": str(pin.get("base_currency", "")),
        "SEED_PERIOD": str(pin.get("period", "")),
        "SEED_UNIT": str(pin.get("unit", "")),
        "SEED_AS_OF": str(pin.get("as_of", "")),
    }


def _inject_verified_claims(verdict, researcher_out: str) -> None:
    """검증 통과 시 리서처 산출물의 claims(+seed_candidate)를 verdict.raw에 실제 주입한다.

    B2 배선: 바깥 심화 루프(collect_verified_claims·_extract_seed_candidate)와 seed drift
    검출은 verdict.raw['verified_claims']/['seed_candidate']를 읽는다. 그러나 검증기 JSON(raw)
    자체엔 그 키가 없다 — 여기서 *리서처* stdout을 파싱해 채워 넣어야 실런에서 pass 2 심화·
    seed 위반 검출이 동작한다(안 채우면 항상 delta=0 → pass 1 직후 조기종료로 심화가 죽는다).
    """
    try:
        parsed = extract_json_block(researcher_out)
    except Exception:  # noqa: BLE001 - 리서처 JSON 파싱 실패는 빈 claim으로 취급
        return
    verdict.raw["verified_claims"] = parsed.get("claims", []) or []
    if parsed.get("seed_candidate"):
        verdict.raw["seed_candidate"] = parsed["seed_candidate"]


def _run_stage_c_verify(ctx: "ResearchContext", researcher_out: str) -> Verdict:
    """c 검증 경로: 리서처 stdout의 DATA_QUALITY_OUTPUT JSON을 코드 검증기로 검증한다(모델 0회).

    c 스테이지는 검증기=코드(data_quality 어댑터)다. crossmodel 프롬프트/모델 호출을 타지 않고,
    리서처가 낸 cleaned_files/transform_manifest/derived_claims/schema_expectations/sanity_rules를
    그대로 verify로 넘겨 원본 CSV에서 독립 재계산한다. verifier_agent는 계약상 반대모델(claude)로
    넘기되 data_quality 어댑터는 모델을 실제로 부르지 않는다. 이 슬라이스(1)에선 c가 순회에 없어
    호출되지 않지만, Slice 2가 STAGE_PROMPT["c"]를 채우면 run_stage_loop의 c 분기가 이 함수를 탄다.
    """
    try:
        stage_out = extract_json_block(researcher_out)  # DATA_QUALITY_OUTPUT fenced JSON
    except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 빈 스켈레톤으로 진행
        stage_out = {"cleaned_files": [], "transform_manifest": {"steps": []},
                     "derived_claims": [], "schema_expectations": {}, "sanity_rules": {}}
    return verify(
        "data_quality", stage_out, ctx.run_dir,
        verifier_agent="claude", config=ctx.config,
    )


def run_stage_loop(stage: StageId, outer_pass: int, ctx: ResearchContext) -> StageResult:
    """안쪽 루프: 리서치→검증→보정 최대 3회. 통과=resolved, 소진=exhausted_unverified.

    silent pass-through 금지: 검증을 못 넘긴 채 상한에 도달하면 exhausted_unverified를
    명시 반환하고 상태에 기록한다(스펙 §8 F1). blocked verdict면 즉시 blocked 반환.
    """
    researcher, verifier, _reason = choose_researcher(stage)

    prior_feedback = ""
    last_verdict = None
    inner = 0
    for inner in range(1, INNER_MAX + 1):
        ctx.state.update({"outer_pass": outer_pass, "stage": stage, "inner_round": inner})
        _persist_state(ctx)

        # 스테이지별 값 dict. seed 5필드·MIN_FINDINGS·CSV 경로 등을 스테이지에 맞춰 채운다.
        values = {
            "REQUEST": ctx.request,
            "WORKSPACE": str(ctx.config.workspace),
            "SEED_CONTRACT": ctx.seed_contract,
            "SEED_PIN": json.dumps(ctx.state.get("seed_pin") or {}, ensure_ascii=False),
            "STAGE_ID": stage,
            "OUTER_PASS": str(outer_pass),
            "INNER_ROUND": str(inner),
            "PRIOR_FEEDBACK": prior_feedback,
            "INNER_FEEDBACK": prior_feedback,   # b 프롬프트 명칭
            "DEEPEN_DELTA": prior_feedback,     # pass 2 심화 delta(피드백 없으면 빈 값)
            "STAGE_A_OUTPUT": ctx.stage_outputs.get("a", ""),
            # c 리서처(codex)용 CSV 경로. config에 있으면 그 값을, 없으면 워크스페이스 안내.
            "CSV_PATHS": getattr(ctx.config, "research_csv_paths", "") or "(워크스페이스의 입력 CSV)",
            # crossmodel 검증기의 최소 findings 쿼터(config crossmodel_min_findings, 기본 3).
            "MIN_FINDINGS": str(getattr(ctx.config, "crossmodel_min_findings", 3)),
        }
        values.update(_seed_fields(ctx))  # SEED_COMPANY/MARKET/CURRENCY/PERIOD/UNIT/AS_OF 분해 주입
        researcher_out = _run_agent_step(
            ctx, agent=researcher, role_id="researcher",
            name=f"stage_{stage}_p{outer_pass}_r{inner}_researcher",
            prompt_name=STAGE_PROMPT[stage], prompt_values=values,
            next_step=f"research:{stage}",
            dry_output=f"[dry-run: {researcher} {stage} researcher output]",
        )

        if stage == "c":
            # c: 리서처 stdout(DATA_QUALITY_OUTPUT)을 코드 검증기로 검증(모델 0회).
            verdict = _run_stage_c_verify(ctx, researcher_out)
        else:
            # 스테이지별 검증기 프롬프트(b는 전용 b_market_verifier.md, 그 외 crossmodel).
            verifier_out = _run_agent_step(
                ctx, agent=verifier, role_id="verifier",
                name=f"stage_{stage}_p{outer_pass}_r{inner}_verifier",
                prompt_name=STAGE_VERIFIER_PROMPT.get(stage, "crossmodel_verifier.md"),
                prompt_values={
                    **values,  # seed 5필드·MIN_FINDINGS를 검증기 프롬프트에도 넘긴다(b_market_verifier 등)
                    "STAGE_ID": stage,
                    "RESEARCHER_OUTPUT": researcher_out,
                    "STAGE_OUTPUT_JSON": researcher_out,  # b 검증기 명칭
                },
                next_step=f"verify:{stage}",
                dry_output=(
                    # dry-run은 코드 재계산 경로를 타게 하려고 유효 verdict를 흉내낸다.
                    # unchallenged_but_weak를 채워 §4.1② 최소 findings 쿼터를 만족(dry-run pass 유지).
                    f"CROSSMODEL_VERDICT: pass\n```json\n"
                    f'{{"adapter":"crossmodel","stage_id":"{stage}","verdict":"pass",'
                    f'"findings":[],"coverage":{{"axes_checked":["support"],"axes_missing":[]}},'
                    f'"unchallenged_but_weak":["dry-run"],"tokens_seen":0}}\n```\n'
                ),
            )
            verdict = verify(
                STAGE_ADAPTER[stage], {"stage_id": stage, "verifier_raw_text": verifier_out},
                ctx.run_dir, verifier_agent=verifier, config=ctx.config,
            )
        last_verdict = verdict
        ctx.state.setdefault("stage_status", {})[stage] = verdict.status
        _persist_state(ctx)

        if verdict.status == "pass":
            _inject_verified_claims(verdict, researcher_out)  # B2: 리서처 claims→verdict.raw 실주입
            ctx.stage_outputs[stage] = researcher_out
            return StageResult(
                stage_id=stage, status="resolved",
                output_path=f"stage_{stage}_p{outer_pass}_r{inner}_researcher.md",
                verdict=verdict, inner_rounds=inner,
            )
        if verdict.status == "blocked":
            ctx.stage_outputs[stage] = researcher_out
            return StageResult(
                stage_id=stage, status="blocked",
                output_path=f"stage_{stage}_p{outer_pass}_r{inner}_researcher.md",
                verdict=verdict, inner_rounds=inner,
            )
        prior_feedback = "\n".join(f"- [{f.severity}] {f.category}: {f.fix_directive}" for f in verdict.findings)

    # 상한 도달, 미통과 → silent pass-through 금지: 명시적으로 미검증 표기.
    ctx.stage_outputs[stage] = ctx.stage_outputs.get(stage, "")
    return StageResult(
        stage_id=stage, status="exhausted_unverified",
        output_path=f"stage_{stage}_p{outer_pass}_r{inner}_researcher.md",
        verdict=last_verdict, inner_rounds=inner,
    )


def _coverage_matrix_md(results: list[StageResult]) -> str:
    """스테이지별 verify_status 표(상단 강제). 100% 미만이면 경고 배너 문구를 앞에 붙인다."""
    status_map = {"resolved": "passed", "exhausted_unverified": "exhausted_unverified", "blocked": "blocked"}
    rows = "\n".join(f"| {r.stage_id} | {status_map.get(r.status, r.status)} |" for r in results)
    table = "| stage | verify_status |\n| --- | --- |\n" + rows
    all_passed = all(r.status == "resolved" for r in results)
    banner = "" if all_passed else "**경고: 일부 스테이지가 검증을 통과하지 못했습니다(UNVERIFIED).**\n\n"
    return banner + table


def run_research_workflow(args: Namespace, config: Config, request: str, run_dir: Path) -> int:
    """리서치 워크플로 진입점(최소경로 슬라이스).

    seed 확정 → 최소경로 스테이지(a, derive)를 안쪽 루프로 돌리고 → HTML 리포트를
    바탕화면에 저장한다. dry-run이면 CLI 미호출로 프롬프트/커맨드/상태만 렌더한다.
    """
    budget = AgentCallBudget(args.max_agent_calls)
    ctx = ResearchContext(
        args=args, config=config, request=request, run_dir=run_dir, budget=budget, seed_contract="",
    )
    ctx.state = {"outer_pass": 1, "stage": "seed", "inner_round": 0, "seed_pin": {},
                 "verified_claims": [], "stage_status": {}}
    _persist_state(ctx)

    seed_out = _run_agent_step(
        ctx, agent="claude", role_id="researcher", name="00_seed_contract",
        prompt_name="seed_contract.md",
        prompt_values={"REQUEST": request, "WORKSPACE": str(config.workspace)},
        next_step="seed",
        dry_output='SEED_CONTRACT_JSON\n```json\n{"company":"[dry-run]","base_currency":"KRW"}\n```\n',
    )
    ctx.seed_contract = seed_out
    try:
        ctx.state["seed_pin"] = extract_json_block(seed_out)
    except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 최소경로는 진행
        ctx.state["seed_pin"] = {}
    _persist_state(ctx)

    results: list[StageResult] = []
    try:
        for stage in MINIMAL_PATH:
            result = run_stage_loop(stage, outer_pass=1, ctx=ctx)
            results.append(result)
            write_json(ctx.run_dir / f"stage_result_{stage}.json", {
                "stage_id": result.stage_id, "status": result.status,
                "output_path": result.output_path, "inner_rounds": result.inner_rounds,
                "verdict_status": (result.verdict.status if result.verdict else None),
            })
    except Exception as exc:  # 예산 소진(AgentCallBudgetStopped 포함)은 부분 상태로 안전 종료.
        from autoagent.runner import AgentCallBudgetStopped
        if isinstance(exc, AgentCallBudgetStopped):
            print(f"Research run stopped by budget before {exc.next_step}: {run_dir}")
            return 0
        raise

    body_md = render_template(
        "final_html_report.md",
        {
            "COVERAGE_MATRIX_MD": _coverage_matrix_md(results),
            "REQUEST": request,
            "SEED_CONTRACT": ctx.seed_contract,
            "STAGE_A_OUTPUT": ctx.stage_outputs.get("a", "(없음)"),
            "DERIVE_OUTPUT": ctx.stage_outputs.get("derive", "(없음)"),
        },
    )
    html = render_report_html(title="리서치 리포트", body_md=body_md)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"research_report_{stamp}.html"
    write_text(run_dir / "final_report.html", html)  # 감사추적용 사본(run_dir)
    if args.dry_run:
        print(f"Research dry run written to {run_dir}")
        return 0
    desktop_path = write_desktop_report(html, filename)
    try:
        import os
        os.startfile(str(desktop_path))  # Windows: 기본 브라우저로 열기
    except Exception:  # noqa: BLE001 - 오픈 실패해도 파일은 남았으므로 치명 아님
        pass
    print(f"Research run complete: {run_dir}\nReport: {desktop_path}")
    return 0
```
  주: dry-run 검증기 dry_output이 유효 verdict를 흉내내 코드 재계산 경로(`verify`→pass)를 실제로 타게 한다. `AgentCallBudgetStopped` 처리는 routed와 동형(예산 소진 안전 종료).

- [ ] **Step 2: import 스모크(실패 확인)** — Run: `python -c "import autoagent.workflows.research as r; print(r.MINIMAL_PATH)"`
  Expected: `['a', 'derive']` (import 에러 없이). CLI 분기는 Task 7에서 붙는다.

- [ ] **Step 3: 단위 렌더 스모크(통과 확인)** — Run:
  `python -c "import argparse,pathlib,tempfile; from autoagent.config import load_config; from autoagent.artifacts import DEFAULT_CONFIG; from autoagent.workflows.research import run_research_workflow; a=argparse.Namespace(dry_run=True,read_only=False,max_agent_calls=0); c=load_config(DEFAULT_CONFIG); d=pathlib.Path(tempfile.mkdtemp()); print(run_research_workflow(a,c,'삼성전자 리서치',d)); print(sorted(p.name for p in d.iterdir()))"`
  Expected: `0` 반환 + 산출물에 `00_seed_contract_prompt.md`, `stage_a_p1_r1_researcher_prompt.md`, `stage_a_p1_r1_verifier_prompt.md`, `verdict_crossmodel_a.json`, `stage_result_a.json`, `stage_result_derive.json`, `research_state.json`, `final_report.html` 포함.

- [ ] **Step 4: commit**
```bash
git add autoagent/workflows/research.py
git commit -m "feat(research): 중첩 루프 오케스트레이터(run_research_workflow/run_stage_loop, silent pass-through 금지)"
```

---

### Task 7: CLI/run 분기 (`--workflow research`)

`--workflow`에 `research`를 추가하고 `cli.main`에서 `run_research_workflow`로 분기한다. end-to-end dry-run으로 최소경로 전체 렌더를 확인한다.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\cli.py` (`--workflow` choices, import, 워크플로 분기)

**Interfaces:**
- Consumes: `workflows.research.run_research_workflow(args, config, request, run_dir) -> int`.
- Produces: `--workflow research` CLI 경로.

- [ ] **Step 1: 실패 확인(분기 없음)** — Run: `python run.py --dry-run --workflow research --request "삼성전자 회사 리서치"`
  Expected: argparse 에러 `argument --workflow: invalid choice: 'research' (choose from 'simple', 'routed', 'decompose')` (exit 2).

- [ ] **Step 2: 최소 구현 — choices** — `cli.py`의 `--workflow` 정의를 교체:
```python
    parser.add_argument("--workflow", choices=["simple", "routed", "decompose", "research"], default="simple", help="Workflow to run")
```

- [ ] **Step 3: 최소 구현 — import + 분기** — `cli.py`의 `if args.workflow == "decompose":` 블록 뒤, `return run_simple_workflow(...)` 앞에 추가:
```python
    if args.workflow == "research":
        from autoagent.workflows.research import run_research_workflow
        return run_research_workflow(args, config, request, run_dir)
```

- [ ] **Step 4: 통과 확인(end-to-end dry-run)** — Run: `python run.py --dry-run --workflow research --request "삼성전자 회사 리서치"`
  Expected: exit 0 + stdout에 `Research dry run written to ...runs\<stamp>`. 해당 run_dir에 `00_request.md`, `metadata.json`(workflow=research), `00_seed_contract_prompt.md`+`_command.json`, `stage_a_p1_r1_researcher_prompt.md`, `stage_a_p1_r1_verifier_prompt.md`, `verdict_crossmodel_a.json`, `stage_result_a.json`, `stage_result_derive.json`, `research_state.json`, `final_report.html` 생성. 한글 내용은 Read(utf-8)로 확인(cat 금지 — cp949 깨짐).

- [ ] **Step 5: 전체 회귀** — Run: `python -m pytest tests/ -q`
  Expected: `test_types`3 + `test_routing_researcher`6 + `test_roles_and_prompts`4 + `test_crossmodel_adapter`11 + `test_html_report`5 = `29 passed`. 그리고 `python run.py --dry-run --workflow routed --task-type backend --request "add endpoint"` → exit 0.

- [ ] **Step 6: commit**
```bash
git add autoagent/cli.py
git commit -m "feat(research): --workflow research CLI 분기(최소경로 end-to-end dry-run)"
```

---

## Slice 2 — data_quality 어댑터 + csv_validator + c 스테이지

Slice 1의 타입(`Finding`/`Verdict`/`StageResult`/`StageId`)과 `adapters.verify` 디스패처를 전제로 소비하고, `data_quality` 분기와 `c` 스테이지 자산을 채운다. c 스테이지는 리서처=Codex, 검증기=**코드**(모델 0회). 인코딩 폴백·sha256·kind별 tolerance·claim 원본 독립 재계산이 핵심.

---

### Task 8: `csv_validator.py` — 인코딩 폴백 + sha256 로더

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\data\__init__.py`
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\data\csv_validator.py` (로더 + `CSVQualityMetrics` 골격까지)
- Create: `C:\Users\systran\Desktop\AutoAgent\tests\data\__init__.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\data\test_csv_encoding.py`
- Create: `C:\Users\systran\Desktop\AutoAgent\pytest.ini`

**Interfaces:**
- Consumes: 없음(순수 stdlib).
- Produces:
  - `@dataclass CSVQualityMetrics(path: str, row_count: int, column_count: int, columns: list[str], null_ratio_by_column: dict[str, float], duplicate_row_count: int, duplicate_ratio: float, format_anomalies: list[str], encoding_detected: str)`
  - `def _sha256_of_file(path: Path) -> str`
  - `def _read_csv_rows(path: Path) -> tuple[str, list[str], list[list[str]]]` — `(encoding_detected, header, data_rows)`; 폴백 `utf-8→utf-8-sig→cp949`, 전부 실패 시 `ValueError`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/data/__init__.py`(빈), `tests/data/test_csv_encoding.py`:
```python
"""csv_validator의 인코딩 폴백·sha256 결정성 단위테스트."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from autoagent.data.csv_validator import _read_csv_rows, _sha256_of_file


def _write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_utf8_plain(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "u8.csv", "name,city\n가,서울\n".encode("utf-8"))
    enc, header, rows = _read_csv_rows(p)
    assert enc == "utf-8"
    assert header == ["name", "city"]
    assert rows == [["가", "서울"]]


def test_utf8_sig_bom(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "bom.csv", "﻿name,city\n가,서울\n".encode("utf-8-sig"))
    enc, header, rows = _read_csv_rows(p)
    assert enc == "utf-8-sig"
    assert header == ["name", "city"]


def test_cp949_fallback(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "cp949.csv", "이름,도시\n가,서울\n".encode("cp949"))
    enc, header, rows = _read_csv_rows(p)
    assert enc == "cp949"
    assert header == ["이름", "도시"]
    assert rows == [["가", "서울"]]


def test_undecodable_raises_honest_error(tmp_path: Path) -> None:
    p = _write_bytes(tmp_path, "junk.csv", b"\x81\x00\xff\xfe\x9d\x8f\n")
    with pytest.raises(ValueError, match="decode"):
        _read_csv_rows(p)


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    data = b"name,city\na,b\n"
    p = _write_bytes(tmp_path, "h.csv", data)
    assert _sha256_of_file(p) == hashlib.sha256(data).hexdigest()
```

  `pytest.ini`(레포 루트):
```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/data/test_csv_encoding.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.data.csv_validator'` (5 errors during collection).

- [ ] **Step 3: 최소 구현** — `autoagent/data/__init__.py`:
```python
"""리서치 워크플로 데이터 파일 층(CSV 품질 실측 등). stdlib만 사용(의존성 0)."""
from __future__ import annotations
```
  `autoagent/data/csv_validator.py`:
```python
"""CSV 품질 실측(data_quality 어댑터 c 스테이지용).

stdlib `csv`만 쓴다(pandas 불필요, 의존성 0). 인코딩은 cp949 gotcha를 고려해
`utf-8 → utf-8-sig → cp949` 순서로 자동 폴백하고, 모두 실패하면 조용히 skip하지
않고 정직하게 예외를 올린다. 입력 파일 sha256을 provenance로 고정한다.

여기서 산출하는 CSVQualityMetrics는 순수 함수 결과라 결정론이며, adapters.py의
data_quality 분기가 이 지표 + transform_manifest/claim 재계산을 합쳐 Verdict를 만든다.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# 인코딩 폴백 순서(고정). cp949는 한국어 CSV 덤프에서 흔한 마지막 보루.
_ENCODING_FALLBACKS: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp949")


@dataclass
class CSVQualityMetrics:
    """단일 CSV의 결정론적 품질 지표 묶음(계약 고정 필드)."""

    path: str
    row_count: int
    column_count: int
    columns: list[str]
    null_ratio_by_column: dict[str, float]
    duplicate_row_count: int
    duplicate_ratio: float
    format_anomalies: list[str] = field(default_factory=list)
    encoding_detected: str = ""


def _sha256_of_file(path: Path) -> str:
    """파일 바이트의 sha256 hex digest(provenance 고정용)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_csv_rows(path: Path) -> tuple[str, list[str], list[list[str]]]:
    """인코딩 폴백으로 CSV를 읽어 (감지 인코딩, header, data_rows)를 반환한다.

    utf-8→utf-8-sig→cp949 순서로 디코드를 시도하고, 처음 성공한 인코딩으로 파싱한다.
    셋 다 실패하면 조용한 skip 대신 ValueError로 정직하게 올린다(cp949 gotcha).
    """
    last_error: Exception | None = None
    for enc in _ENCODING_FALLBACKS:
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                text = fh.read()
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_error = exc
            continue
        reader = csv.reader(text.splitlines())
        rows = list(reader)
        if not rows:
            return enc, [], []
        header, *data = rows
        return enc, header, data
    raise ValueError(
        f"failed to decode CSV with any of {_ENCODING_FALLBACKS}: {path} ({last_error})"
    )
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/data/test_csv_encoding.py -q`
  Expected: `5 passed`.

- [ ] **Step 5: commit**
```bash
git checkout -b feature/research-slice2-data-quality
git add autoagent/data/__init__.py autoagent/data/csv_validator.py tests/data/__init__.py tests/data/test_csv_encoding.py pytest.ini
git commit -m "feat(research): csv 인코딩 폴백 로더 + sha256"
```

---

### Task 9: `validate_csv` — 품질 지표 실측

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\data\csv_validator.py` (파일 끝에 `validate_csv` 추가)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\data\test_validate_csv.py`

**Interfaces:**
- Consumes: `CSVQualityMetrics`, `_read_csv_rows`, `_sha256_of_file`(Task 8).
- Produces: `def validate_csv(path: Path) -> CSVQualityMetrics` — 결정론. `null` = 빈 문자열/공백만; `duplicate` = 데이터 행 전체 튜플 기준. 파일 못 읽으면 `_read_csv_rows`의 `ValueError` 전파(조용한 skip 금지).

- [ ] **Step 1: 실패 테스트 작성** — `tests/data/test_validate_csv.py`:
```python
"""validate_csv 품질 지표 실측 단위테스트(결정론)."""
from __future__ import annotations

from pathlib import Path

import pytest

from autoagent.data.csv_validator import CSVQualityMetrics, validate_csv


def _w(tmp_path: Path, name: str, text: str, enc: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return p


def test_basic_shape_and_columns(tmp_path: Path) -> None:
    p = _w(tmp_path, "a.csv", "id,name\n1,kim\n2,lee\n")
    m = validate_csv(p)
    assert isinstance(m, CSVQualityMetrics)
    assert m.row_count == 2
    assert m.column_count == 2
    assert m.columns == ["id", "name"]
    assert m.encoding_detected == "utf-8"


def test_null_ratio_counts_blank_and_whitespace(tmp_path: Path) -> None:
    p = _w(tmp_path, "n.csv", "id,name\n1,\n2,   \n3,kim\n")
    m = validate_csv(p)
    assert m.null_ratio_by_column["name"] == pytest.approx(2 / 3)
    assert m.null_ratio_by_column["id"] == 0.0


def test_duplicate_rows_full_tuple(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "id,name\n1,kim\n1,kim\n2,lee\n")
    m = validate_csv(p)
    assert m.duplicate_row_count == 1
    assert m.duplicate_ratio == pytest.approx(1 / 3)


def test_ragged_row_flagged_as_anomaly(tmp_path: Path) -> None:
    p = _w(tmp_path, "r.csv", "id,name\n1,kim,extra\n")
    m = validate_csv(p)
    assert any("column count" in a.lower() for a in m.format_anomalies)


def test_empty_file_is_honest(tmp_path: Path) -> None:
    p = _w(tmp_path, "e.csv", "")
    m = validate_csv(p)
    assert m.row_count == 0
    assert m.column_count == 0
    assert m.columns == []


def test_undecodable_propagates(tmp_path: Path) -> None:
    p = tmp_path / "junk.csv"
    p.write_bytes(b"\x81\x00\xff\xfe\x9d\x8f\n")
    with pytest.raises(ValueError):
        validate_csv(p)
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/data/test_validate_csv.py -q`
  Expected: `ImportError: cannot import name 'validate_csv'` (6 errors during collection).

- [ ] **Step 3: 최소 구현** — `autoagent/data/csv_validator.py` 끝에 추가:
```python
def _is_null(cell: str) -> bool:
    """결측 판정: None/빈문자열/공백만이면 결측으로 본다(관대한 null 정의)."""
    return cell is None or cell.strip() == ""


def validate_csv(path: Path) -> CSVQualityMetrics:
    """CSV 하나를 읽어 결정론적 품질 지표(CSVQualityMetrics)를 산출한다.

    파일을 못 읽으면 _read_csv_rows가 ValueError를 올리고 여기서 잡지 않는다
    (조용한 skip 금지). null 비율은 열별, duplicate는 데이터 행 전체 튜플 기준.
    헤더와 열 수가 다른 행은 format_anomalies로 남기되 파싱은 계속한다.
    """
    encoding, header, data_rows = _read_csv_rows(path)
    columns = list(header)
    column_count = len(columns)
    row_count = len(data_rows)

    anomalies: list[str] = []
    null_counts = {col: 0 for col in columns}
    seen: set[tuple[str, ...]] = set()
    duplicate_row_count = 0
    for idx, row in enumerate(data_rows, start=1):
        if len(row) != column_count:
            anomalies.append(f"row {idx}: column count {len(row)} != header {column_count}")
        for col_idx, col in enumerate(columns):
            cell = row[col_idx] if col_idx < len(row) else ""
            if _is_null(cell):
                null_counts[col] += 1
        key = tuple(row)
        if key in seen:
            duplicate_row_count += 1
        else:
            seen.add(key)

    null_ratio_by_column = {
        col: (null_counts[col] / row_count if row_count else 0.0) for col in columns
    }
    duplicate_ratio = duplicate_row_count / row_count if row_count else 0.0

    return CSVQualityMetrics(
        path=str(path), row_count=row_count, column_count=column_count, columns=columns,
        null_ratio_by_column=null_ratio_by_column, duplicate_row_count=duplicate_row_count,
        duplicate_ratio=duplicate_ratio, format_anomalies=anomalies, encoding_detected=encoding,
    )
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/data/test_validate_csv.py -q`
  Expected: `6 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/data/csv_validator.py tests/data/test_validate_csv.py
git commit -m "feat(research): validate_csv 품질 지표 실측"
```

---

### Task 10: data_quality 결정론 체크 세트 (`autoagent/research/data_quality.py`)

스펙 §4.2의 4대 체크((1)행수 보존 (2)claim 재계산 (3)스키마 정합 (4)sanity)를 순수 함수로 구현한다. 원본에서 독립 재계산·kind별 tolerance 고정을 여기서 확정한다(에이전트가 임계값을 못 바꿔야 tautology 차단).

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\data_quality.py`
- Create: `C:\Users\systran\Desktop\AutoAgent\tests\research\__init__.py`(이미 Slice 1에서 생성됨 — 없으면 빈 파일 생성)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_data_quality_checks.py`

**Interfaces:**
- Consumes: `CSVQualityMetrics`, `_read_csv_rows`(Task 8·9); `Finding`(Slice 1 `types.py`).
- Produces:
  - `def tolerance_for(metric: str) -> float` — count/sum/row_count=0.0, ratio/cagr/mean=0.01.
  - `def check_row_conservation(source_metrics, cleaned_metrics, manifest) -> tuple[dict, list[Finding]]`.
  - `def recompute_claim(source_path: Path, backing_stat: dict) -> float | None` — 원본 독립 재산출(count/sum/mean/ratio).
  - `def check_claims(source_path, derived_claims) -> tuple[list[dict], list[Finding]]`.
  - `def check_schema(cleaned_metrics, schema_expectations) -> tuple[list[dict], list[Finding]]`.
  - `def check_sanity(cleaned_metrics, sanity_rules) -> tuple[list[dict], list[Finding]]`.
  - `CHECK_SET_VERSION = 1`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_data_quality_checks.py`:
```python
"""data_quality 결정론 체크 세트 단위테스트(모델 0회)."""
from __future__ import annotations

from pathlib import Path

from autoagent.data.csv_validator import validate_csv
from autoagent.research.data_quality import (
    check_claims, check_row_conservation, check_sanity, check_schema,
    recompute_claim, tolerance_for,
)


def _w(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_tolerance_exact_for_count_sum_rowcount() -> None:
    assert tolerance_for("count") == 0.0
    assert tolerance_for("sum") == 0.0
    assert tolerance_for("row_count") == 0.0


def test_tolerance_one_percent_for_ratio_cagr_mean() -> None:
    assert tolerance_for("ratio") == 0.01
    assert tolerance_for("cagr") == 0.01
    assert tolerance_for("mean") == 0.01


def test_row_conservation_fully_explained_passes(tmp_path: Path) -> None:
    src = validate_csv(_w(tmp_path, "s.csv", "id\n1\n2\n3\n4\n"))
    cln = validate_csv(_w(tmp_path, "c.csv", "id\n1\n2\n3\n"))
    manifest = {"steps": [{"op": "dedup", "target_cols": ["id"], "params": {"dropped": 1}}]}
    delta, findings = check_row_conservation(src, cln, manifest)
    assert delta["source_rows"] == 4
    assert delta["cleaned_rows"] == 3
    assert delta["dropped"] == 1
    assert findings == []


def test_row_conservation_unexplained_drop_is_finding(tmp_path: Path) -> None:
    src = validate_csv(_w(tmp_path, "s.csv", "id\n1\n2\n3\n4\n5\n"))
    cln = validate_csv(_w(tmp_path, "c.csv", "id\n1\n2\n"))
    manifest = {"steps": [{"op": "dedup", "params": {"dropped": 1}}]}
    delta, findings = check_row_conservation(src, cln, manifest)
    assert delta["dropped"] == 3
    assert any(f.severity in {"critical", "major"} for f in findings)
    assert any("unexplained" in f.detail.lower() for f in findings)


def test_recompute_count(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nbusan,20\nseoul,30\n")
    val = recompute_claim(p, {"metric": "count", "col": "region", "filter": {"region": "seoul"}})
    assert val == 2


def test_recompute_sum(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nbusan,20\nseoul,30\n")
    val = recompute_claim(p, {"metric": "sum", "col": "amt", "filter": {"region": "seoul"}})
    assert val == 40.0


def test_check_claims_exact_mismatch_flags(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nseoul,30\n")
    claims = [{"id": "k1", "text": "seoul sum", "backing_stat": {"metric": "sum", "col": "amt", "value": 41}}]
    recompute, findings = check_claims(p, claims)
    assert recompute[0]["claim_id"] == "k1"
    assert recompute[0]["recomputed_value"] == 40.0
    assert recompute[0]["match"] is False
    assert any(f.claim_id == "k1" for f in findings)


def test_check_claims_ratio_within_tolerance_matches(tmp_path: Path) -> None:
    p = _w(tmp_path, "d.csv", "region,amt\nseoul,10\nseoul,30\nbusan,10\n")
    claims = [{"id": "r1", "text": "seoul share",
               "backing_stat": {"metric": "ratio", "col": "region", "value": 0.67, "filter": {"region": "seoul"}}}]
    recompute, findings = check_claims(p, claims)
    assert recompute[0]["match"] is True
    assert findings == []


def test_schema_diff_type_mismatch_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,x\n2,y\n"))
    diff, findings = check_schema(cln, {"id": "int", "amt": "int"})
    amt = next(d for d in diff if d["col"] == "amt")
    assert amt["ok"] is False
    assert any("amt" in f.detail for f in findings)


def test_schema_diff_all_ok(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,amt\n1,10\n2,20\n"))
    diff, findings = check_schema(cln, {"id": "int", "amt": "int"})
    assert all(d["ok"] for d in diff)
    assert findings == []


def test_sanity_negative_revenue_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,revenue\n1,100\n2,-5\n"))
    checks, findings = check_sanity(cln, {"non_negative_cols": ["revenue"]})
    assert any(c["status"] == "fail" for c in checks)
    assert any("revenue" in f.detail for f in findings)


def test_sanity_duplicate_key_flags(tmp_path: Path) -> None:
    cln = validate_csv(_w(tmp_path, "c.csv", "id,v\n1,a\n1,b\n"))
    checks, findings = check_sanity(cln, {"unique_cols": ["id"]})
    assert any(c["status"] == "fail" and "id" in c.get("col", "") for c in checks)
    assert any("id" in f.detail for f in findings)
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_data_quality_checks.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.data_quality'` (collection error).

- [ ] **Step 3: 최소 구현** — `autoagent/research/data_quality.py`:
```python
"""data_quality 어댑터의 결정론 체크 세트(c 스테이지, 모델 0회).

스펙 §4.2의 4대 체크를 순수 함수로 구현한다:
(1) 행수 보존 — dropped가 transform_manifest로 100% 설명되는가,
(2) claim 재계산 — 원본 CSV에서 **독립 경로**로 재산출(manifest 재실행 아님),
(3) 스키마 정합 — 기대 dtype과 실측 열 타입 대조,
(4) sanity — 중복키·음수매출 등 상식 위반.

tolerance는 metric kind별로 코드가 고정한다(합계·행수=정확일치, 비율·CAGR=1%).
임계값을 여기 하드코딩하는 이유: 에이전트가 못 바꿔야 tautology(자기 기준 통과)를
차단할 수 있기 때문. Finding은 Slice 1 types.py의 계약을 쓴다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autoagent.data.csv_validator import CSVQualityMetrics, _read_csv_rows
from autoagent.research.types import Finding

# verdict schema_version.
CHECK_SET_VERSION = 1

# metric kind별 상대 허용오차(고정). 합계·행수·카운트는 정확일치, 비율류만 1%.
_EXACT_METRICS = {"count", "sum", "row_count"}
_RATIO_METRICS = {"ratio", "cagr", "mean"}


def tolerance_for(metric: str) -> float:
    """metric kind별 상대 허용오차. 정확일치=0.0, 비율/CAGR/평균=0.01(1%)."""
    key = metric.lower()
    if key in _EXACT_METRICS:
        return 0.0
    if key in _RATIO_METRICS:
        return 0.01
    return 0.0  # 미지 metric은 보수적으로 정확일치(느슨함 방지)


def _values_match(claimed: float, actual: float, tol: float) -> bool:
    """claimed가 actual의 tol(상대) 안이면 일치. tol=0이면 정확일치."""
    if tol == 0.0:
        return claimed == actual
    if actual == 0.0:
        return abs(claimed) <= tol
    return abs(claimed - actual) / abs(actual) <= tol


def check_row_conservation(
    source_metrics: CSVQualityMetrics, cleaned_metrics: CSVQualityMetrics, manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[Finding]]:
    """행수 보존 체크. dropped가 manifest step params로 100% 설명 안 되면 major."""
    source_rows = source_metrics.row_count
    cleaned_rows = cleaned_metrics.row_count
    dropped = source_rows - cleaned_rows

    explained = 0
    breakdown: dict[str, int] = {}
    for step in manifest.get("steps", []) or []:
        op = str(step.get("op", "unknown"))
        d = int((step.get("params") or {}).get("dropped", 0))
        if d:
            explained += d
            breakdown[op] = breakdown.get(op, 0) + d

    findings: list[Finding] = []
    if dropped < 0:
        findings.append(Finding(
            severity="major", category="row_growth",
            detail=f"cleaned rows ({cleaned_rows}) > source rows ({source_rows}); row growth of {-dropped} rows",
            fix_directive="join/derive로 인한 행 증가를 manifest에 명시하거나 제거하세요.",
        ))
    elif dropped != explained:
        findings.append(Finding(
            severity="major", category="unexplained_row_loss",
            detail=f"unexplained row loss: dropped={dropped} but manifest explains {explained} (breakdown={breakdown})",
            fix_directive="유실 행 전부를 transform_manifest step의 params.dropped로 설명하세요.",
        ))

    delta = {
        "source_rows": source_rows, "cleaned_rows": cleaned_rows, "dropped": dropped,
        "explained_dropped": explained, "drop_reason_breakdown": breakdown,
    }
    return delta, findings


def _load_records(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """CSV를 헤더+dict 레코드 리스트로 읽는다(체크용 공통 로더)."""
    _enc, header, rows = _read_csv_rows(path)
    records: list[dict[str, str]] = []
    for row in rows:
        rec = {col: (row[i] if i < len(row) else "") for i, col in enumerate(header)}
        records.append(rec)
    return header, records


def _passes_filter(rec: dict[str, str], filt: dict[str, Any] | None) -> bool:
    """단순 equality 필터(모든 키가 문자열 일치해야 통과)."""
    if not filt:
        return True
    return all(str(rec.get(k, "")) == str(v) for k, v in filt.items())


def recompute_claim(source_path: Path, backing_stat: dict[str, Any]) -> float | None:
    """원본 CSV에서 backing_stat을 독립 재산출한다(manifest 재실행 아님).

    지원 metric: count / sum / mean / ratio. 필터는 equality만. 산출 불가면 None.
    """
    metric = str(backing_stat.get("metric", "")).lower()
    col = backing_stat.get("col")
    filt = backing_stat.get("filter")
    _header, records = _load_records(source_path)
    matched = [r for r in records if _passes_filter(r, filt)]

    if metric == "count":
        return float(len(matched))
    if metric == "ratio":
        return float(len(matched)) / len(records) if records else 0.0
    if col is None:
        return None
    nums: list[float] = []
    for r in matched:
        raw = r.get(str(col), "")
        try:
            nums.append(float(raw))
        except (TypeError, ValueError):
            continue
    if metric == "sum":
        return float(sum(nums))
    if metric == "mean":
        return float(sum(nums) / len(nums)) if nums else 0.0
    return None


def check_claims(source_path: Path, derived_claims: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Finding]]:
    """derived_claims를 원본에서 재계산해 tolerance 내 일치 여부를 판정한다."""
    recompute: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for claim in derived_claims:
        stat = claim.get("backing_stat") or {}
        metric = str(stat.get("metric", ""))
        claimed = stat.get("value")
        recomputed = recompute_claim(source_path, stat)
        tol = tolerance_for(metric)
        if recomputed is None or claimed is None:
            match = False
        else:
            match = _values_match(float(claimed), recomputed, tol)
        recompute.append({
            "claim_id": claim.get("id"), "claimed_value": claimed,
            "recomputed_value": recomputed, "tolerance": tol, "match": match,
        })
        if not match:
            findings.append(Finding(
                severity="major", category="claim_mismatch",
                detail=f"claim {claim.get('id')}: claimed {claimed} but recomputed {recomputed} (metric={metric}, tol={tol})",
                fix_directive="원본 데이터에서 재산출한 값과 일치하도록 claim을 정정하세요.",
                claim_id=claim.get("id"),
            ))
    return recompute, findings


def _infer_dtype(values: list[str]) -> str:
    """빈칸 제외 실제 셀들을 보고 int/float/str을 추정한다."""
    seen = [v for v in values if v.strip() != ""]
    if not seen:
        return "empty"
    is_int = True
    is_float = True
    for v in seen:
        try:
            int(v)
        except ValueError:
            is_int = False
        try:
            float(v)
        except ValueError:
            is_float = False
    if is_int:
        return "int"
    if is_float:
        return "float"
    return "str"


def check_schema(
    cleaned_metrics: CSVQualityMetrics, schema_expectations: dict[str, str],
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """기대 dtype vs 실측 추정 dtype 대조(int 기대인데 float도 불일치)."""
    diff: list[dict[str, Any]] = []
    findings: list[Finding] = []
    _header, records = _load_records(Path(cleaned_metrics.path))
    for col, expected in schema_expectations.items():
        if col not in cleaned_metrics.columns:
            diff.append({"col": col, "expected_dtype": expected, "actual_dtype": "missing", "ok": False})
            findings.append(Finding(
                severity="major", category="schema_missing_col",
                detail=f"expected column '{col}' missing from cleaned data",
                fix_directive=f"스키마 기대에 맞춰 '{col}' 열을 산출하거나 기대를 수정하세요.",
            ))
            continue
        actual = _infer_dtype([r.get(col, "") for r in records])
        ok = actual == expected or (expected == "float" and actual == "int") or actual == "empty"
        diff.append({"col": col, "expected_dtype": expected, "actual_dtype": actual, "ok": ok})
        if not ok:
            findings.append(Finding(
                severity="major", category="schema_type_mismatch",
                detail=f"column '{col}': expected {expected} but inferred {actual}",
                fix_directive=f"'{col}' 열 타입을 {expected}로 정제하거나 기대 스키마를 정정하세요.",
            ))
    return diff, findings


def check_sanity(
    cleaned_metrics: CSVQualityMetrics, sanity_rules: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """상식 위반 탐지: 음수 금지 열(non_negative_cols), 유니크 키(unique_cols)."""
    checks: list[dict[str, Any]] = []
    findings: list[Finding] = []
    _header, records = _load_records(Path(cleaned_metrics.path))

    for col in sanity_rules.get("non_negative_cols", []) or []:
        bad = 0
        for r in records:
            raw = r.get(col, "")
            try:
                if float(raw) < 0:
                    bad += 1
            except (TypeError, ValueError):
                continue
        status = "fail" if bad else "pass"
        checks.append({"name": f"non_negative[{col}]", "status": status, "col": col,
                       "metric_expected": 0, "metric_actual": bad, "detail": f"{bad} negative values"})
        if bad:
            findings.append(Finding(
                severity="major", category="negative_value",
                detail=f"column '{col}' has {bad} negative value(s)",
                fix_directive=f"'{col}'의 음수 값을 조사·정정하세요(데이터 오류 가능).",
            ))

    for col in sanity_rules.get("unique_cols", []) or []:
        seen: set[str] = set()
        dups = 0
        for r in records:
            v = r.get(col, "")
            if v in seen:
                dups += 1
            else:
                seen.add(v)
        status = "fail" if dups else "pass"
        checks.append({"name": f"unique[{col}]", "status": status, "col": col,
                       "metric_expected": 0, "metric_actual": dups, "detail": f"{dups} duplicate keys"})
        if dups:
            findings.append(Finding(
                severity="major", category="duplicate_key",
                detail=f"unique column '{col}' has {dups} duplicate key(s)",
                fix_directive=f"'{col}'의 중복 키를 dedup하거나 유니크 가정을 수정하세요.",
            ))
    return checks, findings
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_data_quality_checks.py -q`
  Expected: `14 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/data_quality.py tests/research/test_data_quality_checks.py
git commit -m "feat(research): data_quality 결정론 체크 세트(행수/claim재계산/스키마/sanity)"
```

---

### Task 11: `run_data_quality` — 체크 집계 + Verdict 재계산 + 아티팩트

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\data_quality.py` (파일 끝에 집계 함수 추가)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_data_quality_verdict.py`

**Interfaces:**
- Consumes: Task 10 체크 함수들, `validate_csv`, `_sha256_of_file`; `Verdict`·`Finding`(Slice 1 `types.py`); `write_json`(`autoagent/artifacts.py`).
- Produces: `def run_data_quality(stage_out: dict, run_dir: Path, *, verifier_agent: str) -> Verdict` — 코드 실측만(모델 0회). stage_out은 `cleaned_files[]`/`transform_manifest`/`derived_claims[]`/`schema_expectations`/`sanity_rules`. `run_dir/c_data_quality.json` 영속. **status 재계산**: checks 전부∈{pass,skipped} AND recompute[].match 전부 AND schema_diff 전부 ok → `pass`; 파일 못 읽음(error) 있으면 `blocked`; 그 외 위반은 `needs_changes`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_data_quality_verdict.py`:
```python
"""run_data_quality 집계·verdict 재계산 단위테스트(모델 0회)."""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.research.data_quality import run_data_quality


def _w(d: Path, name: str, text: str) -> Path:
    p = d / name
    p.write_bytes(text.encode("utf-8"))
    return p


def _stage_out(source: Path, cleaned: Path, **over) -> dict:
    base = {
        "cleaned_files": [{"path": str(cleaned), "source_dump_path": str(source)}],
        "transform_manifest": {"steps": []},
        "derived_claims": [],
        "schema_expectations": {},
    }
    base.update(over)
    return base


def test_clean_passthrough_is_pass(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "id,amt\n1,10\n2,20\n")
    cln = _w(tmp_path, "c.csv", "id,amt\n1,10\n2,20\n")
    out = _stage_out(src, cln, schema_expectations={"id": "int", "amt": "int"})
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "pass"
    assert v.adapter == "data_quality"
    assert v.stage_id == "c"
    assert v.findings == []
    assert (tmp_path / "c_data_quality.json").exists()
    raw = json.loads((tmp_path / "c_data_quality.json").read_text(encoding="utf-8"))
    assert raw["overall_ok"] is True
    assert raw["adapter"] == "data_quality"
    assert "provenance" in raw


def test_claim_mismatch_downgrades_to_needs_changes(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "region,amt\nseoul,10\nseoul,30\n")
    cln = _w(tmp_path, "c.csv", "region,amt\nseoul,10\nseoul,30\n")
    out = _stage_out(src, cln,
        derived_claims=[{"id": "k1", "text": "sum", "backing_stat": {"metric": "sum", "col": "amt", "value": 999}}])
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "needs_changes"
    assert any(f.claim_id == "k1" for f in v.findings)


def test_unreadable_source_is_blocked(tmp_path: Path) -> None:
    src = tmp_path / "junk.csv"
    src.write_bytes(b"\x81\x00\xff\xfe\x9d\x8f\n")
    cln = _w(tmp_path, "c.csv", "id\n1\n")
    out = _stage_out(src, cln)
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "blocked"
    assert any(f.category == "file_read_error" for f in v.findings)


def test_unexplained_drop_needs_changes(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "id\n1\n2\n3\n4\n")
    cln = _w(tmp_path, "c.csv", "id\n1\n")
    out = _stage_out(src, cln)
    v = run_data_quality(out, tmp_path, verifier_agent="code")
    assert v.status == "needs_changes"
    assert any(f.category == "unexplained_row_loss" for f in v.findings)
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_data_quality_verdict.py -q`
  Expected: `ImportError: cannot import name 'run_data_quality'` (collection error).

- [ ] **Step 3: 최소 구현 — import 상단 수정** — `autoagent/research/data_quality.py`의 기존 `from typing import Any` 줄을 다음으로 교체:
```python
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from autoagent.research.types import Verdict
```

- [ ] **Step 4: 최소 구현 — 집계 함수** — `autoagent/research/data_quality.py` 끝에 추가:
```python
def run_data_quality(stage_out: dict[str, Any], run_dir: Path, *, verifier_agent: str) -> "Verdict":
    """c 스테이지 data_quality 검증(코드 실측만, 모델 0회).

    stage_out에서 cleaned_files/manifest/claims/schema를 읽어 4대 체크를 돌리고, 코드가
    findings를 집계해 status를 재계산한다. 파일을 못 읽으면 blocked, 위반 있으면
    needs_changes, 전부 통과면 pass. verdict raw를 c_data_quality.json으로 남긴다.
    verifier_agent는 계약상 받되 여기선 'code' 고정(모델 미호출) — provenance에만 기록.
    """
    from autoagent.artifacts import write_json
    from autoagent.data.csv_validator import _sha256_of_file, validate_csv
    from autoagent.research.types import Verdict

    all_findings: list[Finding] = []
    checks: list[dict[str, Any]] = []
    recompute_all: list[dict[str, Any]] = []
    schema_diff_all: list[dict[str, Any]] = []
    row_delta: dict[str, Any] = {}
    provenance: dict[str, Any] = {"files_read": [], "verifier_agent": verifier_agent}
    has_error = False

    manifest = stage_out.get("transform_manifest") or {"steps": []}
    schema_expectations = stage_out.get("schema_expectations") or {}
    derived_claims = stage_out.get("derived_claims") or []

    for entry in stage_out.get("cleaned_files") or []:
        cleaned_path = Path(entry.get("path", ""))
        source_path = Path(entry.get("source_dump_path", ""))
        try:
            source_metrics = validate_csv(source_path)
            cleaned_metrics = validate_csv(cleaned_path)
            provenance["files_read"].append(str(cleaned_path))
            provenance["files_read"].append(str(source_path))
            provenance.setdefault("hashes", {})[str(source_path)] = _sha256_of_file(source_path)
            provenance["hashes"][str(cleaned_path)] = _sha256_of_file(cleaned_path)
        except (ValueError, FileNotFoundError, OSError) as exc:
            has_error = True
            checks.append({"name": "file_read", "status": "error", "file": str(cleaned_path),
                           "detail": f"{type(exc).__name__}: {exc}"})
            all_findings.append(Finding(
                severity="critical", category="file_read_error",
                detail=f"cannot read {cleaned_path} / {source_path}: {exc}",
                fix_directive="입력 CSV 경로·인코딩을 확인하세요(조용한 skip 금지).",
            ))
            continue

        delta, rc_findings = check_row_conservation(source_metrics, cleaned_metrics, manifest)
        row_delta = delta
        all_findings.extend(rc_findings)
        checks.append({"name": "row_conservation", "status": "pass" if not rc_findings else "fail",
                       "file": str(cleaned_path), "detail": str(delta)})

        diff, sc_findings = check_schema(cleaned_metrics, schema_expectations)
        schema_diff_all.extend(diff)
        all_findings.extend(sc_findings)
        checks.append({"name": "schema", "status": "pass" if not sc_findings else "fail", "file": str(cleaned_path)})

        sanity_rules = stage_out.get("sanity_rules") or {}
        if sanity_rules:
            sanity_checks, sn_findings = check_sanity(cleaned_metrics, sanity_rules)
            checks.extend(sanity_checks)
            all_findings.extend(sn_findings)
        else:
            checks.append({"name": "sanity", "status": "skipped", "detail": "no sanity_rules"})

        rc_list, cl_findings = check_claims(source_path, derived_claims)
        recompute_all.extend(rc_list)
        all_findings.extend(cl_findings)
        checks.append({"name": "claim_recompute", "status": "pass" if not cl_findings else "fail",
                       "file": str(source_path)})

    checks_ok = all(c["status"] in {"pass", "skipped"} for c in checks)
    recompute_ok = all(r["match"] for r in recompute_all)
    schema_ok = all(d["ok"] for d in schema_diff_all)
    overall_ok = checks_ok and recompute_ok and schema_ok and not has_error

    if has_error:
        status: str = "blocked"
    elif overall_ok:
        status = "pass"
    else:
        status = "needs_changes"

    raw = {
        "schema_version": CHECK_SET_VERSION, "adapter": "data_quality", "stage_id": "c",
        "overall_ok": overall_ok, "checks": checks, "recompute": recompute_all,
        "row_delta": row_delta, "schema_diff": schema_diff_all, "provenance": provenance,
    }
    write_json(run_dir / "c_data_quality.json", raw)
    return Verdict(status=status, adapter="data_quality", stage_id="c", findings=all_findings, raw=raw)
```

- [ ] **Step 5: 통과 확인** — Run: `python -m pytest tests/research/test_data_quality_verdict.py -q`
  Expected: `4 passed`. 회귀: `python -m pytest tests/ -q` → 누적 `29 passed`(5+6+14+4) + Slice 1의 27이 이 브랜치엔 없다면 이 브랜치 신설분만 수집됨.

- [ ] **Step 6: commit**
```bash
git add autoagent/research/data_quality.py tests/research/test_data_quality_verdict.py
git commit -m "feat(research): run_data_quality 집계+verdict 재계산+아티팩트"
```

---

### Task 12: `adapters.verify`에 `data_quality` 분기 배선

Slice 1의 디스패처 `verify(adapter, stage_out, run_dir, *, verifier_agent, config)`에 `data_quality` 분기만 더한다(`crossmodel` 분기 불변).

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\adapters.py` (`verify` 디스패처 내부)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_adapters_dispatch.py`

**Interfaces:**
- Consumes: `run_data_quality`(Task 11); `Verdict`(Slice 1 `types.py`); Slice 1 `verify` 디스패처.
- Produces: `verify(adapter="data_quality", ...)` 경로(모델 0회, `run_data_quality`로 위임).

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_adapters_dispatch.py`:
```python
"""adapters.verify의 data_quality 디스패치 배선 테스트(모델 0회)."""
from __future__ import annotations

from pathlib import Path

from autoagent.research.adapters import verify


def _w(d: Path, name: str, text: str) -> Path:
    p = d / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_verify_dispatches_data_quality(tmp_path: Path) -> None:
    src = _w(tmp_path, "s.csv", "id,amt\n1,10\n2,20\n")
    cln = _w(tmp_path, "c.csv", "id,amt\n1,10\n2,20\n")
    stage_out = {
        "cleaned_files": [{"path": str(cln), "source_dump_path": str(src)}],
        "transform_manifest": {"steps": []},
        "derived_claims": [],
        "schema_expectations": {"id": "int", "amt": "int"},
    }
    v = verify("data_quality", stage_out, tmp_path, verifier_agent="code", config=None)
    assert v.adapter == "data_quality"
    assert v.stage_id == "c"
    assert v.status == "pass"
    assert (tmp_path / "c_data_quality.json").exists()
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_adapters_dispatch.py -q`
  Expected: FAIL — `data_quality` 분기 부재로 Slice 1 디스패처가 `SystemExit`.

- [ ] **Step 3: 최소 구현** — `autoagent/research/adapters.py`의 `verify` 본문에서, `if adapter in {"data_quality", "source_grounding"}: raise SystemExit(...)` 라인을 삭제하고, `crossmodel` 분기 다음·`source_grounding`/미지 fallback 앞에 아래를 삽입:
```python
    if adapter == "data_quality":
        # data_quality: 코드 실측 전용(모델 0회). config·verifier_agent 모델 미사용.
        from autoagent.research.data_quality import run_data_quality
        return run_data_quality(stage_out, run_dir, verifier_agent=verifier_agent)
    if adapter == "source_grounding":
        raise SystemExit("Adapter 'source_grounding' not implemented in this slice (Slice 3).")
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_adapters_dispatch.py -q`
  Expected: `1 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/adapters.py tests/research/test_adapters_dispatch.py
git commit -m "feat(research): adapters.verify에 data_quality 분기 배선"
```

---

### Task 13: c 스테이지 Codex 리서처 프롬프트

c 스테이지(CSV 정제)는 리서처=Codex, 검증기=코드(모델 0회). 로컬 파일만 대상이므로 웹 불필요.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\c_codex_research.md`
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\artifacts.py` (`PROMPT_ALIASES`에 별칭 1줄)
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py` (`STAGE_ADAPTER`/`STAGE_PROMPT`에 c 엔트리 배선)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_c_prompt_render.py`

**Interfaces:**
- Consumes: `render_template(name, values)`, `PROMPT_ALIASES`(`autoagent/artifacts.py`), Slice 1 `run_stage_loop`의 c 분기(`_run_stage_c_verify`)·`STAGE_ADAPTER`/`STAGE_PROMPT`.
- Produces: 별칭 `"c_codex_research.md" -> "research/c_codex_research.md"`; `STAGE_ADAPTER["c"]="data_quality"`·`STAGE_PROMPT["c"]="c_codex_research.md"` 배선(오케스트레이터 c 순회 시 KeyError 방지); 출력 계약(`DATA_QUALITY_OUTPUT` JSON: `cleaned_files/transform_manifest/derived_claims/schema_expectations/sanity_rules`).

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_c_prompt_render.py`:
```python
"""c 스테이지 codex 리서처 프롬프트의 render 검증(dry-run 대체 단위테스트)."""
from __future__ import annotations

from autoagent.artifacts import PROMPT_ALIASES, render_template


def test_c_prompt_alias_registered() -> None:
    assert PROMPT_ALIASES["c_codex_research.md"] == "research/c_codex_research.md"


def test_c_prompt_renders_all_placeholders() -> None:
    rendered = render_template(
        "c_codex_research.md",
        {"WORKSPACE": "C:/ws", "REQUEST": "고객 CSV 정제", "SEED_PIN": '{"currency": "KRW"}',
         "CSV_PATHS": "data/customers.csv", "OUTER_PASS": "1", "INNER_ROUND": "1", "PRIOR_FEEDBACK": ""},
    )
    assert "{{" not in rendered
    assert "transform_manifest" in rendered
    assert "derived_claims" in rendered
    assert "schema_expectations" in rendered
    assert "DATA_QUALITY_OUTPUT" in rendered
    assert "웹" in rendered
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_c_prompt_render.py -q`
  Expected: FAIL — `KeyError: 'c_codex_research.md'`(별칭 미등록) + `FileNotFoundError`(프롬프트 부재).

- [ ] **Step 3: 최소 구현 — 프롬프트** — `prompts/research/c_codex_research.md`:
```markdown
# 역할

당신은 리서치 하네스 c 스테이지(CSV 데이터 정제)의 리서처 Codex입니다.
이 스테이지는 **로컬 파일만** 다룹니다. 웹 검색/fetch는 사용하지 않습니다(당신의
샌드박스는 네트워크가 차단되어 있고, 웹 리서치는 다른 스테이지에서 Claude가 수행합니다).

# 작업공간
{{WORKSPACE}}

# 원본 사용자 요청
{{REQUEST}}

# 고정 시드(seed pin, 변경 금지)
바깥 루프 불변식입니다. 아래 식별자·통화·기간·단위를 그대로 따르세요.
```json
{{SEED_PIN}}
```

# 입력 CSV 경로
{{CSV_PATHS}}

# 루프 컨텍스트
- outer_pass: {{OUTER_PASS}}
- inner_round: {{INNER_ROUND}}
- 직전 검증 피드백(있으면 반영):
{{PRIOR_FEEDBACK}}

# 작업
입력 CSV를 정제하고, 정제 과정을 **완전히 추적 가능한 manifest**로 남기세요.
검증기는 코드 실측(모델 아님)이라 다음을 **원본에서 독립 재계산**합니다:
행수 보존, claim 수치, 스키마 타입, sanity.

규칙:
- 원본을 훼손하지 말고 정제 결과를 **새 파일**로 쓰세요(source_dump_path는 원본 유지).
- 유실되는 모든 행은 manifest step의 `params.dropped`로 **정확히** 설명하세요.
- 수치 claim은 반드시 `backing_stat`(metric/col/filter/value)을 붙이세요.
  합계·행수·카운트는 **정확일치**, 비율·CAGR만 1% 오차까지 허용됩니다.
- 인코딩이 깨지면 조용히 건너뛰지 말고 그 사실을 보고하세요(cp949 가능성).
- 무엇도 커밋/푸시/업로드하지 마세요.

# 출력 계약
결과 첫 줄: `STAGE_C_STATUS: completed` (또는 `partial` / `blocked`)
그다음, 아래 스키마의 JSON을 펜스로 정확히 산출하세요(코드가 이 블록만 파싱):

DATA_QUALITY_OUTPUT
```json
{
  "cleaned_files": [{"path": "정제결과.csv", "source_dump_path": "원본덤프.csv"}],
  "transform_manifest": {"steps": [{"op": "dedup", "target_cols": ["id"], "params": {"dropped": 0}}]},
  "derived_claims": [{"id": "c1", "text": "설명", "backing_stat": {"metric": "sum", "value": 0, "col": "amt", "filter": {}}}],
  "schema_expectations": {"id": "int", "amt": "float"},
  "sanity_rules": {"non_negative_cols": ["amt"], "unique_cols": ["id"]}
}
```

# 자체 리뷰
산출을 마치기 전, manifest의 dropped 합이 (원본 행수 − 정제 행수)와 정확히 같은지,
모든 backing_stat이 원본에서 재현 가능한지 스스로 점검하세요. 불일치는
`SELF_REVIEW:` 절에 명시하세요. 독립 코드 검증이 뒤이어 수행됩니다.
```

- [ ] **Step 4: 최소 구현 — 별칭** — `autoagent/artifacts.py`의 `PROMPT_ALIASES` 끝(마지막 엔트리 뒤, `}` 앞)에 추가:
```python
    "c_codex_research.md": "research/c_codex_research.md",
```

- [ ] **Step 5: 최소 구현 — 오케스트레이터 c 배선** — `autoagent/workflows/research.py`의 `STAGE_ADAPTER`/`STAGE_PROMPT`에 c 엔트리를 추가해 오케스트레이터가 c를 순회할 때 KeyError를 내지 않게 한다(Slice 1 `run_stage_loop`의 `if stage == "c":` 분기가 `_run_stage_c_verify`로 코드 검증기를 탄다 — crossmodel 프롬프트/모델 미호출):
```python
STAGE_ADAPTER = {"a": "crossmodel", "b": "crossmodel", "c": "data_quality", "d": "source_grounding", "derive": "crossmodel"}
STAGE_PROMPT = {"a": "a_researcher.md", "b": "b_market_researcher.md", "c": "c_codex_research.md", "d": "d_fact_report.md", "derive": "derive.md"}
```
  주: d 엔트리는 Task 18이 이미 채웠다면 중복 없이 c만 더한다(둘 다 없으면 위처럼 한 번에 확정). c는 `STAGE_VERIFIER_PROMPT`에 넣지 않는다(코드 검증기라 crossmodel 프롬프트를 안 씀).

- [ ] **Step 6: 통과 확인** — Run: `python -m pytest tests/research/test_c_prompt_render.py -q`
  Expected: `2 passed`. 이어 c 순회 KeyError 부재를 단위로 확인:
  `python -c "from autoagent.workflows.research import STAGE_ADAPTER, STAGE_PROMPT; assert STAGE_ADAPTER['c']=='data_quality' and STAGE_PROMPT['c']=='c_codex_research.md'; print('c wired')"`
  Expected: `c wired`.

- [ ] **Step 7: commit**
```bash
git add prompts/research/c_codex_research.md autoagent/artifacts.py autoagent/workflows/research.py tests/research/test_c_prompt_render.py
git commit -m "feat(research): c 스테이지 codex 리서처 프롬프트+별칭+오케스트레이터 c 배선(data_quality)"
```

---

### Task 14: `choose_researcher`의 c 스테이지 계약 회귀 락

Slice 1이 신설한 `choose_researcher(stage)`가 c 스테이지에서 `(codex, claude, reason)`을 주는지 회귀 테스트로 못박는다(Slice 1 코드는 수정하지 않음 — 소비만).

**Files:**
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_routing_c.py`

**Interfaces:**
- Consumes: `choose_researcher(stage)`(Slice 1 `autoagent/routing.py`).
- Produces: 없음(회귀 락 전용).

- [ ] **Step 1: 검증 테스트 작성** — `tests/research/test_routing_c.py`:
```python
"""c 스테이지 라우팅 계약 회귀 락(Slice 1 choose_researcher 소비)."""
from __future__ import annotations

from autoagent.routing import choose_researcher


def test_c_stage_researcher_is_codex() -> None:
    researcher, verifier, reason = choose_researcher("c")
    assert researcher == "codex"
    assert verifier == "claude"  # c의 검증기 모델은 반대편이나 실제 호출은 0회(코드 검증)
    assert isinstance(reason, str) and reason


def test_web_stages_researcher_is_claude() -> None:
    for stage in ("a", "b", "d", "derive"):
        researcher, verifier, _ = choose_researcher(stage)
        assert researcher == "claude"
        assert verifier == "codex"
```

- [ ] **Step 2: 통과 확인** — Run: `python -m pytest tests/research/test_routing_c.py -q`
  Expected: `2 passed`(Slice 1 병합 전제). 미병합이면 `ImportError: cannot import name 'choose_researcher'` → Slice 1 선행 필요.

- [ ] **Step 3: 전체 스위트 확인** — Run: `python -m pytest tests/ -q`
  Expected: Slice 1(29) + Slice 2 신설(5+6+14+4+1+2+2 = 34) 합산. 이 브랜치가 Slice 1 위에 쌓였다면 `63 passed`.

- [ ] **Step 4: commit**
```bash
git add tests/research/test_routing_c.py
git commit -m "test(research): c 스테이지 라우팅 계약 회귀 락"
```

---

## Slice 3 — `source_grounding` 어댑터 + d 스테이지 + 스냅샷 파이프라인

Slice 1의 타입/`adapters.verify` 디스패처와 Slice 2를 전제로, `adapters.verify`에 `"source_grounding"` 분기를, 스테이지 순회에 `d`를 끼운다. 웹 fetch는 Claude만 수행해 `runs/sources/*.txt` 스냅샷으로 저장하고, 이후 대조(Codex 검증기 포함)는 스냅샷만 읽는다.

---

### Task 15: 스냅샷 저장 파이프라인 (`autoagent/research/snapshots.py`)

Claude가 WebFetch로 긁은 원문 텍스트를 받아 결정론적으로 스냅샷 파일 + 메타(url·fetch_ts·http_status·sha256)를 저장하는 순수 코드 층. Claude 호출 자체는 Task 18에서 배선.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\snapshots.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_snapshots.py`

**Interfaces:**
- Consumes: `autoagent.artifacts.write_text`, `autoagent.artifacts.write_json`.
- Produces:
  - `@dataclass SourceSnapshot(ref_id: str, url: str, snapshot_path: str, fetch_ts: str, http_status: int, sha256: str, char_count: int)`
  - `def slugify_ref(ref_id: str) -> str` — 안전한 파일명 세그먼트(경로이탈·비ASCII 차단, 빈 결과면 `ValueError`).
  - `def save_snapshot(sources_dir: Path, ref_id: str, url: str, fetched_text: str, *, http_status: int, fetch_ts: str | None = None) -> SourceSnapshot`.
  - `def write_sources_manifest(run_dir: Path, snapshots: list[SourceSnapshot]) -> Path`.
  - `def load_snapshot_text(sources_dir: Path, ref_id: str) -> str`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_snapshots.py`:
```python
"""snapshots 결정론 층 테스트(스냅샷 저장·메타·되읽기).

Claude WebFetch 원문을 받아 runs/sources/*.txt로 고정하는 순수 코드라 pytest로 못박는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoagent.research.snapshots import (
    SourceSnapshot, load_snapshot_text, save_snapshot, slugify_ref, write_sources_manifest,
)


def test_slugify_ref_keeps_safe_ascii():
    assert slugify_ref("S1") == "s1"
    assert slugify_ref("src_2-a") == "src_2-a"


def test_slugify_ref_strips_path_traversal_and_nonascii():
    assert "/" not in slugify_ref("../etc/passwd")
    assert "\\" not in slugify_ref("a\\b")
    out = slugify_ref("회사::/../x")
    assert out and "/" not in out and "\\" not in out and ".." not in out


def test_slugify_ref_rejects_empty_result():
    with pytest.raises(ValueError):
        slugify_ref("///")


def test_save_snapshot_writes_file_and_computes_hash(tmp_path: Path):
    sources = tmp_path / "sources"
    snap = save_snapshot(sources, "S1", "https://example.com/a",
                         "Acme reported revenue of 12M in 2024.",
                         http_status=200, fetch_ts="2026-07-30T00:00:00Z")
    assert (sources / "s1.txt").read_text(encoding="utf-8") == "Acme reported revenue of 12M in 2024."
    assert snap.snapshot_path == "sources/s1.txt"
    assert snap.http_status == 200
    assert snap.char_count == len("Acme reported revenue of 12M in 2024.")
    assert len(snap.sha256) == 64
    assert load_snapshot_text(sources, "S1") == "Acme reported revenue of 12M in 2024."


def test_save_snapshot_default_fetch_ts_is_utc_iso(tmp_path: Path):
    snap = save_snapshot(tmp_path, "s2", "u", "body", http_status=200)
    assert snap.fetch_ts.endswith("Z") and "T" in snap.fetch_ts


def test_write_sources_manifest_roundtrips(tmp_path: Path):
    snaps = [
        save_snapshot(tmp_path / "src", "s1", "u1", "x", http_status=200, fetch_ts="2026-07-30T00:00:00Z"),
        save_snapshot(tmp_path / "src", "s2", "u2", "yy", http_status=404, fetch_ts="2026-07-30T00:00:00Z"),
    ]
    manifest = write_sources_manifest(tmp_path, snaps)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert [s["ref_id"] for s in data["sources"]] == ["s1", "s2"]
    assert data["sources"][1]["http_status"] == 404
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_snapshots.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.snapshots'` (collection error).

- [ ] **Step 3: 최소 구현** — `autoagent/research/snapshots.py`:
```python
"""소스 스냅샷 저장 층(§2.1).

Claude WebFetch/defuddle이 긁어온 원문 텍스트를 결정론적으로 runs/sources/*.txt에
고정하고 fetch 메타(url·fetch_ts·http_status·sha256·char_count)를 남긴다. 이후 모든
대조(Codex 검증기 포함)는 재fetch 없이 이 스냅샷만 읽어 링크썩음·본문변동을 배제한다.
순수 함수라 pytest로 못박는다(모델 호출 없음).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoagent.artifacts import write_json, write_text

# 파일명 세그먼트 허용 문자: 영숫자·하이픈·언더스코어. 나머지는 '_'로 접는다.
_SAFE_SEGMENT = re.compile(r"[^a-z0-9_-]+")


@dataclass
class SourceSnapshot:
    """한 소스의 스냅샷 파일 + fetch 메타(sources_manifest.json 항목이자 검증 입력)."""

    ref_id: str
    url: str
    snapshot_path: str   # run_dir 기준 상대경로(예: "sources/s1.txt")
    fetch_ts: str        # ISO8601 UTC
    http_status: int
    sha256: str
    char_count: int


def slugify_ref(ref_id: str) -> str:
    """ref_id를 안전한 파일명 세그먼트로 정규화한다(경로이탈·비ASCII 차단).

    소문자화 후 [a-z0-9_-] 외 문자를 '_'로 접고 양끝 '_'·'-'를 다듬는다. '..'가
    남지 않도록 점은 애초에 허용문자에서 빠져 '_'가 된다. 결과가 비면(전부 불법문자)
    조용히 빈 파일명을 쓰지 않고 ValueError를 던진다(정직한 에러).
    """
    lowered = ref_id.strip().lower()
    slug = _SAFE_SEGMENT.sub("_", lowered).strip("_-")
    if not slug:
        raise ValueError(f"ref_id로 안전한 파일명을 만들 수 없음: {ref_id!r}")
    return slug


def save_snapshot(
    sources_dir: Path, ref_id: str, url: str, fetched_text: str, *,
    http_status: int, fetch_ts: str | None = None,
) -> SourceSnapshot:
    """원문 텍스트를 sources_dir/<slug>.txt에 저장하고 SourceSnapshot을 만든다.

    sha256/char_count는 저장한 원문 그대로에서 계산한다(부분문자열 대조의 기준).
    fetch_ts 미지정이면 지금(UTC) 시각을 ISO8601로 채운다. snapshot_path는 run_dir
    기준 상대경로("sources/<slug>.txt")로 넣어 아티팩트 이식성을 유지한다.
    """
    slug = slugify_ref(ref_id)
    path = sources_dir / f"{slug}.txt"
    write_text(path, fetched_text)  # utf-8, newline="\n"
    sha256 = hashlib.sha256(fetched_text.encode("utf-8")).hexdigest()
    ts = fetch_ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SourceSnapshot(
        ref_id=ref_id, url=url, snapshot_path=f"sources/{slug}.txt", fetch_ts=ts,
        http_status=int(http_status), sha256=sha256, char_count=len(fetched_text),
    )


def write_sources_manifest(run_dir: Path, snapshots: list[SourceSnapshot]) -> Path:
    """스냅샷 메타 배열을 run_dir/sources_manifest.json에 기록하고 경로를 반환한다."""
    path = run_dir / "sources_manifest.json"
    write_json(path, {"sources": [asdict(s) for s in snapshots]})
    return path


def load_snapshot_text(sources_dir: Path, ref_id: str) -> str:
    """저장된 스냅샷 원문을 utf-8로 되읽는다(검증 코드층의 부분문자열 대조용)."""
    slug = slugify_ref(ref_id)
    return (sources_dir / f"{slug}.txt").read_text(encoding="utf-8")
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_snapshots.py -q`
  Expected: `6 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/snapshots.py tests/research/test_snapshots.py
git commit -m "feat(research): 소스 스냅샷 저장 층(runs/sources/*.txt + 메타)"
```

---

### Task 16: 결정론 grounding 검사 (`autoagent/research/grounding.py`)

스펙 §4.3-①: 코드가 먼저 결정적 실측 — `fabricated_sources`(ref∉sources), `dead_sources`(status≠200 or 본문 빈), `orphan_claims`(fact인데 무인용), `matched_quote ⊆ fetched_text` 부분문자열. 모델 없이 순수 코드.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\grounding.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_grounding.py`

**Interfaces:**
- Consumes: `autoagent.research.types.Finding`(Slice 1).
- Produces:
  - `def normalize_for_match(text: str) -> str` — 공백/대소문자 정규화.
  - `def quote_is_grounded(matched_quote: str, fetched_text: str) -> bool` — 부분문자열 판정(빈 quote=False).
  - `@dataclass DeterministicGrounding(fabricated_sources, dead_sources, orphan_claims, unverified_quotes, findings)`(모두 list, default_factory).
  - `def run_deterministic_checks(stage_out: dict, snapshot_texts: dict[str, str]) -> DeterministicGrounding`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_grounding.py`:
```python
"""결정론 grounding 검사 테스트(§4.3-①).

matched_quote ⊆ fetched_text 부분문자열·fabricated/dead/orphan 실측은 모델 없는
순수 코드라 pytest로 못박는다. 근거 날조를 부분문자열로 차단.
"""
from __future__ import annotations

from autoagent.research.grounding import (
    normalize_for_match, quote_is_grounded, run_deterministic_checks,
)


def test_normalize_collapses_whitespace_and_case():
    assert normalize_for_match("Acme   Corp\n reported") == "acme corp reported"


def test_quote_grounded_true_when_substring_present():
    fetched = "In 2024 Acme reported revenue of 12M USD across all regions."
    assert quote_is_grounded("Acme reported revenue of 12M", fetched) is True


def test_quote_grounded_ignores_whitespace_and_case_diff():
    fetched = "Acme reported\nrevenue of  12M"
    assert quote_is_grounded("acme REPORTED revenue of 12m", fetched) is True


def test_quote_not_grounded_when_absent():
    fetched = "Acme reported revenue of 12M USD."
    assert quote_is_grounded("Acme projects revenue of 50M by 2030", fetched) is False


def test_empty_quote_is_not_grounded():
    assert quote_is_grounded("", "any text") is False
    assert quote_is_grounded("   ", "any text") is False


def _stage_out():
    return {
        "claims": [
            {"id": "c1", "text": "Acme revenue was 12M in 2024.", "kind": "fact",
             "cited_source_refs": ["s1"], "quoted_span": "revenue of 12M"},
            {"id": "c2", "text": "Acme will dominate by 2030.", "kind": "fact",
             "cited_source_refs": [], "quoted_span": ""},
            {"id": "c3", "text": "Acme cites a ghost.", "kind": "fact",
             "cited_source_refs": ["s9"], "quoted_span": "ghost quote"},
            {"id": "c4", "text": "We recommend expanding.", "kind": "recommendation",
             "cited_source_refs": [], "quoted_span": ""},
            {"id": "c5", "text": "From dead source.", "kind": "fact",
             "cited_source_refs": ["s2"], "quoted_span": "anything"},
        ],
        "sources": [
            {"ref_id": "s1", "url": "u1", "fetched_text": "In 2024 Acme reported revenue of 12M USD.", "http_status": 200},
            {"ref_id": "s2", "url": "u2", "fetched_text": "", "http_status": 404},
        ],
    }


def test_orphan_fact_detected():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD."})
    assert "c2" in res.orphan_claims
    assert "c4" not in res.orphan_claims


def test_fabricated_source_detected():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD."})
    assert "s9" in res.fabricated_sources


def test_dead_source_detected():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    assert "s2" in res.dead_sources


def test_unverified_quote_when_not_substring():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    assert "c1" not in res.unverified_quotes
    so = _stage_out()
    so["claims"][0]["quoted_span"] = "revenue of 999B"
    res2 = run_deterministic_checks(so, {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    assert "c1" in res2.unverified_quotes


def test_findings_carry_severity_and_claim_id():
    res = run_deterministic_checks(_stage_out(), {"s1": "In 2024 Acme reported revenue of 12M USD.", "s2": ""})
    cats = {(f.category, f.severity) for f in res.findings}
    assert ("fabricated_source", "critical") in cats
    assert ("dead_source", "critical") in cats
    assert ("orphan_claim", "major") in cats
    orphan = [f for f in res.findings if f.category == "orphan_claim"]
    assert orphan and orphan[0].claim_id == "c2"
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_grounding.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.grounding'` (collection error).

- [ ] **Step 3: 최소 구현** — `autoagent/research/grounding.py`:
```python
"""결정론 source-grounding 검사(§4.3-①).

모델 없이 코드로 fabricated/dead/orphan/부분문자열을 실측한다. matched_quote가
스냅샷 원문(fetched_text)의 부분문자열이 아니면 근거 날조로 보고 unsupported를 강제한다.
정규화는 공백·대소문자 차이만 흡수하고(스냅샷 줄바꿈 차이) 내용은 보존한다(느슨화 금지).
결과는 Slice 1의 Finding으로 집계해 어댑터 병합(Task 17)이 소비한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from autoagent.research.types import Finding


_WS = re.compile(r"\s+")


def normalize_for_match(text: str) -> str:
    """부분문자열 대조용 정규화: 소문자화 + 연속 공백류를 단일 스페이스로.

    스냅샷 저장 시 개행/들여쓰기 차이만 흡수한다. 구두점·숫자·단어는 건드리지 않아
    의미 왜곡(paraphrase)은 그대로 불일치로 남긴다(느슨화로 날조를 통과시키지 않음).
    """
    return _WS.sub(" ", text.strip().lower())


def quote_is_grounded(matched_quote: str, fetched_text: str) -> bool:
    """정규화 후 matched_quote가 fetched_text의 부분문자열인지 판정한다.

    빈 quote(또는 공백뿐)는 근거 없음(False) — '인용 없이 supported' 날조를 차단한다.
    """
    q = normalize_for_match(matched_quote)
    if not q:
        return False
    return q in normalize_for_match(fetched_text)


@dataclass
class DeterministicGrounding:
    """코드 결정론 검사 결과(§4.3-①). 어댑터 병합이 모델 verdict와 합칠 원자료."""

    fabricated_sources: list[str] = field(default_factory=list)
    dead_sources: list[str] = field(default_factory=list)
    orphan_claims: list[str] = field(default_factory=list)
    unverified_quotes: list[str] = field(default_factory=list)  # quote⊄fetched_text인 claim id
    findings: list[Finding] = field(default_factory=list)


def run_deterministic_checks(stage_out: dict, snapshot_texts: dict[str, str]) -> DeterministicGrounding:
    """§4.3-① 결정적 실측. stage_out(claims/sources)와 {ref_id: 스냅샷원문}을 받는다.

    네 검사: (1)fabricated=claim이 인용한 ref가 sources에 없음, (2)dead=status≠200이거나
    본문 빈 source, (3)orphan=kind==fact인데 인용 없음(추천/추론 면제), (4)unverified_quote=
    quoted_span이 인용 소스 스냅샷의 부분문자열이 아님. snapshot_texts를 우선 쓰되 없으면
    stage_out.sources[].fetched_text로 폴백한다(호출부가 스냅샷 dict를 안 넘겨도 동작).
    """
    sources = {s["ref_id"]: s for s in stage_out.get("sources", [])}
    texts = dict(snapshot_texts)
    for ref, s in sources.items():
        texts.setdefault(ref, s.get("fetched_text", ""))

    res = DeterministicGrounding()
    seen_fabricated: set[str] = set()
    seen_dead: set[str] = set()

    # (2) dead sources: status≠200 or 본문 빈.
    for ref, s in sources.items():
        body = texts.get(ref, "") or ""
        if int(s.get("http_status", 0)) != 200 or not body.strip():
            if ref not in seen_dead:
                seen_dead.add(ref)
                res.dead_sources.append(ref)
                res.findings.append(Finding(
                    severity="critical", category="dead_source",
                    detail=f"소스 {ref}가 죽었거나 본문이 비었습니다(status={s.get('http_status')}).",
                    fix_directive=f"소스 {ref}를 살아있는 URL로 교체하거나 이 소스에 의존하는 인용을 제거하세요.",
                    claim_id=None,
                ))

    for claim in stage_out.get("claims", []):
        cid = claim.get("id")
        kind = claim.get("kind", "fact")
        refs = claim.get("cited_source_refs") or []

        # (3) orphan: fact인데 무인용(추천/추론은 면제).
        if kind == "fact" and not refs:
            res.orphan_claims.append(cid)
            res.findings.append(Finding(
                severity="major", category="orphan_claim",
                detail=f"사실 주장 {cid}에 인용이 없습니다.",
                fix_directive=f"주장 {cid}에 스냅샷 소스를 인용하거나 추론/추천으로 강등하세요.",
                claim_id=cid,
            ))
            continue

        # (1) fabricated: 인용 ref가 sources에 없음.
        for ref in refs:
            if ref not in sources and ref not in seen_fabricated:
                seen_fabricated.add(ref)
                res.fabricated_sources.append(ref)
                res.findings.append(Finding(
                    severity="critical", category="fabricated_source",
                    detail=f"주장 {cid}가 존재하지 않는 소스 {ref}를 인용합니다.",
                    fix_directive=f"소스 {ref}를 sources 목록의 실재 스냅샷으로 교체하세요.",
                    claim_id=cid,
                ))

        # (4) unverified quote: quoted_span이 인용 소스 스냅샷의 부분문자열이 아님.
        span = claim.get("quoted_span") or ""
        if kind == "fact" and span.strip():
            live_refs = [r for r in refs if r in sources and r not in seen_dead]
            grounded = any(quote_is_grounded(span, texts.get(r, "")) for r in live_refs)
            if live_refs and not grounded:
                res.unverified_quotes.append(cid)
                res.findings.append(Finding(
                    severity="major", category="unverified_quote",
                    detail=f"주장 {cid}의 인용문이 스냅샷 원문에 그대로 존재하지 않습니다(날조 의심).",
                    fix_directive=f"주장 {cid}의 quoted_span을 스냅샷 원문의 축자 인용으로 교체하거나 unsupported로 표기하세요.",
                    claim_id=cid,
                ))

    return res
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_grounding.py -q`
  Expected: `10 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/grounding.py tests/research/test_grounding.py
git commit -m "feat(research): 결정론 grounding 검사(fabricated/dead/orphan/부분문자열)"
```

---

### Task 17: `source_grounding` 어댑터 (`autoagent/research/source_grounding.py` + `adapters.verify` 분기)

스펙 §4.3-②③: 반대 모델(Codex) 의미 대조 → 코드 병합, 결정적 위반(fabricated/dead/orphan)은 모델 pass여도 강등. verdict 마커는 `GROUNDING_VERDICT: pass|needs_changes|blocked` + fenced JSON. 모델 stdout은 `stage_out["model_raw_text"]`로 실어 전달(verify 시그니처 불변).

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\source_grounding.py`
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\adapters.py` (`verify` 디스패처에 `"source_grounding"` 분기 — Task 12에서 넣은 `SystemExit` 라인 교체)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_source_grounding.py`

**Interfaces:**
- Consumes: `research.types.{Finding,Verdict}`(Slice 1), `research.grounding.{run_deterministic_checks,DeterministicGrounding}`(Task 16), `research.snapshots.load_snapshot_text`(Task 15), `artifacts.{extract_json_block,write_json}`.
- Produces:
  - `def parse_grounding_verdict(raw_text: str) -> dict` — `GROUNDING_VERDICT` 마커 + fenced JSON 추출(부재 시 방어값).
  - `def merge_and_recompute(stage_out, model_json, det, *, verifier_agent, stage_id, raw_text) -> Verdict`.
  - `def verify_source_grounding(stage_out, run_dir, *, verifier_agent, config, model_raw_text) -> Verdict` — `run_dir/d_grounding_verdict.json` 영속.
  - `adapters.verify(adapter="source_grounding", ...)`가 이 모듈로 위임(계약 시그니처 불변).

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_source_grounding.py`:
```python
"""source_grounding 어댑터 테스트(§4.3-②③).

GROUNDING_VERDICT 마커+JSON 파싱, 결정론 findings와 병합, 결정적 위반의 모델 pass 강등을
코드로 못박는다(free-text 무시, 코드가 status 재계산). Verdict 계약 이름/필드 그대로 사용.
"""
from __future__ import annotations

from pathlib import Path

from autoagent.research.grounding import run_deterministic_checks
from autoagent.research.source_grounding import (
    merge_and_recompute, parse_grounding_verdict, verify_source_grounding,
)

MARKER_OK = """일부 서술...
GROUNDING_VERDICT: pass
```json
{"schema_version": 1, "adapter": "source_grounding", "stage_id": "d", "verdict": "pass",
 "claim_checks": [{"claim_id": "c1", "grounding": "supported", "matched_quote": "revenue of 12M",
                   "claim_span": "revenue was 12M", "notes": "", "source_ref": "s1"}],
 "orphan_claims": [], "dead_sources": [], "fabricated_sources": []}
```
꼬리 서술...
"""


def test_parse_marker_and_json():
    parsed = parse_grounding_verdict(MARKER_OK)
    assert parsed["verdict"] == "pass"
    assert parsed["claim_checks"][0]["claim_id"] == "c1"


def test_parse_missing_marker_is_defensive():
    parsed = parse_grounding_verdict("모델이 마커를 안 붙였습니다.")
    assert parsed["verdict"] is None
    assert parsed["claim_checks"] == []


def _stage_out_clean():
    return {
        "claims": [{"id": "c1", "text": "Acme revenue was 12M.", "kind": "fact",
                    "cited_source_refs": ["s1"], "quoted_span": "revenue of 12M"}],
        "sources": [{"ref_id": "s1", "url": "u1",
                     "fetched_text": "In 2024 Acme reported revenue of 12M USD.", "http_status": 200}],
    }


def test_clean_input_model_pass_stays_pass():
    so = _stage_out_clean()
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "pass"
    assert v.adapter == "source_grounding" and v.stage_id == "d"


def test_orphan_fact_downgrades_model_pass_to_needs_changes():
    so = _stage_out_clean()
    so["claims"].append({"id": "c2", "text": "Acme dominates.", "kind": "fact",
                         "cited_source_refs": [], "quoted_span": ""})
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "needs_changes"


def test_dead_source_blocks_even_if_model_pass():
    so = _stage_out_clean()
    so["sources"].append({"ref_id": "s2", "url": "u2", "fetched_text": "", "http_status": 404})
    so["claims"].append({"id": "c3", "text": "From dead.", "kind": "fact",
                         "cited_source_refs": ["s2"], "quoted_span": "x"})
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"], "s2": ""})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "blocked"


def test_fabricated_source_blocks():
    so = _stage_out_clean()
    so["claims"].append({"id": "c9", "text": "Ghost cite.", "kind": "fact",
                         "cited_source_refs": ["s99"], "quoted_span": "z"})
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    v = merge_and_recompute(so, parse_grounding_verdict(MARKER_OK), det,
                            verifier_agent="codex", stage_id="d", raw_text=MARKER_OK)
    assert v.status == "blocked"


def test_model_contradicted_forces_needs_changes():
    so = _stage_out_clean()
    det = run_deterministic_checks(so, {"s1": so["sources"][0]["fetched_text"]})
    model = {"verdict": "pass", "claim_checks": [
        {"claim_id": "c1", "grounding": "contradicted", "notes": "소스는 반대를 말함"}]}
    v = merge_and_recompute(so, model, det, verifier_agent="codex", stage_id="d", raw_text="")
    assert v.status == "needs_changes"
    assert any(f.category == "contradicted" and f.severity == "critical" for f in v.findings)


def test_verify_persists_verdict_json(tmp_path: Path):
    from autoagent.research.snapshots import save_snapshot
    save_snapshot(tmp_path / "sources", "s1", "u1",
                  "In 2024 Acme reported revenue of 12M USD.", http_status=200)
    v = verify_source_grounding(_stage_out_clean(), tmp_path,
                                verifier_agent="codex", config=None, model_raw_text=MARKER_OK)
    assert v.status == "pass"
    assert (tmp_path / "d_grounding_verdict.json").exists()


def test_adapters_verify_dispatches_source_grounding(tmp_path: Path):
    from autoagent.research.adapters import verify
    from autoagent.research.snapshots import save_snapshot
    save_snapshot(tmp_path / "sources", "s1", "u1",
                  "In 2024 Acme reported revenue of 12M USD.", http_status=200)
    so = {**_stage_out_clean(), "model_raw_text": MARKER_OK}
    v = verify("source_grounding", so, tmp_path, verifier_agent="codex", config=None)
    assert v.adapter == "source_grounding" and v.status == "pass"
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_source_grounding.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.source_grounding'` (collection error).

- [ ] **Step 3: 최소 구현 — 모듈** — `autoagent/research/source_grounding.py`:
```python
"""source_grounding 어댑터(§4.3 하이브리드).

Codex 검증기 stdout의 GROUNDING_VERDICT 마커+fenced JSON을 파싱하고(free-text 무시),
Task 16의 결정론 검사와 병합해 코드가 status를 재계산한다. 결정적 위반(fabricated/dead=
blocked, orphan/quote 미검증=needs_changes)은 모델이 pass라 적어도 강등한다(§4.3 F4).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from autoagent.artifacts import extract_json_block, write_json
from autoagent.research.grounding import DeterministicGrounding, run_deterministic_checks
from autoagent.research.snapshots import load_snapshot_text
from autoagent.research.types import Finding, Verdict


_MARKER = re.compile(r"GROUNDING_VERDICT:\s*(pass|needs_changes|blocked)", re.IGNORECASE)


def parse_grounding_verdict(raw_text: str) -> dict[str, Any]:
    """Codex stdout에서 GROUNDING_VERDICT 마커 + fenced JSON을 파싱한다.

    마커를 최우선으로 verdict를 읽되, 실제 구조는 fenced JSON에서 취한다(artifacts의
    extract_json_block 재사용). 마커/JSON이 없으면 예외 대신 방어 기본값을 돌려줘
    런을 죽이지 않는다(코드가 결정론 findings만으로 needs_changes를 만들 수 있게).
    """
    marker = _MARKER.search(raw_text)
    verdict = marker.group(1).lower() if marker else None
    try:
        data = extract_json_block(raw_text)
    except Exception:  # noqa: BLE001 — JSON 부재/파싱실패 모두 방어값으로
        data = {}
    return {
        "verdict": data.get("verdict", verdict),
        "claim_checks": data.get("claim_checks", []),
        "orphan_claims": data.get("orphan_claims", []),
        "dead_sources": data.get("dead_sources", []),
        "fabricated_sources": data.get("fabricated_sources", []),
        "schema_version": data.get("schema_version"),
    }


def _model_findings(model_json: dict[str, Any]) -> list[Finding]:
    """모델 claim_checks의 contradicted/unsupported를 Finding으로 승격한다.

    grounding∈{contradicted}=critical, {unsupported}=major. supported/partially_supported/
    no_source는 여기서 finding으로 만들지 않는다(no_source는 결정론 orphan 검사가 잡음).
    """
    findings: list[Finding] = []
    for chk in model_json.get("claim_checks", []):
        grounding = chk.get("grounding")
        cid = chk.get("claim_id")
        if grounding == "contradicted":
            findings.append(Finding(
                severity="critical", category="contradicted",
                detail=f"주장 {cid}가 인용 소스와 모순됩니다: {chk.get('notes', '')}",
                fix_directive=f"주장 {cid}를 소스가 실제로 지지하는 내용으로 수정하거나 제거하세요.",
                claim_id=cid,
            ))
        elif grounding == "unsupported":
            findings.append(Finding(
                severity="major", category="unsupported",
                detail=f"주장 {cid}가 인용 소스로 지지되지 않습니다: {chk.get('notes', '')}",
                fix_directive=f"주장 {cid}에 지지 근거를 스냅샷에서 인용하거나 강등하세요.",
                claim_id=cid,
            ))
    return findings


def _recompute_status(findings: list[Finding], det: DeterministicGrounding) -> str:
    """코드가 status를 재계산한다(모델 자유서술 무시).

    - 결정적 dead/fabricated가 있으면 blocked(판정 불가 → 게이트, §4.3).
    - critical/major finding이 하나라도 있으면 needs_changes(모델 pass여도 강등).
    - 그 외 pass.
    """
    if det.dead_sources or det.fabricated_sources:
        return "blocked"
    if any(f.severity in {"critical", "major"} for f in findings):
        return "needs_changes"
    return "pass"


def merge_and_recompute(
    stage_out: dict[str, Any], model_json: dict[str, Any], det: DeterministicGrounding, *,
    verifier_agent: str, stage_id: str, raw_text: str,
) -> Verdict:
    """모델 verdict + 결정론 findings를 병합하고 코드가 status를 재계산한 Verdict를 만든다.

    findings = 결정론(det.findings) + 모델(contradicted/unsupported). status는 결정적
    위반 우선으로 코드가 재계산해 모델 pass를 무시할 수 있다(강등). raw에 원문/모델 JSON/
    결정론 요약을 담아 감사추적을 남긴다.
    """
    findings = list(det.findings) + _model_findings(model_json)
    status = _recompute_status(findings, det)
    raw = {
        "adapter": "source_grounding", "stage_id": stage_id,
        "model_verdict": model_json.get("verdict"), "recomputed_status": status,
        "deterministic": {
            "fabricated_sources": det.fabricated_sources, "dead_sources": det.dead_sources,
            "orphan_claims": det.orphan_claims, "unverified_quotes": det.unverified_quotes,
        },
        "model_claim_checks": model_json.get("claim_checks", []), "raw_text": raw_text,
    }
    return Verdict(status=status, adapter="source_grounding", stage_id=stage_id, findings=findings, raw=raw)


def verify_source_grounding(
    stage_out: dict[str, Any], run_dir: Path, *, verifier_agent: str, config: Any, model_raw_text: str,
) -> Verdict:
    """d 스테이지 하이브리드 검증: 스냅샷 로드 → 결정론 검사 → 모델 병합 → 영속.

    model_raw_text는 Codex 검증기 stdout(오케스트레이터가 주입). 스냅샷은 run_dir/sources/
    에서 ref_id별로 되읽어 stage_out.sources[].fetched_text보다 우선한다(단일 소스 오브
    트루스). verdict JSON을 run_dir/d_grounding_verdict.json에 남긴다.
    """
    sources_dir = run_dir / "sources"
    snapshot_texts: dict[str, str] = {}
    for s in stage_out.get("sources", []):
        ref = s.get("ref_id")
        try:
            snapshot_texts[ref] = load_snapshot_text(sources_dir, ref)
        except (FileNotFoundError, ValueError):
            pass  # 스냅샷 파일 부재 시 stage_out.fetched_text로 폴백(run_deterministic_checks가 처리)

    det = run_deterministic_checks(stage_out, snapshot_texts)
    model_json = parse_grounding_verdict(model_raw_text)
    verdict = merge_and_recompute(
        stage_out, model_json, det, verifier_agent=verifier_agent, stage_id="d", raw_text=model_raw_text,
    )
    write_json(run_dir / "d_grounding_verdict.json", {
        "status": verdict.status, "adapter": verdict.adapter, "stage_id": verdict.stage_id,
        "findings": [
            {"severity": f.severity, "category": f.category, "detail": f.detail,
             "fix_directive": f.fix_directive, "claim_id": f.claim_id}
            for f in verdict.findings
        ],
        "raw": verdict.raw,
    })
    return verdict
```

- [ ] **Step 4: 최소 구현 — 디스패처 분기** — `autoagent/research/adapters.py`의 `verify`에서 Task 12가 넣은 `if adapter == "source_grounding": raise SystemExit(...)` 라인을 다음으로 교체:
```python
    if adapter == "source_grounding":
        # d 스테이지 하이브리드: 스냅샷 결정론 + Codex 의미대조. 모델 stdout은 오케스트레이터가
        # stage_out["model_raw_text"]에 실어 전달한다(verify 계약 시그니처 불변 유지).
        from autoagent.research.source_grounding import verify_source_grounding
        return verify_source_grounding(
            stage_out, run_dir, verifier_agent=verifier_agent, config=config,
            model_raw_text=stage_out.get("model_raw_text", ""),
        )
```

- [ ] **Step 5: 통과 확인** — Run: `python -m pytest tests/research/test_source_grounding.py -q`
  Expected: `9 passed`.

- [ ] **Step 6: commit**
```bash
git add autoagent/research/source_grounding.py autoagent/research/adapters.py tests/research/test_source_grounding.py
git commit -m "feat(research): source_grounding 어댑터(GROUNDING_VERDICT 파싱+하이브리드 병합+강등)"
```

---

### Task 18: d 스테이지 오케스트레이션 + 프롬프트 (Claude 웹 리서처 / Codex 스냅샷 검증기)

스펙 §3·§4.3: d 팩트리포트 스테이지는 Claude 리서처가 웹으로 팩트리포트를 쓰고, 코드가 스냅샷을 저장하며, Codex 검증기가 스냅샷만 읽어 의미대조한다. `research.py`에 d 전용 배선을 넣고 두 프롬프트를 신설한다. 모델 호출부라 dry-run 렌더로 검증.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\d_fact_report.md`
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\d_grounding_verify.md`
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\artifacts.py` (`PROMPT_ALIASES`에 2 별칭)
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py` (`STAGE_ADAPTER`/`STAGE_PROMPT`에 `d` 추가 + `run_stage_loop`에 d 분기)

**Interfaces:**
- Consumes: `render_template`, `roles.{load_roles,resolve_role}`, `runner.{run_process,require_command,write_command_artifact}`, `routing.choose_researcher`, `research.snapshots.{save_snapshot,write_sources_manifest}`(Task 15), `research.adapters.verify`(Task 17 분기 배선), `artifacts.extract_json_block`.
- Produces: d 리서처/검증기 프롬프트 2종 + 별칭; `research.py`의 d 스테이지 배선(스냅샷 저장 → source_grounding verify). `run_stage_loop`이 d에서 리서처 JSON을 파싱해 스냅샷을 남기고, 검증기 stdout을 `stage_out["model_raw_text"]`로 실어 `verify("source_grounding", ...)`를 호출.

- [ ] **Step 1: d 리서처 프롬프트 작성** — `prompts/research/d_fact_report.md`:
```markdown
# d 스테이지 — 웹 팩트리포트 (리서처: Claude)

너는 리서치 하네스의 **d 팩트리포트 리서처**다. 앞선 스테이지에서 확정된 canonical seed와
회사/시장 맥락을 근거로, 웹에서 **검증 가능한 사실만** 모아 팩트리포트를 작성한다.

## 입력
- REQUEST: {{REQUEST}}
- SEED(불변식, 바꾸지 마라): {{SEED_PIN}}
- 선행 스테이지 요약: {{PRIOR_STAGE_SUMMARY}}
- 직전 검증 피드백(있으면 반영): {{PRIOR_VERDICT_FEEDBACK}}

## 도구
- **웹은 너만 쓴다**: `WebSearch`로 후보를 찾고 `WebFetch`로 원문을 가져와라. 긴 페이지는
  `defuddle`로 클린화해라. 검증기(Codex)는 웹을 못 쓰고 네가 남긴 스냅샷만 읽는다.
- **모든 사실 주장은 네가 실제로 fetch한 원문에서 축자 인용(quoted_span)으로 뒷받침**해라.
  모델 지식으로 채운 주장은 근거 없음(unsupported)으로 강등되니 쓰지 마라.

## 산출 (반드시 이 순서)
1. 사람이 읽는 팩트리포트 markdown(각 사실에 [ref_id] 인용 표기).
2. 그다음 아래 스키마의 fenced JSON 한 블록. **fetched_text에는 인용을 포함하는 원문 발췌를
   그대로** 넣어라(검증기가 스냅샷으로 저장·대조한다).

```json
{
  "stage_id": "d",
  "report_md": "<위 팩트리포트 markdown 전문>",
  "claims": [
    {"id": "c1", "text": "<주장>", "kind": "fact|inference|recommendation",
     "cited_source_refs": ["s1"], "quoted_span": "<원문에서 그대로 복사한 인용문>"}
  ],
  "sources": [
    {"ref_id": "s1", "url": "<fetch한 URL>", "http_status": 200,
     "fetched_text": "<인용을 포함하는 원문 발췌(축자)>", "fetch_ts": "<ISO8601>"}
  ]
}
```

## 규칙
- 사실(kind=fact)은 **반드시** cited_source_refs와 quoted_span을 채워라(무인용 fact는 자동 반송).
- 추천/추론(recommendation/inference)은 뒷받침 사실이 supported면 직접 인용 면제.
- quoted_span은 sources의 해당 fetched_text에 **부분문자열로 그대로 존재**해야 한다(코드가 검증).
- 시점 의존 사실(주가·환율·시장규모)엔 as-of 날짜를 text에 명시해라.
- SEED를 재정의하지 마라(심화만 허용).
```

- [ ] **Step 2: d 검증기 프롬프트 작성** — `prompts/research/d_grounding_verify.md`:
```markdown
# d 스테이지 — source-grounding 검증 (검증기: Codex, 반대 모델)

너는 리서치 하네스의 **d 스테이지 grounding 검증기**다. 리서처(Claude)의 팩트리포트가
**첨부된 스냅샷 원문만으로** 실제 뒷받침되는지 적대적으로 대조한다. 방어가 아니라 공격이다.

## 절대 규칙
- **오직 아래 `sources[].fetched_text`(하네스가 저장한 스냅샷)만 근거로 삼아라.** 웹 접속·
  재fetch·네 사전지식으로 채우기는 금지다(그렇게 채운 지지 판정은 무효).
- 코드가 이미 결정적 위반(fabricated/dead/orphan/인용 부분문자열 불일치)을 병행 실측한다.
  너는 **의미 대조**에 집중해라: (1)인용 소스가 그 주장을 실제로 지지하나 (2)paraphrase가
  왜곡(may→will, 추정→확정, 상관→인과)됐나 (3)소스가 오히려 반대(contradicted)를 말하나.

## 입력
- REPORT_MD: {{REPORT_MD}}
- CLAIMS_JSON: {{CLAIMS_JSON}}
- SOURCES_SNAPSHOTS_JSON(ref_id·url·http_status·fetched_text): {{SOURCES_SNAPSHOTS_JSON}}

## 산출 (반드시 첫 줄 마커 + fenced JSON)
첫 줄에 정확히 다음 마커 한 줄:

`GROUNDING_VERDICT: pass|needs_changes|blocked`

그다음 fenced JSON 한 블록:

```json
{
  "schema_version": 1, "adapter": "source_grounding", "stage_id": "d",
  "verdict": "pass|needs_changes|blocked",
  "claim_checks": [
    {"claim_id": "c1",
     "grounding": "supported|partially_supported|unsupported|contradicted|no_source",
     "matched_quote": "<스냅샷 원문에서 그대로 복사한 지지 문장>",
     "claim_span": "<주장에서 대조한 부분>", "notes": "<판정 근거>", "source_ref": "s1"}
  ],
  "orphan_claims": [], "dead_sources": [], "fabricated_sources": []
}
```

## 판정 기준
- fact 주장이 스냅샷에서 지지되면 supported, 일부만 지지되면 partially_supported.
- 인용은 있으나 원문이 지지 안 하면 unsupported. 원문이 반대면 contradicted(critical).
- matched_quote는 반드시 **스냅샷 원문의 축자 문장**이어야 한다(날조 시 코드가 걸러낸다).
- 확인 불가한 주장은 지지로 적지 말고 unsupported/no_source로 정직히 표기해라.
- 최종 status는 코드가 결정론 결과와 병합해 재계산하니, 너는 관측한 대로 채워라.
```

- [ ] **Step 3: 별칭 추가** — `autoagent/artifacts.py`의 `PROMPT_ALIASES` 끝에 추가:
```python
    "d_fact_report.md": "research/d_fact_report.md",
    "d_grounding_verify.md": "research/d_grounding_verify.md",
```

- [ ] **Step 4: `research.py` d 스테이지 배선** — `autoagent/workflows/research.py`에서 아래를 수정/추가한다.
  (a) 상단 import에 스냅샷 추가:
```python
from autoagent.research.snapshots import save_snapshot, write_sources_manifest
```
  (b) `STAGE_ADAPTER`·`STAGE_PROMPT`에 d 엔트리 확정(Task 13이 Slice 2에서 이미 c·d를 채웠다면 이 딕셔너리는 그대로 두고 넘어간다 — 아래는 최종 형태이며 재확정해도 무해):
```python
STAGE_ADAPTER = {"a": "crossmodel", "b": "crossmodel", "c": "data_quality", "d": "source_grounding", "derive": "crossmodel"}
STAGE_PROMPT = {"a": "a_researcher.md", "b": "b_market_researcher.md", "c": "c_codex_research.md", "d": "d_fact_report.md", "derive": "derive.md"}
```
  (c) `run_stage_loop`의 검증 디스패치(Slice 1의 `if stage == "c": ... else: <crossmodel>`)에 **d 분기를 추가**한다. Slice 1 코드는 이미 c 분기와 stage-aware 검증기 프롬프트(`STAGE_VERIFIER_PROMPT`)를 가지므로, 기존 `if stage == "c":` 앞(또는 `elif`로) d 분기만 끼워 넣어 최종 형태를 만든다(c/else 분기는 보존):
```python
        if stage == "c":
            # c: 리서처 stdout(DATA_QUALITY_OUTPUT)을 코드 검증기로 검증(모델 0회).
            verdict = _run_stage_c_verify(ctx, researcher_out)
        elif stage == "d":
            # d: 리서처 JSON 파싱 → 스냅샷 저장 → Codex 검증기(스냅샷만) → source_grounding verify.
            verdict = _run_stage_d_verify(ctx, researcher_out, stage, outer_pass, inner)
        else:
            # 스테이지별 검증기 프롬프트(b는 전용 b_market_verifier.md, 그 외 crossmodel).
            verifier_out = _run_agent_step(
                ctx, agent=verifier, role_id="verifier",
                name=f"stage_{stage}_p{outer_pass}_r{inner}_verifier",
                prompt_name=STAGE_VERIFIER_PROMPT.get(stage, "crossmodel_verifier.md"),
                prompt_values={
                    **values, "STAGE_ID": stage,
                    "RESEARCHER_OUTPUT": researcher_out, "STAGE_OUTPUT_JSON": researcher_out,
                },
                next_step=f"verify:{stage}",
                dry_output=(
                    f"CROSSMODEL_VERDICT: pass\n```json\n"
                    f'{{"adapter":"crossmodel","stage_id":"{stage}","verdict":"pass",'
                    f'"findings":[],"coverage":{{"axes_checked":["support"],"axes_missing":[]}},'
                    f'"unchallenged_but_weak":["dry-run"],"tokens_seen":0}}\n```\n'
                ),
            )
            verdict = verify(
                STAGE_ADAPTER[stage], {"stage_id": stage, "verifier_raw_text": verifier_out},
                ctx.run_dir, verifier_agent=verifier, config=ctx.config,
            )
```
  주: `values`는 Slice 1 `run_stage_loop`가 이 분기 앞에서 이미 만든 스테이지별 값 dict다(seed 5필드·MIN_FINDINGS 포함). dry_output verdict는 `unchallenged_but_weak`를 채워 §4.1② 쿼터를 만족한다(dry-run pass 유지).
  그리고 파일에 d 전용 헬퍼를 추가:
```python
def _parse_stage_out(raw: str) -> dict[str, Any]:
    """리서처 stdout에서 fenced JSON stage_out을 뽑는다(실패 시 빈 스켈레톤)."""
    try:
        return extract_json_block(raw)
    except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 최소 스켈레톤으로 진행
        return {"stage_id": "d", "claims": [], "sources": [], "report_md": raw[:2000]}


def _run_stage_d_verify(ctx: ResearchContext, researcher_out: str, stage: str, outer_pass: int, inner: int):
    """d 검증 경로: 스냅샷 저장 → Codex 검증기 렌더/실행 → source_grounding verify.

    dry-run이면 검증기 stdout은 빈 문자열이고 결정론 검사만으로 verify가 돈다(모델 미호출).
    """
    stage_out = _parse_stage_out(researcher_out)
    # 리서처가 fetch한 sources[].fetched_text를 runs/sources/*.txt 스냅샷으로 고정.
    sources_dir = ctx.run_dir / "sources"
    snaps = []
    for s in stage_out.get("sources", []):
        snaps.append(save_snapshot(
            sources_dir, s.get("ref_id", "s?"), s.get("url", ""), s.get("fetched_text", ""),
            http_status=int(s.get("http_status", 0)), fetch_ts=s.get("fetch_ts"),
        ))
    write_sources_manifest(ctx.run_dir, snaps)

    # Codex 검증기(스냅샷만) 렌더/실행.
    import json as _json
    verifier_out = _run_agent_step(
        ctx, agent="codex", role_id="verifier",
        name=f"stage_{stage}_p{outer_pass}_r{inner}_verifier",
        prompt_name="d_grounding_verify.md",
        prompt_values={
            "REPORT_MD": stage_out.get("report_md", ""),
            "CLAIMS_JSON": _json.dumps(stage_out.get("claims", []), ensure_ascii=False),
            "SOURCES_SNAPSHOTS_JSON": _json.dumps(stage_out.get("sources", []), ensure_ascii=False),
        },
        next_step=f"verify:{stage}",
        dry_output="",  # dry-run: 모델 없이 결정론 검사만
    )
    return verify(
        "source_grounding", {**stage_out, "model_raw_text": verifier_out},
        ctx.run_dir, verifier_agent="codex", config=ctx.config,
    )
```

- [ ] **Step 5: dry-run 렌더 검증** — Run: `python run.py --dry-run --workflow research --request "Acme Corp 회사/시장 리서치 후 웹 팩트리포트"`
  Expected: exit 0. (MINIMAL_PATH가 아직 `["a","derive"]`라 d는 Slice 4에서 순회에 들어가지만, 이 태스크는 d 배선의 존재·프롬프트 별칭·헬퍼를 확보한다.) 별칭/프롬프트 렌더 단위 확인:
  `python -c "from autoagent.artifacts import render_template; t=render_template('d_fact_report.md', {'REQUEST':'r','SEED_PIN':'{}','PRIOR_STAGE_SUMMARY':'-','PRIOR_VERDICT_FEEDBACK':'-'}); assert '{{' not in t; print('d researcher render OK')"`
  Expected: `d researcher render OK`.

- [ ] **Step 6: 회귀 + 전체 pytest** — Run: `python -m pytest tests/ -q` → Slice 1~3 신설 테스트 전부 통과. `python run.py --dry-run --workflow routed --task-type backend --request "add health endpoint"` → exit 0.

- [ ] **Step 7: commit**
```bash
git add prompts/research/d_fact_report.md prompts/research/d_grounding_verify.md autoagent/artifacts.py autoagent/workflows/research.py
git commit -m "feat(research): d 팩트리포트 스테이지(Claude 웹 리서처/Codex 스냅샷 검증기)"
```

---

## Slice 4 — b 스테이지 + 바깥 심화 루프 + seed 계약 + 수렴 게이트

스펙 §5(canonical seed read-only pin·pass간 diff 모순감지·수렴게이트·as-of 메타) + §1(바깥 루프). 신설: `seed_contract.py`(순수)·`convergence.py`(순수), b 시장분석 스테이지, `run_research_workflow`의 outer_pass 1..2 루프. `research_state.json`에 `seed_pin`·`verified_claims` 영속. Slice 1의 `types.py`/`routing.choose_researcher`/`research.py`/`research_state.json` 스키마를 그대로 소비한다.

> **통합 노트:** 이 슬라이스의 Task 22(`run_outer_loop`)는 `research.py` 안에 `persist_research_state`/`load_research_state`/`collect_verified_claims`를 신설한다. Slice 5의 Task 25는 이와 별개로 재개 전용 `state.py`(`load_or_init_state`/`resume_point`/`pin_seed`)를 신설한다 — 전자는 바깥 루프의 claim 수집·delta용, 후자는 `--resume` done 스킵·seed read-only pin용으로 책임이 다르다. Slice 5 Task 27(오케스트레이터 통합)이 두 층을 최종적으로 엮어 `research_state.json` 한 파일에 수렴시킨다.

---

### Task 19: seed 계약 — canonical seed pin + 계약 위반 검출 (`autoagent/research/seed_contract.py`)

첫 pass에서 canonical seed(회사·시장·기준통화·기간·단위)를 확정해 read-only pin으로 굳히고, pass 2가 seed를 바꾸려 하면 결정론적으로 검출하는 순수 모듈.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\seed_contract.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_seed_contract.py`

**Interfaces:**
- Consumes: 없음(순수, stdlib만).
- Produces:
  - `@dataclass(frozen=True) SeedPin(company: str, market: str, base_currency: str, period: str, unit: str, as_of: str | None = None)`
  - `CANONICAL_FIELDS = ("company", "market", "base_currency", "period", "unit")`
  - `def build_seed_pin(raw: dict) -> SeedPin` — 누락 필드 시 `ValueError`.
  - `def seed_pin_to_dict(pin: SeedPin) -> dict`
  - `def seed_pin_from_dict(d: dict) -> SeedPin`
  - `def detect_seed_violations(pinned: SeedPin, candidate: dict) -> list[str]` — 언급된 canonical 값이 pin과 다르면 위반, 미언급은 위반 아님.
  - `def pin_as_of(pin: SeedPin, as_of: str) -> SeedPin`

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_seed_contract.py`:
```python
"""seed 계약 결정론 로직 테스트(스펙 §5 seed read-only pin·위반 검출)."""
from __future__ import annotations

import pytest

from autoagent.research.seed_contract import (
    build_seed_pin, detect_seed_violations, seed_pin_from_dict, seed_pin_to_dict,
)


def _raw() -> dict:
    return {
        "company": "Acme Corp", "market": "국내 EV 충전 인프라", "base_currency": "KRW",
        "period": "2021-2025", "unit": "억원", "extra_noise": "무시돼야 함",
    }


def test_build_seed_pin_extracts_canonical_fields():
    pin = build_seed_pin(_raw())
    assert pin.company == "Acme Corp"
    assert pin.market == "국내 EV 충전 인프라"
    assert pin.base_currency == "KRW"
    assert pin.period == "2021-2025"
    assert pin.unit == "억원"
    assert pin.as_of is None


def test_build_seed_pin_missing_field_raises():
    raw = _raw()
    del raw["base_currency"]
    with pytest.raises(ValueError) as exc:
        build_seed_pin(raw)
    assert "base_currency" in str(exc.value)


def test_seed_pin_roundtrip_dict():
    pin = build_seed_pin(_raw())
    assert seed_pin_from_dict(seed_pin_to_dict(pin)) == pin


def test_detect_seed_violations_none_when_candidate_matches():
    pin = build_seed_pin(_raw())
    candidate = {"company": "Acme Corp", "base_currency": "KRW", "narrative": "더 깊은 분석"}
    assert detect_seed_violations(pin, candidate) == []


def test_detect_seed_violations_flags_changed_currency():
    pin = build_seed_pin(_raw())
    violations = detect_seed_violations(pin, {"company": "Acme Corp", "base_currency": "USD"})
    assert len(violations) == 1
    assert "base_currency" in violations[0]
    assert "KRW" in violations[0] and "USD" in violations[0]


def test_detect_seed_violations_ignores_absent_fields():
    pin = build_seed_pin(_raw())
    assert detect_seed_violations(pin, {"narrative": "시장 규모 심화"}) == []
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_seed_contract.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.seed_contract'`.

- [ ] **Step 3: 최소 구현** — `autoagent/research/seed_contract.py`:
```python
"""canonical seed 계약(스펙 §5 seed 계약).

첫 outer pass에서 회사 식별자·시장 정의·기준통화·기간·단위를 확정해 read-only로
pin한다. pass 2는 seed를 못 바꾸고 심화만 허용 — 바꾸면 detect_seed_violations가
결정론적으로 잡아 모순 게이트 신호로 승격한다. 순수 함수(모델 호출 없음).
"""
from __future__ import annotations

from dataclasses import dataclass, replace


# canonical seed의 필수 5필드. 이 이름으로 raw dict에서 뽑고, 이 이름으로 위반을 검사한다.
CANONICAL_FIELDS = ("company", "market", "base_currency", "period", "unit")


@dataclass(frozen=True)
class SeedPin:
    """바깥 루프 불변식으로 굳힌 canonical seed. frozen이라 코드 경로에서 변형 불가(read-only pin)."""

    company: str
    market: str
    base_currency: str
    period: str
    unit: str
    as_of: str | None = None  # 시점 의존 seed의 as-of 날짜(주가·환율 기준일 등, 선택)


def build_seed_pin(raw: dict) -> SeedPin:
    """자유 dict에서 canonical 5필드를 뽑아 SeedPin을 만든다. 누락 필드는 ValueError."""
    missing = [f for f in CANONICAL_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        raise ValueError(f"seed에 canonical 필드 누락(확정 실패): {missing}")
    return SeedPin(
        company=str(raw["company"]).strip(), market=str(raw["market"]).strip(),
        base_currency=str(raw["base_currency"]).strip(), period=str(raw["period"]).strip(),
        unit=str(raw["unit"]).strip(), as_of=(str(raw["as_of"]).strip() if raw.get("as_of") else None),
    )


def seed_pin_to_dict(pin: SeedPin) -> dict:
    """research_state.json의 seed_pin 필드로 직렬화한다."""
    return {
        "company": pin.company, "market": pin.market, "base_currency": pin.base_currency,
        "period": pin.period, "unit": pin.unit, "as_of": pin.as_of,
    }


def seed_pin_from_dict(d: dict) -> SeedPin:
    """재개 시 research_state.json의 seed_pin을 역직렬화한다(빈 pin이면 ValueError)."""
    return build_seed_pin(d)


def detect_seed_violations(pinned: SeedPin, candidate: dict) -> list[str]:
    """pass 2+ 산출물이 pin된 canonical 값과 다른 값을 주장하면 위반 문자열 목록을 반환한다.

    candidate가 어떤 canonical 필드를 아예 언급 안 하면(부분 심화 산출) 위반이 아니다.
    언급했는데 값이 다르면 seed drift 모순으로 본다(스펙 §5 pass간 diff 모순감지).
    """
    violations: list[str] = []
    pinned_map = seed_pin_to_dict(pinned)
    for field in CANONICAL_FIELDS:
        if field not in candidate:
            continue
        got = str(candidate[field]).strip()
        expected = str(pinned_map[field]).strip()
        if got and got != expected:
            violations.append(
                f"seed 계약 위반: {field} pin='{expected}' 인데 pass 산출물이 '{got}'로 변경 시도"
            )
    return violations


def pin_as_of(pin: SeedPin, as_of: str) -> SeedPin:
    """as-of 날짜만 확정/보강한 새 pin을 반환한다(canonical 5필드는 불변)."""
    return replace(pin, as_of=as_of.strip() or None)
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_seed_contract.py -q`
  Expected: `6 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/seed_contract.py tests/research/test_seed_contract.py
git commit -m "feat(research): canonical seed 계약 pin + 위반 검출(§5)"
```

---

### Task 20: verified_claims delta + as-of 정렬 (`autoagent/research/convergence.py`)

pass N vs N-1의 검증된 claim 집합을 정규화·비교해 (1)값이 뒤집힌 모순과 (2)순증가 delta를 산출하는 순수 모듈.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\convergence.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_convergence.py`

**Interfaces:**
- Consumes: 없음(stdlib `hashlib`/`re`만).
- Produces:
  - `def normalize_claim_key(claim: dict) -> str` — `claim_id` 우선, 없으면 정규화 텍스트 sha1(12자).
  - `@dataclass ClaimDelta(added: list[dict], unchanged: list[str], contradictions: list[dict], delta_count: int)`
  - `def diff_verified_claims(prev: list[dict], curr: list[dict]) -> ClaimDelta` — 같은 key라도 `as_of` 다르면 시점차(added), 같으면 값 뒤집힘=모순.
  - `def is_converged(delta: ClaimDelta, *, min_new_claims: int) -> bool`

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_convergence.py`:
```python
"""pass간 검증 claim delta·모순 검출 결정론 로직 테스트(스펙 §5)."""
from __future__ import annotations

from autoagent.research.convergence import ClaimDelta, diff_verified_claims, normalize_claim_key


def test_normalize_claim_key_prefers_claim_id():
    assert normalize_claim_key({"claim_id": "c1", "text": "무관"}) == "c1"


def test_normalize_claim_key_hashes_text_when_no_id():
    k1 = normalize_claim_key({"text": "시장 규모는 5000억원 이다."})
    k2 = normalize_claim_key({"text": "시장 규모는  5000억원 이다."})
    assert k1 == k2
    assert k1 != normalize_claim_key({"text": "완전 다른 주장"})


def test_diff_added_and_delta_count():
    prev = [{"claim_id": "c1", "value": "5000"}]
    curr = [{"claim_id": "c1", "value": "5000"}, {"claim_id": "c2", "value": "12%"}]
    delta = diff_verified_claims(prev, curr)
    assert isinstance(delta, ClaimDelta)
    assert delta.delta_count == 1
    assert [c["claim_id"] for c in delta.added] == ["c2"]
    assert delta.unchanged == ["c1"]
    assert delta.contradictions == []


def test_diff_flags_contradiction_when_value_flips():
    prev = [{"claim_id": "c1", "value": "5000"}]
    curr = [{"claim_id": "c1", "value": "9000"}]
    delta = diff_verified_claims(prev, curr)
    assert delta.delta_count == 0
    assert len(delta.contradictions) == 1
    assert delta.contradictions[0]["claim_id"] == "c1"
    assert delta.contradictions[0]["prev_value"] == "5000"
    assert delta.contradictions[0]["curr_value"] == "9000"


def test_diff_as_of_difference_is_not_contradiction():
    prev = [{"claim_id": "c1", "value": "1300", "as_of": "2025-01-01"}]
    curr = [{"claim_id": "c1", "value": "1400", "as_of": "2025-06-01"}]
    delta = diff_verified_claims(prev, curr)
    assert delta.contradictions == []
    assert delta.delta_count == 1


def test_diff_empty_prev_all_added():
    curr = [{"claim_id": "c1", "value": "x"}, {"claim_id": "c2", "value": "y"}]
    delta = diff_verified_claims([], curr)
    assert delta.delta_count == 2
    assert delta.contradictions == []
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_convergence.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.convergence'`.

- [ ] **Step 3: 최소 구현** — `autoagent/research/convergence.py`:
```python
"""pass간 검증 claim delta·모순 검출 + 수렴 판정(스펙 §5).

바깥 루프 pass N vs N-1의 검증된 claim을 정규화 key로 대조한다:
- 같은 key인데 값이 뒤집히면 '심화 아닌 모순'(contradiction) → 게이트 신호.
- 단, as-of가 다르면 시점차 갱신이라 모순이 아니라 added(심화)로 본다(§5 as-of 메타).
- 새로 검증된 claim 수(delta_count)가 임계 이하면 수렴 → 조기 종료.
순수 함수(모델 호출 없음). claim은 어댑터 표현과 무관하게 dict로 다룬다.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_WS = re.compile(r"\s+")


def normalize_claim_key(claim: dict) -> str:
    """claim의 안정 key. claim_id가 있으면 그대로, 없으면 정규화 텍스트 sha1(12자)."""
    cid = claim.get("claim_id")
    if cid:
        return str(cid)
    text = _WS.sub(" ", str(claim.get("text", ""))).strip().lower()
    return "h:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _value_of(claim: dict) -> str:
    # 값 비교 대상. value 우선, 없으면 정규화 텍스트로 폴백.
    if "value" in claim:
        return str(claim["value"]).strip()
    return _WS.sub(" ", str(claim.get("text", ""))).strip().lower()


@dataclass
class ClaimDelta:
    """pass간 검증 claim 비교 결과."""

    added: list[dict]           # 이번 pass에서 새로 검증된 claim(as-of 갱신 포함)
    unchanged: list[str]        # 값·시점 그대로인 claim key
    contradictions: list[dict]  # 같은 key·같은 as-of인데 값이 뒤집힌 모순
    delta_count: int            # len(added) — 수렴 게이트 입력


def diff_verified_claims(prev: list[dict], curr: list[dict]) -> ClaimDelta:
    """이전/이번 pass의 검증 claim 목록을 대조해 ClaimDelta를 만든다."""
    prev_by_key: dict[str, dict] = {normalize_claim_key(c): c for c in prev}
    added: list[dict] = []
    unchanged: list[str] = []
    contradictions: list[dict] = []
    for c in curr:
        key = normalize_claim_key(c)
        if key not in prev_by_key:
            added.append(c)
            continue
        p = prev_by_key[key]
        if str(c.get("as_of") or "") != str(p.get("as_of") or ""):
            added.append(c)  # as-of 시점차 → 심화(added)
            continue
        if _value_of(c) != _value_of(p):
            contradictions.append(
                {"claim_id": c.get("claim_id") or key,
                 "prev_value": _value_of(p), "curr_value": _value_of(c)}
            )
        else:
            unchanged.append(key)
    return ClaimDelta(added=added, unchanged=unchanged, contradictions=contradictions, delta_count=len(added))


def is_converged(delta: ClaimDelta, *, min_new_claims: int) -> bool:
    """새로 검증된 claim이 임계 미만이고 모순이 없으면 수렴(조기 종료 가능)으로 판정한다."""
    return delta.delta_count < max(min_new_claims, 1) and not delta.contradictions
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_convergence.py -q`
  Expected: `6 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/convergence.py tests/research/test_convergence.py
git commit -m "feat(research): pass간 claim delta·모순·수렴 판정(§5)"
```

---

### Task 21: 수렴 게이트 조기종료 + 모순 게이트 신호 판정 (`convergence.py` 확장)

`ClaimDelta`를 받아 바깥 루프가 (1)수렴 시 조기종료할지, (2)모순/seed 위반 시 게이트로 승격할지를 결정하는 순수 결정 함수.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\convergence.py` (게이트 결정 함수 추가)
- Modify: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_convergence.py` (게이트 테스트 추가)

**Interfaces:**
- Consumes: `ClaimDelta`(Task 20), `detect_seed_violations` 결과(list[str], Task 19).
- Produces:
  - `@dataclass OuterPassDecision(action: Literal["continue","early_stop","gate"], reason: str, contradictions: list[dict])`
  - `def decide_outer_pass(delta: ClaimDelta, seed_violations: list[str], *, outer_pass: int, max_outer: int, min_new_claims: int) -> OuterPassDecision` — 우선순위: 모순/seed위반 gate > 수렴/마지막 early_stop > continue.

- [ ] **Step 1: 실패 테스트 작성** — `test_convergence.py` 하단에 추가:
```python
from autoagent.research.convergence import OuterPassDecision, decide_outer_pass


def _delta(added=0, contradictions=None):
    return ClaimDelta(added=[{}] * added, unchanged=[], contradictions=contradictions or [], delta_count=added)


def test_decide_gate_on_contradiction_even_if_converged():
    delta = _delta(added=0, contradictions=[{"claim_id": "c1"}])
    d = decide_outer_pass(delta, [], outer_pass=2, max_outer=2, min_new_claims=2)
    assert isinstance(d, OuterPassDecision)
    assert d.action == "gate"
    assert d.contradictions == [{"claim_id": "c1"}]


def test_decide_gate_on_seed_violation():
    delta = _delta(added=5)
    d = decide_outer_pass(delta, ["seed 계약 위반: base_currency ..."], outer_pass=2, max_outer=2, min_new_claims=2)
    assert d.action == "gate"
    assert "seed" in d.reason


def test_decide_early_stop_on_convergence():
    d = decide_outer_pass(_delta(added=1), [], outer_pass=1, max_outer=2, min_new_claims=2)
    assert d.action == "early_stop"
    assert "수렴" in d.reason


def test_decide_early_stop_at_last_pass():
    d = decide_outer_pass(_delta(added=9), [], outer_pass=2, max_outer=2, min_new_claims=2)
    assert d.action == "early_stop"


def test_decide_continue_when_progress_and_not_last():
    d = decide_outer_pass(_delta(added=5), [], outer_pass=1, max_outer=2, min_new_claims=2)
    assert d.action == "continue"
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_convergence.py -q`
  Expected: `ImportError: cannot import name 'OuterPassDecision'` — 기존 6 passed + 신규 5 error.

- [ ] **Step 3: 최소 구현** — `convergence.py` 끝에 추가:
```python
from typing import Literal


@dataclass
class OuterPassDecision:
    """바깥 루프의 다음 행동. gate는 §6.2에 따라 절대 생략 불가."""

    action: Literal["continue", "early_stop", "gate"]
    reason: str
    contradictions: list[dict]


def decide_outer_pass(
    delta: ClaimDelta, seed_violations: list[str], *,
    outer_pass: int, max_outer: int, min_new_claims: int,
) -> OuterPassDecision:
    """pass 결과로 다음 행동을 결정한다. 우선순위: 모순/seed위반 gate > 수렴/마지막 early_stop > continue."""
    # (1) 모순 또는 seed 계약 위반 = 분기점 게이트(절대 생략 안 함, §6.2).
    if delta.contradictions or seed_violations:
        bits = []
        if delta.contradictions:
            bits.append(f"검증 claim 모순 {len(delta.contradictions)}건")
        if seed_violations:
            bits.append(f"seed 계약 위반 {len(seed_violations)}건")
        return OuterPassDecision(action="gate", reason="; ".join(bits), contradictions=delta.contradictions)
    # (2) 수렴(신규 검증 claim이 임계 미만) 또는 마지막 pass 도달 = 조기/정상 종료.
    if delta.delta_count < max(min_new_claims, 1):
        return OuterPassDecision(
            action="early_stop",
            reason=f"수렴(신규 검증 claim {delta.delta_count} < 임계 {min_new_claims})",
            contradictions=[],
        )
    if outer_pass >= max_outer:
        return OuterPassDecision(
            action="early_stop", reason=f"바깥 루프 상한 도달(pass {outer_pass}/{max_outer})", contradictions=[],
        )
    # (3) 개선 충분 + 여지 있음 = 다음 pass 진행.
    return OuterPassDecision(
        action="continue",
        reason=f"개선 지속(신규 검증 claim {delta.delta_count}), 다음 pass 진입", contradictions=[],
    )
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_convergence.py -q`
  Expected: `11 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/convergence.py tests/research/test_convergence.py
git commit -m "feat(research): 수렴 게이트 조기종료 + 모순 게이트 신호 판정(§5·§6.2)"
```

---

### Task 22: 바깥 루프 배선 — `run_outer_loop` + verified_claims 수집/delta 연결

Task 19~21의 순수 로직을 `research.py`에 배선한다: preamble에서 seed 확정→`seed_pin` 영속, `for outer_pass in 1..2` 루프, pass 끝마다 검증 claim 수집→pass간 diff→`decide_outer_pass`로 조기종료/게이트 판정. **silent pass-through 금지**(exhausted_unverified 격리). 결정론 부분만 `run_stage`를 주입해 pytest.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py` (아래 함수 신설/배선)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_research_state.py`

**Interfaces:**
- Consumes: `seed_contract.{build_seed_pin,seed_pin_to_dict,seed_pin_from_dict,detect_seed_violations}`(Task 19), `convergence.{diff_verified_claims,decide_outer_pass}`(Task 20·21), `artifacts.{write_json,read_text}`, 기존 `run_stage_loop`(Slice 1).
- Produces:
  - `def persist_research_state(run_dir: Path, state: dict) -> None`
  - `def load_research_state(run_dir: Path) -> dict | None`
  - `def collect_verified_claims(stage_results: list[StageResult]) -> list[dict]` — resolved 스테이지의 `verdict.raw['verified_claims']`만(exhausted/blocked 제외 — F1). 이 값은 Slice 1 `run_stage_loop`의 `_inject_verified_claims`가 pass 시 리서처 stdout에서 실제 채워 넣은 것이다(B2 배선 — 하드코딩 아님).
  - `def run_outer_loop(ctx, *, run_stage=None) -> dict` — `ctx.run_dir/stages/seed_raw/max_outer/min_new_claims`를 소비, 주입 가능한 `run_stage`. 반환은 최종 state.
  - (테스트) `test_run_stage_loop_injects_researcher_claims_into_verdict_raw` — `run_stage_loop` 실경로를 태워 `verdict.raw['verified_claims']`가 리서처 claims로 채워지는지 검증(손주입 금지).

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_research_state.py`:
```python
"""바깥 루프 배선·research_state 영속 결정론 테스트(모델 호출은 stub 주입)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from autoagent.research.types import StageResult, Verdict
from autoagent.workflows.research import (
    collect_verified_claims, load_research_state, persist_research_state, run_outer_loop,
)


def _verdict(claims):
    return Verdict(status="pass", adapter="crossmodel", stage_id="b", findings=[], raw={"verified_claims": claims})


def _resolved(stage, claims):
    return StageResult(stage_id=stage, status="resolved", output_path=f"{stage}.json",
                       verdict=_verdict(claims), inner_rounds=1)


def test_persist_and_load_roundtrip(tmp_path: Path):
    state = {"outer_pass": 1, "stage": "b", "inner_round": 2,
             "seed_pin": {"company": "X"}, "verified_claims": [], "stage_status": {}}
    persist_research_state(tmp_path, state)
    assert (tmp_path / "research_state.json").exists()
    assert load_research_state(tmp_path) == state


def test_collect_verified_claims_excludes_unverified():
    resolved = _resolved("b", [{"claim_id": "c1", "value": "1"}])
    exhausted = StageResult(stage_id="d", status="exhausted_unverified", output_path="d.json",
                            verdict=Verdict(status="needs_changes", adapter="source_grounding", stage_id="d",
                                            findings=[], raw={"verified_claims": [{"claim_id": "x"}]}),
                            inner_rounds=3)
    claims = collect_verified_claims([resolved, exhausted])
    assert [c["claim_id"] for c in claims] == ["c1"]


def _ctx(tmp_path, seed_raw, stage_claims_by_pass):
    calls = {"n": 0}

    def stub_run_stage(stage, outer_pass, ctx):
        claims = stage_claims_by_pass.get(outer_pass, {}).get(stage, [])
        calls["n"] += 1
        return _resolved(stage, claims)

    return SimpleNamespace(run_dir=tmp_path, stages=["b"], seed_raw=seed_raw,
                           max_outer=2, min_new_claims=2, calls=calls, run_stage=stub_run_stage)


def test_outer_loop_early_stops_on_convergence(tmp_path: Path):
    seed = {"company": "Acme", "market": "M", "base_currency": "KRW", "period": "2021-2025", "unit": "억원"}
    per_pass = {1: {"b": [{"claim_id": "c1"}, {"claim_id": "c2"}, {"claim_id": "c3"}]},
                2: {"b": [{"claim_id": "c1"}, {"claim_id": "c2"}, {"claim_id": "c3"}]}}
    ctx = _ctx(tmp_path, seed, per_pass)
    state = run_outer_loop(ctx, run_stage=ctx.run_stage)
    assert state["seed_pin"]["base_currency"] == "KRW"
    assert state["outer_pass"] == 2
    assert state["outer_decision"]["action"] == "early_stop"
    saved = json.loads((tmp_path / "research_state.json").read_text(encoding="utf-8"))
    assert saved["seed_pin"]["company"] == "Acme"


def test_outer_loop_gates_on_seed_violation(tmp_path: Path):
    seed = {"company": "Acme", "market": "M", "base_currency": "KRW", "period": "2021-2025", "unit": "억원"}

    def stub_run_stage(stage, outer_pass, ctx):
        if outer_pass == 2:
            return StageResult(stage_id=stage, status="resolved", output_path="b.json",
                               verdict=Verdict(status="pass", adapter="crossmodel", stage_id=stage, findings=[],
                                               raw={"verified_claims": [{"claim_id": "z"}],
                                                    "seed_candidate": {"base_currency": "USD"}}),
                               inner_rounds=1)
        return _resolved(stage, [{"claim_id": "c1"}, {"claim_id": "c2"}, {"claim_id": "c3"}])

    ctx = SimpleNamespace(run_dir=tmp_path, stages=["b"], seed_raw=seed,
                          max_outer=2, min_new_claims=2, calls={"n": 0}, run_stage=stub_run_stage)
    state = run_outer_loop(ctx, run_stage=stub_run_stage)
    assert state["outer_decision"]["action"] == "gate"
    assert "seed" in state["outer_decision"]["reason"]


def test_run_stage_loop_injects_researcher_claims_into_verdict_raw(tmp_path: Path, monkeypatch):
    """B2 실배선: run_stage_loop이 pass 시 *리서처* stdout의 claims를 verdict.raw에 실제 주입한다.

    손으로 raw={"verified_claims":...}를 넣지 않고, run_stage_loop의 실제 경로를 태워
    collect_verified_claims가 읽는 verdict.raw['verified_claims']가 채워지는지 검증한다.
    (안 채워지면 실런에서 delta=0 → pass 2 심화가 죽는다.)
    """
    import argparse

    from autoagent.config import load_config
    from autoagent.artifacts import DEFAULT_CONFIG
    from autoagent.workflows import research as R

    # 리서처 호출은 claims를 담은 유효 STAGE_OUTPUT_JSON을, 검증기 호출은 pass verdict를 돌려준다.
    researcher_json = (
        'STAGE_OUTPUT_JSON\n```json\n'
        '{"stage_id":"a","claims":[{"id":"a1","text":"t"}],'
        '"seed_candidate":{"base_currency":"KRW"}}\n```\n'
    )
    # 유효 pass verdict. unchallenged_but_weak를 채워 §4.1② 최소 findings 쿼터를 만족시킨다
    # (findings 0건이라도 무결을 소스로 증명한 경우 → 강등 안 됨). tokens_seen=0이라 evidence 교차검사도 무해.
    verifier_json = (
        'CROSSMODEL_VERDICT: pass\n```json\n'
        '{"adapter":"crossmodel","stage_id":"a","verdict":"pass","findings":[],'
        '"coverage":{"axes_checked":["support"],"axes_missing":[]},'
        '"unchallenged_but_weak":["s1: 근거는 있으나 표본이 작다"],"tokens_seen":0}\n```\n'
    )

    def fake_step(ctx, *, agent, role_id, name, prompt_name, prompt_values, next_step, dry_output):
        return researcher_json if role_id == "researcher" else verifier_json

    monkeypatch.setattr(R, "_run_agent_step", fake_step)

    cfg = load_config(DEFAULT_CONFIG)
    args = argparse.Namespace(dry_run=True, read_only=False, max_agent_calls=0)
    ctx = R.ResearchContext(args=args, config=cfg, request="r", run_dir=tmp_path,
                            budget=R.AgentCallBudget(0), seed_contract="")
    ctx.state = {"seed_pin": {"company": "Acme", "base_currency": "KRW"}, "stage_status": {}}
    result = R.run_stage_loop("a", 1, ctx)
    assert result.status == "resolved"
    assert result.verdict.raw["verified_claims"] == [{"id": "a1", "text": "t"}]
    assert result.verdict.raw["seed_candidate"] == {"base_currency": "KRW"}
    # collect_verified_claims가 실제로 이 claim을 걷는지까지 확인(실배선).
    assert collect_verified_claims([result]) == [{"id": "a1", "text": "t"}]
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_research_state.py -q`
  Expected: `ImportError: cannot import name 'run_outer_loop'` — 6 error.

- [ ] **Step 3: 최소 구현** — `autoagent/workflows/research.py`에 아래를 추가한다(import 블록에 seed_contract·convergence·`read_text`를 더한다; `import json`은 Slice 1 Task 6이 이미 최상단에 넣었으므로 중복 추가하지 않는다):
```python
# --- Slice 4 배선: 바깥 루프 · seed 계약 · 수렴 게이트 (스펙 §5·§1) ---
from autoagent.artifacts import read_text
from autoagent.research.convergence import decide_outer_pass, diff_verified_claims
from autoagent.research.seed_contract import (
    build_seed_pin, detect_seed_violations, seed_pin_from_dict, seed_pin_to_dict,
)


def persist_research_state(run_dir: Path, state: dict) -> None:
    """매 전이마다 research_state.json을 다시 써 재개 가능하게 한다(task_exec.persist_status 패턴)."""
    write_json(run_dir / "research_state.json", state)


def load_research_state(run_dir: Path) -> dict | None:
    """재개 진입점: 이전 research_state.json이 있으면 읽어 반환, 없으면 None."""
    path = run_dir / "research_state.json"
    if not path.exists():
        return None
    return json.loads(read_text(path))


def collect_verified_claims(stage_results: list[StageResult]) -> list[dict]:
    """resolved 스테이지의 verdict에서 검증된 claim만 모은다.

    exhausted_unverified·blocked 스테이지의 claim은 제외한다(F1 silent pass-through 격리).
    verdict.raw['verified_claims']를 표준 소스로 본다.
    """
    claims: list[dict] = []
    for r in stage_results:
        if r.status != "resolved" or r.verdict is None:
            continue  # F1: 미검증/차단 스테이지는 delta 계산에서 배제
        claims.extend(r.verdict.raw.get("verified_claims", []) or [])
    return claims


def _extract_seed_candidate(stage_results: list[StageResult]) -> dict:
    """pass 산출물이 주장하는 canonical 값 후보를 모은다(seed 위반 검사용)."""
    candidate: dict = {}
    for r in stage_results:
        if r.verdict is None:
            continue
        candidate.update(r.verdict.raw.get("seed_candidate") or {})
    return candidate


def run_outer_loop(ctx, *, run_stage=None) -> dict:
    """바깥 심화 루프(최대 max_outer). preamble에서 seed pin을 굳히고, pass마다 스테이지
    루프→검증 claim 수집→pass간 diff→수렴/모순 판정을 하고 매 전이 research_state.json에
    영속한다. run_stage는 테스트 주입용(기본은 run_stage_loop).
    """
    if run_stage is None:
        run_stage = run_stage_loop

    existing = load_research_state(ctx.run_dir)
    if existing and existing.get("seed_pin"):
        seed_pin = seed_pin_from_dict(existing["seed_pin"])
    else:
        seed_pin = build_seed_pin(ctx.seed_raw)

    state = {
        "outer_pass": 0, "stage": None, "inner_round": 0,
        "seed_pin": seed_pin_to_dict(seed_pin),
        "verified_claims": (existing or {}).get("verified_claims", []),
        "stage_status": {}, "outer_decision": None,
    }
    persist_research_state(ctx.run_dir, state)

    prev_claims: list[dict] = state["verified_claims"]
    for outer_pass in range(1, ctx.max_outer + 1):
        state["outer_pass"] = outer_pass
        stage_results: list[StageResult] = []
        for stage in ctx.stages:
            state["stage"] = stage
            result = run_stage(stage, outer_pass, ctx)
            stage_results.append(result)
            state["stage_status"][f"{outer_pass}:{stage}"] = result.status
            state["inner_round"] = result.inner_rounds
            persist_research_state(ctx.run_dir, state)  # 매 전이 영속(§6.3)

        seed_violations = []
        if outer_pass > 1:
            seed_violations = detect_seed_violations(seed_pin, _extract_seed_candidate(stage_results))

        curr_claims = collect_verified_claims(stage_results)
        delta = diff_verified_claims(prev_claims, curr_claims)
        decision = decide_outer_pass(
            delta, seed_violations, outer_pass=outer_pass, max_outer=ctx.max_outer,
            min_new_claims=ctx.min_new_claims,
        )
        state["verified_claims"] = prev_claims + delta.added  # 모순/미검증은 누적 안 함
        state["outer_decision"] = {
            "action": decision.action, "reason": decision.reason, "contradictions": decision.contradictions,
        }
        persist_research_state(ctx.run_dir, state)

        if decision.action in {"early_stop", "gate"}:
            break  # 수렴 조기종료 또는 모순/seed위반 게이트 승격 — silent 진행 금지
        prev_claims = state["verified_claims"]

    return state
```
  주: `run_outer_loop`는 이 슬라이스에서 자립 배선·테스트되는 참조 구현이다. **Task 28(오케스트레이터 통합)은 `run_outer_loop`를 직접 호출하지 않고** 바깥 루프를 인라인으로 다시 짜되(게이트·재개·매트릭스를 함께 엮어야 하므로), 여기서 확보한 하위 판정 헬퍼(`collect_verified_claims`/`diff_verified_claims`/`decide_outer_pass`/`detect_seed_violations`)를 그대로 재사용한다 — 즉 `run_outer_loop`는 dead가 아니라 헬퍼 집합의 자립 검증체다. `decision.action == "gate"`면 `outer_decision`을 상태에 남기는 것까지가 이 슬라이스의 책임(실제 게이트 정지는 Slice 5).

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/research/test_research_state.py -q`
  Expected: `6 passed`(실배선 테스트 `test_run_stage_loop_injects_researcher_claims_into_verdict_raw` 포함). 이어 `python -m pytest tests/research -q` → Slice 4까지 누적 통과.

- [ ] **Step 5: commit**
```bash
git add autoagent/workflows/research.py tests/research/test_research_state.py
git commit -m "feat(research): 바깥 루프 배선 + seed pin/verified_claims 영속 + 수렴·모순 판정 연결(§5·§1)"
```

---

### Task 23: b 시장분석 스테이지 — Claude 리서처 / Codex crossmodel 검증 프롬프트 + 라우팅 확인

b 스테이지의 리서처(Claude)·crossmodel 검증기(Codex) 프롬프트를 신설하고, `choose_researcher("b")`가 `(claude, codex, ...)`를 주는지 확인하며, seed pin·outer_pass 컨텍스트가 프롬프트에 주입되는지 dry-run 렌더로 검증한다.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\b_market_researcher.md`
- Create: `C:\Users\systran\Desktop\AutoAgent\prompts\research\b_market_verifier.md`
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\artifacts.py` (`PROMPT_ALIASES`에 b 별칭 2개)
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py` (`STAGE_VERIFIER_PROMPT`에 b 엔트리)

**Interfaces:**
- Consumes: `render_template`, `choose_researcher("b") -> ("claude","codex",reason)`(Slice 1), Slice 1 `STAGE_VERIFIER_PROMPT`·`run_stage_loop`의 seed 5필드·`MIN_FINDINGS` 주입.
- Produces: b 리서처/검증기 프롬프트(seed pin·outer_pass·inner feedback·evidence_bundle placeholder) + `STAGE_VERIFIER_PROMPT["b"]="b_market_verifier.md"` 배선(전용 검증기 프롬프트 활성 — dead 해소). crossmodel verdict 마커 `CROSSMODEL_VERDICT: pass|needs_changes|blocked`를 검증기가 내도록 지시.

- [ ] **Step 1: b 리서처 프롬프트 작성** — `prompts/research/b_market_researcher.md`:
```markdown
# b 시장분석 리서처 (Claude · 웹 종합)

너는 시장분석 스테이지 b의 리서처다. WebSearch/WebFetch로 시장 규모·성장·경쟁·규제를
종합한다. Codex는 웹을 못 쓰므로 인용할 원문은 반드시 fetch해 evidence_bundle에 실어라.

## canonical seed (read-only — 절대 바꾸지 마라)
- 회사: {{SEED_COMPANY}}
- 시장 정의: {{SEED_MARKET}}
- 기준통화: {{SEED_CURRENCY}}
- 기간: {{SEED_PERIOD}}
- 단위: {{SEED_UNIT}}
- as-of 기준일: {{SEED_AS_OF}}

이 seed는 첫 pass에서 확정돼 pin됐다. **너는 이 값을 재정의·변경할 수 없다.**
시장 규모/환율/주가 같은 시점 의존 수치엔 반드시 `as_of` 날짜를 붙여라.

## 이번 심화 컨텍스트
- outer_pass: {{OUTER_PASS}} (1=개괄, 2=정밀 심화)
- inner_round: {{INNER_ROUND}}
- 직전 검증 피드백(있으면 이 약점만 좁혀 보정):
{{INNER_FEEDBACK}}

pass 2라면 자유 재작성 금지 — 아래 명시 delta 목표만 심화하라:
{{DEEPEN_DELTA}}

## 출력 계약 (JSON front-matter + 서사 — 필드명 영문 고정)
first fenced json 블록으로 아래를 낸 뒤, 그 아래 한국어 서사(narrative_md)를 붙여라.
```json
{
  "stage_id": "b",
  "claims": [
    {"id": "b1", "text": "...", "kind": "fact|inference|recommendation",
     "source_refs": ["s1"], "confidence": 0.0, "as_of": "YYYY-MM-DD"}
  ],
  "seed_candidate": {"base_currency": "{{SEED_CURRENCY}}"},
  "evidence_bundle": {"sources": [
    {"ref_id": "s1", "url": "...", "fetched_text_excerpt": "원문 발췌", "fetch_ts": "..."}
  ]},
  "loop_ctx": {"outer_pass": {{OUTER_PASS}}, "inner_round": {{INNER_ROUND}}}
}
```
seed_candidate에는 네가 실제 사용한 canonical 값을 그대로 되비춰라(코드가 pin과 대조해
seed drift를 잡는다). 지어낸 소스·미인용 fact 금지.
```

- [ ] **Step 2: b 검증기 프롬프트 작성** — `prompts/research/b_market_verifier.md`:
```markdown
# b 시장분석 검증기 (Codex · crossmodel 적대적)

너는 반대 모델 검증자다. 방어하지 말고 공격하라. 첨부된 evidence_bundle의
`fetched_text`만 근거로 삼아라 — 모델 지식으로 채운 주장은 unsupported다.

## 검증 대상 (리서처 산출물 + 원문 evidence_bundle)
{{STAGE_OUTPUT_JSON}}

## canonical seed (이 값 기준으로 정합성 검사)
회사={{SEED_COMPANY}} / 시장={{SEED_MARKET}} / 통화={{SEED_CURRENCY}} /
기간={{SEED_PERIOD}} / 단위={{SEED_UNIT}} / as-of={{SEED_AS_OF}}

## 공격 축 (최소 {{MIN_FINDINGS}}개 약점 강제 — 없으면 소스 ref로 무결 증명)
1. 인용 소스가 실제로 그 수치를 지지하나(unsupported/hallucinated_source)
2. 추론이 사실을 넘나(overreach: 상관→인과, 추정→확정)
3. 누락 축(scope_miss: 경쟁·규제·하방리스크)
4. seed 계약 위반(통화·기간·단위를 몰래 바꿨나 → contradiction)
5. 시점 정합(as_of 없는 시점 의존 수치 = stale)

## 출력 (첫 줄 마커 + fenced JSON — 코드는 이 둘만 파싱)
CROSSMODEL_VERDICT: pass|needs_changes|blocked
```json
{
  "schema_version": 1, "adapter": "crossmodel", "stage_id": "b",
  "verdict": "pass|needs_changes|blocked",
  "findings": [
    {"claim_id": "b1|null", "severity": "critical|major|minor",
     "category": "unsupported|overreach|logic_gap|scope_miss|stale|contradiction|hallucinated_source",
     "quote": "원문 인용", "rebuttal": "...", "fix_directive": "리서처가 할 정확한 보정",
     "evidence_pointer": "s1"}
  ],
  "coverage": {"axes_checked": [], "axes_missing": []},
  "unchallenged_but_weak": [], "reviewer_model": "codex", "tokens_seen": 0
}
```
severity critical/major가 있거나 axes_missing이 비지 않으면 pass라 쓰지 마라 —
코드가 needs_changes로 강등한다.
```

- [ ] **Step 3: 별칭 추가** — `autoagent/artifacts.py`의 `PROMPT_ALIASES` 끝에 추가:
```python
    "b_market_researcher.md": "research/b_market_researcher.md",
    "b_market_verifier.md": "research/b_market_verifier.md",
```

- [ ] **Step 3b: b 검증기 프롬프트 배선** — `autoagent/workflows/research.py`의 `STAGE_VERIFIER_PROMPT`에 b 엔트리를 더해 b 스테이지가 crossmodel 대신 전용 검증기 프롬프트를 렌더하게 한다(Slice 1의 dead b_market_verifier 문제 해소):
```python
STAGE_VERIFIER_PROMPT = {"a": "crossmodel_verifier.md", "b": "b_market_verifier.md", "derive": "crossmodel_verifier.md"}
```
  주: `run_stage_loop`은 이미 `STAGE_VERIFIER_PROMPT.get(stage, "crossmodel_verifier.md")`로 검증기 프롬프트를 고르고 `values`(seed 5필드·`MIN_FINDINGS`·`STAGE_OUTPUT_JSON`)를 넘긴다 — 이 한 줄로 b가 전용 프롬프트를 타고 `{{SEED_COMPANY}}`·`{{MIN_FINDINGS}}`가 실제 치환된다.

- [ ] **Step 4: choose_researcher b 배정 확인** — Run: `python -c "from autoagent.routing import choose_researcher; print(choose_researcher('b'))"`
  Expected: `('claude', 'codex', ...)`.

- [ ] **Step 5: dry-run 렌더 검증** — Run:
  1. 리서처: `python -c "from autoagent.artifacts import render_template; t=render_template('b_market_researcher.md', {'SEED_COMPANY':'Acme','SEED_MARKET':'EV','SEED_CURRENCY':'KRW','SEED_PERIOD':'2021-2025','SEED_UNIT':'억원','SEED_AS_OF':'2025-06-01','OUTER_PASS':'2','INNER_ROUND':'1','INNER_FEEDBACK':'-','DEEPEN_DELTA':'경쟁 심화'}); assert '{{' not in t, '치환 안 된 placeholder 잔존'; assert 'Acme' in t and 'KRW' in t; print('b researcher render OK')"`
     Expected: `b researcher render OK`.
  2. 검증기(seed 5필드·MIN_FINDINGS 치환 확인): `python -c "from autoagent.artifacts import render_template; t=render_template('b_market_verifier.md', {'STAGE_OUTPUT_JSON':'{}','SEED_COMPANY':'Acme','SEED_MARKET':'EV','SEED_CURRENCY':'KRW','SEED_PERIOD':'2021-2025','SEED_UNIT':'억원','SEED_AS_OF':'2025-06-01','MIN_FINDINGS':'3'}); assert '{{' not in t, '치환 안 된 placeholder 잔존'; assert 'Acme' in t and '3' in t; print('b verifier render OK')"`
     Expected: `b verifier render OK`.
  3. `STAGE_VERIFIER_PROMPT` 배선: `python -c "from autoagent.workflows.research import STAGE_VERIFIER_PROMPT; assert STAGE_VERIFIER_PROMPT['b']=='b_market_verifier.md'; print('b verifier wired')"`
     Expected: `b verifier wired`.

- [ ] **Step 6: commit**
```bash
git add prompts/research/b_market_researcher.md prompts/research/b_market_verifier.md autoagent/artifacts.py autoagent/workflows/research.py
git commit -m "feat(research): b 시장분석 스테이지 프롬프트(claude 리서처/codex crossmodel) + 별칭 + STAGE_VERIFIER_PROMPT b 배선(§3)"
```

---

## Slice 5 — 인간 게이트 + 재개 + 커버리지 매트릭스/경고 배너

스펙 §6.2(분기점 전용 게이트)·§6.3(재개)·§2.3(커버리지 매트릭스/배너)·§8 F1(exhausted_unverified 격리). 게이트 판정·상태 영속/재개·매트릭스 렌더는 순수 결정론이라 pytest, `--resume`/`--auto-approve-nonbranch` CLI 배선은 dry-run 렌더로 검증한다.

> **통합 노트:** 이 슬라이스의 `state.py`(Task 25)는 `--resume` done 스킵·seed read-only pin 전용 재개 층이다. Slice 4 Task 22가 `research.py`에 넣은 `persist_research_state`/`load_research_state`는 바깥 루프 claim 수집·delta용이며, 둘 다 같은 `research_state.json` 파일을 읽고 쓴다(스키마 `{outer_pass, stage, inner_round, seed_pin, verified_claims, stage_status}` 공유). Task 27이 오케스트레이터에서 두 층을 최종 수렴시킨다.

---

### Task 24: 게이트 트리거 판정 (`autoagent/research/gates.py`)

스펙 §6.2: 게이트는 (1)고비용 심화 진입 (2)모순 승격 (3)`exhausted_unverified` 다수 (4)`blocked` — 네 분기점에서만 트리거되고 나머지는 자동. `--auto-approve-nonbranch`는 forced가 아닌 게이트만 자동 통과. 순수 판정 함수(정지 부수효과는 Task 27에서 사용할 `pause_at_gate`).

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\gates.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\test_research_gates.py`

**Interfaces:**
- Consumes: `autoagent.research.types.StageResult`(`.status`·`.stage_id`).
- Produces:
  - `@dataclass GateTrigger(kind: Literal["high_cost_deepen","contradiction","exhausted_unverified_many","blocked"], reason: str, forced: bool)`
  - `def evaluate_gate(*, event: str, outer_pass: int, stage_results: list[StageResult], contradiction: bool, config) -> GateTrigger | None`
  - `def should_pause(trigger: GateTrigger | None, *, auto_approve_nonbranch: bool) -> bool`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_research_gates.py`:
```python
"""research/gates.py 게이트 트리거 판정 테스트(순수 함수, 부수효과 없음)."""
from __future__ import annotations

from dataclasses import dataclass

from autoagent.research.gates import GateTrigger, evaluate_gate, should_pause
from autoagent.research.types import StageResult


@dataclass
class _Cfg:
    research_exhausted_gate_threshold: int = 2


def _sr(stage_id: str, status: str) -> StageResult:
    return StageResult(stage_id=stage_id, status=status, output_path=f"{stage_id}.md", verdict=None, inner_rounds=1)


def test_deepen_entry_is_forced_gate() -> None:
    t = evaluate_gate(event="deepen_entry", outer_pass=2, stage_results=[], contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "high_cost_deepen" and t.forced is True
    assert should_pause(t, auto_approve_nonbranch=True) is True


def test_deepen_entry_pass1_not_a_gate() -> None:
    assert evaluate_gate(event="deepen_entry", outer_pass=1, stage_results=[], contradiction=False, config=_Cfg()) is None


def test_contradiction_is_forced_gate() -> None:
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=[_sr("a", "resolved")], contradiction=True, config=_Cfg())
    assert t is not None and t.kind == "contradiction" and t.forced is True
    assert should_pause(t, auto_approve_nonbranch=True) is True


def test_blocked_is_forced_gate() -> None:
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=[_sr("a", "blocked")], contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "blocked" and t.forced is True


def test_exhausted_many_triggers_at_threshold() -> None:
    rs = [_sr("a", "exhausted_unverified"), _sr("b", "exhausted_unverified")]
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "exhausted_unverified_many" and t.forced is False
    assert should_pause(t, auto_approve_nonbranch=False) is True
    assert should_pause(t, auto_approve_nonbranch=True) is False


def test_exhausted_below_threshold_no_gate() -> None:
    rs = [_sr("a", "exhausted_unverified"), _sr("b", "resolved")]
    assert evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg()) is None


def test_all_resolved_is_no_gate() -> None:
    rs = [_sr("a", "resolved"), _sr("b", "resolved")]
    assert evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg()) is None


def test_forced_precedence_blocked_over_exhausted() -> None:
    rs = [_sr("a", "blocked"), _sr("b", "exhausted_unverified"), _sr("c", "exhausted_unverified")]
    t = evaluate_gate(event="stage_boundary", outer_pass=1, stage_results=rs, contradiction=False, config=_Cfg())
    assert t is not None and t.kind == "blocked" and t.forced is True


def test_should_pause_none_is_false() -> None:
    assert should_pause(None, auto_approve_nonbranch=False) is False
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_research_gates.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.gates'`, `0 passed`.

- [ ] **Step 3: 최소 구현** — `autoagent/research/gates.py`:
```python
"""리서치 워크플로 게이트 트리거 판정(스펙 §6.2, 분기점 전용).

게이트는 네 분기점에서만 트리거된다: (1)고비용 심화 진입 (2)모순 승격
(3)exhausted_unverified 다수 (4)blocked. 나머지 전이는 자동이다.
--auto-approve-nonbranch는 forced가 아닌 게이트만 자동 통과시키고,
고비용(high_cost_deepen)·모순(contradiction)·blocked는 forced라 절대 생략하지 않는다.
이 모듈은 순수 판정만 하고 정지 부수효과(산출물·stdout·checkpoint)는 pause_at_gate가 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from autoagent.research.types import StageResult

GateKind = Literal["high_cost_deepen", "contradiction", "exhausted_unverified_many", "blocked"]


@dataclass
class GateTrigger:
    """게이트 트리거 판정 결과. forced면 --auto-approve-nonbranch로도 생략 불가."""

    kind: GateKind
    reason: str
    forced: bool


def evaluate_gate(
    *, event: str, outer_pass: int, stage_results: list[StageResult], contradiction: bool, config: Any,
) -> GateTrigger | None:
    """분기점이면 GateTrigger, 아니면 None을 돌려준다.

    event는 전이 종류("deepen_entry"=바깥 pass 심화 진입, "stage_boundary"=스테이지 경계).
    forced 게이트(blocked > contradiction > high_cost_deepen)를 먼저 판정하고,
    그 다음 exhausted_unverified 다수(비-forced)를 본다.
    """
    if any(sr.status == "blocked" for sr in stage_results):
        blocked_ids = [sr.stage_id for sr in stage_results if sr.status == "blocked"]
        return GateTrigger(kind="blocked",
                           reason=f"Stage(s) blocked, verdict undecidable: {', '.join(blocked_ids)}.", forced=True)
    if contradiction:
        return GateTrigger(kind="contradiction",
                           reason="Verified claim reversed across passes (seed drift / contradiction).", forced=True)
    if event == "deepen_entry" and outer_pass >= 2:
        return GateTrigger(kind="high_cost_deepen", reason=f"Entering high-cost deepen pass {outer_pass}.", forced=True)
    threshold = getattr(config, "research_exhausted_gate_threshold", 2)
    exhausted = [sr.stage_id for sr in stage_results if sr.status == "exhausted_unverified"]
    if len(exhausted) >= threshold:
        return GateTrigger(kind="exhausted_unverified_many",
                           reason=f"{len(exhausted)} stage(s) exhausted_unverified (threshold {threshold}): {', '.join(exhausted)}.",
                           forced=False)
    return None


def should_pause(trigger: GateTrigger | None, *, auto_approve_nonbranch: bool) -> bool:
    """게이트에서 실제로 멈춰야 하는지. forced면 플래그 무시, 그 외는 플래그로 자동통과."""
    if trigger is None:
        return False
    if trigger.forced:
        return True
    return not auto_approve_nonbranch
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/test_research_gates.py -q`
  Expected: `9 passed`.

- [ ] **Step 5: commit**
```bash
git checkout -b feature/research-slice5-gates
git add autoagent/research/gates.py tests/test_research_gates.py
git commit -m "feat(research): 분기점 전용 게이트 트리거 판정(§6.2)"
```

---

### Task 25: 상태 영속 + 재개 (`autoagent/research/state.py`)

스펙 §6.3: `research_state.json`에 매 전이 영속. `--resume`는 done(resolved) 스테이지를 건너뛰고 미완 inner를 이어감, seed pin 고정. task_exec의 `load_exec_state`/`persist_status` 패턴.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\state.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\test_research_state.py`

**Interfaces:**
- Consumes: `autoagent.artifacts.write_json`, `autoagent.artifacts.read_text`; 상태 계약 `{outer_pass, stage, inner_round, seed_pin, verified_claims, stage_status}`.
- Produces:
  - `def load_or_init_state(run_dir: Path) -> dict`
  - `def persist_state(run_dir: Path, state: dict) -> None`
  - `def set_stage_status(run_dir: Path, state: dict, stage: str, status: str) -> None`
  - `def is_stage_done(state: dict, stage: str) -> bool` — resolved만 done.
  - `def resume_point(state: dict) -> tuple[int, str, int]`
  - `def pin_seed(run_dir: Path, state: dict, seed: dict) -> None` — 최초 1회만(read-only).
  - `STAGE_ORDER = ["a", "b", "c", "d", "derive"]`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_research_state.py`:
```python
"""research/state.py 상태 영속/재개 테스트(§6.3)."""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.research.state import (
    is_stage_done, load_or_init_state, persist_state, pin_seed, resume_point, set_stage_status,
)

STAGES = ["a", "b", "c", "d", "derive"]


def test_init_when_absent(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    assert st == {"outer_pass": 1, "stage": "a", "inner_round": 0,
                  "seed_pin": {}, "verified_claims": [], "stage_status": {}}
    assert not (tmp_path / "research_state.json").exists()


def test_persist_then_load_roundtrip(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["outer_pass"] = 2
    persist_state(tmp_path, st)
    assert load_or_init_state(tmp_path)["outer_pass"] == 2


def test_set_stage_status_persists(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    set_stage_status(tmp_path, st, "a", "resolved")
    on_disk = json.loads((tmp_path / "research_state.json").read_text(encoding="utf-8"))
    assert on_disk["stage_status"]["a"] == "resolved"
    assert st["stage_status"]["a"] == "resolved"


def test_is_stage_done_only_resolved(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["stage_status"] = {"a": "resolved", "b": "exhausted_unverified", "c": "blocked"}
    assert is_stage_done(st, "a") is True
    assert is_stage_done(st, "b") is False
    assert is_stage_done(st, "c") is False
    assert is_stage_done(st, "d") is False


def test_resume_point_skips_done(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["stage_status"] = {"a": "resolved", "b": "resolved"}
    st["inner_round"] = 2
    st["outer_pass"] = 1
    outer, stage, inner = resume_point(st)
    assert (outer, stage) == (1, "c")
    assert inner == 0


def test_resume_point_continues_incomplete_inner(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    st["stage_status"] = {"a": "resolved"}
    st["stage"] = "b"
    st["inner_round"] = 2
    outer, stage, inner = resume_point(st)
    assert (outer, stage, inner) == (1, "b", 2)


def test_pin_seed_is_read_only(tmp_path: Path) -> None:
    st = load_or_init_state(tmp_path)
    pin_seed(tmp_path, st, {"company": "Acme", "currency": "USD"})
    assert st["seed_pin"] == {"company": "Acme", "currency": "USD"}
    pin_seed(tmp_path, st, {"company": "OTHER", "currency": "KRW"})
    assert st["seed_pin"] == {"company": "Acme", "currency": "USD"}
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_research_state.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.state'`, `0 passed`.

- [ ] **Step 3: 최소 구현** — `autoagent/research/state.py`:
```python
"""리서치 워크플로 런타임 상태 영속/재개(스펙 §6.3).

research_state.json에 매 전이 영속하고, --resume는 이 파일을 읽어 done(resolved)
스테이지를 건너뛰고 미완 inner_round를 이어간다. seed_pin은 §5 canonical seed
불변식대로 최초 1회만 고정하고 이후 read-only다. task_exec의 load_exec_state(있으면
읽고 없으면 초기화)·persist_status(전이마다 재기록) 패턴과 동형이다.
"""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.artifacts import read_text, write_json

# 파이프라인 스테이지 고정 순서(§1). resume_point가 done 스킵에 쓴다.
STAGE_ORDER = ["a", "b", "c", "d", "derive"]


def load_or_init_state(run_dir: Path) -> dict:
    """research_state.json이 있으면 그대로, 없으면 초기 상태를 돌려준다(파일 미기록).

    초기화는 파일을 쓰지 않는다 — 첫 전이에서 persist_state가 기록한다(task_exec 관례).
    """
    path = run_dir / "research_state.json"
    if path.exists():
        return json.loads(read_text(path))
    return {
        "outer_pass": 1, "stage": "a", "inner_round": 0,
        "seed_pin": {}, "verified_claims": [], "stage_status": {},
    }


def persist_state(run_dir: Path, state: dict) -> None:
    """매 전이마다 research_state.json을 다시 써 재개 지점을 최신으로 유지한다."""
    write_json(run_dir / "research_state.json", state)


def set_stage_status(run_dir: Path, state: dict, stage: str, status: str) -> None:
    """단일 스테이지 status를 갱신하고 즉시 영속한다."""
    state.setdefault("stage_status", {})[stage] = status
    persist_state(run_dir, state)


def is_stage_done(state: dict, stage: str) -> bool:
    """스테이지가 재개 시 건너뛸 수 있는 완료 상태인지.

    resolved만 done이다. exhausted_unverified·blocked는 재개 시 다시 시도해야 하므로
    done이 아니다(§8 F1: 미검증을 조용히 통과시키지 않는다).
    """
    return state.get("stage_status", {}).get(stage) == "resolved"


def resume_point(state: dict) -> tuple[int, str, int]:
    """(outer_pass, 재개할 첫 미완 스테이지, inner_round)를 돌려준다.

    resolved 스테이지는 건너뛴다. 중단됐던 스테이지(state["stage"])가 아직 미완이면
    그 스테이지의 inner_round를 이어가고, 이미 넘어간 스테이지면 다음 미완 스테이지를
    새로 진입(inner_round=0)한다.
    """
    outer = state.get("outer_pass", 1)
    interrupted_stage = state.get("stage", "a")
    saved_inner = state.get("inner_round", 0)
    for stage in STAGE_ORDER:
        if is_stage_done(state, stage):
            continue
        inner = saved_inner if stage == interrupted_stage else 0
        return outer, stage, inner
    return outer, STAGE_ORDER[-1], saved_inner


def pin_seed(run_dir: Path, state: dict, seed: dict) -> None:
    """canonical seed를 최초 1회만 고정한다(§5 read-only 불변식).

    이미 seed_pin이 있으면 무시한다 — pass 2가 seed를 바꿔 계통 표류시키지 못하게 한다.
    """
    if state.get("seed_pin"):
        return
    state["seed_pin"] = dict(seed)
    persist_state(run_dir, state)
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/test_research_state.py -q`
  Expected: `7 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/state.py tests/test_research_state.py
git commit -m "feat(research): research_state.json 영속+재개(done 스킵·inner 이어감·seed pin, §6.3)"
```

---

### Task 26: 커버리지 매트릭스 + 경고 배너 렌더 (`autoagent/research/coverage.py`)

스펙 §2.3(커버리지 매트릭스 상단 강제, 100% 미만이면 경고 배너)·§8 F1(`exhausted_unverified`는 `UNVERIFIED` 배지 격리). 인라인 CSS·순수 문자열 반환.

**Files:**
- Create: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\coverage.py`
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\test_research_coverage.py`

**Interfaces:**
- Consumes: stage_status 매핑(`{stage: "resolved"|"exhausted_unverified"|"blocked"}`).
- Produces:
  - `def coverage_summary(stage_status: dict[str, str], stages: list[str]) -> dict` — `{total, resolved, unverified, blocked, missing, pct_resolved, complete}`.
  - `def render_coverage_matrix_html(stage_status, stages, *, stage_labels=None) -> str`
  - `def render_warning_banner_html(summary: dict) -> str` — complete면 빈 문자열.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_research_coverage.py`:
```python
"""research/coverage.py 커버리지 매트릭스+배너 렌더 테스트(§2.3, §8 F1)."""
from __future__ import annotations

from autoagent.research.coverage import (
    coverage_summary, render_coverage_matrix_html, render_warning_banner_html,
)

STAGES = ["a", "b", "c", "d", "derive"]


def test_summary_all_resolved_is_complete() -> None:
    s = coverage_summary({x: "resolved" for x in STAGES}, STAGES)
    assert s["total"] == 5 and s["resolved"] == 5 and s["pct_resolved"] == 100.0
    assert s["complete"] is True and s["unverified"] == 0 and s["blocked"] == 0 and s["missing"] == 0


def test_summary_counts_unverified_blocked_missing() -> None:
    ss = {"a": "resolved", "b": "exhausted_unverified", "c": "blocked"}
    s = coverage_summary(ss, STAGES)
    assert s["resolved"] == 1 and s["unverified"] == 1 and s["blocked"] == 1 and s["missing"] == 2
    assert s["pct_resolved"] == 20.0 and s["complete"] is False


def test_matrix_marks_exhausted_as_unverified_badge() -> None:
    html = render_coverage_matrix_html({"a": "resolved", "b": "exhausted_unverified"}, ["a", "b"])
    assert "UNVERIFIED" in html
    assert "<table" in html and "</table>" in html
    assert "PASSED" in html


def test_matrix_uses_stage_labels_when_given() -> None:
    html = render_coverage_matrix_html({"a": "resolved"}, ["a"], stage_labels={"a": "회사 리서치"})
    assert "회사 리서치" in html


def test_banner_empty_when_complete() -> None:
    s = coverage_summary({x: "resolved" for x in STAGES}, STAGES)
    assert render_warning_banner_html(s) == ""


def test_banner_present_and_lists_gaps_when_incomplete() -> None:
    s = coverage_summary({"a": "resolved", "b": "exhausted_unverified", "c": "blocked"}, STAGES)
    banner = render_warning_banner_html(s)
    assert banner != ""
    assert "20.0%" in banner or "20%" in banner
    assert "UNVERIFIED" in banner or "b" in banner
    assert "blocked" in banner or "c" in banner


def test_matrix_escapes_html_in_labels() -> None:
    html = render_coverage_matrix_html({"a": "resolved"}, ["a"], stage_labels={"a": "<script>x</script>"})
    assert "<script>" not in html and "&lt;script&gt;" in html
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_research_coverage.py -q`
  Expected: `ModuleNotFoundError: No module named 'autoagent.research.coverage'`, `0 passed`.

- [ ] **Step 3: 최소 구현** — `autoagent/research/coverage.py`:
```python
"""커버리지 매트릭스 + 경고 배너 HTML 렌더(스펙 §2.3, §8 F1).

최종 리포트 상단에 스테이지별 검증 상태 표를 강제로 넣고, 전부 resolved가 아니면
100% 미만 경고 배너를 붙인다. exhausted_unverified 스테이지는 UNVERIFIED 배지로
격리 표기한다(§8 F1: 미검증을 신뢰 결과와 섞지 않는다). pandoc·외부 자원 없이
인라인 CSS만 쓴다(deliver-local-html 준수, standalone HTML).
"""
from __future__ import annotations

from html import escape

# 런타임 status → (표시 라벨, 전경색, 배경색). exhausted는 UNVERIFIED로 격리.
_STATUS_BADGE = {
    "resolved": ("PASSED", "#1a7f37", "#dafbe1"),
    "exhausted_unverified": ("UNVERIFIED", "#9a6700", "#fff8c5"),
    "blocked": ("BLOCKED", "#cf222e", "#ffebe9"),
    "missing": ("SKIPPED", "#57606a", "#eaeef2"),
}


def coverage_summary(stage_status: dict[str, str], stages: list[str]) -> dict:
    """스테이지 상태를 집계한다. stages에 있으나 status 없으면 missing으로 센다."""
    resolved = unverified = blocked = missing = 0
    for stage in stages:
        status = stage_status.get(stage)
        if status == "resolved":
            resolved += 1
        elif status == "exhausted_unverified":
            unverified += 1
        elif status == "blocked":
            blocked += 1
        else:
            missing += 1
    total = len(stages)
    pct = round(resolved / total * 100, 1) if total else 0.0
    return {
        "total": total, "resolved": resolved, "unverified": unverified, "blocked": blocked,
        "missing": missing, "pct_resolved": pct, "complete": resolved == total and total > 0,
    }


def _badge_html(status: str) -> str:
    label, fg, bg = _STATUS_BADGE.get(status, _STATUS_BADGE["missing"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        f'font-size:12px;font-weight:600;color:{fg};background:{bg};">{label}</span>'
    )


def render_coverage_matrix_html(
    stage_status: dict[str, str], stages: list[str], *, stage_labels: dict[str, str] | None = None,
) -> str:
    """스테이지별 검증 상태 표를 HTML로 렌더한다(리포트 상단 강제용).

    각 행: 스테이지 라벨 + 상태 배지. exhausted_unverified는 UNVERIFIED 배지로 격리.
    라벨은 escape로 주입을 막는다.
    """
    stage_labels = stage_labels or {}
    rows = []
    for stage in stages:
        status = stage_status.get(stage) or "missing"
        label = escape(stage_labels.get(stage, stage))
        rows.append(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #d0d7de;">{label}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #d0d7de;">{_badge_html(status)}</td></tr>'
        )
    return (
        '<table style="border-collapse:collapse;width:100%;max-width:640px;margin:0 0 16px;'
        'font-family:system-ui,-apple-system,sans-serif;">'
        '<thead><tr>'
        '<th style="text-align:left;padding:6px 12px;border-bottom:2px solid #24292f;">스테이지</th>'
        '<th style="text-align:left;padding:6px 12px;border-bottom:2px solid #24292f;">검증 상태</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_warning_banner_html(summary: dict) -> str:
    """커버리지 100% 미만이면 경고 배너 HTML, 완전하면 빈 문자열."""
    if summary.get("complete"):
        return ""
    parts = []
    if summary["unverified"]:
        parts.append(f'{summary["unverified"]}개 스테이지 UNVERIFIED')
    if summary["blocked"]:
        parts.append(f'{summary["blocked"]}개 스테이지 blocked')
    if summary["missing"]:
        parts.append(f'{summary["missing"]}개 스테이지 미착수(SKIPPED)')
    detail = ", ".join(parts) if parts else "일부 스테이지가 검증을 통과하지 못했습니다"
    return (
        '<div style="border:2px solid #cf222e;background:#ffebe9;border-radius:8px;'
        'padding:12px 16px;margin:0 0 16px;font-family:system-ui,-apple-system,sans-serif;">'
        f'<strong style="color:#cf222e;">⚠ 검증 커버리지 {summary["pct_resolved"]}% '
        "(100% 미만) — 이 리포트는 완전 검증본이 아닙니다.</strong>"
        f'<div style="margin-top:6px;color:#57606a;font-size:14px;">{escape(detail)}. '
        "UNVERIFIED/blocked 스테이지의 주장은 도출(derive)·신뢰도 계산에서 제외되었습니다.</div>"
        "</div>"
    )
```

- [ ] **Step 4: 통과 확인** — Run: `python -m pytest tests/test_research_coverage.py -q`
  Expected: `7 passed`.

- [ ] **Step 5: commit**
```bash
git add autoagent/research/coverage.py tests/test_research_coverage.py
git commit -m "feat(research): 커버리지 매트릭스 표+100%미만 경고배너(§2.3), exhausted→UNVERIFIED 격리(§8 F1)"
```

---

### Task 27: CLI 배선 (`--auto-approve-nonbranch`) + 재개 정지 아티팩트 (`pause_at_gate`)

스펙 §6.2: `--auto-approve-nonbranch` 플래그 신설(분기점 아닌 게이트만 자동 통과), 게이트 도달 시 stdout 고정 라인 + 정지 이유·resume_command 산출물 기록. 기존 `--resume`를 research 재개 경로에 물린다. `routed_common.resume_command_for`의 고정 라인 규약을 따른다.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\cli.py` (`--auto-approve-nonbranch` 추가; research 재개 분기)
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\gates.py` (`pause_at_gate` 추가)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\test_research_gate_pause.py`

**Interfaces:**
- Consumes: `autoagent.workflows.routed_common.resume_command_for`, `autoagent.artifacts.{write_json,write_text}`, `GateTrigger`(Task 24).
- Produces:
  - `def pause_at_gate(run_dir, trigger: GateTrigger, state: dict) -> int` — `gate_status.json` + `gate_required.md` 기록, stdout 고정 라인(`RESEARCH_STATUS`/`RUN_DIR`/`RESUME_COMMAND`/`GATE_KIND`), return 0.
  - cli.py: `args.auto_approve_nonbranch: bool`(default False), `--workflow research` 재개 분기.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_research_gate_pause.py`:
```python
"""게이트 정지 부수효과(pause_at_gate) 테스트: 산출물+고정 stdout 라인."""
from __future__ import annotations

import json
from pathlib import Path

from autoagent.research.gates import GateTrigger, pause_at_gate


def test_pause_writes_status_and_prints_fixed_lines(tmp_path: Path, capsys) -> None:
    trigger = GateTrigger(kind="high_cost_deepen", reason="Entering high-cost deepen pass 2.", forced=True)
    state = {"outer_pass": 2, "stage": "b", "inner_round": 0}
    rc = pause_at_gate(tmp_path, trigger, state)
    assert rc == 0

    status = json.loads((tmp_path / "gate_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "waiting_for_human_approval"
    assert status["approved"] is False
    assert status["gate_kind"] == "high_cost_deepen"
    assert status["forced"] is True
    assert status["reason"] == "Entering high-cost deepen pass 2."
    assert status["run_dir"] == str(tmp_path)
    assert "--resume" in status["resume_command"]
    assert (tmp_path / "gate_required.md").exists()

    out = capsys.readouterr().out
    assert "RESEARCH_STATUS: waiting_for_human_approval" in out
    assert f"RUN_DIR: {tmp_path}" in out
    assert "RESUME_COMMAND: " in out
    assert "GATE_KIND: high_cost_deepen" in out


def test_pause_resume_command_matches_routed_convention(tmp_path: Path) -> None:
    from autoagent.workflows.routed_common import resume_command_for
    pause_at_gate(tmp_path, GateTrigger(kind="contradiction", reason="reversed", forced=True),
                  {"outer_pass": 1, "stage": "a", "inner_round": 1})
    status = json.loads((tmp_path / "gate_status.json").read_text(encoding="utf-8"))
    assert status["resume_command"] == resume_command_for(tmp_path)
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/test_research_gate_pause.py -q`
  Expected: `ImportError: cannot import name 'pause_at_gate' from 'autoagent.research.gates'`, `0 passed`.

- [ ] **Step 3: 최소 구현 — gates.py 정지 부수효과** — `autoagent/research/gates.py` 하단에 추가:
```python
def pause_at_gate(run_dir, trigger: GateTrigger, state: dict) -> int:
    """게이트 도달 시 정지 산출물을 남기고 stdout 고정 라인을 찍는다(§6.2, 무인 deadlock 차단).

    routed_common.block_for_human_approval의 규약을 리서치용으로 옮긴 것:
    - gate_status.json: 기계판독 상태(status/gate_kind/forced/reason/resume_command).
    - gate_required.md: 사람이 읽을 정지 사유 + 재개 명령.
    - stdout 고정 라인: RESEARCH_STATUS/RUN_DIR/RESUME_COMMAND/GATE_KIND — 구동 측(사람/CLI)이
      run_dir·재개 명령을 안정적으로 집도록 한다. resume_command는 routed와 동일 함수로 만든다.
    """
    from pathlib import Path

    from autoagent.artifacts import write_json, write_text
    from autoagent.workflows.routed_common import resume_command_for

    run_dir = Path(run_dir)
    resume_command = resume_command_for(run_dir)
    status = {
        "status": "waiting_for_human_approval", "approved": False, "required": True,
        "gate_kind": trigger.kind, "forced": trigger.forced, "reason": trigger.reason,
        "run_dir": str(run_dir), "resume_command": resume_command,
        "state": {"outer_pass": state.get("outer_pass"), "stage": state.get("stage"),
                  "inner_round": state.get("inner_round")},
    }
    write_json(run_dir / "gate_status.json", status)
    write_text(
        run_dir / "gate_required.md",
        "# 리서치 게이트 — 인간 승인 필요\n\n"
        f"게이트 종류: **{trigger.kind}** (forced={trigger.forced})\n\n"
        f"사유: {trigger.reason}\n\n"
        f"상태: outer_pass={state.get('outer_pass')}, stage={state.get('stage')}, "
        f"inner_round={state.get('inner_round')}\n\n"
        "검토 후 재개하려면(이 명령 실행 자체가 승인):\n\n"
        f"```powershell\n{resume_command}\n```\n",
    )
    print("RESEARCH_STATUS: waiting_for_human_approval")
    print(f"RUN_DIR: {run_dir}")
    print(f"RESUME_COMMAND: {resume_command}")
    print(f"GATE_KIND: {trigger.kind}")
    print(f"Research run waiting for human approval ({trigger.kind}): {run_dir}")
    return 0
```

- [ ] **Step 4: 최소 구현 — cli.py 플래그** — `cli.py`의 `--dry-run` 정의 바로 뒤에 삽입:
```python
    parser.add_argument(
        "--auto-approve-nonbranch", action="store_true",
        help="Research workflow: auto-pass non-branch gates (never skips forced high-cost/contradiction/blocked gates)",
    )
```

- [ ] **Step 5: 최소 구현 — cli.py research 재개 분기** — `cli.py`의 `if args.resume:` 블록에는 이미 `run_dir = Path(args.resume)`(현행 128행)와 `config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)`(현행 130행)가 있다. **그 `write_claude_mcp_config` 줄을 중복 추가하지 말고**, 기존 `mode = resume_mode(run_dir)`(현행 131행) **직전**에 research 우선 분기 3줄만 삽입한다(그래야 `resume_mode`가 `checkpoint.json` 부재로 `SystemExit` 하기 전에 research로 빠진다):
```python
        # (기존) run_dir = Path(args.resume)
        # (기존) config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)
        if (run_dir / "research_state.json").exists():
            from autoagent.workflows.research import run_research_workflow
            return run_research_workflow(args, config, None, run_dir)
        mode = resume_mode(run_dir)  # (기존) — 이 줄 바로 위에 위 3줄을 넣는다
```
  (research 재개는 `request=None`으로 진입 — Task 28의 `run_research_workflow`가 저장된 seed/상태에서 복원.)

- [ ] **Step 6: 통과 확인** — Run:
  1. `python -m pytest tests/test_research_gate_pause.py -q` → `2 passed`.
  2. `python -c "from autoagent.cli import build_parser; a=build_parser().parse_args(['--workflow','research','--auto-approve-nonbranch','--dry-run','--request','x']); print(a.workflow, a.auto_approve_nonbranch)"`
     Expected: `research True`. (parser 빌더 함수명이 다르면 실제 이름으로 맞춘다 — cli.py의 argparse 구성 함수.)

- [ ] **Step 7: commit**
```bash
git add autoagent/cli.py autoagent/research/gates.py tests/test_research_gate_pause.py
git commit -m "feat(research): --auto-approve-nonbranch 플래그 + 게이트 정지 산출물/고정 stdout 라인(§6.2)"
```

---

### Task 28: 오케스트레이터 통합 (게이트 호출 + 재개 + 매트릭스 주입)

`run_research_workflow`를 최소경로 단일 pass에서 **바깥 루프 + 게이트 + 재개 + 커버리지 매트릭스**로 확장한다: 바깥 루프를 인라인으로 돌리며(Task 22의 `run_outer_loop`는 호출하지 않고 그 하위 판정 헬퍼 `collect_verified_claims`/`diff_verified_claims`/`decide_outer_pass`/`detect_seed_violations`만 재사용) 스테이지 경계·심화 진입에서 `evaluate_gate`→`should_pause`→`pause_at_gate`, `state.py`(Task 25)로 done 스킵/재개, 최종 HTML 상단에 매트릭스+배너 주입. `stage_status`는 `set_stage_status`의 **평면 키**(stage→status) 규약 하나로 통일한다. 계약 시그니처 `run_research_workflow(args, config, request, run_dir) -> int`/`run_stage_loop(stage, outer_pass, ctx) -> StageResult` 불변. 모델 호출부라 dry-run 렌더로 검증.

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py` (`run_research_workflow` 재작성 — 바깥 루프·게이트·재개·매트릭스)
- Modify: `C:\Users\systran\Desktop\AutoAgent\prompts\research\final_html_report.md` (상단에 `{{COVERAGE_BANNER}}`/`{{COVERAGE_MATRIX}}` placeholder)

**Interfaces:**
- Consumes: `research.gates.{evaluate_gate,should_pause,pause_at_gate}`(Task 24·27), `research.state.{load_or_init_state,persist_state,set_stage_status,is_stage_done,resume_point,pin_seed,STAGE_ORDER}`(Task 25), `research.coverage.{coverage_summary,render_coverage_matrix_html,render_warning_banner_html}`(Task 26), Slice 4의 pass간 판정 헬퍼 `{collect_verified_claims,diff_verified_claims,decide_outer_pass,detect_seed_violations,seed_pin_from_dict,_extract_seed_candidate}`(Task 20·22), `research.html_report.{render_report_html,write_desktop_report}`, `artifacts.render_template`. (주의: 바깥 루프는 이 오케스트레이터가 **인라인**으로 돌린다 — `run_outer_loop`(Task 22)는 여기서 호출하지 않는다. Task 22의 `run_outer_loop`는 Slice 4 자립 배선·테스트 전용이며 Task 28은 그 하위 헬퍼만 재사용한다.)
- Produces: 통합된 `run_research_workflow`(재개·게이트·매트릭스 주입 포함).

- [ ] **Step 1: 템플릿 placeholder 보장** — `prompts/research/final_html_report.md` 최상단(첫 헤딩 앞)에 삽입:
```markdown
{{COVERAGE_BANNER}}

{{COVERAGE_MATRIX}}
```

- [ ] **Step 2: 실패 확인(현행 dry-run은 매트릭스 미주입)** — Run: `python run.py --dry-run --workflow research --request "Acme 회사 리서치 후 도출 리포트" --max-agent-calls 0`
  Expected: 종료 0이지만 최종 `final_report.html`에 `COVERAGE_MATRIX`가 placeholder 그대로 남거나(미주입) 매트릭스 표가 없다 — 이 부재가 실패-우선 신호. (Slice 1~4까지 최소경로는 `MINIMAL_PATH=["a","derive"]`로 돌지만, 커버리지 매트릭스/배너 HTML은 아직 주입 안 됨.)

- [ ] **Step 3: 최소 구현 — `run_research_workflow` 재작성** — `autoagent/workflows/research.py`의 `run_research_workflow`를 아래로 교체한다(상단 import에 gates·state·coverage를 더한다). `STAGE_LABELS`/전체 스테이지 순회를 도입하고, 게이트를 스테이지 경계·심화 진입에 건다:
```python
from autoagent.research.coverage import (
    coverage_summary, render_coverage_matrix_html, render_warning_banner_html,
)
from autoagent.research.gates import evaluate_gate, pause_at_gate, should_pause
from autoagent.research.state import (
    is_stage_done, load_or_init_state, persist_state, pin_seed, resume_point, set_stage_status, STAGE_ORDER,
)

# 리포트 커버리지 표에 쓸 스테이지 한글 라벨.
STAGE_LABELS = {"a": "회사 리서치", "b": "시장 분석", "c": "CSV 정제", "d": "팩트 리포트", "derive": "도출"}
# 바깥 루프 상한(스펙 §1: 심화 2회). config에 값이 있으면 그것을 우선한다.
DEFAULT_MAX_OUTER = 2
DEFAULT_MIN_NEW_CLAIMS = 2


def run_research_workflow(args: Namespace, config: Config, request: str | None, run_dir: Path) -> int:
    """리서치 워크플로 진입점(전체 파이프라인 + 바깥 루프 + 게이트 + 재개 + 커버리지).

    seed 확정·pin → 바깥 pass 1..N(스테이지 a..derive를 안쪽 루프로) → 스테이지 경계·심화
    진입 게이트 → 커버리지 매트릭스+배너를 상단에 박은 standalone HTML을 바탕화면에 저장한다.
    request=None은 --resume 진입(저장된 seed/상태에서 복원). dry-run이면 CLI 미호출.
    """
    budget = AgentCallBudget(args.max_agent_calls)
    state = load_or_init_state(run_dir)
    max_outer = getattr(config, "research_max_outer", DEFAULT_MAX_OUTER)
    min_new_claims = getattr(config, "research_min_new_claims", DEFAULT_MIN_NEW_CLAIMS)
    auto_nonbranch = getattr(args, "auto_approve_nonbranch", False)

    ctx = ResearchContext(
        args=args, config=config, request=request or "", run_dir=run_dir, budget=budget, seed_contract="",
        state=state,
    )

    # preamble: seed 확정 후 read-only pin(재개면 기존 pin 재사용, seed 스텝 스킵).
    if not state.get("seed_pin"):
        seed_out = _run_agent_step(
            ctx, agent="claude", role_id="researcher", name="00_seed_contract",
            prompt_name="seed_contract.md",
            prompt_values={"REQUEST": ctx.request, "WORKSPACE": str(config.workspace)},
            next_step="seed",
            dry_output='SEED_CONTRACT_JSON\n```json\n{"company":"[dry-run]","market":"[dry-run]",'
                       '"base_currency":"KRW","period":"2021-2025","unit":"억원"}\n```\n',
        )
        ctx.seed_contract = seed_out
        try:
            pin_seed(run_dir, state, extract_json_block(seed_out))
        except Exception:  # noqa: BLE001 - dry-run/파싱 실패여도 최소경로는 진행
            pin_seed(run_dir, state, {"company": "[dry-run]", "market": "-",
                                      "base_currency": "KRW", "period": "-", "unit": "-"})
    else:
        ctx.seed_contract = json.dumps(state["seed_pin"], ensure_ascii=False)

    resume_outer, _resume_stage, _resume_inner = resume_point(state)
    prev_claims: list[dict] = state.get("verified_claims", [])

    # M3: 루프가 한 번도 안 돌아도(예: max_outer=0) 최종 리포트에서 항상 바인딩되도록 선초기화.
    stage_results: list[StageResult] = []
    for outer_pass in range(resume_outer, max_outer + 1):
        state["outer_pass"] = outer_pass
        persist_state(run_dir, state)

        # 고비용 심화 진입 게이트(pass 2+, forced).
        deepen_trigger = evaluate_gate(event="deepen_entry", outer_pass=outer_pass,
                                       stage_results=[], contradiction=False, config=config)
        if should_pause(deepen_trigger, auto_approve_nonbranch=auto_nonbranch):
            return pause_at_gate(run_dir, deepen_trigger, state)

        stage_results = []  # 이 pass의 스테이지 결과(위에서 선초기화한 변수를 pass마다 재설정)
        for stage in STAGE_ORDER:
            state["stage"] = stage
            if is_stage_done(state, stage):
                continue  # 재개 시 resolved 스테이지 건너뜀
            result = run_stage_loop(stage, outer_pass, ctx)
            stage_results.append(result)
            set_stage_status(run_dir, state, stage, result.status)

            # 스테이지 경계 게이트(blocked·exhausted 다수).
            boundary = evaluate_gate(event="stage_boundary", outer_pass=outer_pass,
                                     stage_results=stage_results, contradiction=False, config=config)
            if should_pause(boundary, auto_approve_nonbranch=auto_nonbranch):
                return pause_at_gate(run_dir, boundary, state)

        # pass간 검증 claim 수집·delta·수렴/모순 판정.
        curr_claims = collect_verified_claims(stage_results)
        delta = diff_verified_claims(prev_claims, curr_claims)
        seed_violations = []
        if outer_pass > 1:
            seed_violations = detect_seed_violations(
                seed_pin_from_dict(state["seed_pin"]), _extract_seed_candidate(stage_results)
            )
        decision = decide_outer_pass(delta, seed_violations, outer_pass=outer_pass,
                                     max_outer=max_outer, min_new_claims=min_new_claims)
        state["verified_claims"] = prev_claims + delta.added
        state["outer_decision"] = {"action": decision.action, "reason": decision.reason,
                                   "contradictions": decision.contradictions}
        persist_state(run_dir, state)

        if decision.action == "gate":
            # 모순/seed위반 = forced 게이트(절대 생략 안 함).
            trigger = evaluate_gate(event="stage_boundary", outer_pass=outer_pass,
                                    stage_results=stage_results, contradiction=True, config=config)
            if trigger is not None:
                return pause_at_gate(run_dir, trigger, state)
        if decision.action in {"early_stop", "gate"}:
            break
        prev_claims = state["verified_claims"]

    # 커버리지 매트릭스+배너를 상단에 박은 최종 리포트.
    # M1 stage_status 키 규약 통일: set_stage_status(Task 25)가 평면 키(stage→status)로 쓰므로
    # 리포트도 평면 키만 읽는다(outer 프리픽스 조회 제거 — run_outer_loop의 "{outer}:{stage}" 규약은
    # 이 오케스트레이터에서 쓰지 않는다).
    stage_status_for_report = {s: state["stage_status"].get(s, "missing") for s in STAGE_ORDER}
    summary = coverage_summary(stage_status_for_report, STAGE_ORDER)
    matrix_html = render_coverage_matrix_html(stage_status_for_report, STAGE_ORDER, stage_labels=STAGE_LABELS)
    banner_html = render_warning_banner_html(summary)
    body_md = render_template("final_html_report.md", {
        "COVERAGE_BANNER": banner_html, "COVERAGE_MATRIX": matrix_html,
        "COVERAGE_MATRIX_MD": _coverage_matrix_md(stage_results),  # M3: 루프 앞 선초기화라 항상 바인딩됨
        "REQUEST": ctx.request, "SEED_CONTRACT": ctx.seed_contract,
        "STAGE_A_OUTPUT": ctx.stage_outputs.get("a", "(없음)"),
        "DERIVE_OUTPUT": ctx.stage_outputs.get("derive", "(없음)"),
    })
    html = render_report_html(title="리서치 리포트", body_md=body_md)
    write_text(run_dir / "final_report.html", html)
    if args.dry_run:
        print(f"Research dry run written to {run_dir}")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop_path = write_desktop_report(html, f"research_report_{stamp}.html")
    try:
        import os
        os.startfile(str(desktop_path))
    except Exception:  # noqa: BLE001
        pass
    print(f"Research run complete: {run_dir}\nReport: {desktop_path}")
    return 0
```
  주: `final_html_report.md`는 `COVERAGE_MATRIX_MD`(Slice 1 markdown 표)와 `COVERAGE_MATRIX`/`COVERAGE_BANNER`(HTML) placeholder를 모두 갖는다 — markdown 본문에는 HTML 조각이 그대로 통과되므로(최소 파서가 미지원 태그를 문단으로 감싸도 표는 인라인 style 유지) 상단 강제가 성립한다. `_coverage_matrix_md`는 Slice 1이 이미 정의한 헬퍼.

- [ ] **Step 4: dry-run 검증** — Run:
  1. `python run.py --dry-run --workflow research --request "Acme 회사 리서치 후 도출 리포트" --max-agent-calls 0`
     Expected: 종료 0, run_dir에 `research_state.json`·스테이지별 `*_prompt.md`/`*_command.json`·`final_report.html`. `final_report.html`을 Read(utf-8)로 열어 `UNVERIFIED` 또는 `PASSED` 배지와 스테이지 표가 들어있고 `{{COVERAGE_MATRIX}}` 잔여가 없는지 확인(cp949 깨짐 방지 — cat 금지).
  2. 재개 스모크: `python run.py --dry-run --resume "<위 run_dir>"` → `research_state.json` 감지 → research 재개(resume_point로 done 스킵), 종료 0.

- [ ] **Step 5: 전체 회귀** — Run: `python -m pytest tests/ -q`
  Expected: Slice 1~5 신설 테스트 전부 통과(gates 9 + state 7 + coverage 7 + gate_pause 2 = Slice 5의 25 + 앞 슬라이스 누적). 그리고 `python run.py --dry-run --workflow routed --task-type backend --request "add endpoint"` → exit 0(routed 불변).

- [ ] **Step 6: commit**
```bash
git add autoagent/workflows/research.py prompts/research/final_html_report.md
git commit -m "feat(research): 오케스트레이터에 게이트·재개·커버리지매트릭스 통합(§2.3/§6.2/§6.3/§8 F1)"
```

---

### Task 29: 계층 예산(스테이지별/outer별 tiered cap) + capture 상한(MAX_CAPTURE_CHARS) (`research.py`)

스펙 §6.4 부분 이행: runner의 평면 `AgentCallBudget`(전역 카운트) 위에 **스테이지별·outer별 호출 상한**(간단한 tiered cap)과 **capture 상한**(`MAX_CAPTURE_CHARS` tail 절단)을 얹어 최악 폭주(F5)와 컨텍스트 팽창을 막는다. 순수 로직이라 pytest로 못박는다. (컨텍스트 외부화=요약+포인터는 이번 슬라이스 비목표 — 말미 "스코프 아웃 델타" 참조.)

**Files:**
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py` (`TieredCallCap`·`_truncate_capture`·상수 신설, `run_stage_loop` 배선)
- Modify: `C:\Users\systran\Desktop\AutoAgent\autoagent\config.py` (`Config`에 tiered cap 기본값 필드)
- Test: `C:\Users\systran\Desktop\AutoAgent\tests\research\test_research_budget.py`

**Interfaces:**
- Consumes: `research.types.StageResult`(불변), 기존 `AgentCallBudget`(전역, 불변).
- Produces:
  - `MAX_CAPTURE_CHARS = 12000`(기본; config `research_max_capture_chars`로 조정).
  - `def _truncate_capture(text: str, limit: int = MAX_CAPTURE_CHARS) -> str` — tail 절단(머리 보존 + 절단 표식). 순수.
  - `@dataclass TieredCallCap(per_stage: int, per_outer: int)` + `def charge(self, outer_pass: int, stage: str) -> None` — 스테이지별/outer별 카운트 증가·초과 시 `TieredBudgetStopped`. 순수.
  - `run_stage_loop`이 매 리서처/검증기 호출 전 `tiered.charge(...)`를 부르고, `ctx.stage_outputs`에 담기는 값은 `_truncate_capture`로 상한 절단.

- [ ] **Step 1: 실패 테스트 작성** — `tests/research/test_research_budget.py`:
```python
"""계층 예산(tiered cap) + capture 절단 단위테스트(순수 로직)."""
from __future__ import annotations

import pytest

from autoagent.workflows.research import (
    MAX_CAPTURE_CHARS, TieredBudgetStopped, TieredCallCap, _truncate_capture,
)


def test_truncate_keeps_head_and_marks_when_over_limit() -> None:
    text = "가" * (MAX_CAPTURE_CHARS + 500)
    out = _truncate_capture(text)
    assert len(out) <= MAX_CAPTURE_CHARS + 64  # 절단 표식 여유
    assert out.startswith("가")
    assert "truncated" in out.lower()


def test_truncate_noop_under_limit() -> None:
    assert _truncate_capture("짧은 텍스트") == "짧은 텍스트"


def test_tiered_cap_charges_per_stage() -> None:
    cap = TieredCallCap(per_stage=2, per_outer=99)
    cap.charge(1, "a"); cap.charge(1, "a")
    with pytest.raises(TieredBudgetStopped):
        cap.charge(1, "a")  # 스테이지 a에서 3번째 → 초과


def test_tiered_cap_isolates_stages_and_passes() -> None:
    cap = TieredCallCap(per_stage=1, per_outer=99)
    cap.charge(1, "a")  # ok
    cap.charge(1, "b")  # 다른 스테이지 → 독립
    cap.charge(2, "a")  # 다른 outer_pass → 독립


def test_tiered_cap_charges_per_outer() -> None:
    cap = TieredCallCap(per_stage=99, per_outer=2)
    cap.charge(1, "a"); cap.charge(1, "b")
    with pytest.raises(TieredBudgetStopped):
        cap.charge(1, "c")  # outer_pass 1 전체 3번째 → 초과
```

- [ ] **Step 2: 실패 확인** — Run: `python -m pytest tests/research/test_research_budget.py -q`
  Expected: `ImportError: cannot import name 'TieredCallCap'` — 5 error.

- [ ] **Step 3: 최소 구현 — research.py** — `autoagent/workflows/research.py` 상단(상수 구역)에 추가:
```python
# capture 상한(§6.4). 리서처/검증기 stdout이 다음 프롬프트로 재주입될 때 tail 절단해
# 컨텍스트 팽창을 막는다. config research_max_capture_chars로 조정.
MAX_CAPTURE_CHARS = 12000


def _truncate_capture(text: str, limit: int = MAX_CAPTURE_CHARS) -> str:
    """텍스트가 limit을 넘으면 머리(limit자)만 남기고 tail을 절단 표식으로 대체한다.

    재주입되는 컨텍스트(예: STAGE_A_OUTPUT을 derive/report로 넘길 때)의 폭주를 막는다.
    JSON 파싱 대상(verify로 넘기는 원문)엔 적용하지 않는다 — 파싱 깨짐 방지.
    """
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return text[:limit] + f"\n\n[... {dropped} chars truncated (MAX_CAPTURE_CHARS)]"


class TieredBudgetStopped(Exception):
    """스테이지별/outer별 호출 상한 초과. 전역 AgentCallBudgetStopped와 별개(계층 예산)."""


@dataclass
class TieredCallCap:
    """전역 예산 위에 얹는 스테이지별/outer별 호출 상한(§6.4, F5 폭주 방어).

    per_stage: (outer_pass, stage) 조합당 최대 모델 호출 수.
    per_outer: 한 outer_pass 전체 최대 모델 호출 수.
    dry-run에선 charge를 부르지 않는다(실제 호출만 과금).
    """

    per_stage: int
    per_outer: int
    _by_stage: dict[tuple[int, str], int] = field(default_factory=dict)
    _by_outer: dict[int, int] = field(default_factory=dict)

    def charge(self, outer_pass: int, stage: str) -> None:
        s = self._by_stage.get((outer_pass, stage), 0) + 1
        o = self._by_outer.get(outer_pass, 0) + 1
        if self.per_stage > 0 and s > self.per_stage:
            raise TieredBudgetStopped(f"stage cap {self.per_stage} exceeded at pass {outer_pass} stage {stage}")
        if self.per_outer > 0 and o > self.per_outer:
            raise TieredBudgetStopped(f"outer cap {self.per_outer} exceeded at pass {outer_pass}")
        self._by_stage[(outer_pass, stage)] = s
        self._by_outer[outer_pass] = o
```
  `ResearchContext`의 `tiered: TieredCallCap | None = None` 필드는 Task 6에서 이미 선언돼 있다(문자열 forward 참조). `run_stage_loop`에서 (a) 각 `_run_agent_step`(리서처·검증기) 호출 직전에 `if ctx.tiered is not None and not ctx.args.dry_run: ctx.tiered.charge(outer_pass, stage)`를 부르며, (b) pass 시 `ctx.stage_outputs[stage] = researcher_out`을 `ctx.stage_outputs[stage] = _truncate_capture(researcher_out, getattr(ctx.config, "research_max_capture_chars", MAX_CAPTURE_CHARS))`로 바꾼다(단 `_inject_verified_claims`·`verify`는 절단 전 원문 `researcher_out`을 그대로 파싱 — 순서: 먼저 inject/verify, 그다음 절단본을 stage_outputs에 저장). `run_research_workflow`는 ctx 생성 시 `tiered=TieredCallCap(getattr(config,"research_per_stage_calls",6), getattr(config,"research_per_outer_calls",40))`로 채운다. `TieredBudgetStopped`는 `AgentCallBudgetStopped`와 동형으로 `run_research_workflow`가 잡아 부분 상태로 안전 종료한다(`except (AgentCallBudgetStopped, TieredBudgetStopped)`).

- [ ] **Step 4: 최소 구현 — config.py** — `Config` dataclass 끝에 추가:
```python
    # 계층 예산(§6.4): 전역 max_agent_calls 위에 얹는 스테이지별/outer별 상한 + capture 절단.
    research_per_stage_calls: int = 6
    research_per_outer_calls: int = 40
    research_max_capture_chars: int = 12000
```

- [ ] **Step 5: 통과 확인** — Run: `python -m pytest tests/research/test_research_budget.py -q`
  Expected: `5 passed`. 이어 dry-run 회귀: `python run.py --dry-run --workflow research --request "Acme 리서치" --max-agent-calls 0` → exit 0(dry-run은 tiered.charge 미호출이라 상한 무영향).

- [ ] **Step 6: commit**
```bash
git add autoagent/workflows/research.py autoagent/config.py tests/research/test_research_budget.py
git commit -m "feat(research): 계층 예산(스테이지별/outer별 tiered cap) + capture 상한 절단(§6.4 부분)"
```

---

## 스코프 아웃 델타 (스펙과의 명시적 차이)

이 플랜이 스펙 대비 **의도적으로 스코프-아웃**한 항목(리뷰어가 "누락"이 아니라 "결정된 델타"로 읽도록 남긴다):

- **§6.4 컨텍스트 외부화(요약+포인터)**: 계층 예산·capture tail 절단(Task 29)까지는 이행하나, "긴 컨텍스트를 요약본+파일 포인터로 외부화해 프롬프트에 요지만 싣는" 부분은 이번 슬라이스 범위에서 제외한다. 현재는 `_truncate_capture`의 tail 절단으로 상한만 강제하며, 요약 생성(추가 모델 호출)·포인터 참조 프로토콜은 후속 작업으로 미룬다. 이 델타로 아주 긴 스테이지 산출물은 요지 요약이 아니라 머리부 절단으로 처리된다(정보 손실 가능 — 후속에서 요약 외부화로 대체).
- **검증 전략 #3 라이브 실증(소규모 라이브 런)**: 아래 "라이브 실증(스코프 아웃)" 참조 — 사용자 인계(백그라운드)로 의도적 스코프-아웃.

---

## 라이브 실증 (스코프 아웃 — 사용자 인계)

스펙 검증전략 #3(회사 1곳 + 작은 CSV로 소규모 라이브 런 실증)은 이 플랜의 **자동 검증 범위에서 의도적으로 제외**한다. 근거: 프로젝트 방침상 라이브 하네스 런은 사용자 주도(백그라운드)이며(MEMORY: "라이브 하네스 런 기본 백그라운드"·"병렬 실행기 라이브 미검증"), 이 플랜의 verify는 결정론 pytest + dry-run 렌더까지만 자동 커버한다. 실제 모델 호출 실증(`python run.py --workflow research --request "..."` 백그라운드 실행)은 **머지 후 사용자에게 인계**한다 — 자동 태스크로 넣지 않는다. 스펙 항목을 못 채운 게 아니라 실행 주체를 사용자로 명시 이관한 델타다.

---

## 전체 검증 (슬라이스 종료)

- **결정론 pytest 스위트** (누적, 순서 무관): `test_types`(3) + `test_routing_researcher`(6) + `test_roles_and_prompts`(4) + `test_crossmodel_adapter`(11, §4.1② 2건 추가) + `test_html_report`(5) + `test_csv_encoding`(5) + `test_validate_csv`(6) + `test_data_quality_checks`(14) + `test_data_quality_verdict`(4) + `test_adapters_dispatch`(1) + `test_c_prompt_render`(2) + `test_routing_c`(2) + `test_snapshots`(6) + `test_grounding`(10) + `test_source_grounding`(9) + `test_seed_contract`(6) + `test_convergence`(11) + `test_research_state`(6, Task 22 — B2 실배선 1건 추가) + `test_research_gates`(9) + `test_research_state`(7, Task 25 — 별도 파일 `tests/test_research_state.py` 루트) + `test_research_coverage`(7) + `test_research_gate_pause`(2) + `test_research_budget`(5, Task 29). 최종 `python -m pytest tests/ -q`가 전부 green이어야 한다.
- **모델 호출부(dry-run)**: `python run.py --dry-run --workflow research --request "..."` → 프롬프트/커맨드/`research_state.json`/`final_report.html` 렌더, CLI 미호출, `metadata.json`의 `workflow`가 `research`, dry-run은 `--max-agent-calls`에 안 셈.
- **재개**: `python run.py --dry-run --resume "<run_dir>"` → `research_state.json` 감지 → research 재개(done 스킵), 종료 0.
- **계약 불변**: `choose_implementer`(routing.py)·`run_research_workflow`/`run_stage_loop`(research.py) 시그니처는 확장 과정에서 변경하지 않는다(호출부만 추가).
- **회귀**: `python run.py --dry-run --workflow routed --task-type backend --request "add endpoint"` exit 0(routed 경로·기존 라우팅·역할 불변).
- **한글 산출물 확인**: 모든 프롬프트/리포트의 한글 내용은 `cat` 금지(cp949 깨짐), Read 도구(utf-8)로 확인.

## Self-Review (스펙 대조)

**1. 스펙 커버리지**
- §1 중첩 루프 엔진 → Task 6(안쪽 루프)·Task 22·28(바깥 루프). §2.1 스냅샷 → Task 15·18. §2.2 CSV/인코딩/sha256 → Task 8·9. §2.3 HTML/커버리지 매트릭스 → Task 5·26·28. §3 라우팅 → Task 2·14·23. §4.1 crossmodel(+§4.1② anti-gaming: tokens_seen 교차검사·min-findings 쿼터) → Task 4. §4.2 data_quality → Task 10·11·12. §4.3 source_grounding → Task 16·17·18. §5 seed 계약·수렴·as-of → Task 19·20·21·22. §6.2 게이트 → Task 21·24·27·28. §6.3 재개 → Task 25·27·28. §6.4 예산/capture → Task 29(계층 예산·MAX_CAPTURE_CHARS 절단 — **컨텍스트 외부화는 스코프 아웃**). §8 F1 silent-pass 격리 → Task 6(exhausted_unverified)·Task 22(collect_verified_claims 제외)·Task 25(is_stage_done)·Task 26(UNVERIFIED 배지). c 스테이지 오케스트레이터 배선(STAGE_ADAPTER/STAGE_PROMPT + 코드 검증 분기) → Task 6·13. verified_claims 실주입(리서처→verdict.raw) → Task 6(`_inject_verified_claims`)·Task 22(실배선 테스트).
- **의도적 스코프 아웃 델타(누락 아님, "스코프 아웃 델타" 절 참조)**: §6.4 컨텍스트 외부화(요약+포인터) — tail 절단으로 대체; 검증전략 #3 라이브 실증 — 사용자 인계(백그라운드). 그 외 커버리지 갭 없음.

**2. Placeholder 스캔** — "TBD"/"TODO"/"비슷하게"/빈 에러핸들링 없음. 모든 코드 스텝에 실제 코드, 모든 명령에 기대 출력. 통과.

**3. 타입 일관성**
- `Finding(severity, category, detail, fix_directive, claim_id)` — Task 1 정의, Task 4·10·16·17에서 동일 필드명으로 소비. ✓
- `Verdict(status: pass|needs_changes|blocked, adapter, stage_id, findings, raw)` — Task 1 정의, Task 4·11·17에서 재계산해 동일 필드로 생성. ✓ (StageResult.status의 `resolved|exhausted_unverified|blocked`와 별개 — 혼동 없음.)
- `StageResult(stage_id, status, output_path, verdict, inner_rounds)` — Task 1 정의, Task 6에서 생성, Task 22·24·28에서 `.status`/`.stage_id`/`.verdict.raw`로 소비. ✓
- `verify(adapter, stage_out, run_dir, *, verifier_agent, config) -> Verdict` — Task 4 정의(시그니처 고정), Task 12(data_quality 분기)·Task 17(source_grounding 분기)에서 시그니처 불변으로 확장, source_grounding은 `stage_out["model_raw_text"]`로 모델 stdout 전달(인자 추가 없음). ✓
- `choose_researcher(stage) -> (researcher, verifier, reason)` — Task 2 정의, Task 14·23에서 소비. 테이블 `{a:claude, b:claude, c:codex, d:claude, derive:claude}` 일관. ✓
- `research_state.json` 스키마 `{outer_pass, stage, inner_round, seed_pin, verified_claims, stage_status}` — Task 6·22·25·28 전부 동일 키. ✓
- 크로스슬라이스 소비 정합: Slice 2 `run_data_quality`가 stage_id="c" Verdict 생성, Slice 3 `verify_source_grounding`가 stage_id="d" Verdict 생성 — 둘 다 Task 1 계약 준수. ✓

**두 상태 층 정합(Task 22 vs Task 25)**: `persist_research_state`(research.py, Slice 4)와 `persist_state`(state.py, Slice 5)는 같은 `research_state.json`을 같은 스키마로 쓴다. Task 28이 오케스트레이터에서 `state.py` 층(load_or_init_state/set_stage_status/resume_point/pin_seed)을 정본 경로로 쓰고, Slice 4의 `collect_verified_claims`/`diff_verified_claims`/`decide_outer_pass`/`detect_seed_violations`를 pass간 판정에 재사용한다 — 두 층의 책임이 분리되고 파일은 하나로 수렴한다. **`run_outer_loop`(Task 22)는 Task 28이 호출하지 않는다**(바깥 루프는 Task 28이 게이트·재개와 함께 인라인 재구현). `run_outer_loop`는 그 하위 헬퍼들의 자립 검증체로만 남고, `stage_status`는 Task 28에서 `set_stage_status`의 **평면 키** 규약으로 통일한다(run_outer_loop의 `"{outer}:{stage}"` 프리픽스 키는 Task 22 자체 테스트에만 쓰이고 리포트 조회 경로엔 안 섞인다). 정합 확인됨.

## 리뷰어용 태스크 독립성 요약

- **Slice 1 (Task 1–7)**: types → routing → roles/prompts → crossmodel → HTML → orchestrator → CLI. 앞 태스크에만 의존. Task 1·2·4·5는 pytest, 3·6·7은 dry-run.
- **Slice 2 (Task 8–14)**: csv loader → validate → checks → run_data_quality → verify 배선 → c 프롬프트 → 라우팅 락. Slice 1의 types·verify 디스패처에 의존. Task 8~12는 pytest, 13은 render, 14는 회귀 락.
- **Slice 3 (Task 15–18)**: snapshots → grounding → source_grounding+verify 배선 → d 스테이지. Task 15·16·17은 pytest, 18은 dry-run. Task 15~17은 Slice 1 types만 있으면 자립.
- **Slice 4 (Task 19–23)**: seed_contract → convergence delta → convergence gate → outer loop 배선 → b 프롬프트. Task 19·20·21·22는 pytest, 23은 render.
- **Slice 5 (Task 24–29)**: gates → state → coverage → cli+pause → orchestrator 통합 → 계층 예산/capture 상한. Task 24·25·26·27·29는 pytest, 28은 dry-run. Task 28이 1~27 전부를 얇게 엮고, Task 29가 §6.4 예산·절단을 얹는다.

## 실행 순서 (블로킹 관계)

Slice 1(Task 1–7)이 `types.py`/`research.py`/`routing.choose_researcher`/`adapters.verify`/`research_state.json`을 신설하므로 **가장 먼저 머지**해야 한다. Slice 2·3·4·5는 그 계약 위에 얹힌다:
- Slice 2·3의 순수 코어(csv·snapshots·grounding·data_quality 체크)는 Slice 1의 `types.py`만 있으면 병렬 착수 가능하나, 각 슬라이스의 `verify` 분기 배선(Task 12·17)·오케스트레이터 배선(Task 18)은 Slice 1 완료 후.
- Slice 4의 outer loop(Task 22)·Slice 5의 orchestrator 통합(Task 28)은 Slice 1의 `research.py` 실물이 있어야 한다.
- 권장 순서: **1 → 2 → 3 → 4 → 5** (번호 순). 각 슬라이스는 자체 feature 브랜치 + PR로 머지(main 직접 push 금지 — 기본 브랜치 보호).

## 관련 파일 (절대경로)

- 신설 코어: `C:\Users\systran\Desktop\AutoAgent\autoagent\research\{__init__,types,adapters,html_report,data_quality,snapshots,grounding,source_grounding,seed_contract,convergence,state,gates,coverage}.py`
- 신설 CSV 층: `C:\Users\systran\Desktop\AutoAgent\autoagent\data\{__init__,csv_validator}.py`
- 신설 오케스트레이터: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\research.py`
- 신설 프롬프트: `C:\Users\systran\Desktop\AutoAgent\prompts\research\{seed_contract,a_researcher,crossmodel_verifier,b_market_researcher,b_market_verifier,c_codex_research,d_fact_report,d_grounding_verify,derive,final_html_report}.md`
- 신설 테스트: `C:\Users\systran\Desktop\AutoAgent\tests\` 하위 `research/test_*.py` 및 루트 `test_research_{gates,state,coverage,gate_pause}.py`, `pytest.ini`
- 수정: `C:\Users\systran\Desktop\AutoAgent\autoagent\{routing,artifacts,cli}.py`, `C:\Users\systran\Desktop\AutoAgent\roles.default.json`
- 소비(불변): `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\{routed_impl,routed_common,task_exec}.py`, `C:\Users\systran\Desktop\AutoAgent\autoagent\{runner,roles,config}.py`

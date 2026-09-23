# 리서치 워크플로 공공데이터 OpenAPI 연동 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `--workflow research`가 seed 확정 직후 data.go.kr 승인 OpenAPI(나라장터 11종 등)를 모델 계획→코드 실행으로 수집해 `openapi/*.json` 스냅샷과 manifest를 남기고, 모든 스테이지 프롬프트가 그 요약을 근거로 받게 한다.

**Architecture:** 레지스트리 JSON(허용 서비스/오퍼레이션 단일 원천) → 카탈로그 렌더 → Claude light 수집계획(`OPENAPI_PLAN_JSON`) → 검증(미등록 거부) → 주입 가능한 fetch로 실행 → `openapi_manifest.json` → `{{OPENAPI_DATA}}` 요약. 스펙: `docs/superpowers/specs/2026-09-23-openapi-research-design.md`.

**Tech Stack:** Python 3 stdlib(urllib/json/hashlib), pytest. 기존 `autoagent.artifacts.write_json/write_text`, `render_template`, `_run_agent_step` 재사용.

## Global Constraints

- 모듈 첫머리 **한국어 docstring**, 함수 한국어 인라인 주석. `from __future__ import annotations`, PEP 604.
- **stdout print는 ASCII+한글만**(em dash 등 비-cp949 문자 → Windows 크래시).
- **인증키 미노출**: 프롬프트·run 아티팩트·stdout·예외 메시지에 serviceKey 값이 남으면 안 된다. manifest URL은 `redact_url`, 저장 본문은 `_scrub`으로 키 문자열 제거. 테스트가 이를 assert한다.
- **허용 호스트 `apis.data.go.kr`만**(https). 레지스트리 외 service/operation은 실행 거부.
- **무회귀**: 키 미설정 머신에서 research dry-run 결과 구조 동일(+manifest 0건, `OPENAPI_DATA="(공공데이터 스냅샷 없음)"`). 기존 pytest 전부 통과(기준 223).
- `git add`는 각 태스크의 파일 목록만(**`git add -A` 금지** — 작업트리에 EOL 노이즈 수정 plans 문서 3개와 개인 노트 `docs/발표준비_공부노트.md`가 있음. 절대 스테이징 금지).
- 커밋 트레일러: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- 레지스트리 내용은 **data.go.kr에서 실제로 읽은 값만** 기재(추정 금지). 확인 못 한 서비스는 `operations: {}` + `note`.

---

### Task 1: `autoagent/data/openapi.py` — 레지스트리·계획·실행기 코어 (테스트 픽스처 레지스트리 사용)

**Files:**
- Create: `autoagent/data/openapi.py`
- Test: `tests/data/test_openapi.py`

**Interfaces (Produces):** `Registry`, `load_registry(path=None)`, `render_catalog_md(reg)`, `PlanCall`, `Plan`, `parse_plan(raw)`, `validate_plan(plan, reg, max_calls)`, `build_url(reg, call, key)`, `redact_url(url)`, `FetchResult`, `default_fetch(url, timeout)`, `ManifestItem`, `execute_plan(calls, reg, key, run_dir, *, fetch=default_fetch, timeout=20, pause_seconds=0.2)`, `write_manifest(run_dir, items, rejected, plan_error=None)`, `render_openapi_summary(run_dir)`, 상수 `PLAN_MARKER="OPENAPI_PLAN_JSON"`, `ALLOWED_HOST`, `DEFAULT_REGISTRY_PATH`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/data/test_openapi.py`:

```python
"""data.go.kr OpenAPI 수집기 단위테스트(네트워크·모델 호출 없음, fetch 주입)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoagent.data import openapi as oa

KEY = "SECRET+KEY/==abc"

FIXTURE = {
    "services": {
        "bid_notice": {
            "name": "테스트 입찰공고", "data_go_kr_id": "1", "format": "json",
            "base_url": "https://apis.data.go.kr/1230000/TestBidService",
            "operations": {"getBidList": {"purpose": "공고 목록", "params": {"inqryDiv": "1"}}},
        },
        "no_ops": {
            "name": "상세기능 미확인", "data_go_kr_id": "2", "format": "xml",
            "base_url": "https://apis.data.go.kr/1230000/NoOps", "operations": {}, "note": "참고문서 확인 필요",
        },
    },
    "common_params": {"numOfRows": "페이지 크기", "pageNo": "페이지"},
}


def _reg(tmp_path: Path, data: dict = FIXTURE) -> oa.Registry:
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return oa.load_registry(p)


def _plan_text(calls: list[dict]) -> str:
    return f"자유 서술\n\n{oa.PLAN_MARKER}\n```json\n{json.dumps({'calls': calls}, ensure_ascii=False)}\n```\n"


def _ok_body(n: int = 2) -> str:
    return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
                                    "body": {"items": [{"a": i} for i in range(n)], "totalCount": n}}})


def test_load_registry_rejects_bad_host(tmp_path):
    bad = json.loads(json.dumps(FIXTURE))
    bad["services"]["bid_notice"]["base_url"] = "https://evil.example.com/x"
    with pytest.raises(ValueError):
        _reg(tmp_path, bad)


def test_render_catalog_lists_ops_and_marks_empty(tmp_path):
    md = oa.render_catalog_md(_reg(tmp_path))
    assert "bid_notice" in md and "getBidList" in md and "numOfRows" in md
    assert "계획에 포함하지 말 것" in md  # no_ops 서비스 표시


def test_parse_plan_marker_and_failures():
    plan = oa.parse_plan(_plan_text([{"id": "c1", "service": "bid_notice", "operation": "getBidList",
                                      "params": {"inqryDiv": 1}, "purpose": "p"}]))
    assert plan.error is None and plan.calls[0].params == {"inqryDiv": "1"}
    assert oa.parse_plan("마커 없음").error
    assert oa.parse_plan(f"{oa.PLAN_MARKER}\n```json\n{{broken\n```").error


def test_validate_plan_rejects_unknown_and_caps(tmp_path):
    reg = _reg(tmp_path)
    plan = oa.parse_plan(_plan_text([
        {"id": "c1", "service": "bid_notice", "operation": "getBidList", "params": {}},
        {"id": "c1", "service": "bid_notice", "operation": "getBidList", "params": {}},   # 중복 id
        {"id": "c2", "service": "nope", "operation": "x", "params": {}},                  # 미등록 service
        {"id": "c3", "service": "bid_notice", "operation": "getOther", "params": {}},      # 미등록 operation
        {"id": "c4", "service": "bid_notice", "operation": "getBidList", "params": {"serviceKey": "x"}},
        {"id": "c5", "service": "bid_notice", "operation": "getBidList", "params": {}},
    ]))
    valid, rejected = oa.validate_plan(plan, reg, max_calls=1)
    assert [c.id for c in valid] == ["c1"]
    reasons = {r["id"]: r["reason"] for r in rejected}
    assert "중복" in reasons["c1"] and "service" in reasons["c2"] and "operation" in reasons["c3"]
    assert "serviceKey" in reasons["c4"] and "상한" in reasons["c5"]


def test_build_and_redact_url(tmp_path):
    reg = _reg(tmp_path)
    call = oa.PlanCall(id="c1", service="bid_notice", operation="getBidList", params={"inqryDiv": "1"})
    url = oa.build_url(reg, call, KEY)
    assert url.startswith("https://apis.data.go.kr/1230000/TestBidService/getBidList?")
    assert "type=json" in url and "inqryDiv=1" in url and KEY not in url  # 키는 URL 인코딩됨
    red = oa.redact_url(url)
    assert "serviceKey=%2A%2A%2A" in red or "serviceKey=***" in red
    assert "SECRET" not in red


def test_execute_plan_success_error_timeout_xml(tmp_path):
    reg = _reg(tmp_path)
    calls = [oa.PlanCall("c1", "bid_notice", "getBidList", {}),
             oa.PlanCall("c2", "bid_notice", "getBidList", {}),
             oa.PlanCall("c3", "bid_notice", "getBidList", {}),
             oa.PlanCall("c4", "bid_notice", "getBidList", {})]
    seen: list[str] = []

    def fake_fetch(url: str, timeout: int) -> oa.FetchResult:
        seen.append(url)
        n = len(seen)
        if n == 1:
            # 서버가 키를 본문에 에코하는 최악 케이스(JSON 유효성은 유지)
            return oa.FetchResult(200, _ok_body(3).replace("NORMAL SERVICE", f"NORMAL SERVICE {KEY}"))
        if n == 2:
            return oa.FetchResult(500, "server error")
        if n == 3:
            raise TimeoutError("timed out")
        return oa.FetchResult(200, "<response><header><resultCode>00</resultCode></header></response>")

    items = oa.execute_plan(calls, reg, KEY, tmp_path, fetch=fake_fetch, pause_seconds=0)
    assert len(items) == 4 and all(KEY in u for u in seen)  # 실제 URL에는 키가 들어간다
    ok, err, to, xml = items
    assert ok.http_status == 200 and ok.result_code == "00" and ok.row_count == 3 and ok.error is None
    assert ok.snapshot_path == "openapi/c1.json"
    assert KEY not in (tmp_path / ok.snapshot_path).read_text(encoding="utf-8")  # 저장 본문 scrub
    assert err.error and "500" in err.error
    assert to.error and "TimeoutError" in to.error and to.snapshot_path is None
    assert xml.snapshot_path == "openapi/c4.xml"
    for it in items:
        assert KEY not in json.dumps(oa.asdict(it))  # manifest 항목 어디에도 키 없음


def test_manifest_and_summary(tmp_path):
    reg = _reg(tmp_path)
    items = oa.execute_plan([oa.PlanCall("c1", "bid_notice", "getBidList", {}, "목적")], reg, KEY, tmp_path,
                            fetch=lambda u, t: oa.FetchResult(200, _ok_body(1)), pause_seconds=0)
    oa.write_manifest(tmp_path, items, [{"id": "c9", "reason": "미등록 service"}], plan_error=None)
    data = json.loads((tmp_path / "openapi_manifest.json").read_text(encoding="utf-8"))
    assert data["items"][0]["id"] == "c1" and data["rejected"][0]["id"] == "c9"
    md = oa.render_openapi_summary(tmp_path)
    assert "c1" in md and "getBidList" in md and str(tmp_path / "openapi" / "c1.json") in md
    assert "거부된 계획 1건" in md and "source_refs" in md


def test_summary_without_manifest(tmp_path):
    assert oa.render_openapi_summary(tmp_path) == "(공공데이터 스냅샷 없음)"
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/data/test_openapi.py -q` → `ModuleNotFoundError: autoagent.data.openapi`.

- [ ] **Step 3: `autoagent/data/openapi.py` 작성**

```python
"""공공데이터포털(data.go.kr) OpenAPI 수집기.

레지스트리 JSON으로 허용 서비스/오퍼레이션을 고정하고, 모델이 낸 수집계획
(OPENAPI_PLAN_JSON 마커 뒤 fenced JSON)을 검증·실행해 응답 원문을
run_dir/openapi/<id>.json 스냅샷과 openapi_manifest.json으로 남긴다.
fetch는 주입 가능해 pytest로 못박는다(모델·네트워크 호출 없음). 인증키는
URL에만 들어가며 manifest·저장 본문·예외 메시지에서는 제거한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from autoagent.artifacts import write_json, write_text

ALLOWED_HOST = "apis.data.go.kr"
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("openapi_registry.json")
PLAN_MARKER = "OPENAPI_PLAN_JSON"
NO_SNAPSHOT_TEXT = "(공공데이터 스냅샷 없음)"
_SAFE_SEGMENT = re.compile(r"[^a-z0-9_-]+")


@dataclass
class Registry:
    """허용 서비스 사전(service_id -> {name, base_url, format, operations, note?})과 공통 파라미터."""

    services: dict[str, dict[str, Any]]
    common_params: dict[str, str] = field(default_factory=dict)


def load_registry(path: Path | None = None) -> Registry:
    """레지스트리 JSON을 읽고 스키마·호스트를 검증한다. 문제가 있으면 ValueError(한국어 사유)."""
    target = path or DEFAULT_REGISTRY_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    services = data.get("services")
    if not isinstance(services, dict) or not services:
        raise ValueError("레지스트리에 services 사전이 없음")
    for sid, svc in services.items():
        for key in ("name", "base_url", "format"):
            if not svc.get(key):
                raise ValueError(f"서비스 {sid}: 필수 키 {key} 누락")
        parts = urlsplit(svc["base_url"])
        # 모델 환각 엔드포인트·임의 호스트로의 요청을 레지스트리 단계에서 차단한다.
        if parts.scheme != "https" or parts.netloc != ALLOWED_HOST:
            raise ValueError(f"서비스 {sid}: base_url은 https://{ALLOWED_HOST}/... 만 허용")
        if svc["format"] not in ("json", "xml"):
            raise ValueError(f"서비스 {sid}: format은 json|xml")
        if not isinstance(svc.get("operations", {}), dict):
            raise ValueError(f"서비스 {sid}: operations는 사전이어야 함")
        svc.setdefault("operations", {})
    return Registry(services=services, common_params=dict(data.get("common_params") or {}))


def render_catalog_md(reg: Registry) -> str:
    """모델용 카탈로그 마크다운. 오퍼레이션이 없는 서비스는 계획 제외를 명시한다."""
    lines = ["# 사용 가능한 공공데이터 OpenAPI 카탈로그", ""]
    for sid, svc in reg.services.items():
        lines += [f"### `{sid}` - {svc['name']}", f"- base_url: {svc['base_url']} (format: {svc['format']})"]
        if svc.get("note"):
            lines.append(f"- 비고: {svc['note']}")
        ops = svc.get("operations") or {}
        if not ops:
            lines += ["- (상세기능 미확인 - 이 서비스는 계획에 포함하지 말 것)", ""]
            continue
        lines += ["", "| operation | 용도 | 파라미터 |", "| --- | --- | --- |"]
        for op, meta in ops.items():
            params = ", ".join(f"`{k}`={v}" for k, v in (meta.get("params") or {}).items()) or "-"
            lines.append(f"| `{op}` | {meta.get('purpose', '')} | {params} |")
        lines.append("")
    lines += ["## 공통 파라미터", ""]
    lines += [f"- `{k}`: {v}" for k, v in reg.common_params.items()]
    lines += ["", "규칙: service/operation은 위 표기 그대로만 쓴다. serviceKey는 하네스가 붙이므로 params에 넣지 않는다."]
    return "\n".join(lines)


@dataclass
class PlanCall:
    """수집계획 한 항목(모델 산출)."""

    id: str
    service: str
    operation: str
    params: dict[str, str] = field(default_factory=dict)
    purpose: str = ""


@dataclass
class Plan:
    calls: list[PlanCall]
    error: str | None = None


def parse_plan(raw: str) -> Plan:
    """OPENAPI_PLAN_JSON 마커 뒤 fenced JSON을 파싱한다. 실패는 예외 대신 Plan.error로 돌려준다."""
    match = re.search(rf"{PLAN_MARKER}\s*```(?:json)?\s*(\{{.*?\}})\s*```", raw, flags=re.DOTALL)
    if not match:
        return Plan([], error=f"{PLAN_MARKER} 마커 블록 없음")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return Plan([], error=f"계획 JSON 파싱 실패: {exc}")
    calls: list[PlanCall] = []
    for index, item in enumerate(data.get("calls") or [], start=1):
        if not isinstance(item, dict):
            continue
        calls.append(PlanCall(
            id=str(item.get("id") or f"c{index}"),
            service=str(item.get("service") or ""),
            operation=str(item.get("operation") or ""),
            params={str(k): str(v) for k, v in (item.get("params") or {}).items()},
            purpose=str(item.get("purpose") or ""),
        ))
    return Plan(calls)


def validate_plan(plan: Plan, reg: Registry, max_calls: int) -> tuple[list[PlanCall], list[dict[str, str]]]:
    """레지스트리에 있는 service/operation만 통과시키고 나머지는 거부 사유와 함께 돌려준다."""
    valid: list[PlanCall] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for call in plan.calls:
        svc = reg.services.get(call.service)
        if call.id in seen:
            reason = "id 중복"
        elif svc is None:
            reason = f"미등록 service: {call.service}"
        elif call.operation not in (svc.get("operations") or {}):
            reason = f"미등록 operation: {call.operation}"
        elif "serviceKey" in call.params:
            reason = "params에 serviceKey 금지(하네스가 주입)"
        elif len(valid) >= max_calls:
            reason = f"호출 상한 초과(max {max_calls})"
        else:
            seen.add(call.id)
            valid.append(call)
            continue
        seen.add(call.id)
        rejected.append({"id": call.id, "reason": reason})
    return valid, rejected


def build_url(reg: Registry, call: PlanCall, key: str) -> str:
    """base_url/operation?serviceKey=...&type=json&params. 키는 Decoding 원문을 urlencode가 인코딩한다."""
    svc = reg.services[call.service]
    query: dict[str, str] = {"serviceKey": key}
    if svc["format"] == "json":
        query["type"] = "json"
    query.update(call.params)
    return f"{svc['base_url'].rstrip('/')}/{call.operation}?{urlencode(query)}"


def redact_url(url: str) -> str:
    """serviceKey 값을 ***로 바꾼 URL(manifest 기록용)."""
    parts = urlsplit(url)
    pairs = [(k, "***" if k == "serviceKey" else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _scrub(text: str, key: str) -> str:
    """본문·메시지에서 키(원문·URL인코딩형) 흔적을 제거한다."""
    if not key:
        return text
    return text.replace(key, "***").replace(quote(key, safe=""), "***")


@dataclass
class FetchResult:
    status: int
    body: str


def default_fetch(url: str, timeout: int) -> FetchResult:
    """urllib GET. HTTP 오류도 본문과 함께 FetchResult로 돌려주고, 네트워크 예외는 그대로 올린다."""
    request = Request(url, headers={"Accept": "application/json, application/xml;q=0.9, */*;q=0.8"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 호스트는 레지스트리에서 고정
            return FetchResult(response.status, response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return FetchResult(exc.code, exc.read().decode("utf-8", errors="replace"))


@dataclass
class ManifestItem:
    """한 호출의 결과 메타(openapi_manifest.json 항목이자 스테이지 요약의 원천)."""

    id: str
    service: str
    operation: str
    purpose: str
    url_redacted: str
    fetch_ts: str
    snapshot_path: str | None = None
    http_status: int | None = None
    sha256: str | None = None
    result_code: str | None = None
    result_msg: str | None = None
    row_count: int | None = None
    error: str | None = None


def _summarize_body(body: str) -> tuple[str | None, str | None, int | None, bool]:
    """data.go.kr 표준 JSON(response.header/body)이면 resultCode·resultMsg·행수를 best-effort로 뽑는다."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, None, None, False
    if not isinstance(data, dict):
        return None, None, None, True
    response = data.get("response") if isinstance(data.get("response"), dict) else {}
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    payload = response.get("body") if isinstance(response.get("body"), dict) else {}
    items = payload.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if isinstance(items, list):
        rows: int | None = len(items)
    elif isinstance(items, dict):
        rows = 1
    else:
        rows = 0 if payload else None
    code = header.get("resultCode")
    return (str(code) if code is not None else None), header.get("resultMsg"), rows, True


def execute_plan(
    calls: list[PlanCall], reg: Registry, key: str, run_dir: Path, *,
    fetch: Callable[[str, int], FetchResult] = default_fetch, timeout: int = 20, pause_seconds: float = 0.2,
) -> list[ManifestItem]:
    """검증된 호출을 순서대로 실행해 스냅샷을 저장한다. 개별 실패는 항목 error로 남기고 계속한다."""
    out_dir = run_dir / "openapi"
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[ManifestItem] = []
    for index, call in enumerate(calls, start=1):
        if index > 1 and pause_seconds:
            time.sleep(pause_seconds)  # 개발계정 예의: 연속 호출 간 짧은 간격
        url = build_url(reg, call, key)
        item = ManifestItem(
            id=call.id, service=call.service, operation=call.operation, purpose=call.purpose,
            url_redacted=redact_url(url), fetch_ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        try:
            result = fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001 - 수집 전체를 실패시키지 않고 항목별로 기록
            item.error = _scrub(f"{type(exc).__name__}: {exc}", key)
            items.append(item)
            continue
        body = _scrub(result.body, key)
        code, msg, rows, is_json = _summarize_body(body)
        slug = _SAFE_SEGMENT.sub("_", call.id.lower()).strip("_-") or f"c{index}"
        ext = "json" if is_json else "xml"
        write_text(out_dir / f"{slug}.{ext}", body)
        item.snapshot_path = f"openapi/{slug}.{ext}"
        item.http_status = result.status
        item.sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        item.result_code, item.result_msg, item.row_count = code, msg, rows
        errors = []
        if result.status != 200:
            errors.append(f"HTTP {result.status}")
        if code not in (None, "00"):
            errors.append(f"resultCode {code} {msg or ''}".strip())
        item.error = "; ".join(errors) or None
        items.append(item)
    return items


def write_manifest(run_dir: Path, items: list[ManifestItem], rejected: list[dict[str, str]],
                   plan_error: str | None = None) -> Path:
    """openapi_manifest.json 기록(items·rejected·plan_error)."""
    path = run_dir / "openapi_manifest.json"
    write_json(path, {"items": [asdict(i) for i in items], "rejected": rejected, "plan_error": plan_error})
    return path


def render_openapi_summary(run_dir: Path) -> str:
    """스테이지 프롬프트용 요약 표. manifest가 없으면 NO_SNAPSHOT_TEXT."""
    path = run_dir / "openapi_manifest.json"
    if not path.exists():
        return NO_SNAPSHOT_TEXT
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    rejected = data.get("rejected") or []
    if not items and not rejected and not data.get("plan_error"):
        return NO_SNAPSHOT_TEXT
    lines = ["| id | service | operation | 목적 | 상태 | rows | 파일(절대경로) |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for it in items:
        status = it.get("error") or f"HTTP {it.get('http_status')} / {it.get('result_code') or '-'}"
        file_path = str(run_dir / it["snapshot_path"]) if it.get("snapshot_path") else "-"
        lines.append(f"| {it['id']} | {it['service']} | {it['operation']} | {it.get('purpose', '')} | {status} | "
                     f"{it.get('row_count') if it.get('row_count') is not None else '-'} | {file_path} |")
    if rejected:
        lines.append(f"\n거부된 계획 {len(rejected)}건: " + "; ".join(f"{r['id']}({r['reason']})" for r in rejected))
    if data.get("plan_error"):
        lines.append(f"\n계획 파싱 오류: {data['plan_error']}")
    lines.append("\n사용 규칙: 위 파일을 직접 읽어 근거로 쓰고, 인용 시 source_refs에 해당 파일 경로(openapi/<id>.json)를 명시하라. "
                 "파일에 없는 수치를 만들지 말 것.")
    return "\n".join(lines)
```

`asdict`는 테스트가 `oa.asdict`로 쓰므로 모듈 네임스페이스에 남겨둔다(이미 import).

- [ ] **Step 4: 통과 확인** — `python -m pytest tests/data/test_openapi.py -q`(8 passed) → `python -m pytest tests/ -q`(223+8).
- [ ] **Step 5: 커밋** — `git add autoagent/data/openapi.py tests/data/test_openapi.py` → `feat(openapi): data.go.kr OpenAPI 수집기 코어(레지스트리·계획 검증·실행·manifest)`.

---

### Task 2: 실제 레지스트리 `autoagent/data/openapi_registry.json` (승인 13종)

**Files:**
- Create: `autoagent/data/openapi_registry.json`
- Test: `tests/data/test_openapi_registry.py`

**입력 자료:** 컨트롤러가 사전 수집한 원시 카탈로그 `C:\Users\systran\AppData\Local\Temp\claude\C--Users-systran-Desktop-AutoAgent\36103594-ff1a-4277-8617-f3c97204a334\scratchpad\openapi_catalog_raw.md`(있으면 먼저 읽는다). **각 서비스의 base_url·오퍼레이션명은 data.go.kr 상세 페이지(`https://www.data.go.kr/data/<id>/openapi.do`)를 WebFetch해 반드시 재확인**한다. 원시 자료와 페이지가 다르면 페이지가 우선. 확인 못 한 오퍼레이션은 넣지 않는다.

**서비스 id 규약(고정):** `bid_notice`(입찰공고정보), `pre_spec`(사전규격정보), `order_plan`(발주계획현황), `award`(낙찰정보), `contract`(계약정보), `contract_process`(계약과정통합공개), `user_info`(사용자정보, id 15129466), `procure_request`(조달요청), `price_info`(가격정보현황), `open_standard`(공공데이터개방표준), `private_bid`(누리장터 민간입찰공고), `kisa_domain`(KISA 인터넷주소 검색), `mois_safety`(행안부 안전정보 통합공개).

- [ ] **Step 1: 테스트** — `tests/data/test_openapi_registry.py`:

```python
"""실제 레지스트리 JSON의 정합성(로드·호스트·13종 존재·카탈로그 렌더)."""
from __future__ import annotations

from autoagent.data import openapi as oa

EXPECTED = {"bid_notice", "pre_spec", "order_plan", "award", "contract", "contract_process", "user_info",
            "procure_request", "price_info", "open_standard", "private_bid", "kisa_domain", "mois_safety"}


def test_default_registry_loads_and_covers_13():
    reg = oa.load_registry()
    assert EXPECTED <= set(reg.services)
    assert reg.services["user_info"]["data_go_kr_id"] == "15129466"
    # 오퍼레이션이 있는 서비스는 purpose가 비어 있지 않아야 한다(모델이 고를 근거).
    for sid, svc in reg.services.items():
        for op, meta in svc["operations"].items():
            assert meta.get("purpose"), f"{sid}.{op} purpose 누락"


def test_default_registry_has_usable_core_ops():
    reg = oa.load_registry()
    # 최소한 입찰공고·낙찰·계약·사용자정보는 오퍼레이션 1개 이상 확인돼 있어야 한다.
    for sid in ("bid_notice", "award", "contract", "user_info"):
        assert reg.services[sid]["operations"], f"{sid} 오퍼레이션 미기재"


def test_catalog_renders_without_placeholders():
    md = oa.render_catalog_md(oa.load_registry())
    assert "{{" not in md and "확인필요" not in md
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/data/test_openapi_registry.py -q` → FileNotFoundError.
- [ ] **Step 3: 레지스트리 작성** — Task 1의 스키마대로. `common_params`: `numOfRows`(페이지 크기, 기본 50), `pageNo`(페이지, 기본 1), 날짜 형식 규약(`inqryBgnDt/inqryEndDt`=YYYYMMDDHHMM, `inqryDiv` 코드 의미)을 파라미터 설명에 넣는다. 각 서비스에 `data_go_kr_id`, 확인 출처 URL은 `note`에 요약. `(확인필요)`라는 문자열은 최종 파일에 남기지 않는다(확인 못 하면 생략).
- [ ] **Step 4: 통과 확인** — 위 테스트 3개 + 전체 스위트.
- [ ] **Step 5: 커밋** — `git add autoagent/data/openapi_registry.json tests/data/test_openapi_registry.py` → `feat(openapi): 승인 13종 OpenAPI 레지스트리`.

---

### Task 3: 워크플로 배선 — config·역할·계획 프롬프트·수집 스테이지·OPENAPI_DATA

**Files:**
- Modify: `autoagent/config.py`, `roles.default.json`, `autoagent/workflows/research.py`, `autoagent.config.example.json`
- Modify: `prompts/research/a_researcher.md`, `b_market_researcher.md`, `c_codex_research.md`, `d_fact_report.md`, `derive.md`
- Create: `prompts/research/openapi_plan.md`
- Test: `tests/research/test_openapi_collection.py`

**Interfaces:** Consumes Task 1 API 전부. Produces `Config.data_go_kr_service_key: str | None`, `Config.openapi_max_calls: int`, `research._run_openapi_collection(ctx, *, fetch=None)`, 역할 `openapi_planner`, placeholder `{{OPENAPI_DATA}}`.

- [ ] **Step 1: 테스트** — `tests/research/test_openapi_collection.py`:

```python
"""수집 스테이지 배선 테스트: 키 없음 스킵 / 계획→실행→manifest→state / 프롬프트 미치환 잔존 없음."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from autoagent import config as config_mod
from autoagent.data import openapi as oa
from autoagent.artifacts import render_template
from autoagent.runner import AgentCallBudget
from autoagent.workflows import research as research_mod


def _ctx(tmp_path: Path, key: str | None) -> research_mod.ResearchContext:
    cfg = config_mod.load_config(tmp_path / "absent.json")
    cfg.workspace = tmp_path
    cfg.data_go_kr_service_key = key
    args = Namespace(dry_run=False, read_only=False, max_agent_calls=0)
    return research_mod.ResearchContext(args=args, config=cfg, request="최근 소프트웨어 용역 입찰공고 조사",
                                        run_dir=tmp_path, budget=AgentCallBudget(0), seed_contract="{}", state={})


def test_no_key_skips(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    ctx = _ctx(tmp_path, None)
    research_mod._run_openapi_collection(ctx)
    assert ctx.state["openapi"]["skipped"] == "no_key"
    assert "[openapi]" in capsys.readouterr().out
    assert not (tmp_path / "openapi_manifest.json").exists()


def test_plan_execute_manifest_state(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, "KEY")
    reg = oa.load_registry()
    sid = "user_info" if reg.services["user_info"]["operations"] else next(s for s, v in reg.services.items() if v["operations"])
    op = next(iter(reg.services[sid]["operations"]))
    plan_text = (f"{oa.PLAN_MARKER}\n```json\n" + json.dumps({"calls": [
        {"id": "c1", "service": sid, "operation": op, "params": {"numOfRows": "1"}, "purpose": "t"},
        {"id": "c2", "service": "ghost", "operation": "x", "params": {}}]}) + "\n```\n")
    captured: dict = {}

    def fake_step(ctx_, **kw):
        captured.update(kw)
        return plan_text

    monkeypatch.setattr(research_mod, "_run_agent_step", fake_step)
    body = json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": [{"x": 1}]}}})
    research_mod._run_openapi_collection(ctx, fetch=lambda u, t: oa.FetchResult(200, body))
    assert captured["name"] == "01_openapi_plan" and captured["role_id"] == "openapi_planner"
    assert "KEY" not in captured["prompt_values"]["OPENAPI_CATALOG"]
    manifest = json.loads((tmp_path / "openapi_manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["row_count"] == 1 and manifest["rejected"][0]["id"] == "c2"
    assert ctx.state["openapi"] == {"calls": 1, "rejected": 1, "errors": 0, "manifest": "openapi_manifest.json"}
    assert (tmp_path / "research_state.json").exists()
    # 재호출은 스킵(재개 안전).
    monkeypatch.setattr(research_mod, "_run_agent_step", lambda *a, **k: (_ for _ in ()).throw(AssertionError("재호출")))
    research_mod._run_openapi_collection(ctx)


def test_stage_prompts_render_openapi_data():
    values = {k: "v" for k in (
        "REQUEST", "WORKSPACE", "SEED_CONTRACT", "SEED_PIN", "STAGE_ID", "OUTER_PASS", "INNER_ROUND", "PRIOR_FEEDBACK",
        "INNER_FEEDBACK", "DEEPEN_DELTA", "PRIOR_VERDICT_FEEDBACK", "PRIOR_STAGE_SUMMARY", "STAGE_A_OUTPUT", "CSV_PATHS",
        "MIN_FINDINGS", "SEED_COMPANY", "SEED_MARKET", "SEED_CURRENCY", "SEED_PERIOD", "SEED_UNIT", "SEED_AS_OF")}
    values["OPENAPI_DATA"] = "TABLE_MARK"
    for name in ("a_researcher.md", "b_market_researcher.md", "c_codex_research.md", "d_fact_report.md", "derive.md"):
        out = render_template(name, values)
        assert "TABLE_MARK" in out and "{{" not in out, name
    plan = render_template("openapi_plan.md", {"REQUEST": "r", "SEED_CONTRACT": "s", "OPENAPI_CATALOG": "CAT", "MAX_CALLS": "12"})
    assert "CAT" in plan and oa.PLAN_MARKER in plan and "{{" not in plan


def test_config_key_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "ENVKEY")
    assert config_mod.load_config(tmp_path / "absent.json").data_go_kr_service_key == "ENVKEY"
    p = tmp_path / "c.json"
    p.write_text('{"workspace": ".", "data_go_kr_service_key": "CFGKEY", "openapi_max_calls": 3}', encoding="utf-8")
    cfg = config_mod.load_config(p)
    assert cfg.data_go_kr_service_key == "CFGKEY" and cfg.openapi_max_calls == 3
```

`render_template`의 실제 import 경로가 다르면(research.py 상단 import 참조) 테스트를 그에 맞춘다.

- [ ] **Step 2: 실패 확인** — AttributeError(`_run_openapi_collection`) 등.

- [ ] **Step 3: config.py** — Config 필드(`solo_provider` 근처):

```python
    # data.go.kr OpenAPI 인증키(config > env DATA_GO_KR_SERVICE_KEY). None이면 리서치의 공공데이터 수집 생략.
    data_go_kr_service_key: str | None = None
    openapi_max_calls: int = 12  # 리서치 1회당 수집계획 호출 상한
```
load_config 생성부:
```python
        data_go_kr_service_key=(raw.get("data_go_kr_service_key") or os.environ.get("DATA_GO_KR_SERVICE_KEY") or None),
        openapi_max_calls=int(raw.get("openapi_max_calls") or 12),
```
`autoagent.config.example.json`에 `"data_go_kr_service_key": ""` 추가(빈 값=미설정).

- [ ] **Step 4: roles.default.json** — `refine` 다음 줄에 추가:
```json
    { "id": "openapi_planner", "agent": "claude",  "tier": "light",                             "high_risk_condition": "none",                       "mutating": false, "permission": "plan" },
```

- [ ] **Step 5: `prompts/research/openapi_plan.md`**

```markdown
# 스테이지 01 — 공공데이터 OpenAPI 수집계획 (Claude light)

당신은 리서치 데이터 수집 계획자다. 아래 요청과 seed를 보고, 카탈로그에 있는 공공데이터
OpenAPI 중 **근거로 쓸 가치가 있는 호출만** 골라 계획을 낸다. 웹 검색은 하지 않는다.

## 원 요청
{{REQUEST}}

## canonical seed
{{SEED_CONTRACT}}

## 카탈로그(이 안의 service/operation 이름만 사용)
{{OPENAPI_CATALOG}}

## 규칙
- 요청과 무관하면 `"calls": []`로 낸다(억지로 채우지 말 것).
- 최대 {{MAX_CALLS}}건. 목록형 오퍼레이션은 `numOfRows`를 50 이하로.
- 날짜 파라미터는 카탈로그 규약(YYYYMMDDHHMM 등)대로, 기간은 요청·seed 기간을 따른다.
- `serviceKey`·`type`은 넣지 않는다(하네스가 붙인다).
- 각 호출의 `purpose`에 "이 데이터로 무엇을 확인하려는지"를 한 줄로 쓴다.

## 출력(엄격 — 코드가 파싱한다)
짧은 근거 서술 뒤, 마지막에 마커 + fenced JSON 한 블록:

OPENAPI_PLAN_JSON
```json
{"calls": [{"id": "c1", "service": "bid_notice", "operation": "<카탈로그 오퍼레이션>", "params": {"inqryDiv": "1", "inqryBgnDt": "202509010000", "inqryEndDt": "202509232359", "numOfRows": "50"}, "purpose": "최근 30일 관련 입찰공고 파악"}]}
```
```

- [ ] **Step 6: research.py 배선**

import 추가: `from autoagent.data.openapi import (PLAN_MARKER, execute_plan, load_registry, parse_plan, render_catalog_md, render_openapi_summary, validate_plan, write_manifest)`.

`_seed_fields` 앞에 함수 추가:

```python
def _run_openapi_collection(ctx: "ResearchContext", *, fetch=None) -> None:
    """seed pin 직후 1회: 계획(Claude light) → 레지스트리 검증 → 실행 → openapi_manifest.json.

    state["openapi"]가 있으면(재개) 스킵한다. 키가 없거나 레지스트리를 못 읽으면 경고만 남기고
    생략해 리서치 본체는 계속 진행한다. fetch는 테스트 주입용(None이면 기본 urllib).
    """
    state = ctx.state
    if state.get("openapi") is not None:
        return
    key = ctx.config.data_go_kr_service_key
    if not key and not ctx.args.dry_run:
        print("[openapi] 인증키 없음 - 공공데이터 수집을 생략합니다(config data_go_kr_service_key 또는 env DATA_GO_KR_SERVICE_KEY)")
        state["openapi"] = {"skipped": "no_key"}
        _persist_state(ctx)
        return
    try:
        registry = load_registry()
    except (OSError, ValueError) as exc:
        print(f"[openapi] 레지스트리 로드 실패 - 수집 생략: {exc}")
        state["openapi"] = {"skipped": f"registry: {exc}"}
        _persist_state(ctx)
        return
    plan_out = _run_agent_step(
        ctx, agent="claude", role_id="openapi_planner", name="01_openapi_plan",
        prompt_name="openapi_plan.md",
        prompt_values={"REQUEST": ctx.request, "SEED_CONTRACT": ctx.seed_contract,
                       "OPENAPI_CATALOG": render_catalog_md(registry), "MAX_CALLS": str(ctx.config.openapi_max_calls)},
        next_step="openapi_plan",
        dry_output=f'{PLAN_MARKER}\n```json\n{{"calls": []}}\n```\n',
    )
    plan = parse_plan(plan_out)
    if plan.error:
        write_text(ctx.run_dir / "01_openapi_plan_error.txt", plan.error)
    valid, rejected = validate_plan(plan, registry, ctx.config.openapi_max_calls)
    items = []
    if valid and not ctx.args.dry_run:
        kwargs = {"fetch": fetch} if fetch is not None else {}
        items = execute_plan(valid, registry, key or "", ctx.run_dir, **kwargs)
    write_manifest(ctx.run_dir, items, rejected, plan.error)
    state["openapi"] = {"calls": len(valid), "rejected": len(rejected),
                        "errors": sum(1 for it in items if it.error), "manifest": "openapi_manifest.json"}
    _persist_state(ctx)
```

`run_research_workflow`: seed 블록(`else: ctx.seed_contract = ...`) 바로 다음 줄에 `_run_openapi_collection(ctx)` 호출. `run_stage_loop`의 `values`에 `"OPENAPI_DATA": render_openapi_summary(ctx.run_dir),` 추가.

- [ ] **Step 7: 스테이지 프롬프트 5개** — 각 파일의 "원 요청"/"루프 컨텍스트" 섹션 뒤에 동일 블록 삽입:

```markdown
## 하네스가 수집한 공공데이터(data.go.kr) 스냅샷
{{OPENAPI_DATA}}
```
c_codex_research.md에는 한 줄 추가: "위 파일은 절대경로로 직접 읽을 수 있다(read-only). 수치·목록 근거로 우선 활용하라."

- [ ] **Step 8: 검증** — 신규 테스트 4개 + 전체 스위트, 그리고 dry-run:
  `python run.py --dry-run --workflow research --workspace . --request "최근 소프트웨어 용역 입찰공고 동향"` → exit 0, run_dir에 `01_openapi_plan_prompt.md`·`01_openapi_plan_command.json`(claude light 모델, `--allowedTools`에 WebSearch 포함 확인)·`openapi_manifest.json`(items 0) 존재, `stage_a_*_prompt.md`에 "(공공데이터 스냅샷 없음)" 치환 확인. `python run.py --dry-run --workflow routed --task-type backend --workspace . --request "x"`도 exit 0(타 워크플로 무회귀).
- [ ] **Step 9: 커밋** — `git add autoagent/config.py roles.default.json autoagent/workflows/research.py autoagent.config.example.json prompts/research/openapi_plan.md prompts/research/a_researcher.md prompts/research/b_market_researcher.md prompts/research/c_codex_research.md prompts/research/d_fact_report.md prompts/research/derive.md tests/research/test_openapi_collection.py` → `feat(openapi): 리서치 워크플로에 공공데이터 수집 스테이지 배선(계획 프롬프트·역할·OPENAPI_DATA)`.

---

### Task 4: 라이브 스모크 스크립트 + 문서 + 최종 검증

**Files:**
- Create: `scripts/openapi_smoke.py`
- Modify: `CLAUDE.md`, `README.md`, `commands/aar.md`

- [ ] **Step 1: `scripts/openapi_smoke.py`**

```python
"""data.go.kr OpenAPI 라이브 스모크: 레지스트리 오퍼레이션 1건을 numOfRows=1로 실제 호출한다.

사용: python scripts/openapi_smoke.py [service_id] [operation] [k=v ...]
키는 autoagent.config.json(data_go_kr_service_key) > env DATA_GO_KR_SERVICE_KEY. pytest 대상 아님.
출력은 ASCII+한글만(키·전체 URL은 출력하지 않는다).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autoagent.artifacts import DEFAULT_CONFIG  # noqa: E402
from autoagent.config import load_config  # noqa: E402
from autoagent.data import openapi as oa  # noqa: E402


def main(argv: list[str]) -> int:
    config = load_config(DEFAULT_CONFIG)
    key = config.data_go_kr_service_key
    if not key:
        print("인증키 없음: autoagent.config.json의 data_go_kr_service_key 또는 env DATA_GO_KR_SERVICE_KEY")
        return 2
    reg = oa.load_registry()
    service = argv[1] if len(argv) > 1 else "user_info"
    ops = reg.services[service]["operations"]
    operation = argv[2] if len(argv) > 2 else next(iter(ops))
    params = {"numOfRows": "1", "pageNo": "1"}
    for pair in argv[3:]:
        k, _, v = pair.partition("=")
        params[k] = v
    call = oa.PlanCall(id="smoke", service=service, operation=operation, params=params, purpose="smoke")
    out_dir = Path(__file__).resolve().parents[1] / "runs" / "_openapi_smoke"
    items = oa.execute_plan([call], reg, key, out_dir, pause_seconds=0)
    item = items[0]
    print(f"service={service} operation={operation}")
    print(f"http_status={item.http_status} result_code={item.result_code} result_msg={item.result_msg} row_count={item.row_count}")
    print(f"error={item.error} snapshot={item.snapshot_path}")
    return 0 if item.error is None else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

`runs/`는 gitignored라 스모크 산출물이 커밋되지 않는다. 필요 파라미터(예: 입찰공고는 `inqryDiv`·기간 필수)는 인자로 넘긴다.

- [ ] **Step 2: 라이브 스모크 1회** — 컨트롤러가 `autoagent.config.json`에 키를 넣어둔 뒤:
  `python scripts/openapi_smoke.py user_info` (필수 파라미터가 있으면 레지스트리 설명대로 인자 추가). 기대: `http_status=200 result_code=00`. 실패 시 원인(파라미터 누락/키 미승인/엔드포인트 오기)을 판정해 레지스트리를 고치고 재시도 — 이 결과가 곧 레지스트리 정확도 검증이다. 최소 2개 서비스(user_info + bid_notice)에서 성공을 확인하고 출력을 보고에 기록.
- [ ] **Step 3: 문서** — `CLAUDE.md` Workflows 절 `research` 항목 끝에 한 문장: "seed 직후 `01_openapi_plan`(Claude light)이 data.go.kr 승인 OpenAPI 수집계획을 내고 하네스가 실행해 `openapi/*.json`+`openapi_manifest.json`을 남긴다(레지스트리 `autoagent/data/openapi_registry.json`, 키 `data_go_kr_service_key`/env `DATA_GO_KR_SERVICE_KEY`, 없으면 생략). 라이브 확인은 `scripts/openapi_smoke.py`." `README.md` 리서치 절에 설정 방법(키·레지스트리 확장법·스모크) 소절 추가. `commands/aar.md`에는 게이트 흐름 변화가 없으므로 "공공데이터 수집 결과는 `openapi_manifest.json`" 한 줄만.
- [ ] **Step 4: 최종 검증** — `python -m pytest tests/ -q`(전부 통과), research dry-run exit 0, 스모크 성공 출력. 보고에 **"전체 리서치 라이브 런은 여전히 미실증(사용자 인계); 키·엔드포인트는 스모크로 실증"** 명시.
- [ ] **Step 5: 커밋** — `git add scripts/openapi_smoke.py CLAUDE.md README.md commands/aar.md` (+레지스트리 수정 시 `autoagent/data/openapi_registry.json`) → `feat(openapi): 라이브 스모크 스크립트 + 문서`.

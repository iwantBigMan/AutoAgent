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
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit, urlunsplit
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


def _reserved_param_names(svc: dict, operation: str) -> set[str]:
    """하네스가 주입하는 파라미터명(소문자): serviceKey + 오퍼레이션 key_param, type + 서비스 type_param."""
    op_meta = (svc.get("operations") or {}).get(operation) or {}
    return {"servicekey", str(op_meta.get("key_param", "serviceKey")).lower(),
            "type", str(svc.get("type_param", "type")).lower()}


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
        elif any(k.lower() in _reserved_param_names(svc, call.operation) for k in call.params):
            reason = "params에 serviceKey/type 등 하네스 주입 파라미터 금지"
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
    """base_url/operation?<key_param>=...&<type_param>=json&params. 키는 Decoding 원문을 urlencode가 인코딩한다.

    key_param(오퍼레이션별, 기본 serviceKey)과 type_param(서비스별, 기본 type)은
    레지스트리 오버라이드를 따른다(예: user_info.getUnptRsttCorpInfo02는 ServiceKey,
    mois_safety는 resultType). 인증키 파라미터와 응답형식 파라미터 둘 다 계획 params로
    덮어쓸 수 없도록 query.update(call.params) 이후에 마지막으로 설정한다(플래너가
    type/resultType을 params에 넣어 응답형식을 바꾸는 것을 차단).
    """
    svc = reg.services[call.service]
    op_meta = (svc.get("operations") or {}).get(call.operation, {})
    key_param = op_meta.get("key_param", "serviceKey")
    type_param = svc.get("type_param", "type")
    query: dict[str, str] = {}
    query.update(call.params)
    if svc["format"] == "json":
        query[type_param] = "json"
    query[key_param] = key
    return f"{svc['base_url'].rstrip('/')}/{call.operation}?{urlencode(query)}"


def redact_url(url: str) -> str:
    """serviceKey(대소문자 무관, 예: ServiceKey) 값을 ***로 바꾼 URL(manifest 기록용)."""
    parts = urlsplit(url)
    pairs = [(k, "***" if k.lower() == "servicekey" else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _scrub(text: str, key: str) -> str:
    """본문·메시지에서 키(원문·quote·quote_plus) 흔적을 제거한다."""
    if not key:
        return text
    return text.replace(key, "***").replace(quote(key, safe=""), "***").replace(quote_plus(key, safe=""), "***")


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
    """data.go.kr 표준 JSON(response.header/body)이면 resultCode·resultMsg·행수를 best-effort로 뽑는다.

    라이브 스모크에서 확인된 비표준 실측 응답 3형태도 처리한다(모두 best-effort):
    (1) 최상위 키가 `response`가 아닌 `*.ResponseError` 래퍼(나라장터 필수값 누락 에러) -
        header를 가진 첫 top-level dict 값을 response로 취급한다.
    (2) body에 `items`가 아닌 단수 `item`이 직결된 경우(행안부 mois_safety 성공 응답).
    (3) header가 없고 대신 `response.result`에 코드가 있는 경우(KISA whois JSON) -
        result.result_code/result_msg를 쓰고, rows는 response.whois 존재 여부로 판단한다.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, None, None, False
    if not isinstance(data, dict):
        return None, None, None, True
    response = data.get("response") if isinstance(data.get("response"), dict) else None
    if response is None:
        for value in data.values():
            if isinstance(value, dict) and isinstance(value.get("header"), dict):
                response = value
                break
    response = response or {}
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    payload = response.get("body") if isinstance(response.get("body"), dict) else {}
    items = payload.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if items is None:
        items = payload.get("item")
    if isinstance(items, list):
        rows: int | None = len(items)
    elif isinstance(items, dict):
        rows = 1
    else:
        rows = 0 if payload else None
    if header:
        code = header.get("resultCode")
        msg = header.get("resultMsg")
    else:
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        code = result.get("result_code")
        msg = result.get("result_msg")
        whois = response.get("whois")
        rows = 1 if isinstance(whois, dict) and whois else None
    return (str(code) if code is not None else None), msg, rows, True


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
            url_redacted=_scrub(redact_url(url), key), fetch_ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        # 성공 코드는 서비스별로 다를 수 있다(표준 "00", KISA whois는 "10000") - 레지스트리 ok_codes로 선언.
        ok_codes = reg.services[call.service].get("ok_codes") or ["00"]
        if code is not None and code not in ok_codes:
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

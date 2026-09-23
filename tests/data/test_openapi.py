"""data.go.kr OpenAPI 수집기 단위테스트(네트워크·모델 호출 없음, fetch 주입)."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote, quote_plus

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
    key_encoded = quote(KEY, safe="")
    assert len(items) == 4 and all(key_encoded in u for u in seen)  # 실제 URL에는 키가 들어간다(URL 인코딩된 형태로)
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


def test_scrub_removes_plus_encoded_key_with_space():
    key = "AB CD+/="
    text = f"raw:{key} q:{quote(key, safe='')} qp:{quote_plus(key, safe='')}"
    out = oa._scrub(text, key)
    assert key not in out and quote(key, safe="") not in out and quote_plus(key, safe="") not in out
    assert out.count("***") == 3


def _reg_with_overrides(tmp_path: Path) -> oa.Registry:
    """key_param(오퍼레이션별)·type_param(서비스별) 오버라이드가 있는 레지스트리 픽스처."""
    data = json.loads(json.dumps(FIXTURE))
    data["services"]["bid_notice"]["operations"]["getBidCap"] = {
        "purpose": "대문자 키 오퍼레이션", "params": {}, "key_param": "ServiceKey",
    }
    data["services"]["mois_like"] = {
        "name": "테스트 안전정보", "data_go_kr_id": "3", "format": "json",
        "base_url": "https://apis.data.go.kr/1741000/TestSafety", "type_param": "resultType",
        "operations": {"getSafety": {"purpose": "안전정보 조회", "params": {}}},
    }
    return _reg(tmp_path, data)


def test_build_url_honors_op_level_key_param_override(tmp_path):
    reg = _reg_with_overrides(tmp_path)
    call = oa.PlanCall(id="c1", service="bid_notice", operation="getBidCap", params={})
    url = oa.build_url(reg, call, KEY)
    assert "ServiceKey=" in url
    assert "serviceKey=" not in url


def test_build_url_key_param_cannot_be_overridden_by_planner_params(tmp_path):
    # validate_plan은 대소문자 무관하게 serviceKey 계열 params를 걸러내지만(별도 테스트로 확인),
    # build_url 자신도 key_param과 이름이 정확히 같은 planner params로 실제 키가 덮이지 않아야 한다.
    reg = _reg_with_overrides(tmp_path)
    call = oa.PlanCall(id="c1", service="bid_notice", operation="getBidCap", params={"ServiceKey": "attacker"})
    url = oa.build_url(reg, call, KEY)
    key_encoded = quote(KEY, safe="")
    assert "attacker" not in url
    assert f"ServiceKey={key_encoded}" in url


def test_validate_plan_rejects_servicekey_case_insensitively(tmp_path):
    reg = _reg_with_overrides(tmp_path)
    plan = oa.parse_plan(_plan_text([
        {"id": "c1", "service": "bid_notice", "operation": "getBidCap", "params": {"ServiceKey": "x"}},
        {"id": "c2", "service": "bid_notice", "operation": "getBidCap", "params": {"SERVICEKEY": "x"}},
    ]))
    valid, rejected = oa.validate_plan(plan, reg, max_calls=10)
    assert valid == []
    reasons = {r["id"]: r["reason"] for r in rejected}
    assert "serviceKey" in reasons["c1"] and "serviceKey" in reasons["c2"]


def test_build_url_honors_service_level_type_param_override(tmp_path):
    reg = _reg_with_overrides(tmp_path)
    call = oa.PlanCall(id="c1", service="mois_like", operation="getSafety", params={})
    url = oa.build_url(reg, call, KEY)
    assert "resultType=json" in url
    assert "type=json" not in url


def test_redact_url_redacts_capitalized_servicekey(tmp_path):
    reg = _reg_with_overrides(tmp_path)
    call = oa.PlanCall(id="c1", service="bid_notice", operation="getBidCap", params={})
    url = oa.build_url(reg, call, KEY)
    red = oa.redact_url(url)
    assert "SECRET" not in red
    assert "ServiceKey=%2A%2A%2A" in red or "ServiceKey=***" in red


def test_build_url_type_param_cannot_be_overridden_by_planner_params(tmp_path):
    # 계획 params에 응답형식 파라미터(type)를 넣어도 레지스트리가 정한 json이 최종값이어야 한다.
    reg = _reg(tmp_path)
    call = oa.PlanCall(id="c1", service="bid_notice", operation="getBidList", params={"type": "xml"})
    url = oa.build_url(reg, call, KEY)
    assert "type=json" in url
    assert "type=xml" not in url


def test_build_url_service_level_type_param_cannot_be_overridden(tmp_path):
    # type_param 오버라이드(resultType)가 있는 서비스도 동일하게 보호되어야 한다.
    reg = _reg_with_overrides(tmp_path)
    call = oa.PlanCall(id="c1", service="mois_like", operation="getSafety", params={"resultType": "xml"})
    url = oa.build_url(reg, call, KEY)
    assert "resultType=json" in url
    assert "resultType=xml" not in url


def test_summarize_body_standard_shape_still_works():
    code, msg, rows, is_json = oa._summarize_body(_ok_body(2))
    assert code == "00" and msg == "NORMAL SERVICE" and rows == 2 and is_json


def test_summarize_body_response_error_wrapper():
    # 나라장터 필수값 누락 에러: 최상위 키가 response가 아니다.
    body = json.dumps({"nkoneps.com.response.ResponseError": {
        "header": {"resultCode": "08", "resultMsg": "필수값 입력 에러"}}})
    code, msg, rows, is_json = oa._summarize_body(body)
    assert code == "08" and msg == "필수값 입력 에러" and rows is None and is_json


def test_summarize_body_singular_item_field():
    # 행안부(mois_safety) 성공 응답: items가 아니라 단수 item.
    body = json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
                                    "body": {"numOfRows": 1, "pageNo": 1, "totalCount": 23089,
                                             "item": [{"a": 1}]}}})
    code, msg, rows, is_json = oa._summarize_body(body)
    assert code == "00" and msg == "NORMAL_CODE" and rows == 1 and is_json


def test_summarize_body_kisa_whois_result_wrapper():
    # KISA whois(answer=json): header 없이 response.result에 코드가 있다.
    body = json.dumps({"response": {"result": {"result_code": "10000", "result_msg": "정상 응답 입니다."},
                                    "whois": {"krdomain": {"name": "example.kr"}}}})
    code, msg, rows, is_json = oa._summarize_body(body)
    assert code == "10000" and msg == "정상 응답 입니다." and rows == 1 and is_json


def test_execute_plan_honors_service_ok_codes(tmp_path):
    """서비스별 ok_codes(예: KISA whois "10000")를 성공으로 보고, 미선언 서비스는 "00"만 성공."""
    data = json.loads(json.dumps(FIXTURE))
    data["services"]["whois_like"] = {
        "name": "테스트 whois", "data_go_kr_id": "4", "format": "xml", "ok_codes": ["10000"],
        "base_url": "https://apis.data.go.kr/B551505/whois",
        "operations": {"domain_name": {"purpose": "도메인 조회", "params": {}}},
    }
    reg = _reg(tmp_path, data)
    kisa_body = json.dumps({"response": {"result": {"result_code": "10000", "result_msg": "ok"}, "whois": {"krdomain": {"name": "x"}}}})
    items = oa.execute_plan([oa.PlanCall("k1", "whois_like", "domain_name", {}),
                             oa.PlanCall("b1", "bid_notice", "getBidList", {})],
                            reg, "KEY", tmp_path, fetch=lambda u, t: oa.FetchResult(200, kisa_body), pause_seconds=0)
    assert items[0].result_code == "10000" and items[0].error is None
    assert items[1].error is not None and "10000" in items[1].error

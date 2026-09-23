"""data.go.kr OpenAPI 라이브 스모크: 레지스트리 오퍼레이션 1건을 numOfRows=1로 실제 호출한다.

사용: python scripts/openapi_smoke.py [service_id] [operation] [k=v ...]
예: python scripts/openapi_smoke.py user_info getPrcrmntCorpBasicInfo02 bizno=1234567890
service_id 기본값은 user_info, operation 기본값은 해당 서비스의 첫 오퍼레이션이다.
레지스트리 전체 서비스: bid_notice, pre_spec, order_plan, award, contract,
contract_process, user_info, procure_request, price_info, open_standard,
private_bid, kisa_domain, mois_safety (autoagent/data/openapi_registry.json).
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

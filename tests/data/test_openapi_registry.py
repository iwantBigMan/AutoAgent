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

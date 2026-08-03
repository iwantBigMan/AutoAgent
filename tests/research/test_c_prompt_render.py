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

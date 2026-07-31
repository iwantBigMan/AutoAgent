"""d 스테이지 프롬프트(리서처/검증기) render + 별칭 검증(test_c_prompt_render.py와 동형)."""
from __future__ import annotations

from autoagent.artifacts import PROMPT_ALIASES, render_template


def test_d_prompt_aliases_registered() -> None:
    assert PROMPT_ALIASES["d_fact_report.md"] == "research/d_fact_report.md"
    assert PROMPT_ALIASES["d_grounding_verify.md"] == "research/d_grounding_verify.md"


def test_d_researcher_prompt_renders_all_placeholders() -> None:
    rendered = render_template(
        "d_fact_report.md",
        {
            "REQUEST": "Acme Corp 리서치",
            "SEED_PIN": '{"company": "Acme"}',
            "PRIOR_STAGE_SUMMARY": "-",
            "PRIOR_VERDICT_FEEDBACK": "-",
        },
    )
    assert "{{" not in rendered
    assert "quoted_span" in rendered
    assert "fetched_text" in rendered
    assert "웹은 너만 쓴다" in rendered


def test_d_verifier_prompt_renders_all_placeholders() -> None:
    rendered = render_template(
        "d_grounding_verify.md",
        {
            "REPORT_MD": "# 리포트",
            "CLAIMS_JSON": "[]",
            "SOURCES_SNAPSHOTS_JSON": "[]",
        },
    )
    assert "{{" not in rendered
    assert "GROUNDING_VERDICT" in rendered
    assert "matched_quote" in rendered
    assert "재fetch" in rendered

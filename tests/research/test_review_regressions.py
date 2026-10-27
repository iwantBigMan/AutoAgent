"""빈 근거 오통과 및 보고서 무한 반복 회귀 검증."""
import subprocess
import sys

import pytest

from autoagent.research.data_quality import run_data_quality
from autoagent.research.source_grounding import verify_source_grounding


@pytest.mark.parametrize("md", ["#### Heading", "| orphan row |", "#nospace", "###### H6"])
def test_unknown_markdown_terminates(md):
    code = "from autoagent.research.html_report import markdown_to_html; print(markdown_to_html(" + repr(md) + "))"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0
    assert md in proc.stdout


@pytest.mark.parametrize("stage", [{}, {"cleaned_files": []}, {"cleaned_files": [None]}])
def test_absent_csv_artifacts_are_blocked(tmp_path, stage):
    verdict = run_data_quality(stage, tmp_path, verifier_agent="code")
    assert verdict.status == "blocked"
    assert not verdict.raw["overall_ok"]


@pytest.mark.parametrize("raw", ["invalid response", "GROUNDING_VERDICT: pass\n```json\n{}\n```",
                               'GROUNDING_VERDICT: blocked\n```json\n{"verdict":"blocked","claim_checks":[]}\n```'])
def test_invalid_grounding_is_not_promoted(tmp_path, raw):
    stage = {"claims": [{"id": "x", "kind": "inference"}], "sources": []}
    verdict = verify_source_grounding(stage, tmp_path, verifier_agent="codex", config=None, model_raw_text=raw)
    assert verdict.status == "blocked"


def test_missing_semantic_check_cannot_pass(tmp_path):
    stage = {"claims": [{"id": "x", "kind": "inference"}], "sources": []}
    raw = 'GROUNDING_VERDICT: pass\n```json\n{"verdict":"pass","claim_checks":[]}\n```'
    verdict = verify_source_grounding(stage, tmp_path, verifier_agent="codex", config=None, model_raw_text=raw)
    assert verdict.status == "needs_changes"

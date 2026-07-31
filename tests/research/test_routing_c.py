"""c 스테이지 **검증 경로** 락(오케스트레이터 행동 계약, 라우팅 테이블 값 아님).

배경: choose_researcher("c") → (codex, claude) 값 자체는 이미
tests/research/test_routing_researcher.py가 5스테이지 전부 parametrize로 잠가둔다.
이 파일이 원래 하던 라우팅 테이블 중복 검증은 회귀 방어력이 0이라 삭제하고,
그 대신 아직 아무 테스트도 잠그지 않은 계약을 락한다:

    c 스테이지는 run_stage_loop 안에서 **모델 crossmodel 검증기를 호출하지 않고**
    코드 검증기(data_quality 어댑터)로 간다(Task 13 B1-fix 행동).

run_stage_loop("c", ...)를 _run_agent_step monkeypatch로 몰아서, 검증기 role로는
호출되지 않음(코드 경로라 모델 0회)을 확인하고, verify가 adapter="data_quality"로
불렸는지를 verdict.adapter로 확인한다. 누가 STAGE_ADAPTER["c"]를 "crossmodel"로
되돌리거나 c 분기(_run_stage_c_verify 호출)를 지우면 이 테스트가 fail한다:
  - STAGE_ADAPTER["c"]를 "crossmodel"로 바꾸면 → run_stage_loop의 c 분기가 없어
    실제로는 무관하지만(if stage=="c" 분기가 하드코딩) 방어 목적상
    run_stage_loop의 `if stage == "c":` 분기 자체를 지우면 verifier role 호출이
    발생 → verifier_called 어서션이 fail한다.
  - _run_stage_c_verify가 verify(..., adapter="data_quality"가 아닌 다른 값)를
    부르게 바뀌면 → result.verdict.adapter 어서션이 fail한다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from autoagent.config import load_config
from autoagent.artifacts import DEFAULT_CONFIG
from autoagent.runner import AgentCallBudget
from autoagent.workflows import research as R

# c 리서처(codex)가 내는 DATA_QUALITY_OUTPUT stdout. cleaned_files를 비워 파일 IO 없이
# run_data_quality가 즉시 pass로 재계산하게 한다(코드 검증 경로 자체의 배선만 확인 목적).
_C_RESEARCHER_OUT = (
    "DATA_QUALITY_OUTPUT\n```json\n"
    '{"cleaned_files":[],"transform_manifest":{"steps":[]},'
    '"derived_claims":[],"schema_expectations":{},"sanity_rules":{}}\n```\n'
)


def _config():
    return load_config(DEFAULT_CONFIG)


def _ctx(tmp_path: Path) -> R.ResearchContext:
    args = argparse.Namespace(dry_run=False, read_only=False, max_agent_calls=0)
    ctx = R.ResearchContext(
        args=args, config=_config(), request="c 스테이지 검증 경로 락", run_dir=tmp_path,
        budget=AgentCallBudget(0), seed_contract="",
    )
    ctx.state = {"outer_pass": 1, "stage": "c", "inner_round": 0, "seed_pin": {},
                 "verified_claims": [], "stage_status": {}}
    return ctx


def test_c_stage_uses_code_verifier_not_crossmodel_model_call(tmp_path: Path, monkeypatch) -> None:
    """c 스테이지 실행 시 verifier role로 _run_agent_step이 호출되지 않고(모델검증 0회),

    verify가 data_quality 어댑터로 불려 verdict.adapter == "data_quality"가 된다.
    """
    verifier_role_calls: list[str] = []

    def fake_agent_step(ctx, *, agent, role_id, name, prompt_name, prompt_values, next_step, dry_output):
        if role_id == "verifier":
            verifier_role_calls.append(agent)  # 이 리스트가 채워지면 안 됨(코드 검증 경로 위반)
        return _C_RESEARCHER_OUT

    monkeypatch.setattr(R, "_run_agent_step", fake_agent_step)

    ctx = _ctx(tmp_path)
    result = R.run_stage_loop("c", outer_pass=1, ctx=ctx)

    # 핵심 계약 1: 모델 crossmodel 검증기가 호출되지 않는다(코드 검증 경로라 verifier role 0회).
    assert verifier_role_calls == []
    # 핵심 계약 2: 실제로 data_quality 어댑터를 탔다(코드 재계산 경로 확인).
    assert result.verdict is not None
    assert result.verdict.adapter == "data_quality"
    assert result.status == "resolved"
    assert result.verdict.status == "pass"

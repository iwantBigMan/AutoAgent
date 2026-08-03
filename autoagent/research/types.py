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

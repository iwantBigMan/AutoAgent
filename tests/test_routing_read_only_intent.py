"""읽기 전용 의도 감지의 범위 회귀 테스트.

요청 전체가 검토 전용일 때만 구현을 막고, 특정 파일·기능에 대한 금지 조건이 섞인
구현 요청은 구현 라우트로 보내야 한다.
"""
import pytest

from autoagent.routing import read_only_intent, route_task


IMPLEMENTATION_WITH_SCOPED_PROHIBITIONS = [
    "롤백 호환성 테스트 체계를 구현해주세요. 이미 적용된 마이그레이션 파일은 임의 수정하지 마세요.",
    "검증 스크립트를 구현해주세요. 운영 DB downgrade나 덤프 자동 복원을 구현하지 마세요.",
    "테스트를 추가해 주세요. 적용 이력을 확인하기 전에는 기존 revision을 변경하지 마세요.",
    "검증 하네스를 만들어 주세요. 승인 시스템 등을 과도하게 구현하지 마세요.",
    "점검 스크립트를 추가해 주세요. deploy/scripts/backup.sh는 수정 금지 파일입니다.",
    "도커 볼륨을 read-only로 마운트하는 검증 스크립트를 추가해 주세요.",
    "Add a health endpoint. Do not modify the existing migrations.",
    "Implement the export job; never change the public API.",
]


@pytest.mark.parametrize("text", IMPLEMENTATION_WITH_SCOPED_PROHIBITIONS)
@pytest.mark.parametrize("task_type", ["auto", "backend"])
def test_scoped_prohibition_keeps_implementation_route(text, task_type):
    assert not read_only_intent(text)
    route = route_task(task_type, text)
    assert route["task_type"] == "backend"
    assert route["layers"]


@pytest.mark.parametrize("text", [
    "API review only",
    "Do not modify React components; review only",
    "DB를 수정하지 말고 검토만 해줘",
    "React 코드 리뷰만 해줘",
    "인증 흐름을 분석만 해 주세요. 결과는 추가 문서로 정리하지 않아도 됩니다.",
])
@pytest.mark.parametrize("task_type", ["auto", "backend", "frontend"])
def test_explicit_review_only_blocks_implementation(text, task_type):
    assert read_only_intent(text)
    route = route_task(task_type, text)
    assert route["task_type"] == "review"
    assert not route["layers"]


@pytest.mark.parametrize("text", [
    "DB 스키마는 변경하지 마세요. 현재 인덱스 구성을 설명해 주세요.",
    "Don't modify anything; explain how the auth flow works.",
    "코드는 수정하지 말고 배포 스크립트 위험만 알려줘",
    "read-only로 배포 스크립트를 살펴봐 주세요.",
])
def test_prohibition_without_affirmative_implementation_stays_review_on_auto(text):
    assert read_only_intent(text)
    assert route_task("auto", text)["task_type"] == "review"


@pytest.mark.parametrize("text", [
    "DB 스키마는 변경하지 마세요. 현재 인덱스 구성을 설명해 주세요.",
    "Don't modify anything; explain how the auth flow works.",
])
def test_explicit_task_type_is_escape_hatch_for_scoped_prohibition(text):
    assert route_task("backend", text)["task_type"] == "backend"


@pytest.mark.parametrize("text", [
    "새 기능은 만들지 말고 기존 코드의 결합도를 평가해 주세요",
    "기능을 추가하지 말고 구조 위험을 알려 주세요",
    "파일은 수정·삭제하지 말고 의존 관계를 설명해 주세요",
])
def test_negated_implementation_verbs_do_not_count_as_intent(text):
    assert read_only_intent(text)
    assert route_task("auto", text)["task_type"] == "review"

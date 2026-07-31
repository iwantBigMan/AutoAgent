"""markdown→HTML 변환·문서 조립 테스트(순수·결정론).

외부 의존 없이 헤딩/굵게/표/불릿/문단이 HTML로 변환되고, 완결 문서가 인라인 style을
품고 self-contained(외부 리소스 참조 없음)인지 고정한다.

write_desktop_report()는 실제 파일시스템에 쓰는 함수라 Path.home()을 tmp_path로
monkeypatch해 실제 바탕화면 오염 없이 검증한다(리뷰 Important 1). filename에
경로 traversal(`../evil.html` 등)이 섞여도 타깃 디렉터리 안에 갇히는지도 고정한다
(리뷰 Important 2).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autoagent.research.html_report import (
    markdown_to_html,
    render_report_html,
    write_desktop_report,
)


def test_heading_and_bold() -> None:
    html = markdown_to_html("# 제목\n\n본문 **강조** 끝")
    assert "<h1>제목</h1>" in html
    assert "<strong>강조</strong>" in html


def test_bullets_become_list() -> None:
    html = markdown_to_html("- 하나\n- 둘")
    assert "<ul>" in html and "<li>하나</li>" in html and "<li>둘</li>" in html


def test_table_rows() -> None:
    md = "| stage | status |\n| --- | --- |\n| a | passed |"
    html = markdown_to_html(md)
    assert "<table>" in html
    assert "<th>stage</th>" in html and "<th>status</th>" in html
    assert "<td>a</td>" in html and "<td>passed</td>" in html


def test_html_escaped() -> None:
    html = markdown_to_html("본문 <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_full_document_self_contained() -> None:
    doc = render_report_html(title="리서치 리포트", body_md="# 제목\n\n본문")
    assert doc.lstrip().lower().startswith("<!doctype html>")
    assert "<style>" in doc
    assert "<title>리서치 리포트</title>" in doc
    assert "http://" not in doc and "https://" not in doc.split("본문")[0]
    assert "<h1>제목</h1>" in doc


def test_markdown_to_html_empty_input() -> None:
    # 빈 입력 가드: 빈 문자열은 빈 출력이어야 한다(예외 없이).
    assert markdown_to_html("") == ""


def _patch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    # Path.home()을 tmp_path로 monkeypatch해 실제 ~/Desktop을 절대 건드리지 않는다.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_write_desktop_report_writes_file_with_desktop_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    (home / "Desktop").mkdir()

    path = write_desktop_report("<html>본문</html>", "report.html")

    assert path == home / "Desktop" / "report.html"
    assert path.read_text(encoding="utf-8") == "<html>본문</html>"


def test_write_desktop_report_falls_back_to_home_without_desktop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    # Desktop 디렉터리를 만들지 않음 -> 홈으로 폴백해야 한다.

    path = write_desktop_report("본문", "no-desktop.html")

    assert path == home / "no-desktop.html"
    assert path.read_text(encoding="utf-8") == "본문"


def test_write_desktop_report_is_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    (home / "Desktop").mkdir()

    path = write_desktop_report("한글 리포트 내용", "utf8.html")

    assert path.read_bytes().decode("utf-8") == "한글 리포트 내용"


def test_write_desktop_report_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # filename에 ../나 경로 구분자가 섞여도 타깃 디렉터리(Desktop) 밖으로 못 나가야 한다.
    home = _patch_home(monkeypatch, tmp_path)
    (home / "Desktop").mkdir()

    path = write_desktop_report("evil", "../evil.html")

    assert path.parent == home / "Desktop"
    assert path == home / "Desktop" / "evil.html"
    # 실제로 Desktop 밖(홈 루트)에는 파일이 생기지 않아야 한다.
    assert not (home / "evil.html").exists()


def test_write_desktop_report_rejects_nested_path_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    (home / "Desktop").mkdir()

    path = write_desktop_report("evil", "../../../etc/evil.html")

    assert path == home / "Desktop" / "evil.html"
    assert path.parent == home / "Desktop"

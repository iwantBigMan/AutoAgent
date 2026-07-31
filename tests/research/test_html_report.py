"""markdown→HTML 변환·문서 조립 테스트(순수·결정론).

외부 의존 없이 헤딩/굵게/표/불릿/문단이 HTML로 변환되고, 완결 문서가 인라인 style을
품고 self-contained(외부 리소스 참조 없음)인지 고정한다.
"""
from __future__ import annotations

from autoagent.research.html_report import markdown_to_html, render_report_html


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

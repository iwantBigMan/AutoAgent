"""리서치 리포트 HTML 렌더(외부 의존 0).

markdown 본문을 최소 파서로 HTML로 바꾸고 인라인 CSS로 감싼 standalone 문서를 만든다
(pandoc 회피). 산출물은 바탕화면 standalone HTML로 전달한다(deliver-local-html 준수,
아티팩트 아님). 지원 문법: #/##/### 헤딩, **굵게**, `- ` 불릿, GFM 표(파이프+구분행), 문단.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """인라인 마크업 처리(먼저 escape 후 굵게만 복원). XSS/깨짐 방지로 escape가 먼저다."""
    escaped = _html.escape(text)
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


def _is_table_sep(line: str) -> bool:
    # | --- | --- | 형태의 구분행(셀이 대시/콜론/공백뿐).
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(set(c) <= set("-: ") and c for c in cells)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_html(md: str) -> str:
    """지원 문법 한정 markdown → HTML 조각(문서 래퍼 없음)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # 헤딩
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        # 표: 헤더행 + 구분행 + 바디행들
        if stripped.startswith("|") and i + 1 < n and _is_table_sep(lines[i + 1]):
            header = _split_row(stripped)
            out.append("<table>")
            out.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead>")
            out.append("<tbody>")
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i].strip())
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        # 불릿 리스트
        if stripped.startswith("- "):
            out.append("<ul>")
            while i < n and lines[i].strip().startswith("- "):
                out.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        # 문단(연속 비어있지 않은 줄 합침)
        para: list[str] = []
        while i < n and lines[i].strip() and not lines[i].strip().startswith(("#", "|", "- ")):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(out)


_STYLE = """
body{font-family:-apple-system,Segoe UI,Roboto,'Malgun Gothic',sans-serif;max-width:860px;
margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a}
h1{border-bottom:2px solid #333;padding-bottom:.3rem}
h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{border:1px solid #ccc;padding:.4rem .6rem;text-align:left}
th{background:#f2f2f2}
code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}
.warn{background:#fff3cd;border:1px solid #ffe08a;padding:.6rem;border-radius:4px}
""".strip()


def render_report_html(*, title: str, body_md: str, prepend_html: str = "") -> str:
    """본문 markdown을 완결된 standalone HTML 문서로 만든다(인라인 CSS, 외부 리소스 0).

    prepend_html은 markdown 변환을 거치지 않고 body 최상단에 그대로(unescaped) 삽입한다
    — 코드가 생성한 신뢰된 인라인-CSS HTML(커버리지 배너/매트릭스)용 통로다([T28a]).
    markdown_to_html._inline이 모든 텍스트를 html.escape()하기 때문에, 그 경로로 넘기면
    실제 스타일 대신 escape된 소스 문자열(`&lt;div…&gt;`)이 그대로 보이는 문제를 피한다.
    """
    body_html = markdown_to_html(body_md)
    safe_title = _html.escape(title)
    return (
        "<!doctype html>\n<html lang=\"ko\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{safe_title}</title>\n"
        f"<style>\n{_STYLE}\n</style>\n"
        "</head>\n<body>\n"
        f"{prepend_html}\n"
        f"{body_html}\n"
        "</body>\n</html>\n"
    )


def write_desktop_report(html: str, filename: str) -> Path:
    """바탕화면(~/Desktop)에 리포트 HTML을 UTF-8로 기록하고 경로를 반환한다.

    브라우저 오픈은 호출부가 결정한다(os.startfile). Desktop이 없으면 홈에 저장.
    filename은 `..`나 경로 구분자를 포함해도 `Path(filename).name`으로 정규화해
    베이스네임만 취한다 — 타깃 디렉터리 밖으로 쓰는 path traversal을 막는다.
    """
    desktop = Path.home() / "Desktop"
    target_dir = desktop if desktop.exists() else Path.home()
    safe_name = Path(filename).name
    path = target_dir / safe_name
    path.write_text(html, encoding="utf-8", newline="\n")
    return path

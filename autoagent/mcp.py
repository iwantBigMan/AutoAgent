"""MCP 통합 헬퍼(증분 2).

- write_claude_mcp_config: config.mcp_servers를 Claude용 `.aa_mcp.json`으로 쓰고 경로를 반환한다.
  타깃 레포의 `.mcp.json`에 의존하지 않고 하네스가 서버 목록을 소유하기 위함.
- check_mcp_symmetry: Claude에 줄 서버 목록과 `~/.codex/config.toml`의 `[mcp_servers.*]`가
  대칭(같은 서버가 양쪽에)인지 검사해 경고 문자열을 반환한다(차단이 아니라 soft 경고).

두 함수 모두 mcp_servers가 비어 있으면 아무것도 하지 않는다(opt-in → 기존 동작 불변).
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from autoagent.config import Config


def write_claude_mcp_config(config: Config, out_dir: Path, dry_run: bool = False) -> str | None:
    """config.mcp_servers가 있으면 {out_dir}/.aa_mcp.json에 mcpServers 형태로 쓰고 절대경로를 반환한다.

    비어 있으면 None을 반환한다(호출부가 --mcp-config를 붙이지 않아 기존 명령과 바이트 동일).
    out_dir은 실행별 run_dir이라 동시/재개 실행이 같은 파일을 공유·경합하지 않는다.
    dry_run이면 파일을 쓰지 않고 경로만 반환한다(dry-run은 명령만 렌더, 디스크 무변경).
    """
    if not config.mcp_servers:
        return None
    path = out_dir / ".aa_mcp.json"
    if not dry_run:
        payload = {"mcpServers": config.mcp_servers}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"Failed to write MCP config ({path}): {exc}")
    return str(path)


def check_mcp_symmetry(config: Config, codex_config_path: Path | None = None) -> list[str]:
    """Claude에 줄 mcp_servers와 Codex config.toml의 [mcp_servers.*] 서버 이름을 비교한다.

    같지 않으면 경고 문자열 목록을 반환한다(차단 아님). mcp_servers가 비면 검사하지 않는다.
    크로스모델 불변식(리뷰어=반대 모델)상, 한쪽에만 있는 서버는 비대칭 리뷰를 부른다.
    """
    if not config.mcp_servers:
        return []
    claude_servers = set(config.mcp_servers.keys())
    path = codex_config_path or (Path.home() / ".codex" / "config.toml")
    if not path.exists():
        return [f"Codex config not found ({path}); cannot verify MCP server symmetry."]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"Failed to read Codex config ({path}): {exc}"]

    # 예상 형태는 [mcp_servers.<name>] 서브테이블(dict). 배열([[mcp_servers]]) 등 다른 형태면
    # .keys() 크래시 대신 경고로 안전하게 빠진다(방어).
    raw_servers = data.get("mcp_servers")
    if raw_servers and not isinstance(raw_servers, dict):
        return [f"Codex config mcp_servers has unexpected shape ({type(raw_servers).__name__}); "
                f"expected [mcp_servers.<name>] sub-tables. Cannot verify symmetry."]
    codex_servers = set((raw_servers or {}).keys())
    warnings: list[str] = []
    missing_in_codex = claude_servers - codex_servers
    missing_in_claude = codex_servers - claude_servers
    if missing_in_codex:
        warnings.append(
            f"MCP servers on Claude but not Codex: {sorted(missing_in_codex)} "
            f"-- reviewer may be asymmetric; add them to ~/.codex/config.toml [mcp_servers.*]."
        )
    if missing_in_claude:
        warnings.append(
            f"MCP servers on Codex but not Claude: {sorted(missing_in_claude)} (info)."
        )
    return warnings

# 포터빌리티: 하드코딩 제거 + 자동 감지 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 머신 종속 하드코딩 3곳(커맨드의 하네스 절대경로, `.cmd` CLI 명령명, 기본 workspace 개인 경로)을 자동 감지/자기등록으로 대체해, 클론만 받으면 파일 수정 없이 동작하게 한다.

**Architecture:** `cli.main()`이 매 실행 `~/.autoagent/home`에 자기 ROOT를 자기등록하고 `/aa`·`/aar`는 env→포인터 순으로 해석. `load_config`는 CLI 명령명을 후보 순회(`shutil.which`)로 감지하되 명시값은 그대로 두고, workspace 하드코딩 폴백은 제거해 `Path | None`으로 만들고 cli의 `require_workspace` 가드가 안내한다. 스펙: `docs/superpowers/specs/2026-09-22-portability-autodetect-design.md`.

**Tech Stack:** Python 3(stdlib만: shutil/pathlib), pytest.

## Global Constraints

- 모든 모듈 첫머리 **한국어 docstring**, 함수 한국어 인라인 주석(기존 스타일).
- `from __future__ import annotations`, PEP 604 타입(`str | None`).
- **stdout `print`는 ASCII + 한글만**(em dash 등 비-cp949 문자는 Windows cp949 크래시).
- **무회귀 계약**: config에 명시값이 있는 머신은 동작 바이트 동일. `autodetect_cli`는 절대경로가 아닌 **후보 이름**을 반환(command json 아티팩트 바이트 호환). dry-run은 CLI 미설치 머신에서도 동작(자동 감지는 절대 raise하지 않음).
- workspace는 **자동 추측 금지**(cwd 폴백 없음) — 미지정은 명확한 안내 후 종료.
- 브랜치 `feature/portability-autodetect`. 커밋은 각 태스크의 `git add` 파일 목록만 스테이징(**`git add -A` 금지** — 작업트리에 이 기능과 무관한 미커밋 수정 `autoagent/workflows/research.py`·`routed_impl.py`·plans 문서들이 있음. 절대 스테이징·수정하지 말 것).
- 커밋 메시지 끝에 트레일러: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: config.py — CLI 명령 자동 감지 + workspace 하드코딩 제거

**Files:**
- Modify: `autoagent/config.py`
- Test: `tests/test_portability.py` (신규)

**Interfaces:**
- Produces: `autodetect_cli(explicit: str | None, candidates: list[str]) -> str` (모듈 공개, Task 1 테스트가 소비), `Config.workspace: Path | None`, `load_config`가 workspace 미지정 시 `None` 반환(Task 2의 cli 가드가 소비).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_portability.py` 생성:

```python
"""포터빌리티(하드코딩 제거+자동 감지) 단위테스트: CLI 명령 감지·workspace 미지정."""
from __future__ import annotations

from autoagent import config as config_mod


def test_autodetect_cli_explicit_passthrough(monkeypatch):
    # 명시값은 which를 묻지 않고 그대로 반환한다(무회귀 계약).
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    assert config_mod.autodetect_cli("my-claude.exe", ["claude.cmd", "claude"]) == "my-claude.exe"


def test_autodetect_cli_auto_and_none_pick_first_found(monkeypatch):
    # 미지정(None)과 "auto"는 동일 취급 — 후보 순회 첫 발견 '이름'을 반환.
    monkeypatch.setattr(
        config_mod.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    assert config_mod.autodetect_cli(None, ["claude.cmd", "claude"]) == "claude"
    assert config_mod.autodetect_cli("auto", ["claude.cmd", "claude"]) == "claude"


def test_autodetect_cli_none_found_falls_back_to_first(monkeypatch):
    # 전부 미발견이어도 raise하지 않는다(dry-run은 CLI 없이도 돌아야 함).
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    assert config_mod.autodetect_cli(None, ["codex.cmd", "codex"]) == "codex.cmd"


def test_load_config_workspace_none_without_sources(tmp_path, monkeypatch):
    # config 파일도 env도 없으면 workspace는 None(하드코딩 폴백 제거).
    monkeypatch.delenv("AUTOAGENT_WORKSPACE", raising=False)
    cfg = config_mod.load_config(tmp_path / "absent.json")
    assert cfg.workspace is None


def test_load_config_workspace_env_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAGENT_WORKSPACE", str(tmp_path))
    cfg = config_mod.load_config(tmp_path / "absent.json")
    assert cfg.workspace == tmp_path


def test_load_config_explicit_commands_preserved(tmp_path, monkeypatch):
    # config 명시값은 which 결과와 무관하게 그대로(소유자 머신 무회귀).
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    p = tmp_path / "c.json"
    p.write_text(
        '{"workspace": ".", "claude_command": "claude.cmd", "codex_command": "codex.cmd"}',
        encoding="utf-8",
    )
    cfg = config_mod.load_config(p)
    assert cfg.claude_command == "claude.cmd"
    assert cfg.codex_command == "codex.cmd"
```

- [ ] **Step 2: 실패 확인**

```bash
python -m pytest tests/test_portability.py -q
```

Expected: FAIL — `AttributeError: module 'autoagent.config' has no attribute 'shutil'` 류(autodetect_cli 미존재 포함).

- [ ] **Step 3: config.py 수정**

(a) import 블록에 `shutil` 추가(`import os` 옆):

```python
import json
import os
import shutil
```

(b) `_merge_tiers` 함수 뒤에 모듈 함수 추가:

```python
def autodetect_cli(explicit: str | None, candidates: list[str]) -> str:
    """CLI 명령명을 결정한다. 명시값(비어있지 않고 "auto" 아님)은 그대로 반환한다.

    미지정/"auto"면 candidates를 순서대로 shutil.which로 탐지해 처음 발견된
    '이름'을 반환한다(절대경로가 아니라 이름 — command json 아티팩트 바이트 호환).
    아무것도 없으면 candidates[0] 폴백 — dry-run은 CLI 미설치 머신에서도 돌아야
    하므로 여기서 죽지 않는다(실호출은 runner.require_command가 명확히 실패).
    """
    if explicit and explicit != "auto":
        return explicit
    for name in candidates:
        if shutil.which(name):
            return name
    return candidates[0]
```

(c) `Config` 데이터클래스의 `workspace: Path` → `workspace: Path | None`
(필드 주석 추가: `# None=미지정. cli가 --workspace/재개 복원으로 채우거나 require_workspace로 안내 종료.`)

(d) `load_config`의 workspace 블록(기존 117-121행)을 교체:

```python
    workspace_raw = raw.get("workspace") or os.environ.get("AUTOAGENT_WORKSPACE")
    # 하드코딩 개인 경로 폴백은 제거됐다(포터빌리티). 미지정이면 None —
    # cli.require_workspace가 안내 후 종료하거나 --workspace/재개 복원이 채운다.
    workspace = Path(workspace_raw) if workspace_raw else None
```

(e) `load_config` docstring의 workspace 설명 줄을 갱신:

```python
    workspace는 (프로젝트 config) > 전역 config > AUTOAGENT_WORKSPACE env 순으로
    결정하고, 셋 다 없으면 None(--workspace/재개 복원 또는 cli 가드의 몫).
```

(f) `Config(...)` 생성부의 명령 두 줄을 교체:

```python
        claude_command=autodetect_cli(raw.get("claude_command"), ["claude.cmd", "claude"]),
        codex_command=autodetect_cli(raw.get("codex_command"), ["codex.cmd", "codex"]),
```

- [ ] **Step 4: 테스트 통과 + 전체 회귀 확인**

```bash
python -m pytest tests/test_portability.py -q
python -m pytest tests/ -q
```

Expected: 신규 6 passed; 전체 스위트 전부 passed(기준선 212 + 6). 기존 테스트가 하드코딩 폴백에 의존해 깨지면 그 테스트의 **의미를 보존**하며 env/config 주입으로 갱신하고 보고에 명시.

- [ ] **Step 5: 커밋**

```bash
git add autoagent/config.py tests/test_portability.py
git commit -m "feat(portability): CLI 명령 자동 감지 + 기본 workspace 하드코딩 제거"
```

---

### Task 2: cli.py — 홈 포인터 자기등록 + require_workspace 가드

**Files:**
- Modify: `autoagent/cli.py`
- Test: `tests/test_portability.py` (추가)

**Interfaces:**
- Consumes: Task 1의 `Config.workspace: Path | None`.
- Produces: `write_home_pointer(pointer: Path | None = None) -> None`, `require_workspace(config) -> None` (cli 모듈 공개; Task 3의 커맨드 문서가 `~/.autoagent/home` 규약을 소비).

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_portability.py`에 추가:

```python
import pytest

from autoagent.artifacts import ROOT


def test_write_home_pointer_creates_and_is_idempotent(tmp_path):
    from autoagent.cli import write_home_pointer
    pointer = tmp_path / ".autoagent" / "home"
    write_home_pointer(pointer)
    assert pointer.read_text(encoding="utf-8").strip() == str(ROOT)
    before = pointer.stat().st_mtime_ns
    write_home_pointer(pointer)  # 같은 내용이면 재기록하지 않는다
    assert pointer.stat().st_mtime_ns == before


def test_write_home_pointer_failure_warns_and_survives(tmp_path, capsys):
    from autoagent.cli import write_home_pointer
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # 부모 자리에 파일을 놓아 mkdir 실패 유도
    write_home_pointer(blocker / "home")       # 예외가 밖으로 새면 안 된다
    assert "[home]" in capsys.readouterr().out


def test_require_workspace_raises_guidance():
    from types import SimpleNamespace
    from autoagent.cli import require_workspace
    with pytest.raises(SystemExit) as exc:
        require_workspace(SimpleNamespace(workspace=None))
    assert "AUTOAGENT_WORKSPACE" in str(exc.value)


def test_require_workspace_passes_when_set(tmp_path):
    from types import SimpleNamespace
    from autoagent.cli import require_workspace
    require_workspace(SimpleNamespace(workspace=tmp_path))  # raise 없음
```

- [ ] **Step 2: 실패 확인**

```bash
python -m pytest tests/test_portability.py -q
```

Expected: 신규 4개 FAIL — `ImportError: cannot import name 'write_home_pointer'` 류.

- [ ] **Step 3: cli.py 수정**

(a) `solo_banner` 함수 뒤에 헬퍼 2개 추가:

```python
def write_home_pointer(pointer: Path | None = None) -> None:
    """~/.autoagent/home에 하네스 ROOT 절대경로 한 줄을 기록한다(자기등록).

    /aa·/aar 커맨드가 클론 위치를 하드코딩 없이 찾는 근거 파일. 내용이 이미
    같으면 재기록하지 않고, 기록 실패(권한 등)는 경고만 남기고 런을 계속한다.
    """
    target = pointer if pointer is not None else Path.home() / ".autoagent" / "home"
    line = str(DEFAULT_CONFIG.parent)
    try:
        if target.exists() and target.read_text(encoding="utf-8").strip() == line:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(line + "\n", encoding="utf-8")
    except OSError as exc:
        # 자기등록은 편의 기능 - 실패해도 런 자체는 계속한다(ASCII+한글만 출력).
        print(f"[home] 홈 포인터 기록 실패(계속 진행): {exc}")


def require_workspace(config) -> None:
    """workspace 미지정(None)이면 해결 방법 3가지를 안내하고 종료한다."""
    if config.workspace is None:
        raise SystemExit(
            "Workspace not set. Fix one of: (1) copy autoagent.config.example.json to "
            'autoagent.config.json and set "workspace", (2) set AUTOAGENT_WORKSPACE, '
            "(3) pass --workspace <path>."
        )
```

(b) `main()` 첫 줄 직후(파싱 다음)에 자기등록 호출:

```python
def main() -> int:
    args = build_parser().parse_args()
    write_home_pointer()  # 자기등록: /aa·/aar가 이 클론을 찾을 수 있게 한다(dry-run 포함)
```

(c) `--resume` 분기에서 config workspace를 그대로 쓰는 두 재개 경로에 가드 삽입
(routed checkpoint 재개는 checkpoint에서 복원하므로 가드 없음 — 기존 그대로):

```python
        config.mcp_config_path = write_claude_mcp_config(config, run_dir, dry_run=args.dry_run)
        if (run_dir / "research_state.json").exists():
            # research 재개는 config workspace를 그대로 쓴다(상태파일에 복원 없음) - None 방어.
            require_workspace(config)
            return run_research_workflow(args, config, None, run_dir)
        mode = resume_mode(run_dir)
        if mode == "task_graph":
            # task_graph 재개도 config workspace를 직접 쓴다 - None 방어.
            require_workspace(config)
            return run_task_graph_execution(args, config, run_dir)
        return resume_routed_workflow(args, config)
```

(d) fresh-run 경로의 기존 존재 검사 앞에 가드 삽입:

```python
    require_workspace(config)
    if not config.workspace.exists():
        raise SystemExit(f"Workspace does not exist: {config.workspace}")
```

- [ ] **Step 4: 테스트 통과 + 전체 회귀 + dry-run 확인**

```bash
python -m pytest tests/test_portability.py -q
python -m pytest tests/ -q
python run.py --dry-run --workflow routed --task-type backend --workspace . --request "포터빌리티 스모크"
```

Expected: 신규 10개 전부 passed, 전체 스위트 passed, dry-run exit 0 + `RUN_DIR` 출력. dry-run 후 `~/.autoagent/home` 파일이 존재하고 내용이 하네스 ROOT 경로인지 확인(`cat` 또는 Read):

```bash
head -n1 ~/.autoagent/home
```

Expected: `C:\Users\...\AutoAgent` (실행한 클론의 절대경로).

- [ ] **Step 5: 커밋**

```bash
git add autoagent/cli.py tests/test_portability.py
git commit -m "feat(portability): 홈 포인터 자기등록 + require_workspace 가드"
```

---

### Task 3: 커맨드·example config·문서 반영 + 최종 검증

**Files:**
- Modify: `commands/aa.md`, `commands/aar.md`, `autoagent.config.example.json`, `README.md`, `CLAUDE.md`

주의: `~/.claude/commands/aa.md`는 레포 파일의 심링크 — **레포 파일만** 편집.

**Interfaces:**
- Consumes: Task 2의 `~/.autoagent/home` 규약(한 줄, ROOT 절대경로), `AUTOAGENT_HOME` env 우선.

- [ ] **Step 1: commands/aa.md 해석 절차로 교체**

(a) 도입부 문장(8행 부근) 교체 — old:

```
session is in). The harness lives at `C:\Users\systran\Desktop\AutoAgent`.
```

new:

```
session is in). The harness location (HARNESS) is resolved in section 1 —
never hardcode it.
```

(b) `## 1. Parse arguments` 섹션 끝에 불릿 추가:

```markdown
- HARNESS = 환경변수 `AUTOAGENT_HOME`이 있으면 그 값, 없으면 `~/.autoagent/home`
  파일의 첫 줄(공백·CR 트림). Git Bash 예:
  `HARNESS="${AUTOAGENT_HOME:-$(head -n1 ~/.autoagent/home 2>/dev/null | tr -d '\r')}"`
  둘 다 비어 있으면 정지하고 사용자에게 안내한다: "하네스 클론 디렉터리에서
  `python run.py --dry-run --workflow routed --task-type docs --workspace . --request "ping"`
  을 한 번 실행하면 자동 등록됩니다."
```

(c) 섹션 2의 실행 명령에서 하드코딩 경로 교체 — old:

```
python "C:\Users\systran\Desktop\AutoAgent\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --project "PROJECT" --workspace . --request "REQUEST"
```

new:

```
python "<HARNESS>\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --project "PROJECT" --workspace . --request "REQUEST"
```

(`<HARNESS>`를 위에서 해석한 값으로 치환하라는 문장이 주변에 없으면 명령 아래
"Substitute ..." 줄에 HARNESS를 추가.)

- [ ] **Step 2: commands/aar.md 동일 교체**

aa.md와 같은 3곳: 도입부 문장(8행 부근), 섹션 1 불릿(위 블록 그대로), 실행
명령(32행 부근)의 `C:\Users\systran\Desktop\AutoAgent` → `<HARNESS>`.

- [ ] **Step 3: example config를 auto로**

`autoagent.config.example.json` — old:

```json
  "claude_command": "claude.cmd",
  "codex_command": "codex.cmd",
```

new:

```json
  "claude_command": "auto",
  "codex_command": "auto",
```

- [ ] **Step 4: README·CLAUDE.md 갱신**

(a) `README.md`의 "## 요구사항" 아래 기본 작업공간 블록 — old:

```
기본 작업공간:

```text
C:\Users\systran\Desktop\LanguageDetection
```
```

new:

```
작업공간(workspace)은 기본값이 없으며 반드시 지정해야 합니다:

1. `autoagent.config.example.json`을 `autoagent.config.json`으로 복사해 `workspace` 지정(권장), 또는
2. 환경변수 `AUTOAGENT_WORKSPACE`, 또는
3. 실행 시 `--workspace <경로>`.

CLI 명령명은 미지정/`"auto"`면 자동 감지합니다(`claude.cmd`→`claude`,
`codex.cmd`→`codex` 순). 하네스는 매 실행 `~/.autoagent/home`에 자기 경로를
기록(자기등록)하며, `/aa`·`/aar` 커맨드가 이 파일(또는 `AUTOAGENT_HOME` env)로
클론 위치를 찾습니다.
```

(b) `CLAUDE.md`의 Environment/gotchas 절 해당 줄 — old:

```
- `autoagent.config.json` is **gitignored**; precedence: config file >
  `AUTOAGENT_WORKSPACE` env > hardcoded default.
```

new:

```
- `autoagent.config.json` is **gitignored**; workspace precedence: config file >
  `AUTOAGENT_WORKSPACE` env > **없으면 명시 요구**(하드코딩 기본값 제거 —
  `cli.require_workspace`가 안내 종료). CLI 명령명은 미지정/`auto`면 자동 감지
  (`claude.cmd`→`claude` 순, `config.autodetect_cli`). `cli.main()`은 매 실행
  `~/.autoagent/home`에 자기 경로를 자기등록하고 /aa·/aar가 이를 읽는다
  (`AUTOAGENT_HOME` env 우선).
```

- [ ] **Step 5: 최종 검증**

```bash
python -m pytest tests/ -q
python run.py --dry-run --workflow routed --task-type backend --workspace . --request "포터빌리티 최종 스모크"
python run.py --dry-run --workflow research --workspace . --request "리서치 스모크"
```

Expected: 전부 passed; dry-run 2종 exit 0(기존 산출물 구조 무회귀). 추가로
config·env 없는 상황 시뮬레이션(빈 config 경로 + `--workspace` 생략)으로 가드
메시지 확인:

```bash
python run.py --config ./no-such-config.json --workflow routed --task-type docs --request "ping" --dry-run
```

Expected: exit != 0, stderr에 `Workspace not set. Fix one of: ...` 안내.

- [ ] **Step 6: 커밋**

```bash
git add commands/aa.md commands/aar.md autoagent.config.example.json README.md CLAUDE.md
git commit -m "feat(portability): /aa·/aar 하네스 경로 해석 절차 + example auto + 문서 갱신"
```

- [ ] **Step 7: 라이브 미실증 표기**

완료 보고에 반드시 포함: `/aa`·`/aar`의 HARNESS 해석은 Claude Code 세션이
수행하는 마크다운 지시라 **하네스 테스트로 검증 불가 — 라이브 미실증(사용자
인계)**. 홈 포인터 기록·가드·자동 감지는 pytest+dry-run으로 검증됨.

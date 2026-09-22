# 포터빌리티: 하드코딩 제거 + 자동 감지 설계

날짜: 2026-09-22
상태: 설계 확정(구현 전)
브랜치: feature/portability-autodetect

## 목적

클론 받은 사람이 **파일 수정 없이** 하네스를 쓸 수 있게, 머신 종속 하드코딩
3곳을 자동 감지로 대체한다:

1. `commands/aa.md`·`aar.md`의 하네스 절대경로(`C:\Users\systran\Desktop\AutoAgent`)
2. CLI 명령명 `.cmd` 고정(`claude.cmd`/`codex.cmd` — Windows 전용)
3. `config.py`의 기본 workspace 개인 경로(`C:\Users\systran\Desktop\LanguageDetection`)

## 확정된 결정 사항

| 결정 | 선택 |
| --- | --- |
| 하네스 위치 | **자기등록 포인터**: `cli.main()`이 실행될 때마다 `~/.autoagent/home`에 자기 ROOT 절대경로를 기록(같으면 무기록, 실패는 경고만). 커맨드는 env → 포인터 순으로 해석 |
| CLI 명령명 | config 명시값이 있으면 **그대로**(무회귀). 없거나 `"auto"`면 후보 순회 자동 감지(`claude.cmd`→`claude`, `codex.cmd`→`codex`). 반환은 후보 **이름**(절대경로 아님 — 기존 command json과 바이트 호환) |
| 기본 workspace | 하드코딩 폴백 **삭제**. config·env·`--workspace` 셋 다 없으면 안내 메시지로 종료. **자동 추측은 하지 않음**(cwd 추측은 하네스 레포 자신을 작업장으로 잡는 사고 위험) |
| dry-run | CLI 미설치 머신에서도 dry-run은 계속 동작해야 함 — 자동 감지 실패 시 예외 대신 기본 이름 폴백(실호출 시 `require_command`가 명확히 실패) |

## 구성 요소

### C1. 하네스 홈 포인터 (`autoagent/cli.py` + `commands/*.md`)

**기록(자기등록)**: `cli.py`에 헬퍼 추가, `main()` 시작부(파싱 직후)에서 호출.

```python
HOME_POINTER = Path.home() / ".autoagent" / "home"

def write_home_pointer() -> None:
    """~/.autoagent/home에 하네스 ROOT 절대경로 한 줄을 기록한다(자기등록).

    /aa·/aar 커맨드가 클론 위치를 하드코딩 없이 찾는 근거 파일. 내용이 이미
    같으면 쓰지 않고, 기록 실패(권한 등)는 경고만 하고 런을 계속한다.
    """
```

- 내용: `str(DEFAULT_CONFIG.parent)` 한 줄 + 개행, UTF-8.
- 기록 시점: `--resume` 포함 모든 `main()` 진입(dry-run 포함 — dry-run 스모크가
  곧 등록 행위가 되는 온보딩 시나리오).
- 실패 시 `print("[home] ...")` 경고(ASCII+한글만) 후 계속.

**해석(커맨드 측)**: `commands/aa.md`·`aar.md`의 하드코딩 경로 2곳(설명문 + run
명령)을 해석 절차로 교체. 섹션 1(Parse arguments)에 추가:

- `HARNESS` = 환경변수 `AUTOAGENT_HOME`(있으면) → 없으면 `~/.autoagent/home`
  파일 내용(한 줄, 공백 트림).
- 둘 다 없으면 정지하고 안내: "하네스 클론에서 `python run.py --dry-run
  --workflow routed --task-type docs --workspace . --request "ping"`을 한 번
  실행하면 자동 등록됩니다."
- 이후 모든 `run.py` 참조는 `<HARNESS>\run.py`.

### C2. CLI 명령 자동 감지 (`autoagent/config.py`)

```python
def autodetect_cli(explicit: str | None, candidates: list[str]) -> str:
    """CLI 명령명을 결정한다. 명시값(비어있지 않고 "auto"가 아님)은 그대로 반환.

    아니면 candidates를 순서대로 shutil.which로 탐지해 처음 발견된 '이름'을
    반환한다(절대경로가 아니라 이름 — 기존 command json 아티팩트와 바이트 호환).
    아무것도 없으면 candidates[0]을 반환한다(dry-run은 계속 동작해야 하므로
    여기서 죽지 않는다 — 실호출은 require_command가 명확한 메시지로 실패).
    """
```

- `load_config`에서: `claude_command=autodetect_cli(raw.get("claude_command"), ["claude.cmd", "claude"])`,
  `codex_command=autodetect_cli(raw.get("codex_command"), ["codex.cmd", "codex"])`.
- `"auto"`는 미지정과 동일 취급(example config에 의도를 드러내는 값).
- 소유자 Windows 머신: `which("claude.cmd")` 성공 → `"claude.cmd"` 반환으로
  기존과 바이트 동일.
- `autoagent.config.example.json`: 두 필드 값을 `"auto"`로 변경.

### C3. 기본 workspace 하드코딩 제거 (`autoagent/config.py` + `autoagent/cli.py`)

- `load_config`의 workspace 해석에서 하드코딩 폴백 삭제:

```python
workspace_raw = raw.get("workspace") or os.environ.get("AUTOAGENT_WORKSPACE")
workspace = Path(workspace_raw) if workspace_raw else None
```

- `Config.workspace` 타입을 `Path | None`으로 변경.
- **가드 위치는 cli.py** (load_config가 아님): `--workspace`가 config 없이도
  쓰이는 경로(/aa가 `--workspace .`로 호출)가 있으므로, fresh-run 경로의 기존
  `if not config.workspace.exists():` 검사 **직전**에 None 가드를 둔다:

```python
if config.workspace is None:
    raise SystemExit(
        "Workspace not set. Fix one of: (1) copy autoagent.config.example.json to "
        "autoagent.config.json and set \"workspace\", (2) set AUTOAGENT_WORKSPACE, "
        "(3) pass --workspace <path>."
    )
```

- 재개 경로는 각자 checkpoint/refine_status/research_state에서 workspace를
  복원하므로 원칙상 영향 없음 — 단, 각 재개 분기가 복원 **후** workspace를
  쓰는지 구현 시 확인하고, 복원 실패 시 None이 흘러가지 않게 같은 안내
  메시지의 가드를 재개 공통 지점에도 둔다(방어).

## 무회귀 계약

- config에 명시값이 있는 머신(소유자 포함): C2·C3 모두 동작 바이트 동일.
- dry-run: CLI 미설치·config 없음 + `--workspace .` 조합에서도 기존처럼 동작
  (자동 감지 폴백 + cli 가드는 workspace가 주어졌으므로 미발동).
- `commands/*.md`: `AUTOAGENT_HOME`을 설정한 사용자는 포인터 파일 없이도 동작.

## 검증 계획

- pytest 신규 `tests/test_portability.py`:
  - `autodetect_cli`: 명시값 통과 / "auto"·None 자동 감지(monkeypatch로 which
    제어) / 전부 미발견 시 첫 후보 폴백.
  - `write_home_pointer`: tmp HOME(monkeypatch)에 기록·동일 내용 무갱신·실패
    경고 후 생존.
  - workspace: config·env 없음 → `load_config().workspace is None`; cli 가드
    메시지(SystemExit) 확인.
- dry-run 3종(routed/research/decompose) 기존 산출물 무회귀 + `~/.autoagent/home`
  생성 확인.
- `/aa`·`/aar` 라이브 동작은 미실증으로 사용자 인계(커맨드 마크다운의 해석
  절차는 Claude Code 세션이 수행하므로 하네스 테스트로 검증 불가).

## 비목표

- workspace 자동 추측(cwd 등) — 명시 강제 유지.
- 데스크탑 앱화 — 별도 논의로 보류.
- 키워드 오탐 수정 — 별도 설계(승인 대기) 유지.

# `aa` per-project 자동 전달 + `--project` 자기부트스트랩 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aa` 스킬이 현재 프로젝트명(cwd basename)으로 `--project`를 자동 전달해 런을 `projects/<name>/`로 격리하고, 하네스의 `--project`가 config를 자기부트스트랩(없으면 생성)하게 한다.

**Architecture:** 하네스 `cli.main`이 `load_config` 직전에 `ensure_project_config`로 프로젝트 config를 보장(SystemExit 폐지). `aa.md` 스킬은 basename을 `--project`로 넘긴다. `--project` 미지정 경로는 불변.

**Tech Stack:** Python 3.13, stdlib json/pathlib. slash-command 스킬(`commands/aa.md`). pytest 스위트 없음.

## Global Constraints

- 기준선(base)은 main(PR #14·#15 병합본). 브랜치 `feature/aa-per-project`.
- 테스트 스위트 없음 — 검증은 인라인 python + dry-run. dry-run은 `--max-agent-calls` 미포함.
- 한국어 docstring·주석(식별자만 영문). `from __future__ import annotations`; PEP 604.
- `--project` 미지정 경로는 **동작 보존**(top-level `runs/`).
- **자기수정 위험**: 이 변경은 `cli.py`/`artifacts.py`를 건드리므로 하네스로 도그푸딩하면 크래시 가능.
  인라인 또는 fresh 서브에이전트로 구현하고 **새 프로세스로** 검증한다.
- main push 금지 — feature 브랜치 + PR.

---

## Task 1: 하네스 `--project` 자기부트스트랩

**Files:**
- Modify: `autoagent/artifacts.py` (헬퍼 추가)
- Modify: `autoagent/cli.py` (`main`에서 호출)

**Interfaces:**
- Produces: `ensure_project_config(config_dir: Path, project: str, workspace: Path) -> Path` — 없으면
  `projects/<project>/config.json`을 `{"workspace": <abs>}`로 생성(있으면 무동작), 경로 반환.

- [ ] **Step 1: `ensure_project_config` 헬퍼 추가**

`autoagent/artifacts.py`의 `make_run_dir` 위(또는 `validate_project_name` 아래)에 추가. 파일 상단에
`import json`이 없으면 추가한다(대개 이미 있음):

```python
def ensure_project_config(config_dir: Path, project: str, workspace: Path) -> Path:
    """projects/<project>/config.json이 없으면 workspace만 채워 생성한다(있으면 무동작).

    반환은 config 경로. 이름은 validate_project_name으로 검증한다(경로 이탈 방지).
    생성 시 안내를 출력한다. projects/*/config.json은 gitignored라 커밋되지 않는다.
    """
    validate_project_name(project)
    cfg = config_dir / "projects" / project / "config.json"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            json.dumps({"workspace": str(workspace)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[project] 새 프로젝트 config 생성: {cfg} (workspace={workspace})")
    return cfg
```

- [ ] **Step 2: 단위 검증**

```bash
cd /c/Users/systran/Desktop/AutoAgent
python - <<'PY'
import json, tempfile
from pathlib import Path
from autoagent.artifacts import ensure_project_config

root = Path(tempfile.mkdtemp())
# (1) 없으면 생성
p = ensure_project_config(root, "demo", Path("C:/ws/demo"))
assert p.exists() and json.loads(p.read_text(encoding="utf-8"))["workspace"] == "C:/ws/demo"
# (2) 있으면 무동작(내용 보존)
p.write_text('{"workspace":"C:/ws/keep","extra":1}', encoding="utf-8")
ensure_project_config(root, "demo", Path("C:/ws/other"))
assert json.loads(p.read_text(encoding="utf-8"))["workspace"] == "C:/ws/keep"
# (3) 나쁜 이름 거부
for bad in ("..", "a/b", "a\\b", ""):
    try:
        ensure_project_config(root, bad, Path("C:/ws")); raise AssertionError(f"{bad!r} 통과하면 안됨")
    except SystemExit:
        pass
print("Task1 단위 OK")
PY
```
Expected: `Task1 단위 OK`.

- [ ] **Step 3: `cli.py`에서 `load_config` 직전 호출**

`autoagent/cli.py`의 import에 `ensure_project_config` 추가(기존 artifacts import 라인 확장):

```python
from autoagent.artifacts import DEFAULT_CONFIG, ensure_project_config, make_run_dir, read_text, write_metadata, write_text
```

그리고 `main`의 `config = load_config(...)` 앞에 삽입:

수정 전:
```python
    args = build_parser().parse_args()
    config = load_config(Path(args.config), project=args.project)
```
수정 후:
```python
    args = build_parser().parse_args()
    if args.project:
        # --project가 요구하는 config를 미리 보장한다. workspace는 --workspace(abs) 우선, 없으면 cwd.
        ws = Path(args.workspace).resolve() if args.workspace else Path.cwd()
        ensure_project_config(Path(args.config).parent, args.project, ws)
    config = load_config(Path(args.config), project=args.project)
```

- [ ] **Step 4: 통합(dry-run) + 회귀 + compile**

```bash
cd /c/Users/systran/Desktop/AutoAgent
rm -rf projects/testproj
python ./run.py --dry-run --project testproj --workspace . --workflow routed --task-type docs --read-only --request "구조 리뷰" >/dev/null 2>&1
echo "config 생성?"; test -f projects/testproj/config.json && echo YES
echo "run이 프로젝트 밑?"; ls -d projects/testproj/runs/*/ 2>/dev/null | head -1
rm -rf projects/testproj
# 회귀: --project 없으면 여전히 top-level runs/
before=$(ls -d runs/*/ 2>/dev/null | wc -l)
python ./run.py --dry-run --workflow routed --task-type docs --read-only --request "x" >/dev/null 2>&1
after=$(ls -d runs/*/ 2>/dev/null | wc -l)
echo "top-level runs 증가(회귀 유지)?"; test "$after" -gt "$before" && echo YES
python -m compileall -q autoagent/ && echo "compile OK"
```
Expected: `YES`(config 생성), `projects/testproj/runs/...` 경로 출력, `YES`(회귀), `compile OK`.

- [ ] **Step 5: 커밋**

```bash
cd /c/Users/systran/Desktop/AutoAgent
git add autoagent/artifacts.py autoagent/cli.py
git commit -m "feat: --project 자기부트스트랩(config 없으면 workspace로 생성)

cli.main이 load_config 직전 ensure_project_config로 projects/<name>/config.json을
보장(없으면 --workspace/cwd로 생성, SystemExit 폐지). --project 미지정 경로는 불변.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: 커밋 생성.

---

## Task 2: `aa` 스킬이 basename으로 `--project` 전달

**Files:**
- Modify: `commands/aa.md`

- [ ] **Step 1: 프로젝트명 도출 단계 추가**

`commands/aa.md`의 "## 1. Parse arguments" 마지막 항목 아래에 추가:

```markdown
- PROJECT = 현재 작업 디렉터리의 basename(예: `.../LanguageDetection` → `LanguageDetection`).
  이 이름으로 런을 `projects/<PROJECT>/`에 격리한다. config가 없으면 하네스가 현재 workspace로
  자동 생성한다.
```

- [ ] **Step 2: 실행 명령에 `--project` 추가**

"## 2"의 코드블록을 교체:

수정 전:
```
python "C:\Users\systran\Desktop\AutoAgent\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --workspace . --request "REQUEST"
```
수정 후:
```
python "C:\Users\systran\Desktop\AutoAgent\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --project "PROJECT" --workspace . --request "REQUEST"
```

그리고 그 아래 설명에 한 줄 추가:

```markdown
`--project "PROJECT"`로 런이 `projects/PROJECT/runs/<stamp>`에 격리된다. `RUN_DIR:`와
`resume_command`는 하네스가 절대경로로 출력·임베드하므로 아래 섹션 로직은 그대로 동작한다.
```

- [ ] **Step 3: 커밋**

```bash
cd /c/Users/systran/Desktop/AutoAgent
git add commands/aa.md
git commit -m "feat: aa 스킬이 cwd basename으로 --project 전달(프로젝트별 런 격리)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: 커밋 생성.

---

## Task 3: 문서 커밋 + PR

- [ ] **Step 1: 설계·계획 문서 커밋**

```bash
cd /c/Users/systran/Desktop/AutoAgent
git add docs/superpowers/
git commit -m "docs: aa per-project 자동전달 설계·계획 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: 푸시 + PR**

```bash
cd /c/Users/systran/Desktop/AutoAgent
git push -u origin feature/aa-per-project
gh pr create --base main --title "feat: aa 스킬 per-project 자동전달 + --project 자기부트스트랩" --body "설계: docs/superpowers/specs/2026-07-16-aa-per-project-autopass-design.md
계획: docs/superpowers/plans/2026-07-16-aa-per-project-autopass.md

aa 스킬이 cwd basename으로 --project를 전달해 런을 projects/<name>/로 격리.
하네스의 --project는 config가 없으면 현재 workspace로 자기부트스트랩(SystemExit 폐지).
--project 미지정 경로는 동작 보존. 검증: 단위 + dry-run 통합/회귀 + compileall.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
Expected: PR URL 출력.

---

## 검증 요약

- 단위: `ensure_project_config` 생성/무동작/이름검증.
- 통합: `--project testproj` dry-run → config 생성 + `projects/testproj/runs/`.
- 회귀: `--project` 없으면 top-level `runs/` 유지.
- compileall.
- 수동: `/aa`가 `projects/<basename>/runs/`로 격리되는지 실행 확인.

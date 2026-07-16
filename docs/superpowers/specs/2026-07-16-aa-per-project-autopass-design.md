# `aa` 스킬 per-project 자동 전달 + `--project` 자기부트스트랩 (설계)

- 날짜: 2026-07-16
- 상태: 설계 승인됨(구현 계획 대기)
- 범위: `aa` 스킬이 런을 프로젝트별로 격리하도록 `--project`를 자동 전달. 이를 가능케 하는
  하네스의 `--project` 자기부트스트랩(config 없으면 생성)을 함께 도입.

## 배경 / 문제

per-project 워크스페이스 레지스트리는 이미 구현돼 있다(`--project <name>` → `projects/<name>/config.json`
로드 + `projects/<name>/runs/`에 산출물). 그러나 **opt-in**이라 `--project`를 줘야만 격리된다.
`aa` 스킬은 `--workspace .`만 넘기고 `--project`를 안 붙여서, 모든 런이 하네스 저장소의
top-level `runs/`에 쌓인다 — 여러 타깃을 다뤄도 섞인다.

크로스모델 협업을 여러 타깃에 일관 적용하는 게 하네스의 목적이므로, **스킬이 프로젝트별로
자동 격리**하는 게 결에 맞다.

**제약**: 지금 `--project X`를 주면 `load_config`가 `projects/X/config.json`을 요구하고 없으면
`SystemExit`한다. 따라서 스킬이 그냥 `--project`를 붙이면 미등록 프로젝트에서 즉사한다.
→ "이름 자동 결정 + config 부트스트랩"이 반드시 동반돼야 한다.

## 목표 / 비목표

**목표**
- `aa` 스킬이 현재 프로젝트명을 자동 도출해 `--project`를 전달, 런을 `projects/<name>/`로 격리.
- `--project`가 미등록이면 하네스가 **현재 workspace로 config를 자동 생성**(SystemExit 폐지).
  이 부트스트랩은 스킬뿐 아니라 **모든 CLI 진입점**에 균일 적용(per-command 하지 않고 하네스 일반해법).

**비목표**
- `--project` 없이 직접 CLI를 쓰는 기존 경로의 동작 변경(그대로 top-level `runs/`).
- 프로젝트 config의 풍부한 스키마/편집 UI. 부트스트랩은 최소(`{"workspace": <abs>}`)만 만든다.
- 프로젝트명 정규화(소문자화 등). basename을 그대로 쓴다(validate 통과 범위).

## 설계

### 1. 하네스: `--project` 자기부트스트랩

`autoagent/artifacts.py`에 헬퍼 추가:

```python
def ensure_project_config(config_dir: Path, project: str, workspace: Path) -> Path:
    """projects/<project>/config.json이 없으면 workspace만 채워 생성한다(있으면 무동작).

    반환값은 config 경로. 이름은 validate_project_name으로 검증(경로 이탈 방지).
    생성 시 안내를 출력한다. projects/*/config.json은 gitignored라 커밋되지 않는다.
    """
    validate_project_name(project)
    cfg = config_dir / "projects" / project / "config.json"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"workspace": str(workspace)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[project] 새 프로젝트 config 생성: {cfg} (workspace={workspace})")
    return cfg
```

`autoagent/cli.py`의 `main`에서 `load_config` **직전**에 호출(이 시점에 `args.workspace`를 안다):

```python
    args = build_parser().parse_args()
    if args.project:
        # --project가 요구하는 config를 미리 보장한다. workspace는 --workspace(abs) 우선, 없으면 cwd.
        ws = Path(args.workspace).resolve() if args.workspace else Path.cwd()
        ensure_project_config(Path(args.config).parent, args.project, ws)
    config = load_config(Path(args.config), project=args.project)
```

`Path(args.config).parent`는 `load_config`가 `project_config_path`를 계산할 때 쓰는 `path.parent`와
동일하므로 생성 위치와 조회 위치가 일치한다.

### 2. 스킬: `aa.md`가 basename으로 `--project` 전달

`commands/aa.md`에:
- "## 1. Parse arguments" 뒤에 프로젝트명 도출 단계 추가:
  "PROJECT = 현재 작업 디렉터리의 basename." (예: `.../LanguageDetection` → `LanguageDetection`)
- "## 2"의 실행 명령에 `--project "PROJECT"` 추가:
  ```
  python "...\run.py" --workflow routed --task-type TYPE --max-review-rounds 1 --max-agent-calls N --project "PROJECT" --workspace . --request "REQUEST"
  ```
- 런 산출물이 `projects/PROJECT/runs/<stamp>`에 생김을 명시. `RUN_DIR:`/resume_command는 하네스가
  절대경로로 출력·임베드하므로 섹션 3·4 로직은 변경 없이 동작.

## 동작 보존

- `--project` 미지정(직접 CLI 기존 사용) → `ensure_project_config` 미호출, `make_run_dir(None)` →
  top-level `runs/`. **완전 동일.**
- `--project X`인데 config가 이미 있으면 → `ensure_project_config`는 무동작, 기존과 동일.

## 검증 계획

1. **단위** — 임시 디렉터리에서 `ensure_project_config`:
   - config 없으면 생성 + 내용 `{"workspace": ...}` 확인.
   - 이미 있으면 무동작(기존 내용 보존).
   - `..`/`/` 이름은 `SystemExit`.
2. **통합(dry-run)** — `python run.py --dry-run --project testproj --workspace . --workflow routed
   --task-type docs --read-only --request "x"` → `projects/testproj/config.json` 생성되고 run이
   `projects/testproj/runs/<stamp>`에 생기는지 확인. 확인 후 `projects/testproj/` 정리.
3. **회귀** — `--project` 없이 dry-run 1회 → 여전히 top-level `runs/`.
4. `compileall` 통과.
5. **스킬** — `/aa`를 실제로 한 번 돌려 런이 `projects/<basename>/runs/`로 가는지 육안 확인
   (구현 후 수동).

## 리스크 / 완화

- **직접 CLI에서 프로젝트명 오타** → 미등록으로 취급돼 새 gitignored 디렉터리 생성. "새 프로젝트
  config 생성" 안내를 출력해 눈에 띄게 한다(허용된 트레이드오프). 스킬 경로는 이름을 자동 도출해 오타 없음.
- **cwd basename에 공백/특수문자** → validate_project_name은 `/ \ . ..`만 막으므로 대부분 통과.
  희귀 케이스라 별도 정규화는 비목표.
- **자기수정 위험(도그푸딩)** → 이 변경은 `cli.py`/`artifacts.py`를 건드린다. 하네스로 하네스를
  구현하면 실행 중 모듈 skew로 크래시할 수 있다([[harness-self-modification-crash]]). 구현은 인라인
  또는 fresh 서브에이전트로 하고 **새 프로세스로** 검증한다.
```

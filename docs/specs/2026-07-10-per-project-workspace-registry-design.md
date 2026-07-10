# 프로젝트별 워크스페이스 레지스트리 설계

> 작성일: 2026-07-10 · 상태: 설계(승인 대기)

## 목표

AutoAgent를 여러 타깃 프로젝트에 재사용할 수 있도록, **프로젝트 단위로 config와
runs를 분리**한다. 지금은 전역 config 하나·전역 `runs/` 하나뿐이라 여러 타깃을
돌리면 실행 이력이 섞이고 프로젝트별 정책을 표현할 수 없다.

## 배경 — 현재 구조

세 가지가 전부 전역 1개다.

| 대상 | 현재 위치 | 근거 |
|---|---|---|
| config | `autoagent.config.json` 하나 | `config.py:36 load_config` (`config > env > default`) |
| runs | `ROOT/runs/<stamp>` (프로젝트 구분 없음) | `artifacts.py:77 make_run_dir` |
| roles override | `roles.json` 하나 | `ROOT` (이번 범위에서 **손대지 않음**) |

`--workspace`는 로드 후 `config.workspace`만 갈아끼우는 일회성 오버라이드이며
(`cli.py:89`) runs 경로에는 반영되지 않는다.

## 핵심 결정 (승인됨)

- **상태 위치 = A**: 프로젝트 상태를 AutoAgent 레포 안(`projects/<name>/`)에 둔다.
  타깃 레포는 건드리지 않는다(기존 "하네스 불가침" 스코프 원칙 유지).
- **config 레이어링 = (i) 레이어드**: 전역 config는 공용 기본값 층, 프로젝트
  config는 override 층. 반복되는 모델/예산을 프로젝트마다 다시 쓰지 않는다.

## 아키텍처

### 디렉터리 레이아웃

```
AutoAgent/
├─ autoagent.config.json           # 전역 = 하네스 공용 기본값(gitignore, 로컬)
├─ roles.default.json / roles.json # 역할 레지스트리(전역 유지 — 범위 밖)
└─ projects/
   ├─ .gitkeep
   ├─ _example/
   │  └─ config.json               # 스키마 예시(커밋됨, 플레이스홀더 경로)
   └─ LanguageDetection/
      ├─ config.json               # workspace(필수) + 선택 override(gitignore)
      └─ runs/<stamp>/             # 이 프로젝트 실행 이력만
```

### 설정 병합 규칙

기존 precedence를 **한 겹만** 확장한다.

```
per-project config  >  global config  >  AUTOAGENT_WORKSPACE(env)  >  하드코딩 default
```

- 병합은 **얕은 병합(shallow merge)**: `merged = {**global_raw, **project_raw}`.
  프로젝트 config에 있는 키만 전역 값을 덮는다. 없는 키는 전역에서 상속.
- `workspace`도 병합 결과에서 뽑되 기존 순서를 그대로 탄다:
  `merged.get("workspace") or env or default`. 프로젝트 config는 사실상 여기에
  자신의 workspace를 적어 넣는 것이 존재 이유다.

### 층별 역할

- **전역 `autoagent.config.json`** — 프로젝트 무관 공용값:
  `claude_command`/`codex_command`, 모델·effort 티어, `codex_sandbox`,
  `codex_approval`, `timeout_seconds`, `default_max_agent_calls_*`,
  `claude_impl_permission` 등. 한 곳에서 관리(DRY).
- **`projects/<name>/config.json`** — 그 프로젝트만의 값:
  - `workspace`(권장 필수 — 프로젝트 config의 존재 이유)
  - 선택 override: 위 전역 키 중 프로젝트마다 다를 수 있는 것(예: `default_workflow`,
    모델 티어, `codex_sandbox`). 최소 스펙에서는 `workspace` 하나만 필수로 본다.

`_example/config.json` 예시:

```json
{
  "workspace": "C:\\path\\to\\your\\target\\project"
}
```

## 컴포넌트 변경

### 1) `autoagent/config.py — load_config`

시그니처에 프로젝트 인자를 추가하고 두 층을 병합한다.

- `load_config(path: Path, project: str | None = None) -> Config`
- `project`가 없으면 **오늘과 완전히 동일** — 전역 `path` 하나만 읽는다.
- `project`가 있으면:
  1. 전역 raw = `path`가 있으면 읽기(없으면 `{}`).
  2. 프로젝트 config 경로 = `path.parent / "projects" / project / "config.json"`.
     `path`는 기본값이 `ROOT/autoagent.config.json`이라 `path.parent`가 곧 ROOT이며,
     이렇게 하면 config.py가 artifacts를 새로 import하지 않아도 된다.
     없으면 `SystemExit(f"Project config not found: {project_config_path}")`.
  3. `raw = {**global_raw, **project_raw}` 로 병합한 뒤 기존 로직대로 Config 조립.

### 2) `autoagent/artifacts.py — make_run_dir`

runs 루트를 프로젝트별로 분기한다.

- `make_run_dir(project: str | None = None) -> Path`
- 베이스 = `project`이면 `ROOT/projects/<project>/runs`, 아니면 기존 `ROOT/runs`.
- 나머지(타임스탬프·충돌 시 `_NN` 접미사) 로직은 그대로.

### 3) `autoagent/cli.py — main / build_parser`

- 새 인자: `--project <name>` (프로젝트 레지스트리 이름). 기본 `None`.
- `config = load_config(Path(args.config), project=args.project)`.
- `run_dir = make_run_dir(project=args.project)`.
- `--workspace`는 기존대로 병합 후 최상위 오버라이드로 유지(`cli.py:89`).
- 메타데이터(`write_metadata`)에 `"project": args.project` 추가.

### 4) `--resume` (변경 없음)

`resume_routed_workflow`는 `args.resume`의 run_dir(절대경로)만 보고
`checkpoint.json`에서 workspace를 복원하며 `make_run_dir`를 호출하지 않는다
(`routed.py:77-92`). 따라서 프로젝트별 runs와 무관하게 그대로 동작한다.
**플랜에서 dry-run으로 재확인만 한다.**

### 5) `.gitignore`

프로젝트 상태(로컬 절대경로 포함)는 커밋하지 않는다. 예시만 커밋한다.

추가:
```
projects/*/runs/
projects/*/config.json
!projects/_example/config.json
!projects/.gitkeep
```

## 데이터 흐름

```
run.py --project LanguageDetection --workflow routed --request "..."
  └─ load_config(autoagent.config.json, project="LanguageDetection")
        = {전역 기본값} ⊕ {프로젝트 override}  → Config(workspace=타깃)
  └─ make_run_dir("LanguageDetection")
        = projects/LanguageDetection/runs/<stamp>/
  └─ run_routed_workflow(...)  # 이하 기존과 동일
```

레지스트리에 없는 임시 타깃:
```
run.py --workspace C:\...\OtherProject --request "..."
  └─ --project 없음 → 전역 config + ROOT/runs (오늘과 동일)
  └─ --workspace가 병합 후 workspace를 최종 오버라이드
```

## 하위호환

- `--project` 미지정 시 동작·산출물 경로가 **오늘과 100% 동일**.
- 기존 `ROOT/runs/`는 그대로 두고 건드리지 않는다(legacy). 마이그레이션 없음.
- 기존 dry-run 산출물(`*_command.json`)은 프로젝트 경로와 무관하므로 **바이트 동일**해야 한다.

## 에러 처리

- `--project` 지정했는데 `projects/<name>/config.json` 없음 → 명확한 `SystemExit`.
- 병합 후 `workspace`가 존재하지 않는 경로 → 기존 검사(`cli.py:99`) 그대로 걸림.
- 프로젝트 config JSON 파싱 실패 → `json.loads` 예외 그대로 표면화(기존 전역 config와 동일 취급).

## 범위 밖 (YAGNI — 이번엔 하지 않음)

- 프로젝트별 roles override (`projects/<name>/roles.json`) — 전역 유지.
- `commands/`, `notes/`, `task_graphs/`, `approvals/`, `policy.md` 하위 디렉터리.
- 프로젝트 목록/생성 CLI(`--list-projects`, `init-project`) — 수동으로 폴더+config 생성.
- 리스크 정책의 구조화된 override 스키마 — 필요해지면 별도 설계.

## 검증 (테스트 스위트 없음 → dry-run)

1. **하위호환**: 리팩터 전/후 `--dry-run --workflow routed`(그리고 simple/decompose)의
   모든 `*_command.json`이 **바이트 동일**(SHA-256 비교, `--project` 미지정).
2. **프로젝트 경로**: `--project _example --dry-run ...` 실행 시 run_dir이
   `projects/_example/runs/<stamp>/`로 생성됨을 확인.
3. **레이어링**: `_example/config.json`에 `claude_model` override를 넣고 `--project _example`
   dry-run → 해당 스텝 `*_command.json`의 모델이 override 값인지 확인. 제거 시 전역 값 상속 확인.
4. **에러**: 존재하지 않는 `--project foo` → `Project config not found` 종료 메시지 확인.
5. **resume**: 게이트까지 간 `--project` run을 만들고 `--resume`로 이어질 때 workspace가
   checkpoint에서 복원되어 정상 진입하는지 dry-run으로 확인.

# decompose 병렬 실행기 설계

> 작성일: 2026-07-12 · 상태: 설계 · 승인됨(구현 대기)

## 목표

`decompose`가 만들어 **사람이 승인한** task_graph를, 의존성 기반 **제한적 병렬**로
실행하는 실행기를 만든다. 각 노드는 격리된 git worktree에서 교차모델(구현자와
반대 모델 리뷰) 미니 루프로 구현되고, 결과는 통합 브랜치로 병합된 뒤 통합
평가·리포트를 거친다.

핵심은 **속도보다 격리·집중 컨텍스트·레이어별 반대모델 리뷰**다. 큰 다층 요청
(예: db 스키마 + 백엔드 API + 프론트 컴포넌트)에서 각 레이어가 자기 컨텍스트와
자기 반대모델 리뷰어를 갖고 병렬로 전진한다.

## 배경 — 현재 구조

`decompose.py`(`run_decompose_workflow`)는 **planner까지만** 만들어 놨다.

| 있는 것 | 근거 |
|---|---|
| 요청 → task_graph 분해(claude) | `decompose.py:26-46` |
| 노드별 `type`·`dependencies`·`risk_level`·`allowed_paths`/`blocked_paths` | `dry_run_task_graph` 스키마 |
| codex 계획 리뷰 → 승인 게이트 정지 | `decompose.py:50-80` |
| **실행기 = 없음** | docstring: *"승인된 그래프의 순차 실행은 후속 워크플로우"* |

즉 planner→문서→(레이어 노드 + 의존성)→사람 승인까지가 이미 있고, **미룬 것은
실행기 하나**다. 이 스펙이 그 실행기를 짓되, 순차가 아니라 **의존성 병렬**로 짓는다.

또한 알아야 할 두 가지 현행 제약:

- **`--resume`는 routed 전용이다.** `resume_routed_workflow`(`routed.py:77`)는
  `checkpoint.json`을 읽어 routed 계획 산출물(01/02/03)을 복원하고
  `run_implementation_route`로 진입한다. **decompose는 checkpoint를 쓰지 않는다.**
- **구현 루프는 코어와 꼬리가 묶여 있다.** `run_implementation_route`
  (`routed_impl.py:21`)는 구현(04)→리뷰/수정 반복(05/06)이라는 **코어**와,
  최종리뷰(07)→평가(08)→보고(09)라는 **꼬리**를 한 함수에서 순서대로 한다.
  병렬 실행기는 **코어는 노드마다, 꼬리는 run에 1회**여야 한다.

## 핵심 결정 (승인됨)

브레인스토밍에서 확정한 것:

1. **decompose의 실행기로 짓는다.** `routed`는 단일 구현자·교차 리뷰의 집중
   파이프라인으로 그대로 두고 손대지 않는다.
2. **worktree-per-노드 격리.** 노드마다 타깃 레포의 격리된 worktree + 브랜치.
3. **출력 = (A) 통합 브랜치.** 레인이 자기 브랜치에 커밋 → 실행기가 통합
   브랜치로 병합 → 사람이 브랜치/PR 리뷰. **main 병합·push는 사람(불가침 유지).**
4. **트리거 = decompose → 게이트 → `--resume`.** routed의 resume 패턴 재사용.
   새 top-level 워크플로를 만들지 않는다.
5. **제네릭 DAG wavefront.** `dependencies`를 위상정렬 — 의존성 충족 노드들을 한
   파도로 동시 실행 → barrier → 다음 파도. db/backend/frontend 하드코딩 없음.
6. **게이트 = 전체 그래프 1회.** 기존 decompose 게이트 그대로, 노드별 게이트 없음.
7. **노드별 = 미니 routed 루프**(구현→반대모델 리뷰→수정), 구현자/리뷰어는 노드
   `type`으로 `choose_implementer` 재사용(리뷰어=반대모델 불변식 유지).
8. **동시성 상한 = config `max_parallel_lanes`, 기본 2.** 무한 스폰 금지.
9. **충돌 = stop-and-report + `allowed_paths` 겹침 경고.** 자동 해결 안 함.
10. **노드 실패 = 안전편향 정지.** 진행 중 형제는 마치고, 통합 병합은 하지 않고
    정지·리포트. 부분 병합으로 깨진 트리를 만들지 않는다.

## 아키텍처

### 전체 흐름

```
python run.py --workflow decompose --request "..."
  → task_graph.json + 게이트 정지 (오늘 그대로, + checkpoint 저장)
  → 사람이 계획 검토·승인
python run.py --resume <run_dir>          # 승인 = resume 실행
  → [신규] run_decompose_exec_workflow
        1. baseline 확인(타깃 워킹트리 clean)
        2. 위상정렬 → 파도 리스트
        3. 파도마다: 노드들을 worktree 격리 + 병렬 실행(≤ max_parallel_lanes)
                     → barrier
        4. 통합 브랜치로 순차 병합 (충돌 시 stop-and-report)
        5. 통합 트리에 최종리뷰 → 평가 → 리포트 (run 1회)
        6. worktree 정리 + 레인 브랜치 삭제
```

### 실행 모델 — wavefront

- 입력은 승인된 `task_graph.json`의 `tasks[]`. 각 task는 `id`·`type`·
  `dependencies`(선행 `id` 목록)·`risk_level`·`allowed_paths`를 갖는다.
- **위상정렬**: 의존성이 없는(또는 이미 완료된) 노드가 한 파도. 순환 의존이
  발견되면 실행 전 `SystemExit`.
- **파도 실행**: 파도 안의 노드들을 `max_parallel_lanes`만큼 동시에 돌린다.
  파도의 **모든** 노드가 끝나야(barrier) 다음 파도로 간다.
- **코드-생성 노드만 레인이 된다.** `type`이 `backend`/`frontend`인 노드만
  worktree + 구현 루프 대상. `docs`/`review` 타입 노드는 baseline에 대해 **읽기전용
  분석 1회**만 돌리고(브랜치 없음, 병합 없음) 그 산출물은 리포트에 합류한다.
  (근거: `choose_implementer`는 docs/review에 "구현 스텝 없음"을 반환한다 —
  `routing.py:280`.)

### 노드 격리 — worktree

- baseline = 실행기 시작 시점 타깃 레포의 `HEAD`. 모든 레인이 **같은 baseline에서**
  분기하므로 3-way 병합이 깨끗하다(디스조인트 경로면 충돌 0).
- 노드 `n`마다:
  `git -C <target> worktree add <run_dir>/worktrees/<n.id> -b aa/<stamp>/<n.id> <baseline>`
- worktree는 **AutoAgent의 run_dir 밑**(`<run_dir>/worktrees/<id>`)에 둔다. 타깃
  레포의 워킹디렉터리를 더럽히지 않는다(추가되는 것은 git ref뿐이고 정리 대상).
  `run_dir`은 `runs/`(또는 `projects/<name>/runs/`) 아래라 AutoAgent git에 안 잡힌다.

### 노드별 루프 — 기존 코어 재사용

노드 하나의 구현은 **routed의 구현→리뷰→수정 코어를 그대로** 돈다. 단 두 가지가
노드별로 바뀐다:

- **`cwd`** = 그 노드의 worktree. `run_role_step`은 `cwd=config.workspace`
  (`routed_impl.py:219`)를 쓰므로, **노드용 `Config` 사본**을 만들어
  `config.workspace = <worktree 경로>`로 둔다(`dataclasses.replace`). 병렬 레인이
  전역 `config.workspace`를 서로 밟지 않게 하는 핵심.
- **`run_dir`(out_dir)** = 노드 전용 하위 디렉터리 `<run_dir>/nodes/<id>/`.
  `run_role_step`은 `run_dir/04_..._impl.md` 식으로 쓰므로, 노드마다 out_dir을
  분리해야 아티팩트가 충돌하지 않는다.

노드 route는 `route_task(node["type"], node["description"])`로 파생하되(구현자/
리뷰어·subtype·api/service 분류를 그대로 얻음), **그래프가 선언한 `risk_level`·
`subtype`이 있으면 그것으로 덮는다**(승인된 그래프의 위험 라벨이 우선). 이렇게 하면
db·high-risk 노드가 기존 roles 레지스트리(`resolve_role`)를 통해 xhigh effort·write
권한 등 그대로 배정받는다.

### 동시성 & 예산

- **동시성 기전 = 스레드 풀** `max_workers=max_parallel_lanes`. 각 워커가 노드
  하나의 코어 루프를 동기 실행하고, 그 안에서 `run_process`가 `claude.cmd`/
  `codex.cmd` 서브프로세스를 띄운다. 서브프로세스 대기 중 GIL이 풀려 실제 병렬이
  된다(작업이 서브프로세스 바운드라 스레드로 충분, asyncio 불필요).
- **예산 = 전 레인 공유 풀.** `AgentCallBudget` 하나를 모든 노드가 공유한다.
  **`before_call`을 스레드 세이프하게** 만든다(`runner.py`의 `AgentCallBudget`에
  `threading.Lock` 추가, 또는 실행기가 락으로 감쌈). 소진 시 새 노드·새 파도를
  시작하지 않고, 진행 중 노드만 마무리한 뒤 정지·리포트.

### 통합 & 출력

- **통합 브랜치** `aa/<stamp>`를 baseline에서 만든다. 완료된 레인 브랜치를
  노드 id 순(위상 순)으로 순차 `git merge`.
- **충돌 = stop-and-report**: 병합 중 충돌 시 즉시 중단, 해당 병합을 abort하고
  충돌 파일 목록·레인 브랜치·worktree를 **보존**한 채 "수동 병합 필요" 리포트.
  자동 해결(fix 에이전트)은 하지 않는다.
- **겹침 경고(얕은 가드)**: 파도 실행 **전에** 노드들의 `allowed_paths`를 비교해
  겹치는 경로가 있으면 경고를 리포트에 남긴다(차단은 아님 — 구조적 강제는 범위 밖).
- **성공 정리**: 통합·평가까지 끝나면 worktree 제거(`git worktree remove`) +
  레인 브랜치 삭제(`git branch -D aa/<stamp>/<id>`). 통합 브랜치 `aa/<stamp>`는
  **남긴다**(사람 리뷰 대상). **실패 시 전부 보존**하고 경로를 리포트.

### 통합 평가 & 리포트 (run 1회)

- 통합 브랜치 트리에 대해 **run 레벨에서 1회**: 최종리뷰(codex) → 평가(codex,
  `run_evaluation`) → 최종보고(claude, `run_final_report`). 기존 routed 꼬리 헬퍼를
  재사용한다.
- 평가는 노드들의 `validation_commands`(그래프에 이미 있음)를 통합 트리에서
  실행해 이음새를 검증한다. **타깃에 테스트 스위트가 없으면 이 검증이 약해진다** —
  하네스 전체의 기존 한계 그대로다.

### dry-run

- 병렬 없이 노드를 순회하며 프롬프트·`*_command.json`만 렌더한다(서브프로세스·
  worktree·git 없음). 노드별 out_dir에 산출물이 쌓인다. 기존 검증법(바이트 비교)을
  그대로 유지한다.

## 컴포넌트 변경

### 1) `autoagent/workflows/routed_impl.py` — 코어 추출 리팩터

`run_implementation_route`에서 구현→리뷰/수정 **코어(현 35~107행)**를 헬퍼로 뽑는다.

```python
def run_impl_review_fix(
    *, args, config, common, route, request, budget, run_dir,
) -> tuple[str, str, str, bool]:
    """구현(04) → 리뷰/수정 반복(05/06)을 돌고 (implementation, review, fix, resolved) 반환."""
    # 현 35~107행을 그대로 이동. run_role_step 호출부는 불변.
```

- `run_implementation_route`는 이 헬퍼를 호출한 뒤 기존 꼬리(최종리뷰→평가→보고)를
  그대로 이어간다. **routed의 동작·산출물은 불변**(리팩터는 순수 이동).
- **하위호환 게이트**: 리팩터 전/후 `--workflow routed --dry-run`의 모든
  `*_command.json`·`*_prompt.md`가 **바이트 동일**해야 한다.

### 2) `autoagent/worktree.py` (신규) — git worktree/통합 헬퍼

Korean docstring. 순수 git 조작만 담당(오케스트레이션은 실행기가):

- `add_worktree(target, path, branch, baseline) -> None`
- `remove_worktree(target, path) -> None` / `delete_branch(target, branch)`
- `create_integration_branch(target, name, baseline)`
- `merge_branch(target, branch) -> MergeResult`  # 충돌 시 abort + 충돌 파일 목록
- `warn_path_overlap(nodes) -> list[str]`         # allowed_paths 겹침 경고
- Windows 유의: `worktree remove` 실패 시 `--force`, CRLF 경고는 무해.

### 3) `autoagent/workflows/decompose_exec.py` (신규) — 실행기 본체

`run_decompose_exec_workflow(args, config, run_dir) -> int`:

1. `checkpoint.json` + `task_graph.json` 로드(없으면 `SystemExit`).
2. `git_baseline_status(config.workspace)`로 baseline clean 확인(아니면
   `block_implementation`).
3. 위상정렬 → 파도 리스트(순환 시 `SystemExit`). `allowed_paths` 겹침 경고.
4. 파도마다 `ThreadPoolExecutor(max_workers=config.max_parallel_lanes)`:
   각 코드-생성 노드에 대해 worktree 생성 → 노드용 `Config` 사본
   (`workspace=worktree`) + 노드 out_dir(`nodes/<id>/`) → `run_impl_review_fix` →
   레인 브랜치에 커밋. docs/review 노드는 읽기전용 1회. barrier.
5. 노드 실패 시: 진행 중 형제 완료 후 정지, 성공/실패/스킵 리포트, worktree 보존.
6. 통합 브랜치 병합(순차, 충돌 시 stop-and-report).
7. 통합 트리에 최종리뷰→`run_evaluation`→`run_final_report`(run 1회).
8. 성공 정리(worktree 제거 + 레인 브랜치 삭제, 통합 브랜치 유지).

### 4) `autoagent/workflows/decompose.py` — 게이트에 checkpoint 추가

게이트 정지 전에 재개 상태를 저장하고 표준 재개 핸드오프를 낸다.

- `write_approval_required`를 확장/대체해 **checkpoint.json**을 쓴다:
  ```json
  { "version": 1, "workflow": "decompose", "stage": "awaiting_approval",
    "request": "...", "workspace": "...", "config_path": "...",
    "task_graph": "task_graph.json", "max_review_rounds": ..., "max_agent_calls": ... }
  ```
- `routed_common.resume_command_for(run_dir)` + `ROUTED_STATUS`/`RUN_DIR`/
  `RESUME_COMMAND` stdout 라인을 재사용해 `/aa`·사람이 동일 형식으로 재개하게 한다.

### 5) `autoagent/cli.py` — `--resume` 분기

`--resume` 처리에서 `checkpoint.json`의 `workflow`를 읽어 분기한다.

- `"routed"`(또는 필드 없음 → **하위호환 기본**) → `resume_routed_workflow`(현행).
- `"decompose"` → `run_decompose_exec_workflow`.
- argparse에 새 플래그 없음(`--resume` 재사용). `max_parallel_lanes`는 config.

### 6) `autoagent/config.py` — `max_parallel_lanes`

- `Config`에 `max_parallel_lanes: int = 2` 추가 + 로드. 전역/프로젝트 config에서
  override 가능(레이어드 규칙 그대로).

### 7) `autoagent/runner.py` — 예산 스레드 세이프

- `AgentCallBudget.before_call`을 `threading.Lock`으로 보호(공유 풀 동시 감소
  경합 방지). 단일 스레드 경로(routed)에는 영향 없음.

## 데이터 흐름 / 예시

3-레이어 요청(db → backend, frontend)이 이렇게 분해·실행된다:

```
task_graph: n1(db, deps=[])  n2(backend, deps=[n1])  n3(frontend, deps=[n1])
위상 파도:  [n1]  →  [n2, n3]

파도1: n1 worktree(aa/S/n1) 에서 db 구현→codex 리뷰→수정, 커밋
파도2: n2, n3 동시 (max_parallel_lanes=2)
        n2 worktree(aa/S/n2): backend 구현(claude)→codex 리뷰→수정
        n3 worktree(aa/S/n3): frontend 구현(codex)→claude 리뷰→수정
통합:  aa/S ← merge n1, n2, n3  (경로 디스조인트면 무충돌)
평가:  aa/S 트리에서 validation_commands + codex 평가 → claude 리포트
정리:  worktree 3개 제거, aa/S/n1..n3 삭제, aa/S 유지
```

계약이 planner 문서에서 동결되고 사람이 게이트에서 승인했기 때문에, n2/n3가 n1의
스키마와 서로의 존재를 안 보고도 병렬로 안전하게 전진한다(사람 승인 = 계약 동결).

## 하위호환

- `--workflow decompose`(실행기 없이 게이트까지)는 **오늘과 동일** — checkpoint를
  하나 더 쓸 뿐 산출물·정지 동작 불변.
- `--resume`에 routed checkpoint를 주면 **오늘과 동일**(`workflow` 필드 없으면
  routed로 간주).
- `--workflow routed`/`simple`은 전혀 영향 없음(코어 추출은 순수 이동, 바이트 동일
  게이트로 보증).
- 타깃 레포: 실행기는 worktree·브랜치를 **추가했다가 정리**하고, 통합 브랜치
  `aa/<stamp>`만 남긴다. **main·push는 절대 건드리지 않는다.**

## 에러 처리

- 승인 없이(=checkpoint 없이) `--resume` → `SystemExit`(현행 메시지 재사용).
- task_graph 순환 의존 → 실행 전 `SystemExit`.
- baseline 더티 → `block_implementation`(현행 재사용), 실행기 진입 차단.
- 노드 루프 실패/블록 → 안전편향 정지(형제 완료 후 통합 안 함), 전체 worktree 보존.
- 통합 병합 충돌 → stop-and-report, 병합 abort, worktree·브랜치 보존.
- 예산 소진 → 진행 중 노드만 마무리, 새 파도 시작 안 함, `stopped_by_budget` 리포트.
- worktree 생성/제거 실패(Windows 잠금 등) → 명확한 에러 + 잔여 worktree 경로 안내.

## 범위 밖 (YAGNI — 이번엔 하지 않음)

- **자동 충돌 해결**(fix 에이전트 merge resolve) — stop-and-report만.
- **`allowed_paths` disjoint 강제 + 전용 통합 노드**(충돌 예방 (3)) — 경고만.
- **실패 노드 자동 재시도 / 파도 간 동적 재계획.**
- **노드별 승인 게이트.**
- **`--max-parallel-lanes` CLI 플래그** — config로만.
- **노드 타입별 정교한 처리**(예: infra/worker 전용 루프) — backend/frontend/
  docs/review 4종만.

## 검증 (테스트 스위트 없음 → dry-run)

1. **routed 회귀(코어 추출)**: 리팩터 전/후 `--workflow routed --dry-run`
   (그리고 simple/decompose)의 모든 `*_command.json`·`*_prompt.md`가 **바이트 동일**
   (SHA-256).
2. **실행기 dry-run**: 게이트까지 간 decompose run을 만들고 `--resume`
   `--dry-run` → 노드별 `nodes/<id>/04_*_impl_prompt.md`·`*_command.json`이 파도
   순서대로 렌더되는지 확인. worktree·git 미호출 확인.
3. **위상/파도**: 알려진 소형 그래프(위 3-레이어)로 파도 분할이
   `[n1] → [n2,n3]`인지, 순환 그래프가 `SystemExit`인지 확인.
4. **격리 경로**: dry-run에서 노드용 Config 사본의 `workspace`가 각 worktree
   경로로 세팅되고 out_dir이 `nodes/<id>/`로 분리되는지 확인(아티팩트 충돌 없음).
5. **resume 분기**: routed checkpoint → `resume_routed_workflow`, decompose
   checkpoint → 실행기로 분기되는지, `workflow` 필드 없는 옛 checkpoint가 routed로
   가는지 확인.
6. **비-dry-run 스모크**(가능하면 소형 타깃): 2노드 그래프로 worktree 2개 생성 →
   통합 브랜치 병합 → 정리까지 1회 관통. 충돌 케이스는 같은 파일을 두 레인이
   건드리게 만들어 stop-and-report가 발동하는지 확인.

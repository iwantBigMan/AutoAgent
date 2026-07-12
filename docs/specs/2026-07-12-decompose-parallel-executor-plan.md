# decompose 병렬 실행기 구현 계획

## Goal

`decompose` 워크플로가 만들어 사람이 승인한 `task_graph.json`을 의존성 기반 **wavefront 병렬**로 실행하는 실행기를 구현한다. 각 코드-생성 노드(현재 실행기는 `backend`/`frontend` 노드만 구현한다 — 아래 Global Constraint 7 참조)는 격리된 git worktree에서 routed의 구현→반대모델 리뷰→수정 미니 루프를 돌고, 결과를 통합 브랜치로 병합한 뒤 통합 트리에 대해 run 1회의 최종리뷰·평가·리포트를 수행한다. 트리거는 `decompose → 승인 게이트 → --resume`이며 checkpoint의 `mode` 필드로 분기한다. `max_parallel_lanes=1`이면 순차 실행과 동치다.

## Architecture

- **config.py**: `Config`에 `max_parallel_lanes: int = 2` 추가(데이터클래스 맨 끝) + `load_config` 병합.
- **runner.py**: `AgentCallBudget.before_call`을 `threading.Lock`으로 보호(공유 예산 풀 경합 방지).
- **routed_impl.py**: `run_implementation_route`의 구현→리뷰/수정 코어(현 35~107행)를 `run_impl_review_fix` 헬퍼로 순수 이동. 헬퍼는 **정지 신호를 포함한 5-튜플** `(implementation, review, fix, resolved, stopped)`를 반환하고, 원 함수는 헬퍼가 실제로 정지했을 때만(`stopped is True`) 꼬리를 건너뛴다 — routed 산출물 바이트 불변.
- **worktree.py**(신규): 순수 git 조작 헬퍼(`add_worktree`/`remove_worktree`/`delete_branch`/`create_integration_branch`/`merge_branch`/`warn_path_overlap`/`scope_violations`).
- **decompose.py**: `render_task_graph_brief`(순수함수, `resume_command`를 인자로 받아 재개 명령을 코드펜스로 임베드)로 `approval_brief.md` 렌더 + `mode:"task_graph"` checkpoint 저장 + resume 핸드오프 stdout 재사용.
- **cli.py**: `--resume`에서 checkpoint `mode` 읽어 `task_graph`→실행기, 없음/`routed_impl`→기존 `resume_routed_workflow` 분기.
- **task_exec.py**(신규): `run_task_graph_execution` — 로드→baseline→위상정렬→파도 병렬 실행→통합 병합→통합 평가/리포트→정리.

## Tech Stack

- Python 3(표준 라이브러리만): `dataclasses.replace`, `concurrent.futures.ThreadPoolExecutor`, `threading.Lock`, `subprocess`, `json`, `pathlib`, `fnmatch`, `time`.
- 기존 하네스 모듈(**실제 심볼 검증 완료**):
  - `autoagent.artifacts`: `read_text`, `write_text`, `write_json`, `render_template`, `write_command_artifact`(주의: `write_command_artifact`는 `autoagent.runner`에 정의됨), `DEFAULT_CONFIG`.
  - `autoagent.config.Config`, `autoagent.config.load_config`.
  - `autoagent.runner`: `AgentCallBudget`, `AgentCallBudgetStopped`, `require_command`, `run_process`, `write_command_artifact`, `command_for_agent`(주의: `command_for_agent`는 `autoagent.workflows.routed_impl`에 정의됨).
  - `autoagent.roles`: `load_roles(config_dir)`, `resolve_role(entry, *, config, route, request, agent, read_only)`.
  - `autoagent.safety`: `git_baseline_status(workspace) -> tuple[bool, str]`, `review_needs_changes(review) -> bool`.
  - `autoagent.routing.route_task(task_type, request, requested_implementer="auto") -> dict`.
  - `autoagent.workflows.routed_common`: `run_evaluation`, `run_final_report`, `block_implementation`, `resume_command_for`, `stop_after`.
  - `autoagent.workflows.routed_impl`: `command_for_agent`, `run_role_step`, `run_impl_review_fix`(신규).
- CLI 서브프로세스: `claude.cmd` / `codex.cmd`(worktree를 cwd로).

## Global Constraints

1. **바이트 패리티(SHA-256)**: 리팩터 전후로 `--workflow routed --dry-run`, `--workflow simple --dry-run`, `--workflow decompose --dry-run`의 모든 `*_command.json`·`*_prompt.md`가 **바이트 동일**해야 한다. 비교는 **반드시 `*_command.json`·`*_prompt.md` 확장자로 한정**한다(decompose는 Task 5에서 `approval_brief.md`·`checkpoint.json` 두 신규 파일을 dry-run 경로에도 쓰므로, 디렉터리 통째 비교는 이 두 파일을 오탐으로 잡는다). 검증 방식 (a) 참조.
2. **한국어 문서/주석**: 모든 신규 모듈은 한국어 docstring으로 시작하고, 모든 함수는 한국어 인라인 주석을 단다(식별자만 영문). 기존 스타일과 일치.
3. **타입/스타일**: 모든 신규 모듈은 `from __future__ import annotations`로 시작, PEP604 유니언(`str | None`) 사용, config/state 묶음은 dataclass.
4. **리뷰어=구현자 반대 모델 불변식**: 노드 route는 `route_task(node["type"], node.get("description", ""), "auto")`로 파생하며, 이는 `choose_implementer`가 정한 `implementation_agent`/`review_agent`(항상 반대 모델)를 그대로 담는다. 노드 실행은 `run_impl_review_fix`를 통해 이 route를 소비하므로 불변식이 자동 유지된다. 직접 모델을 지정하지 않는다.
5. **main 브랜치·push 절대 안 건드림**: 실행기는 타깃 레포에 `aa/<stamp>/<id>` 레인 브랜치와 `aa/<stamp>` 통합 브랜치만 만들고, worktree/레인 브랜치는 성공 시 정리한다. 통합 브랜치만 남긴다. `git push`·`main` 병합은 절대 호출하지 않는다.
6. **서브프로세스 cwd=config.workspace 격리**: `run_role_step`(및 `run_impl_review_fix`)이 `cwd=config.workspace`를 쓰므로, 노드마다 `dataclasses.replace(config, workspace=<worktree 경로>)`로 **Config 사본**을 만들어 넘긴다. 전역 `config`는 절대 변형하지 않는다(병렬 레인이 서로 밟지 않게).
7. **실행되는 노드 타입 범위**: decompose 스키마의 노드 `type` 어휘는 `backend`/`frontend`/`docs`/`review`/`test`/`db`/`infra`이지만, **현재 실행기는 `backend`/`frontend` 노드만 레인으로 구현한다**(그 외 프롬프트 파일이 없음 — `PROMPT_ALIASES`에 backend/frontend만 존재, `db`/`test`/`infra` route는 `claude_db_impl.md` 등 없는 경로를 열어 크래시). `db` 타입 노드는 `_node_route`에서 `backend`로 정규화해 실행하고(스키마상 db는 유효 코드-생성 후보이며 `route_task`가 db 키워드로 subtype=db/risk_level=high를 도출), `docs`/`review`/`test`/`infra` 타입 노드는 **skip 처리하되 사용자에게 보이는 리포트(`skipped_nodes.md` 및 `final_report.md`)에 "승인했으나 미실행" 노드 목록을 명시**해 은닉 커버리지 갭을 드러낸다. 이 결정은 스펙 §345("backend/frontend/docs/review 4종")를 db 정규화로 확장한 것으로, 계획에 명시적으로 못박는다.

### 검증 방식(테스트 스위트 없음 → 이 현실에 맞춘 TDD)

- **(a) dry-run 바이트 비교(확장자 한정)**: 리팩터 태스크는 리팩터 전 baseline 산출물을 스크래치패드에 복사해 두고, 리팩터 후 재생성해 **`*_command.json`·`*_prompt.md` 파일만 짝지어** SHA-256 비교한다. PowerShell 예:
  ```powershell
  $before = "<scratchpad>\parity\before\routed"; $after = "<scratchpad>\parity\after\routed"
  Get-ChildItem $before -Recurse -Include *_command.json,*_prompt.md | ForEach-Object {
      $rel = $_.FullName.Substring($before.Length)
      $h1 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
      $h2 = (Get-FileHash "$after$rel" -Algorithm SHA256).Hash
      if ($h1 -ne $h2) { Write-Host "MISMATCH: $rel" }
  }
  ```
  또는 Bash에서 `git -c core.autocrlf=false diff --no-index <before_file> <after_file>`로 파일 단위 비교. **디렉터리 통째 `diff --stat`은 쓰지 않는다**(decompose의 신규 파일 오탐 회피).
- **(b) 순수함수 assert**: 위상정렬/파도 분할, `render_task_graph_brief`, `warn_path_overlap`, `scope_violations`는 `python -c "..."`로 직접 import·호출해 `assert`로 검증(pytest 미가정 — 레포에 테스트 스위트 없음).
- **(c) 소형 스모크**: 실행기 dry-run은 게이트까지 간 run에 `--resume --dry-run`을 걸어 `nodes/<id>/04_*_impl_prompt.md`·`*_command.json`이 파도 순서대로 렌더되는지 확인. 비-dry-run 스모크는 2노드 그래프로 worktree 생성→병합→정리 1회 관통(가능 시).

### 확정된 태스크 실행 순서(의존성 반영)

`render_task_graph_brief`가 `topological_waves`를 지연 import하므로, **`topological_waves`가 Task 5보다 먼저 존재**해야 Task 5의 TDD 검증이 성립한다. 따라서 순서를 다음으로 확정한다:

**1 → 2 → 3 → 7a → 4 → 5 → 6 → 7b → 7c → 7d → 최종 통합 검증**

(7a가 `topological_waves`를 제공하고, 5가 이를 소비한다. 각 태스크는 이 순서에서 독립적으로 TDD 검증 가능하다.)

---

## Task 1 — config.py: `max_parallel_lanes` 추가

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\config.py` — `Config` 데이터클래스(라인 35 뒤), `load_config`의 `Config(...)` 호출(라인 87 뒤).
- **Test(수동)**: `python -c` 로드 확인(아래 스텝).

### Interfaces
- **Consumes**: `raw: dict[str, Any]`(`load_config` 내부), `int(raw.get(...) or N)` int 병합 관용구.
- **Produces**: `Config.max_parallel_lanes: int`(기본 2, 프로젝트 config에서 override 가능).

### Steps

1. **실패 검증 작성**: 스크래치패드 `check_lanes.py`:
   ```python
   from pathlib import Path
   from autoagent.config import load_config
   c = load_config(Path("nonexistent-config.json"))
   assert c.max_parallel_lanes == 2, c.max_parallel_lanes
   print("OK", c.max_parallel_lanes)
   ```
2. **실패 확인**: 레포 루트에서 `python "<scratchpad>\check_lanes.py"` → `AttributeError` 확인.
3. **구현 — 데이터클래스 필드 추가**: 라인 35(`default_max_agent_calls_implementation: int`) 뒤, 데이터클래스 **맨 끝**에 디폴트 필드를 추가(비-디폴트 위치인자 뒤에 와야 함):
   ```python
       default_max_agent_calls_implementation: int
       max_parallel_lanes: int = 2
   ```
4. **구현 — load_config 병합 추가**: 라인 87(`default_max_agent_calls_implementation=int(raw.get("default_max_agent_calls_implementation") or 9),`) 뒤에 기존 int 병합 관용구로 추가:
   ```python
           default_max_agent_calls_implementation=int(raw.get("default_max_agent_calls_implementation") or 9),
           max_parallel_lanes=int(raw.get("max_parallel_lanes") or 2),
   ```
5. **통과 확인**: `python "<scratchpad>\check_lanes.py"` → `OK 2`.
6. **회귀 확인**: `python .\run.py --dry-run --workflow routed --task-type backend --request "smoke"`가 크래시 없이 완료되는지 확인(설정 필드 추가는 렌더에 무영향).
7. **커밋**: `config: max_parallel_lanes 필드 추가(기본 2, 프로젝트 override 가능)`.

---

## Task 2 — runner.py: `AgentCallBudget.before_call` 스레드 세이프

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\runner.py` — import 블록(라인 7~16), `AgentCallBudget`(라인 34~56).
- **Test(수동)**: `python -c` 동시 호출 assert.

### Interfaces
- **Consumes**: `threading.Lock`, `dataclasses.field`.
- **Produces**: `AgentCallBudget.before_call`이 락 하에 check-then-increment를 원자적으로 수행. 시그니처 불변(`before_call(self, *, next_step: str, out_dir: Path, dry_run: bool) -> None`). 기존 위치인자 생성(`AgentCallBudget(args.max_agent_calls)`, routed.py:33/114 등)과 호환 유지.

### Steps

1. **실패 검증 작성**: 스크래치패드 `check_budget.py`:
   ```python
   from concurrent.futures import ThreadPoolExecutor
   from pathlib import Path
   from autoagent.runner import AgentCallBudget, AgentCallBudgetStopped
   b = AgentCallBudget(max_agent_calls=100)
   d = Path(".")
   def call(_):
       for _ in range(1000):
           try:
               b.before_call(next_step="x", out_dir=d, dry_run=False)
           except AgentCallBudgetStopped:
               return
   with ThreadPoolExecutor(max_workers=8) as ex:
       list(ex.map(call, range(8)))
   assert b.used_agent_calls == 100, b.used_agent_calls
   print("OK", b.used_agent_calls)
   ```
   (락이 없으면 경합으로 여러 스레드가 한계를 넘겨 증가해 `used_agent_calls`가 100을 초과할 수 있다. 간헐 실패이므로 여러 번 실행.) 주의: `dry_run=False`라 `stopped_by_budget.md`가 cwd에 쓰인다(스크래치패드에서 실행 권장).
2. **실패 확인**: 실행 → 값이 100 초과로 `AssertionError`(간헐).
3. **구현 — 락 필드 추가**: import 블록을 보강한다. 라인 11(`import subprocess`) 뒤에 `import threading`을 추가하고, 라인 12의 `from dataclasses import dataclass`를 `from dataclasses import dataclass, field`로 바꾼다. `AgentCallBudget`에 락 필드를 추가한다(직렬화·비교 대상 아님):
   ```python
   @dataclass
   class AgentCallBudget:
       max_agent_calls: int
       used_agent_calls: int = 0
       _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
   ```
   (`threading.Lock`은 팩토리 함수라 어노테이션이 엄밀한 타입은 아니지만 런타임 무해하고 관례적으로 통용된다. `default_factory`를 쓰므로 기존 위치인자 생성과 호환된다.)
4. **구현 — before_call 본문을 락으로 감싸기**: dry_run 조기 return은 카운트하지 않으므로 락 밖에 유지하고, check-then-increment만 락으로 원자화한다:
   ```python
       def before_call(self, *, next_step: str, out_dir: Path, dry_run: bool) -> None:
           # 매 에이전트 호출 직전에 부른다. dry_run은 실제 호출이 아니므로 카운트/체크하지 않는다.
           if dry_run:
               return
           # 병렬 레인이 하나의 예산 풀을 공유하므로 check-then-increment를 락으로 원자화한다.
           with self._lock:
               if self.max_agent_calls > 0 and self.used_agent_calls >= self.max_agent_calls:
                   write_text(
                       out_dir / "stopped_by_budget.md",
                       "# Stopped by Agent Call Budget\n\n"
                       "The run stopped before the next agent call.\n\n"
                       f"- max_agent_calls: {self.max_agent_calls}\n"
                       f"- used_agent_calls: {self.used_agent_calls}\n"
                       f"- next_step: {next_step}\n"
                       "- reason: Agent call budget exhausted.\n",
                   )
                   raise AgentCallBudgetStopped(next_step, out_dir)
               self.used_agent_calls += 1
   ```
5. **통과 확인**: `check_budget.py` 여러 번 → `OK 100`.
6. **회귀 확인**: `python .\run.py --dry-run --workflow routed --task-type backend --request "smoke"` 정상 완료(dry_run 경로는 락에 안 걸림).
7. **커밋**: `runner: AgentCallBudget.before_call 스레드 세이프(공유 예산 풀 락)`.

---

## Task 3 — routed_impl.py: `run_impl_review_fix` 코어 추출(순수 이동, 바이트 패리티)

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\routed_impl.py` — 현 35~107행 코어를 신규 헬퍼로 이동, `run_implementation_route`는 헬퍼 호출 후 꼬리(108~174) 유지.
- **Test(수동)**: dry-run 바이트 비교(routed/simple/decompose) + stop-after 조합.

### Interfaces
- **Produces**:
  ```python
  def run_impl_review_fix(
      *, args: Namespace, config: Config, common: dict[str, Any],
      route: dict[str, Any], request: str, budget: AgentCallBudget, run_dir: Path,
  ) -> tuple[str, str, str, bool, bool]:
      """구현(04) → 리뷰/수정 반복(05/06)을 돌고
      (implementation, review, fix, resolved, stopped)를 반환.

      stopped는 stop_after가 implementation/review 단계에서 실제로 매치돼 정지했는지다.
      stop_after 호출·stopped_after.md 기록은 여기서 1회만 수행한다(호출부는 재호출 금지).
      """
  ```
- **Consumes**: 기존 `run_role_step`(불변), `stop_after`, `review_needs_changes`.

**정지 신호 설계(critical 지적 반영)**: 스펙 §215가 코어 시그니처를 4-튜플로 스케치했으나, 원본의 조기 정지 시맨틱을 바이트 단위로 보존하려면 **정지 여부를 caller가 확실히 알아야 한다**. 원본은 `--stop-after review`라도 리뷰 루프가 실제로 실행돼 `stop_after(args, run_dir, "review")`가 호출될 때만 `return 0`한다. `max_review_rounds=0`이면 for 루프가 한 번도 안 돌아 review 단계에 도달하지 못하고, 원본은 그대로 꼬리(07/08/09)로 진행한다. 따라서 caller가 `args.stop_after`만 보고 조기 종료하면 `--stop-after review --max-review-rounds 0`에서 원본은 꼬리를 실행하는데 계획은 이를 건너뛰어 산출물(07/08/09·final_report.md)이 사라진다 → 바이트 패리티 위반. 이를 막기 위해 **헬퍼가 5번째 원소 `stopped: bool`을 반환**하고, caller는 `stopped`가 True일 때만 조기 종료한다. `resolved`(4번째)의 의미는 원본 그대로 유지한다.

### Steps

1. **실패 검증 작성(baseline 캡처)**: 리팩터 전 세 워크플로 dry-run을 스크래치패드에 캡처한다. 각 워크플로를 실행하고 생성된 `runs/<stamp>/`를 `<scratchpad>/parity/before/{routed,simple,decompose}`로 복사한다:
   ```
   python .\run.py --dry-run --workflow routed --task-type backend --request "parity probe"
   python .\run.py --dry-run --workflow simple --request "parity probe"
   python .\run.py --dry-run --workflow decompose --request "parity probe"
   ```
   추가로 다음 stop-after/라운드 조합도 각각 캡처(before)해 둔다(critical 케이스 명시):
   - `--workflow routed --task-type backend --stop-after implementation --request "parity probe"`
   - `--workflow routed --task-type backend --stop-after implementation --max-review-rounds 0 --request "parity probe"`
   - `--workflow routed --task-type backend --stop-after review --request "parity probe"`
   - `--workflow routed --task-type backend --stop-after review --max-review-rounds 0 --request "parity probe"`
   - `--workflow routed --task-type backend --max-review-rounds 2 --request "parity probe"`
   - high-risk 요청: `--workflow routed --task-type backend --request "add auth migration"`
2. **실패 확인(정의 부재)**: `python -c "from autoagent.workflows.routed_impl import run_impl_review_fix"` → `ImportError` 확인.
3. **정지 규약(확정, 단일 문장)**: 헬퍼가 `stop_after(implementation/review)`를 호출·기록하고 부분 상태와 함께 `stopped=True`를 반환한다. caller는 `stop_after`를 재호출하지 말고 `if stopped: return 0`으로만 조기 종료한다(재호출 금지 → `stopped_after.md` 1회 기록 → 바이트 동일). 이 한 문장이 규약이며, 이전 초안의 모순 서술(4-튜플 고정/재판정 왕복)은 폐기한다.
4. **구현 — 신규 헬퍼**(현 35~107행을 그대로 이동, `run_role_step` 호출부 불변):
   ```python
   def run_impl_review_fix(
       *,
       args: Namespace,
       config: Config,
       common: dict[str, Any],
       route: dict[str, Any],
       request: str,
       budget: AgentCallBudget,
       run_dir: Path,
   ) -> tuple[str, str, str, bool, bool]:
       """구현(04) → 리뷰/수정 반복(05/06)을 돌고 (implementation, review, fix, resolved, stopped)를 반환.

       stop_after가 implementation/review 단계에서 실제로 매치되면 그 시점 부분 상태와 stopped=True를,
       아니면 최종 상태와 stopped=False를 돌려준다. stopped_after.md 기록은 여기서 1회만 한다.
       """
       task_type = route["task_type"]
       implementation_agent = route["implementation_agent"]
       review_agent = route["review_agent"]

       implementation = run_role_step(
           args=args, config=config, run_dir=run_dir, budget=budget,
           agent=implementation_agent, role_id="implementer",
           name=f"04_{implementation_agent}_{task_type}_impl",
           prompt_name=f"{implementation_agent}_{task_type}_impl.md",
           prompt_values=common, next_step="implementation",
           dry_output=f"[dry-run: {implementation_agent} {task_type} implementation output]",
           route=route, request=request, mutating=True,
       )
       # 원본 순서 보존: 리뷰/수정 기본값과 resolved(rounds==0)를 루프 전에 세팅.
       rounds = max(args.max_review_rounds, 0)
       current_impl = implementation
       review = "Review skipped (max_review_rounds=0)."
       fix = "No fix step was run."
       resolved = rounds == 0
       if stop_after(args, run_dir, "implementation"):
           return current_impl, review, fix, resolved, True

       for r in range(1, rounds + 1):
           review = run_role_step(
               args=args, config=config, run_dir=run_dir, budget=budget,
               agent=review_agent, role_id="reviewer",
               name=f"05_{review_agent}_{task_type}_review_r{r}",
               prompt_name=f"{review_agent}_{task_type}_review.md",
               prompt_values={**common, "IMPLEMENTATION_RESULT": current_impl},
               next_step="review",
               dry_output=f"[dry-run: {review_agent} {task_type} review output]",
               route=route, request=request, mutating=False,
           )
           if stop_after(args, run_dir, "review"):
               return current_impl, review, fix, resolved, True
           if not review_needs_changes(review):
               resolved = True
               break
           fix = run_role_step(
               args=args, config=config, run_dir=run_dir, budget=budget,
               agent=implementation_agent, role_id="fix",
               name=f"06_{implementation_agent}_{task_type}_fix_r{r}",
               prompt_name=f"{implementation_agent}_{task_type}_fix.md",
               prompt_values={**common, "IMPLEMENTATION_RESULT": current_impl, "REVIEW_RESULT": review},
               next_step="fix",
               dry_output=f"[dry-run: {implementation_agent} {task_type} fix output]",
               route=route, request=request, mutating=True,
           )
           current_impl = fix

       return current_impl, review, fix, resolved, False
   ```
   **바이트 패리티 대응(원본과의 상태 일치)**:
   - `stop_after("implementation")` True: 원본은 `review`/`fix` 기본값과 `resolved=(rounds==0)`이 세팅된 상태로 `return 0`했다. 위에서 정지 전에 동일하게 세팅해 반환한다.
   - `stop_after("review")` True: 원본은 루프 안에서 `return 0`했고 `review`는 방금 실행된 리뷰 결과, `fix`는 아직 기본값/직전값이다. 위 코드도 동일 시점에 반환한다.
   - `max_review_rounds=0`: for 루프 미실행 → `stop_after("review")` 미호출 → `stopped=False` 반환 → caller가 꼬리로 진행(원본과 동일).
5. **구현 — run_implementation_route를 헬퍼 호출로 축소**(라인 30~107을 대체, 꼬리 108~174는 불변):
   ```python
   def run_implementation_route(
       args: Namespace, config: Config, common: dict[str, Any],
       route: dict[str, Any], request: str, budget: AgentCallBudget, run_dir: Path,
   ) -> int:
       # 구현→리뷰/수정 코어를 헬퍼로 돌리고, 헬퍼가 실제로 정지했을 때만 꼬리를 건너뛴다.
       implementation, review, fix, resolved, stopped = run_impl_review_fix(
           args=args, config=config, common=common, route=route,
           request=request, budget=budget, run_dir=run_dir,
       )
       if stopped:
           return 0

       # 이후 최종리뷰/평가/보고는 최신 반영본 기준으로 진행한다.
       write_text(
           run_dir / "review_loop_status.md",
           f"resolved: {str(resolved).lower()}\n"
           f"rounds_configured: {max(args.max_review_rounds, 0)}\n",
       )
       # ... (기존 라인 109~174 꼬리 그대로: final-review 07 → evaluation 08 → report 09,
       #      final_review 블록·evaluation·final_report·stop_after 호출 모두 불변)
   ```
   **패리티 주의점**:
   - 원본 라인 106의 `rounds_configured: {rounds}`는 `rounds = max(args.max_review_rounds, 0)`였다. 위에서 `max(args.max_review_rounds, 0)`로 동일 값을 재계산 → 파일 내용 동일.
   - 원본 꼬리는 `implementation = current_impl`(라인 102) 후 `implementation`을 썼다. 헬퍼가 최종 `current_impl`을 첫 원소로 반환하므로 동치.
   - 꼬리 안의 `stop_after("final-review"/"evaluation"/"report")`는 원본 그대로 꼬리에서 처리(변경 없음).
6. **통과 확인(바이트 비교, 확장자 한정)**: 리팩터 후 스텝 1의 모든 조합을 재실행해 `<scratchpad>/parity/after/*`로 복사하고, 검증 방식 (a)의 확장자 한정 SHA-256 비교로 `*_command.json`·`*_prompt.md`가 **바이트 동일**임을 확인한다. 특히 `--stop-after review --max-review-rounds 0`, `--stop-after implementation --max-review-rounds 0`에서 07/08/09·final_report.md가 before/after 동일하게 존재하는지 확인한다.
7. **커밋**: `routed_impl: run_impl_review_fix 코어 추출(정지 신호 5-튜플, dry-run 바이트 동일)`.

---

## Task 7a — task_exec.py: 스켈레톤 + 로드 + baseline + 위상정렬/파도 + status 영속

> **순서 주의**: `topological_waves`가 Task 5(`render_task_graph_brief`)의 지연 import 대상이므로 Task 5보다 먼저 만든다. 여기서는 위상함수·로드·baseline·스켈레톤까지 채우고, 파도 실행/통합/평가는 7b/7c/7d에서 채운다.

### Files
- **Create**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\task_exec.py`.
- **Test(수동)**: `python -c`로 `topological_waves` assert + 로드 스모크.

### Interfaces
- **Produces**:
  ```python
  def topological_waves(tasks: list[dict[str, Any]]) -> list[list[str]]  # 순수함수, 순환 시 SystemExit
  def load_task_graph(run_dir: Path) -> dict[str, Any]
  def persist_status(run_dir: Path, task_graph: dict[str, Any]) -> None
  def set_status(run_dir, task_graph, node_id, status) -> None
  def run_task_graph_execution(args, config, run_dir) -> int  # 스켈레톤(7a에선 로드/위상/baseline까지)
  ```
- **Consumes**: `read_text`/`write_text`/`write_json`, `git_baseline_status`, `block_implementation`, `warn_path_overlap`, `AgentCallBudget`.

### Steps

1. **실패 검증 작성(위상)**: 스크래치패드 `check_waves.py`:
   ```python
   from autoagent.workflows.task_exec import topological_waves
   tasks = [
       {"id": "n1", "dependencies": []},
       {"id": "n2", "dependencies": ["n1"]},
       {"id": "n3", "dependencies": ["n1"]},
   ]
   assert topological_waves(tasks) == [["n1"], ["n2", "n3"]]
   # done 노드는 만족된 것으로 보고 첫 파도에서 배제
   tasks2 = [
       {"id": "n1", "dependencies": [], "status": "done"},
       {"id": "n2", "dependencies": ["n1"]},
   ]
   assert topological_waves(tasks2) == [["n2"]], topological_waves(tasks2)
   # 순환은 SystemExit
   cyc = [{"id": "a", "dependencies": ["b"]}, {"id": "b", "dependencies": ["a"]}]
   try:
       topological_waves(cyc); raise AssertionError("cycle not detected")
   except SystemExit:
       pass
   print("OK")
   ```
2. **실패 확인**: `ImportError`.
3. **구현 — 모듈 스켈레톤 + topological_waves**:
   ```python
   """decompose 병렬 실행기 본체.

   승인된 task_graph.json을 의존성 wavefront로 실행한다: baseline 확인 → 위상정렬 →
   파도별 worktree 격리 병렬 실행(구현→반대모델 리뷰→수정) → 통합 브랜치 병합 →
   통합 트리 최종리뷰/평가/리포트 → 정리. max_parallel_lanes=1이면 순차 실행과 동치다.
   현재 실행기는 backend/frontend(및 backend로 정규화되는 db) 노드만 구현하고,
   docs/review/test/infra 노드는 skip하며 리포트에 미실행으로 명시한다.
   """
   from __future__ import annotations

   import dataclasses
   import json
   import subprocess
   import time
   from argparse import Namespace
   from concurrent.futures import ThreadPoolExecutor
   from pathlib import Path
   from typing import Any

   from autoagent.artifacts import read_text, write_json, write_text
   from autoagent.config import Config
   from autoagent.routing import route_task
   from autoagent.runner import AgentCallBudget, AgentCallBudgetStopped, require_command, run_process, write_command_artifact
   from autoagent.safety import git_baseline_status
   from autoagent import worktree as wt
   from autoagent.workflows.routed_common import block_implementation, run_evaluation, run_final_report
   from autoagent.workflows.routed_impl import command_for_agent, run_impl_review_fix


   # 프롬프트 파일(PROMPT_ALIASES)에 존재하는, 레인으로 구현 가능한 타입.
   CODE_NODE_TYPES = {"backend", "frontend"}


   def topological_waves(tasks: list[dict[str, Any]]) -> list[list[str]]:
       """의존성 기반 파도 리스트를 만든다. 이미 done인 노드는 만족된 것으로 보고 배제한다.

       한 파도 = 아직 미완이며 모든 의존성이 done이거나 이전 파도에서 처리된 노드들.
       파도 내부 순서는 입력 tasks 순서를 보존해 결정론적이다. 순환 의존이면 SystemExit.
       """
       by_id = {t.get("id"): t for t in tasks}
       done: set[str] = {t.get("id") for t in tasks if t.get("status") == "done"}
       remaining = [t.get("id") for t in tasks if t.get("id") not in done]
       waves: list[list[str]] = []
       satisfied = set(done)
       while remaining:
           wave = [
               nid for nid in remaining
               if all(dep in satisfied for dep in (by_id[nid].get("dependencies") or []))
           ]
           if not wave:
               raise SystemExit(f"task_graph에 순환 의존이 있습니다(진행 불가): {remaining}")
           waves.append(wave)
           satisfied |= set(wave)
           remaining = [nid for nid in remaining if nid not in satisfied]
       return waves
   ```
4. **통과 확인(위상)**: `python "<scratchpad>\check_waves.py"` → `OK`.
5. **구현 — 로드 + status 영속 + 실행기 스켈레톤**:
   ```python
   def load_task_graph(run_dir: Path) -> dict[str, Any]:
       # 승인 게이트에서 저장한 task_graph.json을 읽는다. 없으면 재개 불가로 종료.
       path = run_dir / "task_graph.json"
       if not path.exists():
           raise SystemExit(f"No task_graph.json in {run_dir}; cannot execute.")
       return json.loads(read_text(path))


   def persist_status(run_dir: Path, task_graph: dict[str, Any]) -> None:
       # status 전이마다 task_graph.json을 다시 써 재개 시 done 노드를 건너뛸 수 있게 한다.
       write_json(run_dir / "task_graph.json", task_graph)


   def set_status(run_dir: Path, task_graph: dict[str, Any], node_id: str, status: str) -> None:
       # 단일 노드 status를 갱신하고 즉시 영속한다.
       for t in task_graph.get("tasks", []):
           if t.get("id") == node_id:
               t["status"] = status
               break
       persist_status(run_dir, task_graph)


   def run_task_graph_execution(args: Namespace, config: Config, run_dir: Path) -> int:
       """승인된 task_graph를 wavefront 병렬로 실행한다(재개 진입점)."""
       task_graph = load_task_graph(run_dir)
       tasks = task_graph.get("tasks", []) or []

       # baseline 안전 확인: 타깃 워킹트리가 커밋된 HEAD를 가져야 격리 worktree가 깨끗하다.
       if not args.dry_run:
           ok, git_message = git_baseline_status(config.workspace)
           write_text(run_dir / "git_baseline_status.txt", git_message)
           if not ok:
               return block_implementation(run_dir, git_message)

       waves = topological_waves(tasks)  # 순환이면 여기서 SystemExit
       write_text(run_dir / "waves.txt", "\n".join(" ".join(w) for w in waves) + "\n")

       overlaps = wt.warn_path_overlap(tasks)
       if overlaps:
           write_text(run_dir / "path_overlap_warnings.md",
                      "# allowed_paths 겹침 경고\n\n" + "\n".join(f"- {w}" for w in overlaps) + "\n")

       budget = AgentCallBudget(args.max_agent_calls)
       # 7b/7c/7d에서 파도 실행·병합·평가/리포트를 채운다.
       return 0
   ```
6. **통과 확인(로드 스모크)**: Task 5의 dry-run 산출물(게이트까지 간 decompose run)을 재사용해 `run_task_graph_execution`이 `waves.txt`를 쓰는지 확인한다. (Task 5가 아직 없다면 이 스텝은 임시로 `task_graph.json`만 담은 스텁 run_dir로 대체하고, Task 5 완료 후 실제 run_dir로 재확인.)
   ```python
   import argparse
   from pathlib import Path
   from autoagent.config import load_config
   from autoagent.workflows.task_exec import run_task_graph_execution
   args = argparse.Namespace(dry_run=True, max_agent_calls=0, max_review_rounds=1,
                             read_only=False, stop_after="none", config="autoagent.config.json",
                             implementer="auto", workspace=None)
   config = load_config(Path("autoagent.config.json"))
   run_dir = Path("<task_graph.json 담은 run_dir>")
   run_task_graph_execution(args, config, run_dir)
   assert (run_dir / "waves.txt").exists()
   print("OK")
   ```
7. **커밋**: `task_exec: 스켈레톤 + 위상정렬/파도 + status 영속 + baseline 확인`.

---

## Task 4 — worktree.py(신규): git worktree/통합/스코프 헬퍼

### Files
- **Create**: `C:\Users\systran\Desktop\AutoAgent\autoagent\worktree.py`.
- **Test(수동)**: `python -c`로 `warn_path_overlap`/`scope_violations`(git 미의존 부분) assert + 임시 git 레포 스모크.

### Interfaces
- **Produces**:
  ```python
  @dataclass
  class MergeResult:
      ok: bool
      conflicts: list[str]      # 충돌 파일 목록(ok=True면 빈 리스트)
      message: str

  def add_worktree(target: Path, path: Path, branch: str, baseline: str) -> None
  def remove_worktree(target: Path, path: Path) -> None
  def delete_branch(target: Path, branch: str) -> None
  def create_integration_branch(target: Path, name: str, baseline: str) -> None
  def merge_branch(target: Path, branch: str) -> MergeResult
  def warn_path_overlap(nodes: list[dict[str, Any]]) -> list[str]
  def scope_violations(target: Path, worktree: Path, allowed: list[str], blocked: list[str]) -> list[str]
  ```
- **Consumes**: `subprocess.run`(text, encoding utf-8, errors replace), `fnmatch`(경로 매칭).

### Steps

1. **실패 검증 작성(순수부)**: 스크래치패드 `check_worktree.py`:
   ```python
   from autoagent.worktree import warn_path_overlap
   nodes = [
       {"id": "001", "allowed_paths": ["src/a/**"]},
       {"id": "002", "allowed_paths": ["src/a/**", "src/b/**"]},
       {"id": "003", "allowed_paths": ["src/c/**"]},
   ]
   warns = warn_path_overlap(nodes)
   assert any("001" in w and "002" in w for w in warns), warns
   assert not any("003" in w for w in warns), warns
   print("OK", warns)
   ```
2. **실패 확인**: `ImportError`(모듈 없음).
3. **구현 — 모듈 스켈레톤 + 순수 헬퍼**:
   ```python
   """git worktree/통합/스코프 헬퍼.

   decompose 병렬 실행기가 쓰는 순수 git 조작만 담당한다(오케스트레이션은 task_exec가).
   worktree 추가/제거, 레인 브랜치 삭제, 통합 브랜치 생성/병합, allowed_paths 겹침 경고,
   git diff 기반 soft scope 가드를 제공한다. 자동 충돌 해결·하드 샌드박스는 범위 밖.
   """
   from __future__ import annotations

   import fnmatch
   import subprocess
   from dataclasses import dataclass
   from pathlib import Path
   from typing import Any


   def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
       # 타깃 레포에서 git을 실행하고 CompletedProcess를 반환(호출부가 returncode 판정).
       return subprocess.run(
           ["git", "-C", str(target), *args],
           capture_output=True, text=True, encoding="utf-8", errors="replace",
       )


   @dataclass
   class MergeResult:
       """통합 브랜치 병합 결과. ok=False면 conflicts에 충돌 파일 목록이 담긴다."""
       ok: bool
       conflicts: list[str]
       message: str


   def warn_path_overlap(nodes: list[dict[str, Any]]) -> list[str]:
       # 노드 쌍의 allowed_paths가 하나라도 겹치면 경고 문자열을 만든다(차단 아님).
       warnings: list[str] = []
       for i in range(len(nodes)):
           for j in range(i + 1, len(nodes)):
               a = set(nodes[i].get("allowed_paths") or [])
               b = set(nodes[j].get("allowed_paths") or [])
               shared = sorted(a & b)
               if shared:
                   warnings.append(
                       f"경로 겹침: 노드 {nodes[i].get('id')} 와 {nodes[j].get('id')} 가 "
                       f"{shared} 를 공유합니다(통합 시 충돌 가능)."
                   )
       return warnings
   ```
4. **통과 확인(순수부)**: `python "<scratchpad>\check_worktree.py"` → `OK [...]`.
5. **구현 — scope_violations**:
   ```python
   def scope_violations(target: Path, worktree: Path, allowed: list[str], blocked: list[str]) -> list[str]:
       # worktree에서 baseline(HEAD) 대비 변경된 파일이 allowed_paths 밖(또는 blocked_paths 안)이면 플래그.
       proc = subprocess.run(
           ["git", "-C", str(worktree), "diff", "--name-only", "HEAD"],
           capture_output=True, text=True, encoding="utf-8", errors="replace",
       )
       changed = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
       violations: list[str] = []
       for path in changed:
           # blocked 우선: blocked 패턴에 걸리면 무조건 위반.
           if any(fnmatch.fnmatch(path, pat) for pat in (blocked or [])):
               violations.append(f"blocked 경로 변경: {path}")
               continue
           # allowed가 지정됐는데 어느 패턴에도 안 맞으면 범위 밖 변경.
           if allowed and not any(fnmatch.fnmatch(path, pat) for pat in allowed):
               violations.append(f"allowed 밖 변경: {path}")
       return violations
   ```
   (`allowed`가 빈 리스트면 "전 범위 허용"으로 간주해 allowed 위반을 만들지 않는다 — 경로를 안 좁힌 노드를 과플래그하지 않게.)
6. **구현 — worktree/브랜치/병합 조작**:
   ```python
   def add_worktree(target: Path, path: Path, branch: str, baseline: str) -> None:
       # baseline에서 새 브랜치로 worktree를 추가. path는 run_dir 밑(타깃 워킹트리를 안 더럽힘).
       proc = _git(target, "worktree", "add", str(path), "-b", branch, baseline)
       if proc.returncode != 0:
           raise SystemExit(f"worktree add 실패({branch}): {proc.stderr.strip() or proc.stdout.strip()}")


   def remove_worktree(target: Path, path: Path) -> None:
       # 성공 정리용. Windows 잠금 등으로 실패하면 --force로 한 번 더 시도한다.
       proc = _git(target, "worktree", "remove", str(path))
       if proc.returncode != 0:
           _git(target, "worktree", "remove", "--force", str(path))


   def delete_branch(target: Path, branch: str) -> None:
       # 레인 브랜치 삭제(통합 후 정리). 이미 없으면 무해하게 넘어간다.
       _git(target, "branch", "-D", branch)


   def create_integration_branch(target: Path, name: str, baseline: str) -> None:
       # baseline에서 통합 브랜치를 만든다(레인 브랜치를 여기로 순차 병합).
       proc = _git(target, "branch", name, baseline)
       if proc.returncode != 0:
           raise SystemExit(f"통합 브랜치 생성 실패({name}): {proc.stderr.strip() or proc.stdout.strip()}")


   def merge_branch(target: Path, branch: str) -> MergeResult:
       # 현재 체크아웃된 통합 브랜치(target=통합 worktree)에 레인 브랜치를 병합.
       # 충돌 시 abort하고 충돌 파일을 돌려준다.
       proc = _git(target, "merge", "--no-ff", "--no-edit", branch)
       if proc.returncode == 0:
           return MergeResult(ok=True, conflicts=[], message=proc.stdout.strip())
       diff = _git(target, "diff", "--name-only", "--diff-filter=U")
       conflicts = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
       _git(target, "merge", "--abort")
       return MergeResult(ok=False, conflicts=conflicts, message=f"병합 충돌({branch}): {conflicts}")
   ```
   **주의**: `merge_branch`는 첫 인자가 **통합 브랜치가 체크아웃된 worktree 경로**여야 한다(7c에서 통합 worktree `worktrees/_integration`를 만들어 거기서 병합한다).
7. **통과 확인(스모크)**: 스크래치패드에 임시 git 레포를 만들어 add→커밋→worktree add→remove→branch -D 관통(Bash):
   ```
   set -e
   R="<scratchpad>/wt_smoke"; rm -rf "$R"; mkdir -p "$R"
   git -C "$R" init -q; git -C "$R" config user.email t@t; git -C "$R" config user.name t
   echo base > "$R/f.txt"; git -C "$R" add .; git -C "$R" commit -qm base
   python -c "from pathlib import Path; from autoagent.worktree import add_worktree, remove_worktree, delete_branch; \
   add_worktree(Path('$R'), Path('$R/wt/n1'), 'aa/S/n1', 'HEAD'); \
   remove_worktree(Path('$R'), Path('$R/wt/n1')); delete_branch(Path('$R'), 'aa/S/n1'); print('OK')"
   ```
8. **커밋**: `worktree: git worktree/통합/스코프 순수 헬퍼 신규`.

---

## Task 5 — decompose.py: `render_task_graph_brief` + checkpoint(mode) + 재개 핸드오프

> **순서 주의**: Task 7a(`topological_waves`) 완료 후 착수한다.

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\decompose.py` — import(라인 14~16), `run_decompose_workflow`의 승인 정지부(라인 79~82), `write_approval_required` 보강, 신규 순수함수/체크포인트 함수 추가.
- **Test(수동)**: `python -c`로 `render_task_graph_brief` 문자열 assert + decompose dry-run.

### Interfaces
- **Produces**:
  ```python
  def render_task_graph_brief(task_graph: dict[str, Any], resume_command: str) -> str  # 순수함수 → approval_brief.md
  def write_task_graph_checkpoint(run_dir, *, request, config, args) -> None  # mode:"task_graph"
  ```
- **Consumes**: `from autoagent.workflows.task_exec import topological_waves`(함수 내부 **지연 import**로 순환 회피 — routed_common의 관례와 동일), `resume_command_for`(`routed_common`), `write_json`/`write_text`(이미 import됨), `Namespace`.

**minor 지적 반영(impl/test 불일치)**: `render_task_graph_brief`는 `resume_command` 인자를 받아 '## 다음 단계' 섹션에 **실제 재개 명령을 코드펜스로 임베드**한다(산문만 넣지 않음). 호출부는 `resume_command_for(run_dir)`를 넘긴다. 이로써 검증 스크립트의 `"--resume" in b` assert가 성립한다.

### Steps

1. **실패 검증 작성**: 스크래치패드 `check_brief.py`:
   ```python
   from autoagent.workflows.decompose import render_task_graph_brief
   g = {
       "version": 1, "goal": "다층 기능", "risk_level": "medium",
       "requires_human_approval": True,
       "tasks": [
           {"id": "n1", "title": "db 스키마", "type": "backend", "description": "테이블 추가",
            "risk_level": "high", "approval_required": True, "allowed_paths": ["db/**"],
            "validation_commands": ["git status --short"], "dependencies": []},
           {"id": "n2", "title": "API", "type": "backend", "description": "핸들러",
            "risk_level": "low", "approval_required": False, "allowed_paths": ["api/**"],
            "validation_commands": [], "dependencies": ["n1"]},
       ],
   }
   b = render_task_graph_brief(g, 'python "run.py" --resume "R"')
   assert "실행 순서" in b
   assert "n1" in b and "n2" in b
   assert "high" in b.lower()          # high-risk 강조 섹션
   assert "git status --short" in b    # validation_commands
   assert "--resume" in b              # 하단 재개 명령 임베드
   print("OK len", len(b))
   ```
2. **실패 확인**: `ImportError`(함수 없음).
3. **구현 — render_task_graph_brief**(결정론적 마크다운, 에이전트 호출 없음):
   ```python
   def render_task_graph_brief(task_graph: dict[str, Any], resume_command: str) -> str:
       """승인된 task_graph를 사람이 읽는 approval_brief.md 마크다운으로 결정론적으로 렌더한다.

       JSON을 그대로 반영하므로 별도 에이전트 호출이 없고(비용 0) JSON과 항상 일치한다.
       실행 순서표는 위상정렬 파도 순서로, high-risk/approval_required 노드는 별도 섹션에 강조한다.
       resume_command는 하단 '다음 단계'에 코드펜스로 임베드한다.
       """
       from autoagent.workflows.task_exec import topological_waves  # 순환 import 회피(지연 import)

       tasks = task_graph.get("tasks", []) or []
       by_id = {t.get("id"): t for t in tasks}
       waves = topological_waves(tasks)  # list[list[str]] — 파도별 노드 id

       lines: list[str] = []
       lines.append("# Task Graph 승인 브리핑\n")
       lines.append(f"- 목표: {task_graph.get('goal', '')}")
       lines.append(f"- 그래프 risk_level: {task_graph.get('risk_level', 'unknown')}")
       lines.append(f"- 노드 수: {len(tasks)}")
       lines.append(f"- 최대 병렬 파도 폭: {max((len(w) for w in waves), default=0)}\n")

       lines.append("## 실행 순서 (위상정렬 파도)\n")
       lines.append("| 파도 | id | title | type | risk | allowed_paths | 의존성 |")
       lines.append("|---|---|---|---|---|---|---|")
       for wave_index, wave in enumerate(waves, start=1):
           for node_id in wave:
               t = by_id.get(node_id, {})
               allowed = ", ".join(t.get("allowed_paths") or []) or "-"
               deps = ", ".join(t.get("dependencies") or []) or "-"
               lines.append(
                   f"| {wave_index} | {node_id} | {t.get('title', '')} | "
                   f"{t.get('type', '')} | {t.get('risk_level', '')} | {allowed} | {deps} |"
               )
       lines.append("")

       lines.append("## 노드 설명\n")
       for t in tasks:
           lines.append(f"- **{t.get('id')}** ({t.get('type')}): {t.get('description', '')}")
       lines.append("")

       high_risk = [t for t in tasks if t.get("risk_level") == "high" or t.get("approval_required") is True]
       lines.append("## 위험 노드 (high-risk / approval_required)\n")
       if high_risk:
           for t in high_risk:
               lines.append(f"- **{t.get('id')}** ({t.get('type')}, risk={t.get('risk_level')}): {t.get('title', '')}")
       else:
           lines.append("- 없음")
       lines.append("")

       lines.append("## 검증 명령 (validation_commands)\n")
       for t in tasks:
           cmds = t.get("validation_commands") or []
           if cmds:
               lines.append(f"- {t.get('id')}: {', '.join(cmds)}")
       lines.append("")

       lines.append("## 다음 단계\n")
       lines.append(
           "이 계획대로 진행하려면 아래 재개 명령을 실행하세요(재개 실행 자체가 승인입니다). "
           "특정 노드를 빼거나 고치려면 task_graph.json을 수정한 뒤 재실행하세요.\n"
       )
       lines.append("```powershell")
       lines.append(resume_command)
       lines.append("```\n")
       return "\n".join(lines) + "\n"
   ```
4. **통과 확인(브리핑)**: `python "<scratchpad>\check_brief.py"` → `OK len ...`.
5. **구현 — checkpoint(mode:"task_graph") 저장 함수**:
   ```python
   def write_task_graph_checkpoint(run_dir: Path, *, request: str, config: Config, args: Namespace) -> None:
       """실행기 재개(--resume)에 필요한 상태를 mode:"task_graph"로 저장한다(routed checkpoint와 구분)."""
       checkpoint = {
           "version": 1,
           "mode": "task_graph",
           "stage": "awaiting_approval",
           "request": request,
           "workspace": str(config.workspace),
           "config_path": args.config,
           "task_graph": "task_graph.json",
           "max_review_rounds": args.max_review_rounds,
           "max_agent_calls": args.max_agent_calls,
       }
       write_json(run_dir / "checkpoint.json", checkpoint)
   ```
   import 추가: 라인 14~16 근처에 `from autoagent.workflows.routed_common import resume_command_for`, `from argparse import Namespace`(이미 있음), `from autoagent.config import Config`(이미 있음), `write_json`(이미 import됨).
6. **구현 — 승인 정지부 보강**: `run_decompose_workflow`의 라인 79~82를 다음으로 대체. `approval_brief.md`·`checkpoint.json`은 `*_command.json`/`*_prompt.md`가 아니므로 바이트 패리티 대상 밖이지만, 비교 스텝은 반드시 확장자 한정으로 수행한다(Global Constraint 1):
   ```python
       resume_command = resume_command_for(run_dir)
       # task_graph가 추출된 경우에만 브리핑/체크포인트를 쓴다(추출 실패면 기존 안내로 폴백).
       if task_graph is not None:
           write_text(run_dir / "approval_brief.md", render_task_graph_brief(task_graph, resume_command))
           write_task_graph_checkpoint(run_dir, request=request, config=config, args=args)
       write_approval_required(run_dir)
       write_final_report(run_dir, task_graph, extracted, plan_review)

       print("ROUTED_STATUS: waiting_for_human_approval")
       print(f"RUN_DIR: {run_dir}")
       print(f"RESUME_COMMAND: {resume_command}")
       print(f"Decompose run complete: {run_dir}")
       return 0
   ```
   `run_decompose_workflow`는 이미 `args: Namespace`를 받으므로 그대로 전달 가능.
7. **구현 — write_approval_required 보강**(`approval_brief.md`를 우선 안내 + 재개 명령 명시):
   ```python
   def write_approval_required(run_dir: Path) -> None:
       write_text(
           run_dir / "approval_required.md",
           "# Task Graph Approval Required\n\n"
           "이 run은 요청을 분해만 했습니다(구현 없음).\n\n"
           "먼저 읽어 보세요:\n"
           "- approval_brief.md (사람이 읽는 실행 계획 요약)\n"
           "- 01_claude_decomposition.md\n"
           "- 02_codex_plan_review.md\n"
           "- task_graph.json\n\n"
           "이 계획을 승인하려면 재개 명령을 실행하세요(재개 실행 = 승인).\n"
           f"```powershell\n{resume_command_for(run_dir)}\n```\n",
       )
   ```
8. **통과 확인(dry-run + 바이트 패리티)**:
   - `python .\run.py --dry-run --workflow decompose --request "다층 기능 db+api+ui"` → `runs/<stamp>/`에 `approval_brief.md`·`checkpoint.json`(mode:"task_graph")·`01_*_prompt.md`·`01_*_command.json`·`02_*_prompt.md`·`02_*_command.json` 생성 확인.
   - **확장자 한정 비교(Global Constraint 1)**: Task 3에서 캡처한 decompose before 산출물과 이 run의 `*_command.json`·`*_prompt.md`만 SHA-256으로 짝지어 비교해 바이트 동일 확인(신규 `approval_brief.md`/`checkpoint.json`은 비교 목록에서 제외).
   - `checkpoint.json`에 `"mode": "task_graph"`, `"task_graph": "task_graph.json"`이 있는지 확인.
9. **커밋**: `decompose: approval_brief 렌더 + task_graph checkpoint + 재개 핸드오프`.

---

## Task 6 — cli.py: `--resume`를 checkpoint `mode`로 분기

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\cli.py` — import(라인 13~17), resume 디스패치(라인 95~98).
- **Test(수동)**: 세 개의 최소 checkpoint fixture로 분기 확인.

### Interfaces
- **Consumes**: `checkpoint.json`의 `mode`(없거나 `"routed_impl"`→기존, `"task_graph"`→실행기), `read_text`(라인 13에서 이미 import됨).
- **Produces**: resume 시 `run_task_graph_execution(args, config, run_dir)` 또는 기존 `resume_routed_workflow(args, config)` 호출.

**minor 지적 반영(중복 import 방지)**: cli.py 라인 16에 이미 `from autoagent.workflows.routed import resume_routed_workflow, run_routed_workflow`가 있으므로 **재import하지 않는다**. 새로 추가할 것은 딱 두 줄: `from autoagent.workflows.task_exec import run_task_graph_execution`과 `import json`. `read_text`는 라인 13에서 이미 import돼 있어 `resume_mode`가 그대로 쓴다.

### Steps

1. **실패 검증 작성**: 스크래치패드에 세 fixture run_dir을 만든다:
   ```
   <scratchpad>/rd_routed/checkpoint.json   → {"version":1,"mode":"routed_impl"}
   <scratchpad>/rd_task/checkpoint.json     → {"version":1,"mode":"task_graph"}
   <scratchpad>/rd_legacy/checkpoint.json   → {"version":1}  (mode 없음)
   ```
2. **실패 확인**: `resume_mode` 미정의 → `ImportError`.
3. **구현 — mode 판독 헬퍼 + 분기**:
   import 추가(라인 15~17 근처, **재import 금지**):
   ```python
   import json
   from autoagent.workflows.task_exec import run_task_graph_execution
   ```
   판독 헬퍼(모듈 레벨):
   ```python
   def resume_mode(run_dir: Path) -> str:
       """checkpoint.json의 mode를 읽어 재개 분기 키를 돌려준다.

       mode가 없거나 "routed_impl"이면 기존 routed 재개(하위호환 기본),
       "task_graph"이면 decompose 병렬 실행기로 간다.
       """
       checkpoint_path = run_dir / "checkpoint.json"
       if not checkpoint_path.exists():
           raise SystemExit(f"No checkpoint.json in {run_dir}; cannot resume.")
       checkpoint = json.loads(read_text(checkpoint_path))
       return checkpoint.get("mode") or "routed_impl"
   ```
   `main`의 라인 95~98 교체:
   ```python
       if args.resume:
           if args.request or args.request_file:
               raise SystemExit("--resume cannot be combined with --request/--request-file.")
           run_dir = Path(args.resume)
           mode = resume_mode(run_dir)
           if mode == "task_graph":
               return run_task_graph_execution(args, config, run_dir)
           return resume_routed_workflow(args, config)
   ```
   **주의**: `resume_mode`가 checkpoint 부재 시 던지는 `SystemExit` 메시지는 현행 `resume_routed_workflow`(라인 85~86)와 동일해 하위호환 유지. routed 경로는 `resume_routed_workflow`가 내부에서 checkpoint를 다시 읽지만(이중 읽기) 무해하다.
4. **통과 확인**: fixture로 `resume_mode` 직접 호출:
   ```python
   from pathlib import Path
   from autoagent.cli import resume_mode
   assert resume_mode(Path("<scratchpad>/rd_routed")) == "routed_impl"
   assert resume_mode(Path("<scratchpad>/rd_task")) == "task_graph"
   assert resume_mode(Path("<scratchpad>/rd_legacy")) == "routed_impl"
   print("OK")
   ```
5. **회귀 확인**: 기존 routed 게이트 run(mode 없는 checkpoint, `--require-human-approval`로 생성)에 `--resume --dry-run`을 걸어 `resume_routed_workflow`로 가고 정상 완료되는지 확인.
6. **커밋**: `cli: --resume를 checkpoint mode로 분기(task_graph→실행기, 없음/routed_impl→기존)`.

---

## Task 7b — task_exec.py: 파도 병렬 실행(worktree + Config 사본 + node out_dir + 코어 + scope 가드 + 커밋)

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\task_exec.py` — `run_task_graph_execution`에 파도 실행 루프 추가 + 신규 `run_node`/`_node_route`/`_node_common`.
- **Test(수동)**: dry-run 스모크(노드별 out_dir·Config 사본 workspace 확인).

### Interfaces
- **Produces**:
  ```python
  def run_node(*, args, config, task_graph, node, budget, run_dir, stamp, baseline) -> str
      # status 문자열: "done" | "failed" | "skipped" | "budget_stopped" 반환
  ```
- **Consumes**: `dataclasses.replace(config, workspace=<worktree>)`, `wt.add_worktree`, `run_impl_review_fix`(5-튜플 반환), `wt.scope_violations`, `route_task`.

**important 지적 반영(dry-run workspace 정합)**: 스펙 검증 #6은 "dry-run에서 노드용 Config 사본의 workspace가 각 worktree 경로로 세팅"됨을 요구한다. 이를 만족시키기 위해 **dry-run에서도 실제 git 없이 worktree 경로 문자열(`run_dir/worktrees/<id>`)로 `dataclasses.replace` Config 사본을 만들고 `common["WORKSPACE"]`에 반영**한다(worktree 디렉터리 자체는 생성하지 않음). 이로써 dry-run 산출물의 command.json/prompt에 worktree 경로가 나타나 스펙 #6을 dry-run으로 검증할 수 있다.

**minor 지적 반영(db/test/infra 노드)**: `_node_route`는 `db` 타입을 `backend`로 정규화한다(route_task가 db subtype/high-risk를 도출). `test`/`infra`/`docs`/`review` 타입은 `run_node`에서 skip하되, skip한 노드를 `skipped.md`에 기록하고 최종적으로 리포트에 미실행 목록으로 노출한다(Global Constraint 7).

### Steps

1. **실패 검증 작성(dry-run 노드 아티팩트)**: 스크래치패드 `check_node_dryrun.py` — 2노드 backend 그래프를 담은 run_dir로 `run_task_graph_execution(dry_run=True)` 실행 후, `nodes/n1/04_*_impl_prompt.md`·`nodes/n2/04_*_impl_prompt.md` 존재 + 각 노드 command.json/prompt의 WORKSPACE가 `worktrees/<id>` 경로를 담는지 + `worktrees/` 실제 디렉터리는 안 생겼는지(git 미호출) 확인.
2. **실패 확인**: `run_node` 미정의 → `ImportError`.
3. **구현 — 노드 route/common 구성 + run_node**:
   ```python
   def _node_route(node: dict[str, Any]) -> dict[str, Any]:
       # 노드 type/description으로 route를 파생하되, 그래프가 선언한 risk_level/subtype이 있으면 덮는다.
       # db 타입은 backend로 정규화(프롬프트 파일이 backend/frontend만 존재; route_task가 db subtype 도출).
       node_type = node.get("type", "backend")
       route_type = "backend" if node_type == "db" else node_type
       route = route_task(route_type, node.get("description", ""), "auto")
       if node.get("risk_level"):
           route["risk_level"] = node["risk_level"]
       if node.get("subtype"):
           route["subtype"] = node["subtype"]
       return route


   def _node_common(config: Config, node: dict[str, Any], route: dict[str, Any],
                    request: str, max_review_rounds: int) -> dict[str, Any]:
       # run_impl_review_fix가 프롬프트 렌더에 쓰는 공용 값. routed의 base_values/common 규약과 동일.
       # codex_final.md 등이 요구하는 CLAUDE_CONTEXT/CLAUDE_ARCHITECTURE/CODEX_VALIDATION도 채운다.
       return {
           "REQUEST": request,
           "WORKSPACE": str(config.workspace),
           "TASK_TYPE": route["task_type"],
           "ROUTE_JSON": json.dumps(route, ensure_ascii=False, indent=2),
           "MAX_REVIEW_ROUNDS": str(max(max_review_rounds, 0)),
           "CLAUDE_CONTEXT": node.get("description", ""),
           "CLAUDE_ARCHITECTURE": node.get("rationale", ""),
           "CODEX_VALIDATION": "\n".join(node.get("validation_commands") or []),
       }


   def run_node(
       *, args: Namespace, config: Config, task_graph: dict[str, Any],
       node: dict[str, Any], budget: AgentCallBudget, run_dir: Path, stamp: str, baseline: str,
   ) -> str:
       """노드 하나를 격리 worktree에서 구현→리뷰→수정 코어로 돌리고 status 문자열을 반환한다."""
       node_id = node.get("id")
       node_type = node.get("type", "")
       node_out = run_dir / "nodes" / str(node_id)

       # backend/frontend(및 backend로 정규화되는 db)만 레인. 그 외는 skip하고 미실행으로 기록.
       route_type = "backend" if node_type == "db" else node_type
       if route_type not in CODE_NODE_TYPES:
           write_text(node_out / "skipped.md",
                      f"타입 {node_type} 노드({node_id})는 현재 실행기가 구현하지 않습니다(미실행).\n")
           return "skipped"

       route = _node_route(node)
       goal = task_graph.get("goal", "")
       # dry/non-dry 모두 worktree 경로를 workspace로 삼은 Config 사본을 만든다(스펙 #6).
       worktree_path = run_dir / "worktrees" / str(node_id)
       node_config = dataclasses.replace(config, workspace=worktree_path)
       common = _node_common(node_config, node, route, goal, args.max_review_rounds)

       if not args.dry_run:
           branch = f"aa/{stamp}/{node_id}"
           wt.add_worktree(config.workspace, worktree_path, branch, baseline)

       try:
           implementation, review, fix, resolved, stopped = run_impl_review_fix(
               args=args, config=node_config, common=common, route=route,
               request=goal, budget=budget, run_dir=node_out,
           )
       except AgentCallBudgetStopped:
           # 예산 소진: 실제 실패와 구분해 budget_stopped로 표시(스펙 §113: pending으로 남겨 재개 대상).
           write_text(node_out / "node_budget_stopped.md", f"노드 {node_id}는 예산 소진으로 정지했습니다.\n")
           return "budget_stopped"
       except SystemExit as exc:
           write_text(node_out / "node_failed.md", f"노드 {node_id} 실행 실패: {exc}\n")
           return "failed"

       if not args.dry_run:
           # soft scope 가드: allowed_paths 밖/blocked_paths 안 변경을 플래그(차단 아님).
           violations = wt.scope_violations(
               config.workspace, worktree_path,
               node.get("allowed_paths") or [], node.get("blocked_paths") or [],
           )
           if violations:
               write_text(node_out / "scope_violations.md",
                          "# scope 위반\n\n" + "\n".join(f"- {v}" for v in violations) + "\n")
           # 레인 브랜치에 커밋(구현 산출을 병합 대상으로 고정). 변경 없으면 commit이 실패하나 무해.
           subprocess.run(["git", "-C", str(worktree_path), "add", "-A"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
           subprocess.run(["git", "-C", str(worktree_path), "commit", "-m", f"aa: node {node_id}"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
       return "done"
   ```
4. **구현 — 파도 루프를 run_task_graph_execution에 삽입**(7a의 `budget = AgentCallBudget(...)` 뒤, `return 0` 앞):
   ```python
       stamp = time.strftime("%Y%m%d_%H%M%S")
       baseline = "HEAD"
       by_id = {t.get("id"): t for t in tasks}
       failed = False
       budget_stopped = False
       for wave in waves:
           if budget_stopped:
               break  # 예산 소진 후 새 파도 시작 안 함(스펙 §333).
           results: dict[str, str] = {}
           with ThreadPoolExecutor(max_workers=max(config.max_parallel_lanes, 1)) as pool:
               futures = {}
               for node_id in wave:
                   node = by_id[node_id]
                   set_status(run_dir, task_graph, node_id, "in_progress")
                   futures[pool.submit(
                       run_node, args=args, config=config, task_graph=task_graph,
                       node=node, budget=budget, run_dir=run_dir, stamp=stamp, baseline=baseline,
                   )] = node_id
               for fut, node_id in futures.items():
                   results[node_id] = fut.result()  # run_node가 예외를 삼켜 문자열로 반환
           for node_id, status in results.items():
               if status in {"done", "skipped"}:
                   set_status(run_dir, task_graph, node_id, status)
               elif status == "budget_stopped":
                   # 예산 소진 노드는 pending으로 되돌려 재개 대상으로 남긴다(스펙 §113).
                   set_status(run_dir, task_graph, node_id, "pending")
                   budget_stopped = True
               else:  # "failed"
                   set_status(run_dir, task_graph, node_id, "failed")
                   failed = True
           if failed:
               break  # 실제 실패면 안전편향 정지: 다음 파도 시작 안 함(barrier에서 멈춤).
   ```
   (7c에서 `failed`/`budget_stopped` 시 통합 생략 + 하위 노드 처리를 채운다.)

   **important 지적 반영(budget_stopped ≠ failed)**: 예산 소진 노드는 `failed`로 마킹하지 않고 `pending`으로 되돌려 스펙 §113("실패/예산소진으로 미완인 노드는 pending으로 남아 재개 대상")과 정렬한다. `_mark_blocked_descendants`(7c)와 통합 생략은 **실제 `failed`에만** 적용하고, `budget_stopped`는 "새 파도 시작 안 함" 정지 경로로만 처리한다.
5. **통과 확인(dry-run)**: `check_node_dryrun.py` 실행 → (a) `nodes/n1/04_*_impl_prompt.md`·`nodes/n2/04_*_impl_prompt.md` 존재, (b) 각 노드 command.json/prompt의 WORKSPACE가 `worktrees/n1`·`worktrees/n2` 경로를 담음(스펙 #6), (c) `worktrees/` 실제 디렉터리는 안 생김(dry-run git 미호출), (d) `waves.txt`가 `[["n1"],["n2"]]` 순서를 반영.
6. **커밋**: `task_exec: 파도 병렬 실행(worktree+Config 사본+node out_dir+코어+scope 가드+커밋)`.

---

## Task 7c — task_exec.py: 통합 브랜치 병합(stop-and-report) + 정리 + 미실행 노드 리포트

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\task_exec.py` — 파도 루프 뒤 통합/정리/리포트 로직 추가.
- **Test(수동)**: 임시 git 레포 2노드 스모크(디스조인트 병합 성공 + 동일 파일 충돌 stop-and-report).

### Interfaces
- **Consumes**: `wt.create_integration_branch`, `wt.merge_branch`(→`MergeResult`), `wt.remove_worktree`, `wt.delete_branch`.
- **Produces**: 통합 worktree(`worktrees/_integration`) 안에서 순차 병합, 충돌 시 보존·리포트, 성공 시 레인 worktree/브랜치 정리(통합 브랜치 유지), skip 노드 미실행 목록 리포트.

### Steps

1. **실패 검증 작성(스모크)**: 스크래치패드 `check_merge_smoke.sh` — 임시 git 레포에서 baseline 커밋 후, `create_integration_branch` → 통합 worktree add → 두 레인 브랜치(디스조인트 파일 커밋)를 `merge_branch`로 병합해 `ok=True` 확인. 이어서 같은 파일을 두 레인이 바꾼 케이스 → `merge_branch`가 `ok=False`, `conflicts` 비어있지 않은지 확인.
2. **실패 확인**: 통합 로직 없이 `run_task_graph_execution`이 병합을 안 하므로 통합 브랜치 미생성.
3. **구현 — 통합/정리/blocked/미실행 헬퍼**(파도 루프 뒤에 삽입; dry-run은 통합/병합/정리 전부 건너뜀):
   ```python
   def _mark_blocked_descendants(run_dir: Path, task_graph: dict[str, Any]) -> None:
       # 실제 failed/미완 노드에 (직간접) 의존하는 미완 노드를 blocked로 표시한다.
       tasks = task_graph.get("tasks", [])
       bad = {t.get("id") for t in tasks if t.get("status") in {"failed", "in_progress"}}
       changed = True
       while changed:
           changed = False
           for t in tasks:
               if t.get("status") in {"done", "skipped", "blocked", "failed"}:
                   continue
               if any(dep in bad for dep in (t.get("dependencies") or [])):
                   t["status"] = "blocked"
                   bad.add(t.get("id"))
                   changed = True
       persist_status(run_dir, task_graph)


   def _write_skipped_report(run_dir: Path, tasks: list[dict[str, Any]]) -> None:
       # 승인했으나 미실행(skip)된 노드를 사람이 인지하도록 명시(Global Constraint 7).
       skipped = [t for t in tasks
                  if ("backend" if t.get("type") == "db" else t.get("type")) not in CODE_NODE_TYPES]
       if not skipped:
           return
       lines = ["# 미실행 노드 (승인했으나 실행기가 구현하지 않음)\n"]
       for t in skipped:
           lines.append(f"- **{t.get('id')}** (type={t.get('type')}): {t.get('title', '')}")
       lines.append("\n현재 실행기는 backend/frontend(및 db) 노드만 구현합니다.\n")
       write_text(run_dir / "skipped_nodes.md", "\n".join(lines))


   def _integrate_and_cleanup(
       args: Namespace, config: Config, run_dir: Path, tasks: list[dict[str, Any]],
       stamp: str, baseline: str, failed: bool, budget_stopped: bool,
   ) -> tuple[bool, str]:
       """완료 레인을 통합 브랜치로 순차 병합하고 성공 시 정리한다. (통합성공여부, 통합브랜치명) 반환."""
       integration_branch = f"aa/{stamp}"
       if failed or budget_stopped:
           # 안전편향: 실패/예산소진/블록이 있으면 통합하지 않고 전체 보존.
           reason = "실패 노드" if failed else "예산 소진"
           write_text(run_dir / "integration_report.md",
                      f"# 통합 생략(안전편향 정지: {reason})\n\n"
                      "통합 병합을 하지 않고 worktree와 레인 브랜치를 모두 보존합니다.\n")
           return False, integration_branch

       # 통합 worktree를 baseline에서 만들고 그 안에서 순차 병합(레인 브랜치를 위상 순으로).
       wt.create_integration_branch(config.workspace, integration_branch, baseline)
       integ_wt = run_dir / "worktrees" / "_integration"
       proc = subprocess.run(
           ["git", "-C", str(config.workspace), "worktree", "add", str(integ_wt), integration_branch],
           capture_output=True, text=True, encoding="utf-8", errors="replace",
       )
       if proc.returncode != 0:
           write_text(run_dir / "integration_report.md",
                      f"# 통합 worktree 생성 실패\n\n{proc.stderr.strip() or proc.stdout.strip()}\n")
           return False, integration_branch

       merged: list[str] = []
       done_ids = [t.get("id") for t in tasks
                   if t.get("status") == "done"
                   and ("backend" if t.get("type") == "db" else t.get("type")) in CODE_NODE_TYPES]
       for node_id in done_ids:
           result = wt.merge_branch(integ_wt, f"aa/{stamp}/{node_id}")
           if not result.ok:
               write_text(run_dir / "integration_report.md",
                          "# 통합 병합 충돌(수동 병합 필요)\n\n"
                          f"- 충돌 브랜치: aa/{stamp}/{node_id}\n"
                          f"- 충돌 파일: {result.conflicts}\n"
                          f"- 이미 병합된 노드: {merged}\n\n"
                          "worktree/레인 브랜치를 보존합니다. 수동 병합 후 다시 진행하세요.\n")
               return False, integration_branch
           merged.append(node_id)

       write_text(run_dir / "integration_report.md",
                  f"# 통합 성공\n\n- 통합 브랜치: {integration_branch}\n- 병합 노드: {merged}\n")
       return True, integration_branch


   def _cleanup_lanes(config: Config, run_dir: Path, tasks: list[dict[str, Any]], stamp: str) -> None:
       # 성공 정리: 레인 worktree 제거 + 레인 브랜치 삭제. 통합 브랜치는 남긴다(사람 리뷰 대상).
       for t in tasks:
           if ("backend" if t.get("type") == "db" else t.get("type")) not in CODE_NODE_TYPES:
               continue
           node_id = t.get("id")
           wt.remove_worktree(config.workspace, run_dir / "worktrees" / str(node_id))
           wt.delete_branch(config.workspace, f"aa/{stamp}/{node_id}")
       wt.remove_worktree(config.workspace, run_dir / "worktrees" / "_integration")
   ```
4. **구현 — run_task_graph_execution에 연결**(파도 루프 뒤):
   ```python
       _write_skipped_report(run_dir, tasks)

       if args.dry_run:
           write_text(run_dir / "final_report.md", "# Task Graph dry-run 완료\n\n노드 프롬프트만 렌더했습니다.\n")
           print(f"Task graph dry-run complete: {run_dir}")
           return 0

       if failed:
           _mark_blocked_descendants(run_dir, task_graph)

       integrated, integration_branch = _integrate_and_cleanup(
           args, config, run_dir, tasks, stamp, baseline, failed, budget_stopped,
       )
       if not integrated:
           write_text(run_dir / "final_report.md",
                      "# Task Graph 실행 정지\n\n통합하지 못했습니다. integration_report.md를 보세요.\n"
                      f"worktree/브랜치를 보존합니다: {run_dir / 'worktrees'}\n")
           print(f"Task graph run stopped without integration: {run_dir}")
           return 0

       # 7d에서 통합 평가/리포트를 채운 뒤 정리한다.
       return 0
   ```
5. **통과 확인(스모크)**: `check_merge_smoke.sh` 실행 → 디스조인트 케이스 `ok=True`, 충돌 케이스 `ok=False`+`conflicts` 비어있지 않음. skip 노드가 있는 그래프(docs 노드 포함)로 `_write_skipped_report`가 `skipped_nodes.md`를 남기는지 확인.
6. **커밋**: `task_exec: 통합 브랜치 병합(stop-and-report) + 성공 정리 + 미실행 노드 리포트`.

---

## Task 7d — task_exec.py: 통합 트리 최종리뷰/평가/리포트(run 1회) + 최종 정리

### Files
- **Modify**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\task_exec.py` — 통합 성공 후 꼬리 추가.
- **Modify(선택, DRY)**: `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\routed_impl.py` — 최종리뷰(07) 단계를 `run_final_review` 헬퍼로 추출해 routed_impl과 task_exec가 공유.
- **Test(수동)**: dry-run 관통(노드 프롬프트 파도 순서) + 통합 꼬리 헬퍼 재사용 확인.

### Interfaces
- **Consumes**: `run_evaluation`, `run_final_report`(둘 다 `routed_common`), `dataclasses.replace(config, workspace=integ_wt)`, `AgentCallBudgetStopped`.
- **Produces**: 통합 트리에 대해 run 1회 최종리뷰(codex, 07)→`run_evaluation`(08)→`run_final_report`(09), 그 뒤 `_cleanup_lanes` 호출. 전체 `run_task_graph_execution` 본문을 `try/except AgentCallBudgetStopped`로 감싸 꼬리 예산 소진도 안전 정지.

**minor 지적 반영(DRY — 07 로직 중복)**: 스펙 §180/§222는 "기존 routed 꼬리 헬퍼 재사용"을 요구한다. 최종리뷰(07)를 task_exec에서 손으로 재조립하면 routed_impl:108~139와 이중화되므로, **Task 3에서 남겨둔 routed_impl의 07 블록을 `run_final_review` 헬퍼로 추출**(선택이지만 권장)하고 routed_impl과 task_exec가 동일 헬퍼를 쓴다. 이렇게 하면 dry-run 분기(`args.dry_run` 시 프롬프트만 렌더)도 한 곳에만 존재한다.

`run_final_review` 시그니처(routed_impl.py 라인 108~139를 그대로 감싼 것, 반환은 final_review 문자열):
```python
def run_final_review(
    *, args: Namespace, config: Config, common: dict[str, Any], route: dict[str, Any],
    request: str, budget: AgentCallBudget, run_dir: Path,
    implementation: str, review: str, fix: str, name: str = "07_codex_final_review",
) -> str:
    """codex 최종리뷰(07). dry-run이면 프롬프트/커맨드만 렌더하고 [dry-run] 문자열 반환.

    routed_impl의 기존 07 로직(라인 108~139)을 그대로 옮긴 것으로, routed와 실행기가 공유한다.
    바이트 패리티: name 기본값·프롬프트 값·resolve_role 인자가 원본과 동일해야 한다.
    """
    roles = load_roles(DEFAULT_CONFIG.parent)
    final_review_role = resolve_role(
        roles["final-review"], config=config, route=route, request=request,
        agent="codex", read_only=args.read_only,
    )
    final_review_prompt = render_template(
        "codex_final.md",
        {**common, "IMPLEMENTATION_RESULT": implementation, "REVIEW_RESULT": review, "FIX_RESULT": fix},
    )
    if args.dry_run:
        write_text(run_dir / f"{name}_prompt.md", final_review_prompt)
        write_command_artifact(run_dir, name, command_for_agent(config, final_review_role))
        return "[dry-run: Codex final review output]"
    codex = require_command(config.codex_command)
    budget.before_call(next_step="final-review", out_dir=run_dir, dry_run=args.dry_run)
    result = run_process(
        name=name, command=command_for_agent(config, final_review_role, resolved_command=codex),
        prompt=final_review_prompt, cwd=config.workspace, out_dir=run_dir,
        timeout_seconds=config.timeout_seconds,
    )
    write_text(run_dir / f"{name}.md", result)
    return result
```
routed_impl의 `run_implementation_route` 꼬리에서 라인 108~139를 이 헬퍼 호출로 대체하되, **바이트 패리티 대상**이므로 원본과 동일한 `name="07_codex_final_review"`·프롬프트 값·`resolve_role` 인자를 유지하고, `stop_after("final-review")` 호출은 헬퍼 밖(호출부)에 그대로 둔다. 이 대체 자체가 Task 3의 바이트 비교(확장자 한정) 대상에 포함되므로, Task 3 검증을 이 추출 이후 재실행해 07 산출물이 before와 동일함을 확인한다. **추출을 하지 않기로 하면**, task_exec는 위 헬퍼 본문과 동일한 인라인 코드를 쓰되 07 로직 복제를 감수한다(권장하지 않음).

### Steps

1. **실패 검증 작성**: 스크래치패드 `check_tail_dryrun.py` — 임시 디렉터리를 통합 worktree로 흉내 낸 workspace로 한 Config 사본으로 `run_final_report(... dry_run=True ...)`가 `09_claude_final_report_prompt.md`를 쓰는지, `run_final_review(... dry_run=True ...)`가 `07_codex_final_review_prompt.md`를 쓰는지 확인(꼬리 헬퍼 재사용 가능성 검증).
2. **실패 확인**: 통합 후 평가/리포트 미호출 → 산출물 없음.
3. **구현 — 통합 꼬리**(Task 7c의 `# 7d에서 ...` 자리 대체):
   ```python
       # 통합 트리에 대해 run 레벨 1회: 최종리뷰(codex 07) → 평가(codex 08) → 최종보고(claude 09).
       integ_wt = run_dir / "worktrees" / "_integration"
       integ_config = dataclasses.replace(config, workspace=integ_wt)
       run_route = route_task("backend", task_graph.get("goal", ""), "auto")
       common = {
           "REQUEST": task_graph.get("goal", ""),
           "WORKSPACE": str(integ_wt),
           "TASK_TYPE": run_route["task_type"],
           "ROUTE_JSON": json.dumps(run_route, ensure_ascii=False, indent=2),
           "MAX_REVIEW_ROUNDS": str(max(args.max_review_rounds, 0)),
           "CLAUDE_CONTEXT": task_graph.get("goal", ""),
           "CLAUDE_ARCHITECTURE": "\n".join(t.get("title", "") for t in tasks),
           "CODEX_VALIDATION": "\n".join(
               cmd for t in tasks for cmd in (t.get("validation_commands") or [])
           ),
       }

       from autoagent.workflows.routed_impl import run_final_review  # 지연 import(순환 회피)
       final_review = run_final_review(
           args=args, config=integ_config, common=common, route=run_route,
           request=common["REQUEST"], budget=budget, run_dir=run_dir,
           implementation="통합 브랜치 트리", review="-", fix="-",
       )
       evaluation = run_evaluation(
           args, integ_config, common, budget, run_dir,
           name="08_codex_evaluation",
           implementation="통합 브랜치 트리", review="-", fix="-", final_review=final_review,
       )
       final = run_final_report(
           args, integ_config, common, budget, run_dir,
           name="09_claude_final_report",
           implementation="통합 브랜치 트리", review="-", fix="-",
           final_review=final_review, evaluation=evaluation,
       )
       write_text(run_dir / "final_report.md", final)

       _cleanup_lanes(config, run_dir, tasks, stamp)
       print(f"Task graph execution complete: {run_dir} (통합 브랜치 {integration_branch})")
       return 0
   ```
   `run_evaluation`/`run_final_report`/`run_final_review`는 내부에서 `args.dry_run` 분기를 이미 갖지만, 통합 꼬리는 `if args.dry_run: return 0`(7c) 뒤에 오므로 dry-run에서는 도달하지 않는다(꼬리는 non-dry 전용).
4. **구현 — 예산 예외 래핑**: `run_task_graph_execution` 본문 전체를 `try/except AgentCallBudgetStopped`로 감싼다(꼬리 07/08/09의 `before_call`이 던지는 경우 처리). 파도 루프는 이미 `budget_stopped`를 문자열로 잡아 정지하므로, 여기서 잡히는 건 꼬리 소진뿐:
   ```python
       try:
           # (load_task_graph ~ 통합 꼬리 전체)
           ...
       except AgentCallBudgetStopped as stopped:
           write_text(run_dir / "final_report.md",
                      f"# 예산 소진 정지\n\nbefore {stopped.next_step}에서 예산이 소진돼 정지했습니다. "
                      "--resume로 이어갈 수 있습니다.\n")
           print(f"Task graph run stopped by budget before {stopped.next_step}: {run_dir}")
           return 0
   ```
5. **codex_final.md placeholder 커버리지 확인(minor 지적 반영)**: `codex_final.md`가 요구하는 placeholder는 `{{WORKSPACE}}`·`{{REQUEST}}`·`{{ROUTE_JSON}}`·`{{CLAUDE_CONTEXT}}`·`{{CLAUDE_ARCHITECTURE}}`·`{{CODEX_VALIDATION}}`·`{{IMPLEMENTATION_RESULT}}`·`{{REVIEW_RESULT}}`·`{{FIX_RESULT}}`이다(실제 템플릿 확인 완료). 통합 꼬리의 `common`이 이 중 앞 6개를, `run_final_review`가 뒤 3개를 채우므로 누락이 없다. 검증 스텝: dry-run 관통 후 `07_codex_final_review_prompt.md`를 grep해 미치환 `{{` 토큰이 남지 않았는지 확인한다(`grep -c "{{" 07_codex_final_review_prompt.md` → 0). 마찬가지로 노드 04/05/06 프롬프트도 미치환 토큰 0을 확인한다.
6. **통과 확인(dry-run 관통)**: 게이트까지 간 decompose run에 `--resume --dry-run`을 걸어 관통:
   ```
   python .\run.py --resume "<게이트 run_dir>" --dry-run
   ```
   확인: (a) `nodes/<id>/04_*_impl_prompt.md`·`*_command.json`이 파도 순서대로 존재, (b) 각 노드 command.json/prompt의 WORKSPACE가 `worktrees/<id>` 경로(스펙 #6), (c) `worktrees/` 실제 디렉터리·git 미호출, (d) dry-run은 `if args.dry_run: return 0`(7c)에서 끝나므로 07/08/09 꼬리는 실행되지 않음 — dry 관통 검증은 노드 산출물과 WORKSPACE에 집중한다.
7. **status 재개 확인**: `done` 노드가 있는 task_graph로 dry 재개 시 `topological_waves`가 그 노드를 배제하고 남은 파도만 렌더하는지 확인(7a `check_waves.py` done 케이스가 커버; 여기선 실제 run_dir로 재확인).
8. **soft scope 가드 확인**: Task 4의 임시 git 레포에 allowed 밖 파일을 변경·커밋한 뒤 `scope_violations`가 위반 리스트를 반환하는지 `python -c`로 확인.
9. **최종리뷰(07) 바이트 패리티 재확인**: `run_final_review` 추출로 routed_impl 꼬리가 바뀌었으므로, Task 3의 확장자 한정 바이트 비교(특히 07 산출물 및 stop-after 조합)를 재실행해 before와 동일함을 확인한다.
10. **커밋**: `task_exec: 통합 트리 최종리뷰/평가/리포트(run 1회) + run_final_review 공용 헬퍼 + 성공 정리`.

---

## 최종 통합 검증(전체 태스크 완료 후)

1. **바이트 패리티 회귀(확장자 한정, 전 워크플로)**: `routed`(backend/frontend, `--stop-after` 각 단계 포함 `implementation`/`review` × `--max-review-rounds 0`, `--max-review-rounds 2`, high-risk 요청 `add auth migration`), `simple`, `decompose` dry-run을 Task 3 이전 baseline과 **`*_command.json`·`*_prompt.md` 파일만** SHA-256 비교해 바이트 동일. `--stop-after review --max-review-rounds 0`에서 07/08/09·final_report.md가 before/after 동일 존재하는지 특히 확인(critical 케이스).
2. **resume 분기**: `mode` 없음/`routed_impl` → `resume_routed_workflow`, `task_graph` → `run_task_graph_execution` 확인(fixture + 실제 게이트 run).
3. **실행기 dry-run 관통**: decompose 게이트 → `--resume --dry-run`이 `nodes/<id>/` 프롬프트를 파도 순서로 렌더, 각 노드 WORKSPACE=worktree 경로, worktree/git 미호출, 미치환 `{{` 토큰 0.
4. **비-dry 스모크(가능 시)**: 소형 타깃에서 2노드 디스조인트 그래프로 worktree 2개 생성 → 통합 병합 → 정리 관통. 동일 파일 두 레인 케이스로 stop-and-report 발동 확인.
5. **불변식 확인**: 노드 route의 `implementation_agent`/`review_agent`가 항상 반대 모델(`choose_implementer` 계약), main·push 미호출(`git log`로 통합 브랜치만 생성됐는지 확인), 전역 `config.workspace` 미변형(노드는 `dataclasses.replace` 사본 사용).
6. **미실행 노드 노출**: docs/review/test/infra 노드가 섞인 그래프로 `skipped_nodes.md`가 미실행 목록을 명시하는지 확인(Global Constraint 7).

## 관련 파일 경로(절대)

- 스펙: `C:\Users\systran\Desktop\AutoAgent\docs\specs\2026-07-12-decompose-parallel-executor-design.md`
- 수정: `C:\Users\systran\Desktop\AutoAgent\autoagent\config.py`, `...\autoagent\runner.py`, `...\autoagent\workflows\routed_impl.py`, `...\autoagent\workflows\decompose.py`, `...\autoagent\cli.py`
- 신규: `C:\Users\systran\Desktop\AutoAgent\autoagent\worktree.py`, `C:\Users\systran\Desktop\AutoAgent\autoagent\workflows\task_exec.py`
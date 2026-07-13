# AutoAgent 설계 spec: task_graph 순차 실행 워크플로우 + 사람이 읽는 승인 브리핑

작성일: 2026-07-09
대상: AutoAgent `decompose` 워크플로우 후속(실행 단계) + `--resume` 확장
상태: **superseded by `2026-07-12-decompose-parallel-executor-design.md`**
  (이 순차 초안 자체는 미구현. 후속 병렬 설계가 2026-07-12 구현·병합됨 — PR #12)
  — 후속 설계가 이 순차 실행기를 의존성 wavefront 병렬로 일반화하며
  (`max_parallel_lanes=1`이면 이 순차 설계와 동치), 이 문서의 `approval_brief`·
  task별 `status` 영속·soft scope 가드·`mode` 디스패치 규약을 그대로 흡수한다.
선행 문서: `2026-07-09-review-loop-and-approval-resume-design.md`(Feature A 반복 루프 / Feature B 재개)

## 1. 배경

원래 컨셉의 핵심 원칙 중 **"큰 요청은 task_graph로 분해한다 / 작은 task 단위로 순차 실행한다 / Human이 task graph를 승인한다"** 가 있으나, 실행 단계가 코드에 없다.

`decompose.py` 실측:
- `01` Claude 분해 → `task_graph.json` 생성. 각 task는 `id`·`title`·`type`·`description`·`allowed_paths`·`blocked_paths`·`expected_files`·`validation_commands`·`dependencies`·`risk_level`·`approval_required`·`status:"pending"` 등 **실행을 전제한 필드**를 이미 갖고 있다.
- `02` Codex plan 리뷰(read-only).
- `write_approval_required`가 직접 명시: **"Next phase is not implemented yet. Approve the task graph manually before running future task execution workflow."** → 정지(return 0).

즉 스키마는 실행을 염두에 두고 만들어졌는데 **그것을 소비하는 실행 엔진이 비어 있다.** 또한 현재 `approval_required.md`는 파일명만 나열해, 사람이 승인하려면 `task_graph.json` 원본을 직접 열어야 하는 UX 문제가 있다.

## 2. 목표 / 비목표

**목표**
- 승인된 `task_graph.json`을 **의존성 순서로 순차 실행**하는 워크플로우를 추가한다.
- 각 task를 routed의 `implement → review → fix`(선행 문서 Feature A 루프)로 실행하고, **리뷰어는 구현자와 다른 모델**을 유지한다.
- 사람의 승인 시점에 **JSON 대신 읽을 수 있는 "승인 브리핑"** 을 자동 렌더해 제시한다(그래프 단위 + high-risk task 단위).
- 진입/승인은 선행 문서의 **`--resume` 단일 경로**를 재사용·확장한다(블랭킷 우회 없음).
- 모든 산출물/상태 변화를 `runs/`에 남긴다.

**비목표**
- 병렬 실행(순차만). 의존성 위상정렬 후 직렬 처리.
- 하드 파일 샌드박스(에이전트의 쓰기를 OS 수준에서 강제). 범위 밖 — 아래는 "프롬프트 주입 + 사후 git diff 검사"의 소프트 가드.
- task 자동 재분해(실행 중 task가 다시 쪼개지는 재귀). 이번 범위 아님.

## 3. 승인 UX — 사람이 읽는 승인 브리핑 (핵심)

사람은 `task_graph.json` 원본을 열지 않는다. AutoAgent가 **task_graph.json을 결정론적으로 마크다운으로 렌더**한 `approval_brief.md`를 읽고 승인한다(별도 에이전트 호출 없음 → JSON과 항상 일치, 비용 0).

### 3.1 그래프 단위 브리핑 (`approval_brief.md`)
decompose 종료 시 `approval_required.md`를 대체/보강해 아래를 포함한다.
- **목표(goal)** 와 전체 요약, 그래프 `risk_level`.
- **실행 순서표**(위상정렬된 순서): `순번 | id | title | type | risk | 대상 경로(allowed_paths) | 의존성`.
- 각 task의 **description·rationale**(한두 줄).
- **high-risk / approval_required task 강조**(별도 섹션에 모아 표시).
- **검증 명령(validation_commands)** 목록.
- 하단에 명시적 안내: *"이 계획대로 진행하려면 `python run.py --resume <이 run_dir>` 를 실행하세요. 특정 task를 빼거나 고치려면 task_graph.json을 수정한 뒤 재실행하세요."*

### 3.2 task 단위 브리핑(high-risk 정지 시)
실행 중 high-risk 또는 `approval_required: true` task 직전에 정지하며 `runs/<dir>/pending_task_<id>_brief.md`를 쓴다.
- 그 task의 title·description·rationale·대상 경로·검증 명령·리스크 사유.
- 안내: *"이 task를 승인하려면 `--resume <run_dir>` 재실행. 건너뛰려면 task_graph.json에서 해당 task status를 `skipped`로 두고 재실행."*

## 4. 실행 흐름

### 4.1 진입 — `--resume` 확장(런 타입 디스패치)
선행 문서에서 `--resume <run_dir>`는 routed 게이트 run을 구현 단계로 이어갔다. 이제 `--resume`가 **run 종류를 판별**해 분기한다.
- `checkpoint.json`의 `mode` 필드로 구분한다.
  - `mode: "routed_impl"` → 기존 `run_implementation_route`(선행 문서).
  - `mode: "task_graph"` → 신규 `run_task_graph_execution`(이 문서).
- decompose 종료 시에도 `checkpoint.json`(`mode: "task_graph"`, `task_graph_path`, `workspace`, `config_path`)을 저장하도록 `decompose.py`를 보강한다.

### 4.2 순차 실행 엔진 (`run_task_graph_execution`)
1. `task_graph.json` 로드 → `dependencies`로 **위상정렬**(순환이면 오류·중단).
2. `status`가 `done`이 아닌 첫 task부터 순서대로:
   - **high-risk/approval_required 이고 아직 미승인**이면 → §3.2 브리핑 + checkpoint 저장 후 **정지**(사람이 `--resume`로 재개 = 그 task 승인).
   - task용 `common` 구성(그래프 goal + task description·rationale·allowed/blocked_paths를 프롬프트 값으로 주입).
   - `run_implementation_route`(task의 `type`으로 라우팅, implement→review→fix 루프, 리뷰어≠구현자)를 그 task에 대해 실행.
   - `validation_commands` 실행 → 결과 기록.
   - **soft scope 가드**: task 후 `git diff --name-only`가 `allowed_paths` 밖(또는 `blocked_paths`)을 건드렸으면 `scope_violation`으로 기록·플래그.
   - task `status`를 `done`/`failed`로 갱신하고 `task_graph.json`을 다시 쓴다(진행 상황 영속).
3. task가 리뷰/검증에서 미해결 실패 → 해당 task `failed`, **그 task에 의존하는 하위 task는 실행하지 않음**(`blocked`로 표시), 리포트에 명시.

### 4.3 상태 · 재개
- `status` 전이: `pending → in_progress → done | failed | blocked | skipped`.
- `--resume`를 다시 실행하면 `done` 이후(또는 정지했던 high-risk task)부터 이어간다 → 자연스러운 재개.
- 최종 `final_report.md`: 총 task 수, done/failed/blocked/skipped 분포, scope_violation 목록, 남은 작업.

## 5. 영향 파일
- `autoagent/workflows/decompose.py` — 종료 시 `approval_brief.md`(§3.1) 렌더 + `checkpoint.json`(`mode:"task_graph"`) 저장.
- `autoagent/workflows/routed_common.py` 또는 신규 `autoagent/workflows/task_exec.py` — 브리핑 렌더러(`render_task_graph_brief`), `run_task_graph_execution`.
- `autoagent/workflows/routed.py`(또는 `cli.py`) — `--resume`의 `mode` 디스패치.
- `autoagent/workflows/routed_impl.py` — task 단위 호출을 위해 `run_implementation_route`를 재사용(필요 시 task별 `common`/라우팅 주입 지점만 소폭 조정).
- (프롬프트) task 실행용 값에 `allowed_paths`/`blocked_paths`/`task_description` 주입 — 기존 impl 템플릿에 placeholder 추가 검토.

## 6. 엣지 케이스 / 리스크
- **의존성 순환**: 위상정렬 실패 시 즉시 중단·명확한 오류.
- **task_graph 수동 편집 후 재개**: 사람이 task를 빼거나 status를 바꾼 뒤 `--resume` → 엔진은 현재 status 기준으로 재개(사람 편집을 신뢰).
- **scope 위반**: soft 가드라 막지는 못하고 플래그만 — 리포트 상단에 강조, 필요 시 사람이 되돌림.
- **부분 실패 후 재개**: `failed` task를 사람이 `pending`으로 되돌리면 재시도됨(수동 제어).
- **예산 소진**: task 중간에 `AgentCallBudgetStopped` → 현재까지 status 저장 후 안전 종료, `--resume`로 이어감.

## 7. 테스트 계획
- **브리핑 렌더(dry 무관)**: 샘플 `task_graph.json` → `render_task_graph_brief`가 순서표·high-risk 섹션·검증 명령을 담은 마크다운을 생성하는지(문자열 단위 검증).
- **위상정렬**: 의존성 있는 그래프의 실행 순서, 순환 시 오류.
- **디스패치**: `checkpoint.json`의 `mode`에 따라 `--resume`가 routed_impl / task_graph로 분기하는지.
- **상태 전이/재개(dry-run)**: 3-task 그래프를 dry로 실행 → status가 순서대로 갱신되고, high-risk task에서 정지, 재개 시 이어지는지.
- **soft scope 가드**: allowed_paths 밖 변경을 흉내 낸 스텁으로 scope_violation 플래그 확인.

## 8. 롤아웃 순서
1. `render_task_graph_brief` + decompose 종료 시 `approval_brief.md`·`checkpoint.json(mode)` 저장 (사람 UX 즉시 개선, 실행 엔진과 독립).
2. `--resume` `mode` 디스패치.
3. `run_task_graph_execution` — 위상정렬 + 순차 루프 + status 영속.
4. task 단위 `run_implementation_route` 재사용 + task용 `common`/프롬프트 주입.
5. high-risk task 단위 정지·브리핑(§3.2).
6. soft scope 가드 + validation_commands 실행.
7. dry-run/스텁 테스트로 검증.

# MCP 통합 구현 계획 (증분 1: Claude `--allowedTools` 주입)

> **에이전트 워커용**: 이 계획은 `superpowers:subagent-driven-development` 또는
> `executing-plans`로 태스크 단위 실행. 스텝은 `- [ ]` 체크박스.

**목표**: 하네스가 Claude 서브프로세스에 MCP 툴 allowlist(`--allowedTools`)를 주입해,
읽기 역할(architect·reviewer 등 `plan` 모드)에서도 MCP 툴을 쓸 수 있게 한다.
2026-07-13 실측(설계문서 §6.3)에서 **필요충분**으로 확정된 최소 변경이다.

**아키텍처**: `config.mcp_allowed_tools`(기본 `[]`) → `command_for_agent` → `claude_command`가
비어있지 않을 때만 `--allowedTools`를 붙인다. **기본 빈 리스트면 플래그가 안 붙어 기존
명령과 바이트 동일**(opt-in). Codex는 `--ask-for-approval never`로 이미 MCP를 쓰므로 무변경.

**Tech Stack**: Python 3, dataclasses, argparse(변경 없음). 검증은 `--dry-run` 바이트 패리티.

## Global Constraints (스펙에서 그대로)
- 모든 `.md`·코드 주석은 **한국어**(식별자·플래그·JSON 키·enum 값만 영문).
- **바이트 패리티 불변식**: `mcp_allowed_tools`가 비어 있으면(기본) routed/simple/decompose
  `--dry-run`의 `*_command.json`·`*_prompt.md`가 리팩터 전과 **SHA-256 동일**해야 한다.
- **크로스모델 불변식 유지**: 리뷰어=구현자 반대 모델. 이 변경은 Claude 명령에만 영향.
- `--allowedTools` 값 포맷은 실측에서 통과한 형태(공백 구분 단일 인자,
  예: `--allowedTools "mcp__serena mcp__context7"`). 툴 1개는 `mcp__server__tool`,
  서버 전체는 `mcp__server` 와일드카드.
- MCP 서버 **발견**은 이 증분에선 타깃 레포의 `.mcp.json` 자동 로드에 맡긴다
  (하네스 생성 `--mcp-config`는 증분 2). 이 증분은 **allowlist 주입만** 담당.

## 비목표 (증분 2로 미룸) — 이후 진행 상황
- ~~하네스가 config 생성·`--mcp-config`/`--strict-mcp-config` 주입~~ → **증분 2a 구현됨(2026-07-13)**:
  `config.mcp_servers` → `.aa_mcp.json` 생성 후 `--mcp-config <경로> --strict-mcp-config` 주입.
- ~~Codex `config.toml` 서버 목록과의 시작 시 대칭 검사~~ → **증분 2b 구현됨(2026-07-13)**:
  `autoagent/mcp.py`의 `check_mcp_symmetry`가 시작 시 경고(차단 아님).
- 네트워크 MCP ↔ Codex 샌드박스 정렬(설계문서 §6.3 미검증 항목) → **증분 2c, 미구현**
  (네트워크 MCP 스모크 선행 필요; 이 머신엔 `uvx`/`uv` 없음).

## 파일 구조
- `autoagent/config.py` — `Config.mcp_allowed_tools: list[str]` 필드 + 로드.
- `autoagent/runner.py` — `claude_command(..., allowed_tools=None)` 파라미터 + 플래그.
- `autoagent/workflows/routed_impl.py` — `command_for_agent`가 `config.mcp_allowed_tools` 전달.
- `autoagent/workflows/decompose.py`, `autoagent/workflows/simple.py` — 직접 `claude_command`
  호출부에 `allowed_tools=config.mcp_allowed_tools` 전달(기본 빈 리스트라 무영향).
- `autoagent.config.json`(gitignore) + `README.md` — `mcp_allowed_tools` 문서화.

---

## Task 1 — config.py: `mcp_allowed_tools` 필드

**Files**: Modify `autoagent/config.py`

**Interfaces**
- Produces: `Config.mcp_allowed_tools: list[str]`(기본 `[]`).

- [ ] **Step 1**: `Config` 데이터클래스 맨 끝(`max_parallel_lanes` 다음)에 필드 추가.
  `field`가 필요하므로 상단 import 확인(`from dataclasses import dataclass, field`).
```python
    max_parallel_lanes: int = 2
    mcp_allowed_tools: list[str] = field(default_factory=list)  # Claude에 주입할 MCP 툴 allowlist
```
- [ ] **Step 2**: `load_config`의 `Config(...)` 생성에 로드 추가(리스트가 아니면 빈 리스트).
```python
        max_parallel_lanes=int(raw.get("max_parallel_lanes") or 2),
        mcp_allowed_tools=list(raw.get("mcp_allowed_tools") or []),
```
- [ ] **Step 3**(검증): 스크래치패드에서 `load_config`로 기본/샘플 로드.
  기본 config(키 없음) → `mcp_allowed_tools == []`; `{"mcp_allowed_tools":["mcp__serena"]}` →
  `["mcp__serena"]` 확인.
- [ ] **Step 4**: 커밋 `config: mcp_allowed_tools 필드 추가(기본 빈 리스트)`.

## Task 2 — runner.py: `claude_command`에 `--allowedTools`

**Files**: Modify `autoagent/runner.py`

**Interfaces**
- Consumes: 호출부가 넘기는 `allowed_tools: list[str] | None`.
- Produces: `allowed_tools`가 비어있지 않을 때만 `--allowedTools "<공백결합>"` 포함한 명령.

- [ ] **Step 1**: `claude_command` 시그니처에 `allowed_tools: list[str] | None = None` 추가(맨 끝).
- [ ] **Step 2**: `--effort` 처리 뒤, `--input-format text` 앞에 삽입.
```python
    if allowed_tools:
        # MCP 툴 allowlist. 헤드리스 plan 모드에서 승인 TTY가 없어 allowlist 없으면
        # MCP 툴 호출이 거부된다(설계문서 §6.3 실측). 공백 구분 단일 인자.
        command.extend(["--allowedTools", " ".join(allowed_tools)])
    command.extend(["--input-format", "text"])
```
- [ ] **Step 3**(검증): `claude_command("claude.cmd","sonnet","plan")` → `--allowedTools` **없음**;
  `claude_command("claude.cmd","sonnet","plan", allowed_tools=["mcp__serena","mcp__context7"])`
  → `["--allowedTools","mcp__serena mcp__context7"]` 포함, 위치는 `--input-format` 앞.
- [ ] **Step 4**: 커밋 `runner: claude_command에 allowed_tools(--allowedTools) 옵션 추가`.

## Task 3 — 호출부 배선 (command_for_agent + 직접 호출부)

**Files**: Modify `autoagent/workflows/routed_impl.py`, `autoagent/workflows/decompose.py`,
`autoagent/workflows/simple.py`

**Interfaces**
- Consumes: `config.mcp_allowed_tools`(Task 1), `claude_command(..., allowed_tools=)`(Task 2).

- [ ] **Step 1**: `command_for_agent`(routed_impl.py)의 claude 분기에 전달.
```python
    if resolved.agent == "claude":
        return claude_command(
            resolved_command or config.claude_command,
            resolved.model,
            resolved.permission_mode,
            resolved.effort,
            skip_permissions=resolved.skip_permissions,
            allowed_tools=config.mcp_allowed_tools,
        )
```
- [ ] **Step 2**: decompose.py의 두 `claude_command(...)` 호출(plan 커맨드 렌더/실행)과
  simple.py의 세 호출에 `allowed_tools=config.mcp_allowed_tools` 추가. (기본 빈 리스트라 무영향.)
- [ ] **Step 3**(검증 — 바이트 패리티): `mcp_allowed_tools` **빈 상태**로 리팩터 전/후
  4개 워크플로 `--dry-run` 산출물 SHA-256 비교(아래 §검증 절차). **전부 동일**해야 함.
- [ ] **Step 4**(검증 — 기능): `autoagent.config.json`에 `"mcp_allowed_tools":["mcp__serena"]`
  임시 설정 후 `--dry-run` → routed **Claude** 스텝 `*_command.json`에만 `--allowedTools`
  등장, **Codex** 스텝엔 없음. 확인 후 임시 설정 원복.
- [ ] **Step 5**: 커밋 `workflows: mcp_allowed_tools를 claude 명령에 배선(기본 무영향)`.

## Task 4 — 문서화

**Files**: Modify `README.md`(옵션/모델 정책 근처), 설계문서 §7 옵션 B 상태 갱신.

- [ ] **Step 1**: README에 `mcp_allowed_tools`(config 키, 기본 `[]`) 설명 한 단락 추가 —
  타깃 레포 `.mcp.json`로 서버 발견 + 이 키로 Claude allowlist 주입, Codex는 무설정.
- [ ] **Step 2**: 설계문서 §7 옵션 B에 "증분 1 구현됨(allowedTools 주입), 증분 2(--mcp-config
  생성·대칭 검사·네트워크 정렬) 미구현" 표기.
- [ ] **Step 3**: 커밋 `docs: mcp_allowed_tools 문서화 + 옵션 B 증분 1 상태`.

---

## 검증 절차 (바이트 패리티)
`--dry-run`은 CLI를 호출하지 않고 프롬프트·`*_command.json`만 렌더한다(예산 비포함).
리팩터 직전 커밋과 현재 HEAD를 각각 체크아웃해 4개 워크플로를 dry-run하고
`*_command.json`·`*_prompt.md`를 SHA-256 비교(기존 role-registry 리팩터의 검증법과 동일):
```
python .\run.py --dry-run --workflow simple   --request "..."
python .\run.py --dry-run --workflow routed --task-type backend  --request "..."
python .\run.py --dry-run --workflow routed --task-type frontend --request "..."
python .\run.py --dry-run --workflow decompose --request "..."
```
- **기본(mcp_allowed_tools=[])**: 전/후 해시 100% 동일 → 회귀 없음(opt-in 증명).
- **샘플 리스트 설정 시**: Claude 스텝 command.json에만 `--allowedTools` 델타(의도된 변화),
  Codex·프롬프트는 불변.

## 리스크
- `--allowedTools` 다중값 인자 포맷(공백 결합)은 실측에서 툴 1개로 통과 — 다중값은
  Claude Code 관례상 동일 인자 공백 구분이 표준이나, Task 2 검증에서 dry 렌더로 형태만
  확정하고, 실동작은 증분 2의 라이브 스모크에서 재확인.
- Codex는 이 증분에서 무변경 → Codex MCP는 `~/.codex/config.toml`에 서버가 있을 때만 작동
  (대칭은 사람이 맞춤; 자동 대칭 검사는 증분 2).

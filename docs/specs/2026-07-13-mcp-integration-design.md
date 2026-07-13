# MCP 통합 설계 — Claude/Codex 대칭 도구층

작성일: 2026-07-13
대상: AutoAgent의 "Tool★" 갭(MCP 거의 없음) 보완 — 서브프로세스로 도는 두 CLI에
      MCP 서버를 붙여 구현·리뷰 품질을 올린다.
상태: 초안(설계). 구현 전 사람 검토 대상.
관련: `docs/AutoAgent_하네스개요.md`(§4축 평가 Tool★), `docs/3_우리하네스_AutoAgent.md`(빈틈 ⑤).

## 1. 배경

AutoAgent의 4축 자기평가에서 **Tool 축이 가장 약하다**(★). 행동은 `prompts/*.md`
중립 채널로 두 CLI에 주입하지만, 에이전트가 쓸 수 있는 **도구(능력)** 는
각 CLI의 네이티브 도구(파일·셸)뿐이고 MCP는 사실상 없다.

능력(브라우저 구동, DB 스키마 조회, LSP 심볼 해석)은 프롬프트·스킬로는 줄 수 없고
**MCP로만** 줄 수 있다. 또한 **Claude 스킬은 Codex가 못 읽으므로**(CLAUDE.md),
크로스모델 대칭을 지키려면 능력은 MCP로 양쪽에 대칭 배치해야 한다.

## 2. 목표 / 비목표

**목표**
- 구현·리뷰 품질에 직접 기여하는 MCP 세트를 **양 CLI에 대칭**으로 붙인다.
- 각 MCP의 `.mcp.json`(Claude) / `~/.codex/config.toml`(Codex) 설정 스니펫을 명시한다.
- 하네스의 **샌드박스/권한 모델과 정렬**해, "한쪽만 도구가 작동하는" 비대칭을 막는다.

**비목표**
- 에이전트 행동을 MCP로 옮기는 것(행동은 계속 `prompts/*.md`). MCP는 **능력만**.
- 자동 커밋/푸시/배포형 MCP(하네스 "비침투·자동 push 없음" 철학과 충돌).
- 실행 테스트 게이트 그 자체(그건 `validation_commands` 네이티브 게이트가 정도(正道);
  본 문서는 도구층만 다룬다).

## 3. 결정 — 분류를 정하는 단 하나의 규칙

**MCP는 역할별이 아니라 CLI별 설정이다.** 그리고 두 CLI 모두 라우팅에 따라 구현자와
리뷰어를 겸한다(auto: frontend→Codex 구현/Claude 리뷰, backend→Claude 구현/Codex 리뷰;
`routing.choose_implementer`). 여기서 세 버킷의 경계가 나온다.

1. **코드 판단에 쓰는 MCP → 무조건 공통(양쪽).** 한쪽만 깔면 구현↔리뷰 스왑 시
   리뷰어가 구현자보다 도구가 부족해져 **비대칭 리뷰**가 된다.
2. **비대칭(한쪽만) 설치의 유일한 정당한 용도 = 두 CLI의 네이티브 능력 격차 메우기**
   (대칭을 *복원*하는 방향). 한쪽에 우위를 주려고 쓰면 규칙 1 위반.
3. 결과적으로 **Claude 전용 버킷은 거의 비어 있다**(Claude Code 네이티브 도구가
   Codex-네이티브를 이미 포괄). 억지로 채우지 않는다.

## 4. 추천 MCP 세트

### 4.1 공통 필수 (양 CLI)

| MCP | 능력 | 왜 공통 | 메우는 갭 |
|-----|------|---------|-----------|
| **Serena**(LSP/시맨틱 코드) | 심볼 해석·find-references·진단 | 구현자·리뷰어 둘 다 필수 | Tool★ + Verification★ |
| **Context7**(라이브러리 최신 문서) | 최신 API 문서 조회 | 구현=정확한 API, 리뷰=오용 적발 | 구현 정확도 |
| **Postgres (read-only)** | 실제 스키마·제약·인덱스 조회 | db 라우트의 architect·구현·리뷰 모두 | db subtype 근거 |
| **Playwright**(선택) | 브라우저 구동 | frontend 구현=Codex/리뷰=Claude 둘 다 | frontend 탐색 검증 |

- Postgres는 **db subtype을 실제로 돌릴 때만**. Playwright는 **인터랙티브 확인용**이며,
  검증 게이트는 네이티브 `validation_commands`(headless `playwright test`)가 우선이다.
- **⚠️ 교정(§6.3 2c 실측)**: **네트워크 MCP는 Codex exec에서 차단**되어 "공통"이 될 수 없다.
  → **Context7(네트워크)는 사실상 Claude 전용**(§4.3), 진짜 공통은 **Serena 같은 로컬(무네트워크)
  MCP뿐**. Postgres는 localhost TCP라 Codex 측에서 동일 차단 가능성 → **Codex 사용 전 검증 필요**.

### 4.2 Codex 전용 (패리티 보충)

> **⚠️ 교정(§6.3 2c 실측)**: 애초 fetch(웹)를 Codex 패리티로 넣으려 했으나, **네트워크 MCP는
> Codex exec에서 차단**됨이 실측으로 확인됐다. 따라서 웹 능력은 Codex MCP로 보충할 수 없다
> (웹은 Claude 네이티브 WebFetch/WebSearch로만). **이 버킷은 현재 비어 있다** — Codex는 로컬
> MCP를 무설정으로 쓰고(대칭 자동), 네트워크 MCP는 양쪽 다 못 쓰므로 패리티 보충 대상이 없다.

### 4.3 Claude 전용

- **없음(당분간).** Claude Code 네이티브 도구가 Codex-네이티브를 포괄한다. 정 둔다면
  항상-Claude 역할(context/architect/report)의 **읽기·서술 전용** 보조만 두되,
  **코드 판단 도구(LSP·linter·test)는 절대 여기 두지 말 것** — Claude가 리뷰어로
  도는 순간 비대칭이 된다.

## 5. 설정 스니펫

두 CLI는 MCP 설정 채널이 다르다. **같은 서버를 양쪽에 등록**해야 대칭이 유지된다.

### 5.1 Claude — target repo의 `.mcp.json`
서브프로세스 `cwd = config.workspace`(타깃 레포)이므로, 타깃 레포 루트의 `.mcp.json`을
Claude Code가 자동 로드한다.

```json
{
  "mcpServers": {
    "serena": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/oraios/serena",
               "serena-mcp-server", "--context", "ide-assistant"]
    },
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"]
    },
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres",
               "postgresql://readonly@localhost:5432/mydb"]
    }
  }
}
```
> 위 command/패키지명은 대표 예시다. 실제 설치법·최신 패키지는 각 서버 README로 확정할 것.

### 5.2 Codex — 전역 `~/.codex/config.toml`
codex effort를 config.toml에 두는 기존 관례(README:206)와 같은 결. **서버 정의를
Claude와 동일하게** 맞춘다.

```toml
[mcp_servers.serena]
command = "uvx"
args = ["--from", "git+https://github.com/oraios/serena", "serena-mcp-server", "--context", "ide-assistant"]

[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[mcp_servers.postgres]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-postgres", "postgresql://readonly@localhost:5432/mydb"]

# Codex 전용(패리티): 웹 fetch
[mcp_servers.fetch]
command = "uvx"
args = ["mcp-server-fetch"]

# 네트워크 MCP(context7/fetch)가 동작하려면 workspace-write 샌드박스에서 네트워크를 열어야 한다.
[sandbox_workspace_write]
network_access = true
```

## 6. 샌드박스 / 권한 정렬 (핵심)

"양쪽에 깔았다"가 곧 "양쪽에서 작동한다"를 뜻하지 않는다. 하네스가 각 역할에 부여하는
샌드박스/권한이 MCP 툴 호출을 막을 수 있다. 코드 기준으로 정리한다.

### 6.1 Codex 쪽
- `codex_exec_command`는 `--ask-for-approval never`(`config.codex_approval` 기본 "never")로
  돈다(`runner.py:90`). → **승인 프롬프트 없이 MCP 툴을 자동 사용**한다(권한 측면은 통과).
- 그러나 **샌드박스가 게이트한다**. `codex_sandbox_for(read_only, configured)`는
  read-only 역할에 `read-only`, 아니면 `configured`(기본 `workspace-write`)를 준다
  (`safety.py:42`, `config.py:71`).
  - **네트워크 MCP(context7/fetch)**: read-only·workspace-write·workspace-write+
    `network_access=true` **세 모드 모두 차단**(§6.3 2c 실측). Codex exec에선 네트워크 MCP를
    **쓸 수 없다**(토글로도 안 풀림). 로컬(무네트워크) MCP만 Codex에서 동작한다.
  - **로컬 stdio MCP(serena/postgres-to-localhost/playwright)**: MCP 서버는 codex가
    관리하는 별도 프로세스라 대체로 샌드박스 밖에서 뜨지만, **localhost 접속·파일 접근이
    샌드박스에 걸리는지 실측 검증 필요**(과신 금지).

### 6.2 Claude 쪽 (결정적 제약)
- `claude_command`는 `--model / --permission-mode / --effort / --input-format text`만
  붙이고 **`--allowedTools`·`--mcp-config`가 없다**(`runner.py:61-87`).
- 읽기 역할(context/architect/reviewer/report)은 `--permission-mode plan`
  (`roles.default.json`). **헤드리스 `claude -p` + plan 모드에서는 승인해줄 TTY가 없어,
  allowlist 없는 MCP 툴 호출이 거부된다**(실측 확정 §6.3; mutating 편집이 막히는 것과 같은
  원리 — `runner.py:70-73` 주석 참조).
- mutating 역할(implementer/fix)은 `claude_impl_permission`(기본 `acceptEdits`,
  opt-in `bypassPermissions`). `bypassPermissions`는 MCP 툴까지 허용하지만
  `acceptEdits`는 편집만 자동이라 MCP 툴 호출 허용 여부가 불확실.

> **함의**: 순수 CLI-level 설정만으로는 **가장 MCP가 필요한 읽기 역할(architect 분석,
> reviewer 검사)에서 Claude가 MCP 툴을 못 쓴다(실측 §6.3).** Codex는 `never`라 쓰는데
> Claude는 plan 모드라 못 쓰면 → **정확히 비대칭 리뷰**가 발생한다. 이걸 풀려면 Claude
> 쪽에 **MCP 툴 allowlist가 필요**하고, 이는 CLI-level 파일(타깃 레포 `.claude/settings.json`의
> `permissions.allow`) 또는 하네스가 `--allowedTools`를 주입해야 가능하다.

### 6.3 실측 결과 (2026-07-13 격리 스모크)
하네스·타깃 레포 없이 `claude -p`와 `codex exec`를 직접 돌려 §6.1–6.2를 검증했다
(공통 MCP: npx `@modelcontextprotocol/server-everything`의 `echo` 툴).

**Claude** (`--mcp-config`+`--strict-mcp-config`, `--model sonnet`으로 plan 읽기 역할 흉내):

| 실행 | 결과 |
|------|------|
| `--permission-mode plan`, allowlist 없음 | ❌ **`TOOL_BLOCKED`(거부)** — plan 모드가 승인을 요구하나 헤드리스라 승인 TTY 없음 |
| `--permission-mode plan` + `--allowedTools "mcp__everything__echo"` | ✅ **성공** — 툴 호출됨(`Echo: MCP_OK_7f3` 반환) |

**Codex** (`config.toml`에 서버 임시 추가 후 원복; `exec --ask-for-approval never --sandbox read-only -m gpt-5.5`):

| 실행 | 결과 |
|------|------|
| Codex `never` + `read-only`, allowlist 개념 없음 | ✅ **성공** — `mcp: everything/echo (completed)` → `Echo: MCP_OK_cdx` |

→ **확정(비대칭)**: 같은 로컬 MCP 서버에 대해 **Codex는 allowlist 없이 곧바로 호출**하는데
**Claude 읽기 역할(plan)은 거부**된다. 즉 **MCP 설정만 양쪽에 넣으면(옵션 A) Codex만 쓰고
Claude 리뷰어는 못 써서 정확히 비대칭 리뷰**가 된다 — 설계의 핵심 우려가 양방향으로 실증됨.
해법: **Claude 쪽 `--allowedTools` 주입(옵션 B 최소 변경)이 필요충분**. Codex는 로컬 MCP엔
추가 조치 불필요(read-only 샌드박스에서도 로컬 stdio 서버 정상 기동).
- 부수 확인: `claude -p`는 stdin을 ~3초 기다린 뒤 진행("no stdin data received in 3s") →
  직접 호출 시 `< /dev/null` 권장(하네스의 stdin 프롬프트 주입 구조와는 별개 현상).
- **네트워크 MCP ↔ Codex 샌드박스 (2c 스모크, uv 설치 후 실측)**: `mcp-server-fetch`(uvx)로
  https 요청을 Codex `exec`에서 시도 → **read-only / workspace-write / workspace-write +
  `network_access=true` 세 모드 모두 차단**(`mcp: fetch/fetch (failed)` → `user cancelled MCP
  tool call`). **`network_access` 토글로도 안 풀림.** 반면 로컬 echo MCP는 정상. 결론:
  **네트워크 MCP는 Codex exec에서 사용 불가** → 네트워크 서버(context7/fetch)는 "공통"이 될 수
  없고 Claude에서만 실효(§4 교정). 근본 원인(샌드박스 vs 승인)은 하네스가 안 쓰는 full-bypass로만
  갈릴 뿐 실무 결론 불변. (Claude는 네트워크 MCP 정상 사용.)
- 부수(해소됨): Codex `gpt-5.6-sol`은 codex 0.143.0에서 400("requires newer version")이었으나
  **0.144.1 업그레이드 후 정상 동작**(2026-07-13 확인). `autoagent.config.json`은
  `gpt-5.6-sol`/effort `medium`으로 설정됨.

## 7. 배치 옵션

### 옵션 A — CLI-level만, 하네스 무변경
- 타깃 레포에 `.mcp.json` + `.claude/settings.json`(`permissions.allow: ["mcp__serena__*", ...]`),
  전역 `~/.codex/config.toml`에 `[mcp_servers.*]` + `network_access`.
- 장점: 코드 0줄. codex-effort 관례와 동일 철학(하네스는 CLI 설정에 관여 안 함).
- 단점: **타깃마다 파일을 뿌려야** 하고(`--project`로 타깃 바뀌면 드리프트),
  Claude allowlist를 사람이 관리, 대칭·샌드박스 정렬이 **수작업**이라 어긋나기 쉽다.

### 옵션 B — 하네스가 최소한의 MCP 인지 (권장, 단계적)
> **구현 상태(2026-07-13)**:
> - **증분 1 — `--allowedTools` 주입**(`config.mcp_allowed_tools`, opt-in): 구현·검증 완료.
> - **증분 2a — 하네스 소유 `--mcp-config`**(`config.mcp_servers` → **run_dir 밑** `.aa_mcp.json` +
>   `--mcp-config`/`--strict-mcp-config`, opt-in, **dry-run은 파일 미기록**): 구현·검증 완료.
> - **증분 2b — 시작 시 Codex 대칭 검사**(`autoagent/mcp.py`의 `check_mcp_symmetry`, 경고): 완료.
> - **증분 2c — 네트워크 MCP ↔ Codex 샌드박스**: 스모크 완료(§6.3) → 네트워크 MCP는 Codex exec에서
>   **사용 불가**로 확인. "정렬" 코드는 불필요(열 수 있는 게 아니라 애초 못 씀) → §4 추천 교정 +
>   "네트워크 MCP는 Claude 전용" 가이드로 대체.
> 검증: 빈 config 바이트 패리티 38파일 동일 + 샘플 설정 시 Claude 명령에만 `--mcp-config`/
> `--strict-mcp-config`/`--allowedTools` 주입, Codex 명령엔 없음, 대칭 경고 발화 확인.
> **적대적 리뷰(3렌즈) 5건 반영·재검증**: 크래시 방어(비-dict `mcp_servers`), dry-run 무기록,
> run_dir 실행별 격리(고정경로 경합 제거), BOM(`utf-8-sig`), mkdir/예외처리.
> 구현 파일: `config.py`·`runner.py`·`mcp.py`(신규)·`cli.py`·`routed_impl.py`·`decompose.py`·
> `simple.py`. 계획: `docs/specs/2026-07-13-mcp-integration-plan.md`.
- `autoagent.config.json`/프로젝트 config에 `mcp` 섹션(서버 목록 + common/codex_only/claude_only)
  을 두고, 하네스가:
  1. Claude에 `--mcp-config <생성한 .mcp.json>` + 읽기 역할에 `--allowedTools "mcp__*"` 주입,
  2. Codex config.toml 존재/서버 일치 여부를 **시작 시 검사**(불일치면 경고 — `validate_roles`와 같은 결),
  3. 네트워크 MCP가 있으면 해당 역할 샌드박스가 네트워크를 허용하는지 점검.
- 장점: **대칭·allowlist·샌드박스 정렬을 코드가 보장**. `--project` 다중 타깃에서도 일관.
- 단점: 하네스가 CLI 설정에 관여 → "defer to CLI config" 관례와 소폭 상충(의도된 예외).
- 절충: **A로 소형 스모크 → 효과 확인되면 B로 productionize.**

## 8. 대칭 보장 체크리스트
- [ ] 같은 서버가 `.mcp.json`과 `config.toml` **양쪽**에 동일 정의로 존재(공통 버킷).
- [ ] Claude 읽기 역할이 MCP 툴을 실제로 호출 가능(allowlist 적용 확인).
- [ ] Codex 읽기 역할(read-only 샌드박스)에서 필요한 MCP가 작동(네트워크 MCP면 불가 인지).
- [ ] 네트워크 MCP는 양쪽에서 켜지거나 양쪽에서 꺼짐(한쪽만 켜지면 비대칭).
- [ ] `--project` 타깃별로 위가 유지되는지 점검.

## 9. 리스크 / 엣지
- **dry-run 무영향**: `--dry-run`은 프롬프트·`*_command.json`만 렌더하고 CLI를 호출하지
  않으므로 바이트 패리티 검증에 영향 없음. MCP는 **라이브 실행에서만** 관여.
- **결정론 저하**: 네트워크 MCP(context7/fetch)는 응답이 시점마다 달라 재현성↓ →
  라이브 전용, 감사 필요 구간에선 주의.
- **비침투 철학**: 모든 추천 MCP는 read-only 또는 로컬 조회. 쓰기/푸시형 금지.
- **샌드박스 비대칭**이 가장 흔한 실패: 네트워크·localhost 접근이 한 CLI에서만 막혀
  리뷰가 갈림. §8 체크리스트로 방지.

## 10. 롤아웃 순서
1. ~~읽기 역할 MCP 호출 가부 실측~~ **[Claude 쪽 완료 2026-07-13 §6.3]** — plan 역할은
   allowlist 필수로 확정. 남은 실측: Codex `never`+샌드박스↔네트워크, 실제 타깃에서 서버 기동.
2. **[확정]** 옵션 B의 `--allowedTools` 주입이 필요충분 → 이 최소 구현을 우선 도입.
3. ~~Context7 공통 추가~~ → **교정(§6.3 2c)**: 네트워크 MCP는 Codex exec 불가 → Context7는
   **Claude 전용**으로만(양쪽 등록 금지). 웹은 Claude 네이티브로 충분.
4. db 라우트를 쓰면 Postgres(read-only) — 단 localhost TCP라 Codex 측 동작을 먼저 검증.
   frontend 인터랙티브가 필요하면 Playwright(로컬 브라우저).
5. ~~Codex 전용 fetch~~ → **철회**: fetch(네트워크)는 Codex exec에서 안 됨(§4.2).
6. 효과 확인되면 옵션 B로 config `mcp` 섹션 + 시작 시 대칭 검사 정식화.

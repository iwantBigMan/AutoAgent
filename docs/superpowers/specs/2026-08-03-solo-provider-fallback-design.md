# solo_provider 폴백 설계 (단일 프로바이더 적대 서브에이전트)

> **작성일:** 2026-08-03
> **범위:** 하네스 전역(routed·research·decompose·simple). 한쪽 프로바이더 토큰이 없을 때 남은 한 프로바이더가 구현+적대리뷰를 겸직하게 하는 폴백.

## 배경 / 문제

AutoAgent는 교차모델 협업이 전제다 — routed는 구현=Codex, 리뷰=반대편 Claude, 계획/보고=Claude,
평가=Codex. research는 스테이지별 researcher와 반대모델 verifier. decompose는 분해=Claude,
계획리뷰=Codex. 따라서 **모든 주 워크플로가 Claude·Codex 토큰을 둘 다 요구**한다.

한쪽 토큰이 소진/부재하면(세션 한도, rate limit 등) `run_process`가 종료코드 ≠0에서
`SystemExit`으로 **하드 크래시**한다(runner.py:176). 폴백도, "세션 한도"와 다른 오류의 구분도 없다.
결과: 한쪽 프로바이더만 살아 있어도 하네스를 못 쓴다.

## 목표

한쪽 토큰이 없을 때, **살아 있는 한 프로바이더가 모든 역할(구현·리뷰·계획·검증·평가·보고)을 겸직**하되
리뷰/검증은 **적대적**으로 수행해 단일 모델 자기검증의 rubber-stamp를 막는다. 정상(양 토큰) 시엔
현행 교차모델 그대로.

## 비목표 (YAGNI)

- **자동 감지**(프리플라이트 프로브/런타임 폴백) — 이번엔 **명시 선언(config)만**. 토큰 유무는 보통
  사용자가 안다. 추후 얇은 감지 안내를 얹을 여지는 남긴다.
- **우회 사이트 DRY 부채 정리** — decompose/simple의 포스처 하드코딩(roles.default.json과 분리)을
  resolve_role로 완전 통합하는 리팩터는 이번 범위 밖(정상 경로 무변경 우선). solo는 정상 경로를
  건드리지 않는 최소 스왑으로 얹는다.
- **verifier_agent 감사추적 정확화** — solo에서 실제 실행 프로바이더와 로깅된 verifier_agent가
  어긋나는 cosmetic 오표기(§부록 참조)는 선택적 후속.

## 결정 사항 (확정)

| 축 | 결정 |
|---|---|
| 발동 | **명시 선언** — `config.solo_provider`(`null`/`"claude"`/`"codex"`). 1회 설정하면 이후 런에 자동 적용. |
| 모드 | 살아 있는 단일 프로바이더가 **전 역할 겸직**(별도 서브프로세스 호출로 컨텍스트 격리). |
| 적대성 | 리뷰/검증은 **적대적 필수**. research 검증 프롬프트는 이미 적대적, routed/decompose 리뷰 프롬프트는 중립적이라 **적대 프리앰블 주입**. |
| 범위 | **general — 네 워크플로 균일**(routed·research·decompose·simple). |
| 정상 경로 | **무변경**(solo_provider=null이면 모든 오버라이드가 no-op). |

## 아키텍처

### 4.1 `config.solo_provider`

- `Config` 데이터클래스에 `solo_provider: str | None = None` 추가(config.py).
- `load_config`에서 파싱: `solo_provider = raw.get("solo_provider") or None`. 값이 None이 아니고
  `{"claude","codex"}`에 없으면 `SystemExit`으로 거부(validate_roles 호출 전, config.py 내).
- precedence: config 파일 > (후속) env > 기본값 None. `autoagent.config.json`은 gitignored라
  각자 로컬 토큰 상황에 맞게 설정.
- `metadata.json`에 `solo_provider` 기록(감사추적).

### 4.2 `resolve_role` 오버라이드 — 주 chokepoint

감사 결과 `resolve_role`(roles.py:82)는 **routed 전 역할 + research 전 역할 + decompose 실행단계
(task_exec)**의 유일 실행 경로다. 여기 맨 앞에서 agent를 덮는다:

```python
def resolve_role(entry, *, config, route, request, agent, read_only):
    # solo 폴백: solo_provider가 설정되면 모든 역할을 그 프로바이더가 겸직한다.
    # 정상(null)이면 no-op이라 교차모델 경로는 바이트 동형.
    if getattr(config, "solo_provider", None):
        agent = config.solo_provider
    ...  # 기존 로직: high-risk 판정, tier_name 결정, config.tiers[agent][tier_name] 조회
```

이 한 줄로 상류에서 하드코딩된 agent(`architect="claude"`, `evaluator="codex"`,
`RESEARCHER_BY_STAGE`, d-stage의 `agent="codex"`, choose_implementer의 반대모델 등)가 **전부**
solo로 접힌다 — 상류 배정은 그대로 두고(무변경) resolve_role이 최종 덮는다.

**tier 팔레트 대칭화(필수):** 역할이 쓰는 tier는 `light`/`standard`/`deep`인데 **codex 팔레트엔
`light`가 없다**(config.py default_tiers). solo=codex면 `context`·`report`(tier=light)가
`config.tiers["codex"]["light"]`에서 **KeyError**. 해소:
- config.py default_tiers의 codex에 `"light": {"model": codex_model, "effort": None}` 추가(claude
  light와 동형: 기본 모델 + effort 생략).
- 방어적 하드닝(선택): resolve_role에서 `config.tiers[agent].get(tier_name) or config.tiers[agent]["standard"]`.

### 4.3 우회 사이트 solo 스왑 (정상 경로 무변경)

`resolve_role`를 **안 거치는** 5곳은 별도 처리한다(감사 확인):
- `decompose.py:41`(01 claude decomposition, permission_mode="plan"=읽기전용 계획)
- `decompose.py:72`(02 codex plan-review, sandbox="read-only")
- `simple.py:45`(01 claude plan)
- `simple.py:68`(02 codex execute, sandbox=config.codex_sandbox=변이)
- `simple.py:92`(03 claude review)

각 사이트는 **의도(intent)** 가 명확하다: plan/review=읽기전용, execute=변이. solo일 때만 그 의도를
살아 있는 프로바이더의 커맨드 빌더로 스왑한다. 공유 헬퍼:

```python
# runner.py 또는 신규 소형 모듈
def solo_command(config, *, intent: str, resolved_command: str) -> list[str]:
    """solo 프로바이더로 intent(plan|review|execute)에 맞는 커맨드를 조립한다.
    plan/review=읽기전용, execute=변이. claude=permission_mode, codex=sandbox로 매핑."""
    provider = config.solo_provider
    if provider == "claude":
        mode = "acceptEdits" if intent == "execute" else "plan"
        return claude_command(resolved_command, config.claude_model, mode,
                              allowed_tools=config.mcp_allowed_tools, mcp_config_path=config.mcp_config_path)
    # codex
    sandbox = config.codex_sandbox if intent == "execute" else "read-only"
    return codex_exec_command(config, resolved_command, sandbox)
```

각 우회 사이트: `if config.solo_provider: cmd = solo_command(config, intent=..., resolved_command=require_command(solo CLI)); else: <기존 빌더 그대로>`.
- **정상(null) 경로는 기존 빌더 그대로 → 바이트 동형**(폴백이 정상 동작을 흔들지 않음).
- dry-run 분기에도 동일 적용해 solo 커맨드 아티팩트가 렌더되게 한다.

### 4.4 적대 프리앰블 (routed/decompose 리뷰 역할)

감사 확인:
- **research 검증 프롬프트 3종**(crossmodel_verifier·b_market_verifier·d_grounding_verify)은 이미
  강하게 적대적("방어 말고 공격", 최소 findings 강제, major시 강등). → **무변경.**
- **routed 리뷰 프롬프트 6종**(backend/frontend/final × claude/codex)은 **중립적**(심지어
  "오버엔지니어링 금지"가 자기검증 억제). → solo에서 rubber-stamp 위험.

해소: 공유 적대 프리앰블을 **solo일 때만** 리뷰 역할 프롬프트 앞에 prepend.
- 신규 `prompts/routed/_solo_adversarial_preamble.md`(중립 채널, 양 프로바이더가 stdin으로 읽음). 핵심
  지시: ① 능동 공격(자기 코드라도 약점을 능동 탐색), ② 최소 3개(research의 `crossmodel_min_findings`
  기본값과 정합) 구체 지적 또는 무결 근거 증명,
  ③ major/critical 발견 시 approve 금지, ④ 컨텍스트·코드에만 근거(모델 일반지식 금지),
  ⑤ "오버엔지니어링 회피"보다 "검증 충분성" 우선.
- 주입 지점(리뷰 역할에만):
  - `routed_impl.run_role_step`에서 `role_id == "reviewer"` 이고 solo면 렌더 프롬프트 앞에 prepend.
  - `routed_impl.run_final_review`에서 solo면 prepend.
  - decompose 실행단계는 `run_impl_review_fix`→`run_role_step`(reviewer)+`run_final_review`를
    재사용하므로 **자동 커버**.
  - `fix`·`implementer` 역할엔 주입 안 함. research 경로엔 주입 안 함(이미 적대적).
- 공유 헬퍼 `maybe_prepend_adversarial(prompt, config, role_kind) -> str`(solo & review role일 때만
  prepend, 아니면 원본 반환).

### 4.5 시작 경고 배너 + metadata

- `cli.py`의 MCP 대칭 검사 블록과 같은 위치에서, `config.solo_provider`가 있으면
  `[solo] SOLO MODE: {provider} 단독 — 교차모델 적대검증 대신 단일 프로바이더 적대검증(엄격도 감소).`
  경고를 stdout에 찍는다.
- `metadata.json`에 solo_provider 기록.

## 워크플로별 solo 동작 (검증됨)

| 워크플로 | solo 동작 | 코드 변경 |
|---|---|---|
| **routed** | 전 역할(context·architect·validation·implementer·reviewer·fix·final-review·evaluation·report)이 solo. 리뷰·final-review에 적대 프리앰블. | resolve_role 오버라이드 + 프리앰블 주입 |
| **research** | 전 역할(researcher·verifier·seed·d-stage 하드코딩 codex 포함)이 solo. 검증 프롬프트 이미 적대적. `parse_crossmodel_verdict`는 모델 무관(마커는 프롬프트가 emit) → 기능 동작. | **resolve_role 오버라이드만**(추가 로직 0) |
| **decompose** | 분해(01)·계획리뷰(02)는 우회 스왑, 실행단계(task_exec)는 resolve_role 오버라이드 + 리뷰 프리앰블. | resolve_role + 우회 스왑 |
| **simple** | plan(01)·execute(02)·review(03) 우회 스왑. | 우회 스왑 |

## 데이터 흐름

```
config.solo_provider 설정(1회)
  → cli.py 시작 시 경고 배너 + metadata 기록
  → 각 워크플로 실행:
       resolve_role 경유 역할  → agent=solo_provider로 덮임 → solo CLI 실행
       우회 사이트(decompose/simple) → solo_command(intent)로 스왑 → solo CLI 실행
       리뷰/final-review 역할(routed/decompose-exec) → 적대 프리앰블 prepend
       research 검증 → 이미 적대적 프롬프트 그대로
  → 산출물은 단일 프로바이더 적대검증 결과. 커버리지/게이트/승인은 현행 유지.
```

## 에러 처리

- **잘못된 solo 값**(예: "gpt4"): load_config에서 즉시 `SystemExit`.
- **tier 팔레트 부재**(codex light): §4.2대로 codex 팔레트에 light 추가(대칭화)로 해소. 방어적
  `.get(...) or standard` 폴백 선택적.
- **solo 프로바이더 CLI 부재**: `require_command`가 기존대로 명확히 실패(양 토큰 다 없는 상황은
  애초에 solo로도 못 돎 — 정상적 실패).
- **정상(null) 경로**: 모든 오버라이드/스왑이 no-op → 교차모델 동작 바이트 동형(회귀 0).

## 테스트 전략

- **단위(결정론)**:
  - `resolve_role` solo 오버라이드: solo=claude/codex일 때 임의 역할의 ResolvedRole.agent가 solo,
    tier가 solo 팔레트에서 뽑힘. null이면 기존 agent 보존(no-op).
  - tier 대칭: `config.tiers["codex"]["light"]` 존재.
  - config 검증: 잘못된 solo 값 SystemExit, null/claude/codex 통과.
  - `solo_command(intent)`: plan/review→읽기전용, execute→변이, 프로바이더별 올바른 빌더.
  - `maybe_prepend_adversarial`: solo & reviewer role일 때만 prepend.
- **dry-run(배선)**: 각 워크플로를 `--dry-run` + solo config로 돌려 커맨드 아티팩트가 solo CLI로
  렌더되고(우회 사이트 포함), 리뷰 프롬프트에 적대 프리앰블이 들어갔는지 확인.
- **라이브 모델 런은 사용자 인계**(하네스 관례).

## 하위호환 체크리스트

- [ ] solo_provider=null(기본): 네 워크플로 전부 교차모델 바이트 동형.
- [ ] resolve_role null 경로: agent 인자 그대로(오버라이드 no-op).
- [ ] 우회 사이트 null 경로: 기존 빌더 그대로.
- [ ] 리뷰 프롬프트 null 경로: 프리앰블 미주입.
- [ ] 잘못된 solo 값: 시작 시 명확한 실패.

## 부록: 검증된 감사 결과 (5종 병렬)

1. **chokepoint**: resolve_role는 routed/research/decompose-실행의 유일 경로. 우회 5곳
   (decompose.py:41,72; simple.py:45,68,92)만 직접 호출 → §4.3로 처리.
2. **agent-assign**: 상류 하드코딩(architect="claude", evaluator="codex", RESEARCHER_BY_STAGE,
   choose_implementer 반대모델, d-stage codex)은 전부 resolve_role 하류라 오버라이드로 덮임.
3. **adversarial-prompt**: routed 리뷰 6종 중립(프리앰블 필요), research 검증 3종 이미 적대(무변경).
4. **config-integration**: config.py 필드+검증, cli.py 경고, metadata 기록 — 기존 패턴과 정합.
5. **cross-model-assumptions**: 감사는 research "깨짐"을 주장했으나 **코드 검증 결과 과장** —
   `parse_crossmodel_verdict`(adapters.py:63)는 모델 무관(마커는 프롬프트가 emit),
   `verifier_agent`는 provenance 로깅 전용(동작 분기 없음), d-stage 하드코딩 codex도 resolve_role
   통과. → research는 **기능 코드 수정 없이** solo 동작(교차'모델'만 빠지고 적대검증 유지).
   남는 건 verifier_agent 감사추적 cosmetic 오표기(선택적 후속).

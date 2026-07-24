# Codex 구현 전담 · Claude 리뷰/계획/문서 전담 (역할 분업 고정) — 설계

**작성일:** 2026-07-23
**상태:** 설계 확정(구현 대기)

## 목표

routed 워크플로의 모델 분업을 명시적으로 고정한다.

- **구현(implement/fix)** 은 항상 **Codex** 가 맡는다(backend·frontend 전부).
- **리뷰(라운드 리뷰 + 최종 리뷰)** 는 항상 구현자의 반대편인 **Claude** 가 맡는다.
- **계획(context·architecture)·최종 보고서** 는 **Claude**, **계획 검증·평가** 는 **Codex** 로 현행 유지.
- Codex는 구현 직후 자기 diff를 **자체 리뷰(self-review)** 한 뒤 Claude 리뷰로 넘긴다.

근거: 사용자 판단상 구현 품질은 Codex가 더 낫고, 크로스모델 리뷰의 독립성은 "구현자 ≠ 리뷰어"를 끝까지 지킬 때 살아난다. 지금은 backend 기본 구현자가 Claude라 이 원칙이 절반만 성립한다.

## 현재 동작(변경 전 사실 확인)

`routing.choose_implementer` (autoagent/routing.py):

| task_type | 기본 구현자 | 기본 리뷰어 |
|---|---|---|
| frontend | codex | claude |
| backend | **claude** (단, test/build/diff-fix 키워드면 codex) | codex (backend 기본) |
| docs / review | claude(구현 스텝 없음) | codex |

단계별 고정 역할 (roles.default.json):

- context = claude, architect = claude, validation = codex
- implementer/reviewer/fix = `route`(구현자·반대편)
- **final-review = codex 고정**, evaluation = codex 고정, report = claude 고정

impl 라우트 순서 (autoagent/workflows/routed_impl.py):

```
04 구현 → (05 리뷰 ⇄ 06 수정) × max_review_rounds → 07 최종리뷰 → 08 평가 → 09 최종보고
```

관찰된 문제:

1. backend 요청은 실제로는 Claude가 구현해 왔다(사용자 기대와 반대).
2. `final-review`(07)가 codex 고정이라, 구현자를 codex로 만들면 "codex가 짠 걸 codex가 최종 검수"가 되어 크로스모델 검증이 깨진다(현재 frontend가 이미 그 상태).
3. 구현자가 자기 결과를 스스로 점검하는 단계가 없다. `codex_impl.md`는 구현·보고만 지시한다.

## 설계

### 변경 1 — 라우팅: 구현 기본값 뒤집기

`choose_implementer`에서 `task_type == "backend"`의 기본을 **codex 구현 / claude 리뷰**로 바꾼다.

- frontend는 이미 `("codex", "claude")` → 불변.
- `CODEX_IMPLEMENTER_TERMS`(test/build/diff-fix) 특례 분기는 결과가 codex로 수렴해 무의미해지므로 제거한다(관련 상수도 함께 정리).
- docs/review는 `("claude", ...)`(구현 스텝 없음) → 불변.
- 명시 `requested_implementer`("claude"/"codex")는 최우선으로 계속 존중한다(수동 오버라이드 유지).

결과: auto 라우팅에서 **모든 구현 = Codex, 모든 리뷰 = Claude.**

### 변경 2 — 최종리뷰(07)를 구현자 반대편으로

`final-review` 역할을 codex 고정에서 **구현자의 반대 모델**(= `route["review_agent"]`)로 바꾼다. 라운드 리뷰(`reviewer`)와 동일한 대칭을 최종 리뷰에도 적용한다.

- `roles.default.json`: `final-review`의 `"agent": "codex"` → `"agent": "route"`.
- `run_final_review`(routed_impl.py): 하드코딩 `agent="codex"` / `"codex_final.md"` / `require_command(codex)` 를 review_agent 기준 분기로 바꾼다.
  - review_agent == "claude" → 신규 프롬프트 `claude_final_review.md`, claude 커맨드.
  - review_agent == "codex" → 기존 `codex_final.md`, codex 커맨드.
  - 산출 파일명은 에이전트를 반영해 `07_{review_agent}_final_review`로 바꾼다(05/06이 이미 `05_{review_agent}_..._review`, `06_{impl}_..._fix`로 에이전트를 반영하는 것과 일관).
  - **함께 고쳐야 하는 참조(자체 리뷰에서 확인)**: `commands/aa.md:60`의 resume용 glob이 `07_codex_final_review*`로 하드코딩돼 있어 파일명을 바꾸면 resume 아티팩트 매칭이 깨진다. `05_*_review*`/`06_*_fix*`와 동일하게 **`07_*_final_review*`로 와일드카드화**한다. (08 evaluation은 codex 고정 유지라 `08_codex_evaluation*` 그대로 둔다.)
- **신규 프롬프트** `prompts/routed/final/claude_final_review.md`: `codex_final.md`의 Claude 대칭본. 첫 줄 `FINAL_STATUS: approved|needs_changes|blocked`, 이하 블로킹 지적/검증 충분성/남은 위험/다음 조치. (주의: 기존 `claude_final.md`는 09 "최종 보고서"라 이름이 겹치지 않게 `claude_final_review.md`로 둔다.)
- `autoagent/artifacts.py` PROMPT_ALIASES에 `"claude_final_review.md": "routed/final/claude_final_review.md"` 추가.
- `evaluation`(08)은 **codex 유지**. Claude 리뷰(05/07) 뒤 codex가 독립 채점하므로 두 모델이 모두 관여하는 구조가 유지된다.

### 변경 3 — Codex 자체 리뷰를 구현 프롬프트에 접기

`prompts/routed/backend/codex_impl.md` 와 `prompts/routed/frontend/codex_impl.md` 의 작업 지시에 자체 리뷰 단계를 추가한다(별도 에이전트 호출 없음):

- 구현을 마친 뒤, 커밋(하지 않지만) 전에 자기 diff를 스스로 리뷰한다.
- 발견한 명백한 결함(회귀, 누락 테스트, 범위 초과, 스타일 불일치)은 직접 고친다.
- 스스로 못 고친/판단 유보한 우려는 결과에 `SELF_REVIEW:` 절로 보고한다.

자체 리뷰는 구현자 자기 턴의 일부이며, 독립 검증 게이트는 뒤따르는 Claude 리뷰(05/07)가 담당한다.

### 변경 후 흐름

```
01 context(claude) → 02 arch(claude) ⇄ 03 validation(codex)
→ 04 구현+SELF_REVIEW(codex) → (05 리뷰(claude) ⇄ 06 수정(codex)) × N
→ 07 최종리뷰(claude) → 08 평가(codex) → 09 최종보고(claude)
```

모델 균형: Codex = {03 검증, 04 구현, 06 수정, 08 평가}, Claude = {01 컨텍스트, 02 아키텍처, 05 리뷰, 07 최종리뷰, 09 보고}. 구현(Codex)은 Claude가 05·07에서 두 번 리뷰하고 08에서 Codex가 독립 채점한다.

## 파급 효과(의도된 동작 변경)

- **high-risk backend 구현 모델 이동**: 지금은 Claude `deep` 티어(opus, effort xhigh). 변경 후 Codex `deep` 티어(gpt-5.6-sol, effort high). codex `deep` 티어는 config 기본 팔레트에 이미 존재하므로 신규 티어 추가는 불필요.
- **07 뒤집힘은 frontend에도 적용**: 현재 frontend 07 = codex인데, frontend 구현이 codex라 반대편 claude로 바뀐다. 즉 backend·frontend 둘 다 07이 Claude가 된다.
- **`run_final_review`는 decompose 실행기와 공유**(docstring: "routed와 실행기가 공유"): 변경이 decompose 태스크 노드의 최종 리뷰에도 전파된다. 플랜에서 decompose가 넘기는 route에 `review_agent`가 실려 있는지, 없으면 어떻게 결정되는지 확인해 회귀를 막는다.
- **문서**: `CLAUDE.md`의 라우팅/구현자 관련 서술(특히 "high-risk backend를 claude(opus)로 구현" 취지의 문구)을 새 규칙에 맞게 갱신.

## 비목표(Out of scope)

- 에이전트 샌드박스/네트워크 개방(codex `danger-full-access`, claude `bypassPermissions`)은 하지 않는다. 이 설계는 "역할 고정"이지 "샌드박스 개방"이 아니다.
- 검증 스테이지(DB-free 1단계)와 Alembic/실 DB 2단계 검증은 이 설계의 범위 밖(PR #17에서 다룸).
- evaluation(08)을 Claude로 옮기지 않는다.
- `--implementer` 수동 오버라이드 동작 변경 없음.

## 검증 전략

이것은 리팩터가 아니라 **의도된 동작 변경**이므로, 기존의 dry-run byte-equality 회귀검증은 부분적으로만 적용한다.

1. **회귀 감시선(byte-identical 유지 요구)**: docs 라우트·review 라우트의 dry-run 산출물(`*_command.json` + `*_prompt.md`)은 변경 전후 byte-identical 이어야 한다(이 두 라우트는 이번 변경과 무관).
2. **의도된 변경(달라지는 게 정상)**: backend·frontend 라벨의 dry-run 산출물은 구현자/최종리뷰 모델이 바뀌므로 달라진다. 이때 diff를 육안 검토해 (a) 04/06이 codex, (b) 05가 claude, (c) 07이 claude(신규 `claude_final_review.md` 렌더), (d) SELF_REVIEW 지시가 04 프롬프트에 포함됐는지 확인한다.
3. **시작 정합성**: `validate_roles`가 통과하는지(final-review가 `route`가 되어 claude·codex 양쪽 `standard` 티어를 참조 → 이미 존재). 하네스가 정상 기동하는지.
4. **라이브 실증(백그라운드)**: LanguageDetection에 backend 요청 1건을 실제 routed로 돌려 `04 codex 구현+SELF_REVIEW → 05/07 claude 리뷰 → 08 codex 평가` 흐름이 산출물로 확인되는지 검증한다. 대상 워크스페이스 소스는 건드리지 않는다(구현 스텝은 워크스페이스를 수정하므로, 실증은 격리/일회성 요청으로 하고 결과 파일만 검토).

## 위험 / 미해결

- **decompose 전파**(위 파급): `run_final_review` 공유로 인한 회귀. 플랜의 별도 태스크로 확인.
- **07 산출 파일명 변경**(자체 리뷰에서 참조처 전수 확인 완료): 기능 참조는 `commands/aa.md:60` glob 한 곳 → `07_*_final_review*`로 와일드카드화(위 변경 2에 포함). `run_final_review`의 `name` 기본값도 갱신. 나머지 참조(`README.md`, `docs/AutoAgent_공부가이드.md`, `docs/AutoAgent_하네스개요.html`, `docs/specs/2026-07-09-*`)는 **서술/도식용**이라 기능에 영향은 없으나 정확성을 위해 갱신 대상으로 둔다(플랜에서 문서 갱신 태스크로 묶음).
- **high-risk backend 품질**: opus/xhigh → codex/high 이동이 고위험 구현 품질에 미치는 영향은 라이브에서 관찰. 필요하면 codex `deep` 티어 effort를 xhigh로 올리는 후속 튜닝(설계 밖).

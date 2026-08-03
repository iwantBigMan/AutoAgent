# AutoAgent

Local harness that orchestrates Claude Code CLI (`claude.cmd`) and Codex CLI
(`codex.cmd`) as subprocesses to collaborate on a **separate target workspace** —
cross-model implement/review with human approval gates. Full reference: `README.md`.

## Critical model
- Subprocesses run with `cwd = config.workspace` (the **target project**, default
  `C:\Users\systran\Desktop\LanguageDetection`) — **not this repo**. This repo is only
  the orchestrator you edit.
- Agent instructions live in `prompts/**/*.md`, rendered by `render_template` with
  `{VAR}` placeholders. **Code orchestrates; prompts carry the "what to do"** — change
  behavior in prompts, not by hardcoding Python.
- Reviewer is always the **opposite model** of the implementer (`routing.choose_implementer`).
  Codex does **not** load Claude skills — keep shared agent behavior in `prompts/*.md`,
  the neutral channel both CLIs read via stdin.
- **역할 분업(고정)**: auto 라우팅에서 모든 구현(backend·frontend)은 **Codex**, 모든 리뷰(라운드 05 + 최종 07)는 반대편 **Claude**가 맡는다. 계획(context·architect)·최종보고는 Claude, 계획검증·평가(08)는 Codex. high-risk backend 구현은 codex `deep` 티어(effort high). Codex 구현자는 결과 전 자기 diff를 자체 리뷰(`SELF_REVIEW`)한다.

## Workflows & layout
- `--workflow simple|routed|decompose|research`; routed = context→architecture⇄validation→
  approval gate→implement→review⇄fix→eval→report.
- `research` = 영업/데이터 리서치(회사·시장·CSV정제·웹팩트리포트→도출): 중첩 루프(안쪽 리서치→검증→보정 ×3,
  바깥 심화 ×2) + 교차모델 적대검증(어댑터 crossmodel/data_quality/source_grounding) + 게이트·재개 + 인용 HTML
  리포트. 코어 `autoagent/research/**`·`autoagent/data/**`, 오케스트레이터 `autoagent/workflows/research.py`,
  프롬프트 `prompts/research/*.md`. 설계 문서: `docs/superpowers/specs|plans/*research*`. 실행: `/aar` 또는
  `run.py --workflow research --request "..."` (+`--auto-approve-nonbranch`/`--resume`).
- `autoagent/workflows/routed_*.py` split by phase: `routed_preamble` (plan),
  `routed_impl` (implement/review loop), `routed_docs` (read-only), `routed_common` (gates),
  plus `task_exec.py` (decompose's parallel executor).
- `autoagent/worktree.py`: git worktree / integration helpers used by the decompose executor.
- decompose: after the approval gate, `--resume` runs the task_graph as a wavefront
  parallel execution — each node isolated in its own worktree, reviewed by the opposite
  model, then merged into an integration branch `aa-integration/<stamp>`; concurrency is
  `config.max_parallel_lanes` (default 2), sequential if `1`.
- Run artifacts land in `runs/YYYYMMDD_HHMMSS/` (gitignored except `.gitkeep`).

## Testing / verification
- **routed/decompose = no unit tests** → verify with dry-run:
  `python .\run.py --dry-run --workflow routed --task-type backend --request "..."`
  (renders every prompt + `*_command.json`, no CLI invoked; dry-run never counts against `--max-agent-calls`).
- **`research` subsystem HAS a deterministic pytest suite** (`autoagent/research/**`·`autoagent/data/**`, pytest.ini):
  `python -m pytest tests/ -q` (~171 tests). 모델 호출부는 여전히 dry-run으로:
  `python run.py --dry-run --workflow research --request "..."` (a→b→c→d→derive 전 스테이지 순회 후 exit 0).
- 리서치 워크플로는 **pytest + dry-run까지만 검증** — 실모델 라이브 런 미실증(사용자 인계). "구현됨"을 "실전 검증됨"으로 단정 말 것.

## Conventions
- Every module opens with a **Korean docstring**; functions carry Korean inline comments.
  Match this style.
- `from __future__ import annotations`; PEP 604 types (`str | None`); dataclasses for
  config/state. Keep modules small and single-purpose.

## Environment / gotchas
- Windows + Git Bash; `LF will be replaced by CRLF` warnings on git ops are harmless.
- Git Bash stdout은 한글을 cp949로 깨뜨린다 — 한글 파일 내용은 `cat`/표시 말고 **Read 도구(utf-8)**로 확인(ASCII 토큰 `grep`은 무방).
- **Pushing to `main` is blocked** (default-branch protection) — use a feature branch + PR.
- `autoagent.config.json` is **gitignored**; precedence: config file >
  `AUTOAGENT_WORKSPACE` env > hardcoded default.
- MCP는 opt-in·**대칭**: 서버를 `mcp_servers`(→Claude)와 `~/.codex/config.toml [mcp_servers.*]`(→Codex) 양쪽에 넣는다(안 하면 `check_mcp_symmetry` 경고). `mcp_allowed_tools`는 Claude의 MCP 툴 allowlist — 리뷰어가 MCP로 편집/실행 못 하게 **읽기(내비) 툴만** 둔다.

# 프롬프트 구조

프롬프트는 워크플로우와 역할별로 묶여 있습니다.

```text
prompts/
  simple/
    plan.md
    execute.md
    review.md
  decompose/
    claude_decompose.md
    codex_plan_review.md
  routed/
    context/
      claude_context.md
      claude_architect.md
      codex_validation.md
    backend/
      claude_impl.md
      codex_impl.md
      claude_review.md
      codex_review.md
      claude_fix.md
      codex_fix.md
    frontend/
      claude_impl.md
      codex_impl.md
      claude_review.md
      codex_review.md
      claude_fix.md
      codex_fix.md
    final/
      claude_final.md
      codex_final.md
      codex_evaluator.md
```

코드는 `autoagent/artifacts.py`의 별칭(alias)을 통해 옛 평면 프롬프트 이름도 여전히 지원합니다.

참고: 프롬프트 본문 설명은 한국어이지만, 코드가 파싱하는 토큰은 영문 그대로 유지됩니다 — `{{...}}` placeholder 전부, 그리고 `REVIEW_STATUS:` / `IMPLEMENTATION_STATUS:` / `FIX_STATUS:` / `FINAL_STATUS:` / `EVALUATION_STATUS:` / `PLAN_REVIEW_STATUS:` / `TASK_GRAPH_JSON` 마커.

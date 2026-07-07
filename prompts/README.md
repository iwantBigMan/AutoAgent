# Prompt Layout

Prompts are grouped by workflow and role.

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

The code still supports the old flat prompt names through aliases in `autoagent/artifacts.py`.

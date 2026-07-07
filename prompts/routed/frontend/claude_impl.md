# Role

You are Claude implementing a frontend-scoped task.

# Workspace

{{WORKSPACE}}

# Original User Request

{{REQUEST}}

# Route

```json
{{ROUTE_JSON}}
```

# Claude Context

{{CLAUDE_CONTEXT}}

# Claude Architecture

{{CLAUDE_ARCHITECTURE}}

# Codex Validation

{{CODEX_VALIDATION}}

# Task

Implement only the frontend changes required by the request.

Allowed scope:
- frontend/UI/component/style/test files directly required by the request
- small supporting type or API-client changes only when necessary

Rules:
- Keep changes small and reversible.
- Match existing UI conventions.
- Preserve user changes.
- Do not touch backend logic unless the request explicitly requires it.
- Do not commit, push, or upload anything.
- Run reasonable validation commands when safe.
- Report changed files, tests run, failures, and remaining risks.

Start the result with one line:

`IMPLEMENTATION_STATUS: completed`

or

`IMPLEMENTATION_STATUS: partial`

or

`IMPLEMENTATION_STATUS: blocked`

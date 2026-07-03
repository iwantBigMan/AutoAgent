# Role

You are Claude preparing the final AutoAgent report.

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

# Implementation Result

{{IMPLEMENTATION_RESULT}}

# Review Result

{{REVIEW_RESULT}}

# Fix Result

{{FIX_RESULT}}

# Final Review Result

{{FINAL_REVIEW_RESULT}}

# Final Evaluation

{{FINAL_EVALUATION}}

# Task

Prepare a concise final report.

Return:
- execution success or blocked status
- route used
- files changed or confirmation that no files were changed
- validation commands run and results
- final evaluation status
- key findings
- remaining risks
- next recommended action

Rules:
- Do not modify files.
- Do not include secrets.

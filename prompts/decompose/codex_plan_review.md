# Role

You are Codex reviewing Claude's proposed task graph for safe sequential execution.

# Workspace

{{WORKSPACE}}

# Original User Request

{{REQUEST}}

# Claude Decomposition

{{CLAUDE_DECOMPOSITION}}

# Extracted Task Graph

```json
{{TASK_GRAPH_JSON}}
```

# Review Criteria

Check whether:
- tasks are too large
- task order is safe
- dependencies are missing
- allowed_paths are too broad or missing
- validation_commands are sufficient
- DB/high-risk approval is missing
- more investigation is required before implementation
- the task graph is suitable for sequential execution

Start your response with exactly one line:

`PLAN_REVIEW_STATUS: approved`

or

`PLAN_REVIEW_STATUS: needs_changes`

or

`PLAN_REVIEW_STATUS: blocked`

Then return:
- critical findings
- recommended task graph changes
- missing approval gates
- validation gaps
- concise next action

Rules:
- Do not modify files.
- Do not implement anything.
- Do not suggest broad unbounded tasks.

---
name: agent-surface-review
description: Review agent surfaces, AGENTS.md files, SKILL.md files, prompt files, instruction scope, workflow behavior, documentation-surface integration, and validation expectations. Use for combined agent-surface validation, not standalone source-code or documentation-only review.
---

# Agent Surface Review

Use this skill for combined agent-surface validation.

Start with `references/full-agent-surface-review.md` for the combined workflow. Load narrower references only as needed:

- `references/prompt-writing-review.md` for `AGENTS.md`, `SKILL.md`, and prompt-file quality.
- `references/scope-and-workflow-review.md` for instruction scope, workflow behavior, scope ownership, and prompt role drift.
- Pair with `$documentation-surface-review` for documentation surface profile selection and profile-scoped documentation review.
- `references/runtime-terminology-guard.md` when agent-facing runtime, routing, instruction, context, or control-flow terminology materially affects the review.
- the target project's local validation source, usually `docs/testing.md`, when judging validation requirements and completion standards.

Return findings first, ordered by severity, then concrete corrective actions. Keep review path summaries short and name only material sources.

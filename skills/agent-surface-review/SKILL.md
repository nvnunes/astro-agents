---
name: agent-surface-review
description: Review agent surfaces, AGENTS.md files, SKILL.md files, prompt files, instruction scope, workflow behavior, documentation-surface integration, and validation expectations. Use for combined agent-surface validation, not standalone source-code or documentation-only review.
---

# Agent Surface Review

For a bounded change, review the changed instructions, affected contracts,
and necessary consumers. Select references before loading them:

- `references/prompt-writing-review.md` for prompt and skill quality.
- `references/scope-and-workflow-review.md` for instruction ownership and
  workflow behavior.
- `references/full-agent-surface-review.md` for an explicitly requested full
  review within the requested target.
- Pair with `$documentation-surface-review` when documentation organization,
  ownership, profile, or completeness is affected, or for a full review.
  A routine operation-reference edit alone does not trigger that workflow.
- `references/runtime-terminology-guard.md` when runtime or control-flow
  terminology materially affects the review.

Follow the target project's validation requirements, usually in `docs/testing.md`.

Return findings by severity, followed by corrective actions. State the reviewed
scope and include a short review path summary naming only material sources.
Full reviews use the full reference's output requirements.

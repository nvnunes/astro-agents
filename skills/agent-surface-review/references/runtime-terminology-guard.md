# Runtime Terminology Guard

## Purpose

Use this reference when agent-facing files use runtime, routing, instruction, context, state, or control-flow terminology that could change how an agent routes or performs work.

This is a small guard for agent-surface review, not a full runtime terminology guide. For human-facing documentation review, use `skills/documentation-surface-review/references/runtime-terminology-review.md`.

## Review Checks

When reviewing `AGENTS.md`, `SKILL.md`, prompt files, or project-local validation instructions, check whether the target surface:

- names the actual mechanism instead of relying on vague hierarchy or layering language
- distinguishes active instructions from supporting context
- distinguishes project convention from runtime-enforced behavior
- avoids implying unsupported authority, permissions, approvals, or tool access
- states whether a route, workflow, handoff, or delegation changes ownership of the next output
- treats session history, compaction notes, retrieved context, and memory as different context sources when that distinction matters
- uses terms such as `route`, `workflow`, `handoff`, `orchestration`, skill
  selection, `override`, `instructions`, and `context` precisely enough for an
  agent to act correctly

## Finding Guidance

Raise a finding when unclear runtime terminology could cause one of these practical failures:

- the agent follows the wrong route or combines incompatible workflows
- a project-local convention is mistaken for a runtime-enforced rule
- a file appears to grant tool, permission, or approval authority that the runtime does not provide
- broad context or stale session history is treated as active instruction
- a review, handoff, or delegation path leaves ownership of the next output unclear

Do not raise findings only because the target project uses different vocabulary. The issue is whether the terms are clear, mechanism-aware, and usable by the active agent surface.

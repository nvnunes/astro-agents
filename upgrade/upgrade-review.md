# Upgrade Review

## Purpose
Use this prompt to handle one core review task at a time within the upgrade workflow.

Use it only for these core review tasks from `astro-agents/docs/upgrade-design.md`:

- `review-1`: review the agent surface
- `review-3`: report remaining issues

Read the durable upgrade-progress record first, keep the work inside one review task, and update only that task's record fields.

## Inputs

- target root or target paths
- upgrade-progress record path, usually `docs/upgrade-progress.md` in the target repo
- optional core review task id or task name
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the review task is not specified, use the task indicated by the upgrade-progress record. If the record does not make one core review task unambiguous, stop and report the blocker rather than guessing.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` and the upgrade-progress record first.
2. Confirm that the requested task is exactly one core review task.
3. Read the prior planning outputs, completed editing-task records, and any already-recorded review outputs needed for the current task.
4. Inspect only the repo files, validation artifacts, and supporting docs needed for the review task.
5. Update only the matching task-ledger row and the matching review output section.
6. Leave orchestration-owned workflow-state, blocker, checkpoint-ledger, and next-step fields to `astro-agents/upgrade/upgrade-orchestrator.md`.

## Core Review Task Guidance

### `review-1`: review the agent surface

- review the upgraded agent surface, documentation, prompts, and validation results together
- use `astro-agents/validation/review/full-agent-surface-review.md` as the default shared validation entrypoint
- prefer a higher-precedence local validation layer only when one actually exists in the target repo and explicitly implements the needed combined review
- if the planning outputs or repo validation docs indicate that a higher-precedence local validation layer should exist and none is present, use the shared entrypoint and record the missing local validation layer as a review gap
- report findings when they exist, or say explicitly that no material issues were found
- keep the review grounded in the upgraded surface rather than reopening design decisions that were already settled unless a real issue now requires that

### `review-3`: report remaining issues

- synthesize any important remaining risks, gaps, or follow-up work after the review phase
- use the prior task outputs and review reports as the main evidence base
- distinguish between blockers, follow-up work, and softer cleanup

## Exclusions

- do not perform editing tasks while reviewing
- do not choose or reinterpret the documentation surface profile
- do not update orchestration-owned workflow fields
- do not broaden the review beyond the current task

## Output
Return one review-task result.

For `review-1`:

- return a review report with:
  - scope reviewed
  - review or validation paths used
  - findings or explicit confirmation that no material issues were found
  - any recommended follow-up

For `review-3`:

- return a remaining-issues report with:
  - blockers, if any
  - important follow-up work
  - softer cleanup or watch items

When a progress record is provided:

- update only the matching task row and review output section
- do not rewrite other task sections or orchestration-owned fields

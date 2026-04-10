# Upgrade Review Public Python

## Purpose
Use this prompt to handle one `public-python` review task at a time within the upgrade workflow.

Use it only for:

- `review-2`: review the public documentation surface

Read the durable upgrade-progress record first, keep the work inside that review task, and update only that task's record fields.

## Inputs

- target root or target paths
- upgrade-progress record path, usually `docs/upgrade-progress.md` in the target repo
- optional review task id or task name
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the review task is not specified, use the task indicated by the upgrade-progress record. If the record does not make `review-2` unambiguous, stop and report the blocker rather than guessing.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` and the upgrade-progress record first.
2. Confirm that the documentation surface profile recorded in the progress record is `public-python`.
3. Confirm that the requested task is `review-2`.
4. Read the prior planning outputs, completed `public-python` editing-task records, and any already-recorded review outputs needed for the current task.
5. Inspect only the repo files, validation artifacts, and supporting docs needed for the public documentation review.
6. Use `astro-agents/validation/review/public-python/documentation-review.md` as the default shared validation entrypoint.
7. Prefer a higher-precedence local validation layer only when one actually exists in the target repo and explicitly implements the needed `public-python` documentation review.
8. If the planning outputs or repo validation docs indicate that a higher-precedence local validation layer should exist and none is present, use the shared entrypoint and record the missing local validation layer as a review gap.
9. Update only the `review-2` task row and `review-2` review output section.
10. Leave orchestration-owned workflow-state, blocker, checkpoint-ledger, and next-step fields to `astro-agents/upgrade/upgrade-orchestrator.md`.

## Review Guidance

- review the public user and developer documentation surface together
- include other public-facing surfaces such as contributor docs, release docs, and tutorial assets only when they are part of the public documentation surface under review
- report findings when they exist, or say explicitly that no material issues were found
- keep the review grounded in the already exposed public docs surface rather than reopening planning decisions unless a real issue now requires that

## Exclusions

- do not use this prompt when the progress record does not declare `public-python`
- do not perform editing tasks while reviewing
- do not choose or reinterpret the documentation surface profile
- do not update orchestration-owned workflow fields

## Output
Return one `public-python` review report with:

- scope reviewed
- review or validation paths used
- findings or explicit confirmation that no material issues were found
- any recommended follow-up

When a progress record is provided:

- update only the `review-2` task row and `review-2` review output section
- do not rewrite other task sections or orchestration-owned fields

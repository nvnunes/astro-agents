# Upgrade Review

## Purpose
Use this prompt to handle one core review task at a time and write the matching saved review artifact under `docs/upgrade/` in the target repo.

Use it only for these core review tasks from `astro-agents/docs/upgrade-design.md`:

- `review the agent surface`
- `report remaining issues`

Keep the work inside one review task and replace only that task's saved review artifact when you rerun it.

## Inputs

- target root or target paths
- documentation surface profile declared in the target repo's root `AGENTS.md`
- optional core review task name
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the root `AGENTS.md` does not yet declare the documentation surface profile, stop and send the user to `astro-agents/upgrade/upgrade-documentation-surface-profile.md` before running review work.

Use:

- `docs/upgrade/review-agent-surface.md` for `review the agent surface`
- `docs/upgrade/review-remaining-issues.md` for `report remaining issues`

If the review task is not unambiguous from the request, stop and ask the user which core review task they want.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` first.
2. Read the target repo's root `AGENTS.md` and confirm that it declares `Documentation surface profile: <profile>.`
3. Confirm that the requested task is exactly one core review task.
4. Read the repo files, validation artifacts, and saved `docs/upgrade/edit-*.md` or `docs/upgrade/review-*.md` files needed for the current review.
5. Keep the review grounded in the repo's current surface and the saved task artifacts that already exist.
6. Create `docs/upgrade/` when it does not already exist, then write or replace only the matching `docs/upgrade/review-*.md` file.

## Core Review Task Guidance

### `review the agent surface`

- review the upgraded agent surface, documentation, prompts, and validation results together
- use `astro-agents/validation/review/full-agent-surface-review.md` as the default shared validation entrypoint
- prefer a higher-precedence local validation layer only when one actually exists in the target repo and explicitly implements the needed combined review
- if the repo validation docs indicate that a higher-precedence local validation layer should exist and none is present, use the shared entrypoint and record the missing local validation layer as a review gap
- report findings when they exist, or say explicitly that no material issues were found
- keep the review grounded in the upgraded surface rather than reopening settled design decisions unless a real issue now requires that

### `report remaining issues`

- synthesize any important remaining risks, gaps, or follow-up work after the review phase
- use the saved edit artifacts, saved review artifacts, and current repo state as the main evidence base
- distinguish between blockers, follow-up work, and softer cleanup

## Exclusions

- do not perform editing tasks while reviewing
- do not choose or reinterpret the documentation surface profile
- do not broaden the review beyond the current task

## Output

Write or replace the matching review artifact in this structure:

```md
# Review: <Task Name>

## Metadata

- task:
- status: `done` | `blocked`
- documentation surface profile:
- prompt used:
- last updated:

## Scope And Oversight

- review scope:
- validation path used:

## Approval

- approval status: `not needed`
- approval reference:

## Output

## Follow-Up
```

For `review the agent surface`, write `docs/upgrade/review-agent-surface.md`.

For `report remaining issues`, write `docs/upgrade/review-remaining-issues.md`.

Return a short review summary with:

- saved file path
- scope reviewed
- review or validation paths used
- findings or explicit confirmation that no material issues were found
- any recommended follow-up

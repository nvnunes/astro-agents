# Upgrade Edit Base

## Purpose
Use this prompt as the shared contract for one core edit task under `astro-agents/upgrade/edit/`.

Each task-specific prompt in this folder should identify:

- the exact task name
- the exact saved artifact path under `docs/upgrade/`
- any task-specific source-of-truth docs or scope constraints

Use the task-specific prompt together with this base prompt. The task-specific prompt owns the exact task. This base prompt owns the common edit behavior.

## Inputs

- target root or target paths
- the exact core edit task named by the task-specific prompt
- the exact saved artifact path named by the task-specific prompt
- documentation surface profile declared in the target repo's root `AGENTS.md`
- optional plan path, defaulting to `docs/upgrade/plan.md` in the target repo
- optional focus areas
- optional target scope that narrows work below the full target root
- optional approval reference when the user has explicitly approved a `designs` or `plans` task summary

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` first.
2. Read the target repo's root `AGENTS.md` and confirm that it declares `Documentation surface profile: <profile>.`
3. If that declaration is missing, stop and send the user to `astro-agents/upgrade/upgrade-documentation-surface-profile.md` before running edit work.
4. Read the task-specific prompt under `astro-agents/upgrade/edit/`.
5. Confirm that exactly one core edit task and one saved artifact path are in scope.
6. Read `docs/upgrade/plan.md` when it exists.
7. Inspect only the repo files and supporting docs needed for that one task.
8. If the saved plan contains a row for the current task:
   - use that row's saved `Change scope` and `Notes` as the primary planning guidance for the task
   - also apply any relevant constraints, out-of-scope decisions, or review expectations recorded in the plan's `## Notes`
   - keep the saved change scope unless current repo evidence clearly contradicts it
9. If the saved plan does not contain a row for the current task, or no saved plan exists:
   - derive the change scope from current repo evidence:
   - `preserve`
   - `restructure`
   - `develop`
   - `n/a` when the task is clearly not needed
10. If current repo evidence materially contradicts the saved plan row for the current task, write the saved artifact with `status: blocked`, explain the mismatch, and send the user back to `astro-agents/upgrade/upgrade-plan.md` to revise or reapprove the plan before editing.
11. Map the required oversight from the task and effective change scope using `astro-agents/docs/upgrade-design.md`.
12. Create `docs/upgrade/` when it does not already exist.
13. If the task is clearly not needed, write the saved artifact with `status: not needed` and stop.
14. If the task is blocked by missing evidence or unresolved ambiguity, write the saved artifact with `status: blocked` and stop.
15. If the required oversight is `designs` or `plans` and there is no explicit approval for the current task in the current thread, write the saved artifact with `status: waiting for approval`, record the needed summary, and stop without making file edits.
16. If the required oversight is `outputs`, or the needed approval has been given explicitly in the current thread, execute the task and then write the saved artifact with `status: done`.

## File Format

Write or replace the task artifact in this structure:

```md
# Edit: <Task Name>

## Metadata

- task:
- status: `not needed` | `waiting for approval` | `done` | `blocked`
- documentation surface profile:
- prompt used:
- last updated:

## Scope And Oversight

- change scope: `preserve` | `restructure` | `develop` | `n/a`
- required oversight: `outputs` | `plans` | `designs` | `n/a`

## Approval

- approval status: `not needed` | `required` | `approved`
- approval reference:

## Output

## Follow-Up
```

For `## Output`:

- when a matching saved plan row exists, record the task-specific plan guidance that was used
- when waiting for approval, write the task-level design or plan summary the user needs to approve
- when done, write what changed, what evidence shaped the work, and the resulting ownership or structure effect
- when not needed or blocked, write the reason clearly and specifically

For `## Follow-Up`:

- keep only real remaining issues, deferred cleanup, or downstream review notes

## Exclusions

- do not work on more than one edit task at a time
- do not choose or reinterpret the documentation surface profile
- do not ignore a matching saved plan row when `docs/upgrade/plan.md` exists
- do not let `docs/upgrade/plan.md` replace task-relevant repo evidence
- do not silently override saved plan guidance when current repo evidence materially contradicts it; send the user back to `astro-agents/upgrade/upgrade-plan.md`
- do not rewrite any other `docs/upgrade/*.md` file
- do not let neighboring edit tasks bleed into the current task

## User-Facing Output

Return a short task summary with:

- saved artifact path
- task status
- whether a matching saved plan row was used
- change scope
- required oversight
- files changed, when any
- any approval still needed or follow-up worth noting

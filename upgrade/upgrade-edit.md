# Upgrade Edit

## Purpose
Use this prompt to handle one core editing task at a time within the upgrade workflow.

Use it only for these core editing tasks from `astro-agents/docs/upgrade-design.md`:

- `edit-core-1`: minimum repo-level `AGENTS.md`
- `edit-core-2`: minimum repo-level `README.md`
- `edit-core-3`: minimum source-of-truth docs
- `edit-core-4`: minimum environment and execution support
- `edit-core-5`: minimum testing and validation support
- `edit-core-6`: additional interface docs
- `edit-core-7`: additional supporting docs

Read the durable upgrade-progress record first, keep the work inside one editing task, and update only that task's record fields.

## Inputs

- target root or target paths
- upgrade-progress record path, usually `docs/upgrade-progress.md` in the target repo
- optional core editing task id or task name
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the editing task is not specified, use the task indicated by the upgrade-progress record. If the record does not make one core editing task unambiguous, stop and report the blocker rather than guessing.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` and the upgrade-progress record first.
2. Confirm that the requested task is exactly one core editing task.
3. Read the `plan-1`, `plan-2`, and `plan-3` outputs needed to understand the task boundary.
4. Use the task's recorded change scope and required oversight when they are present.
5. If the task record is missing the needed change-scope or oversight information, stop and report that blocker instead of improvising execution.
6. Inspect only the repo files and supporting docs needed for the current task.
7. Update only the matching task-ledger row and the matching core editing task section.
8. Leave orchestration-owned workflow-state, blocker, checkpoint-ledger, and next-step fields to `astro-agents/upgrade/upgrade-orchestrator.md`.

## Oversight Handling

- if the task oversight is `outputs`, execute the task and then record the result
- if the task oversight is `designs` or `plans` and the needed approval is not yet recorded, produce the task-level design or plan summary, update the task record to `waiting for approval`, and stop without making file edits
- if the task oversight is `designs` or `plans` and the needed approval is already recorded, execute only the approved task scope and then record the result
- if the approval state is unclear, stop and report the blocker instead of guessing

## Core Task Guidance

### `edit-core-1`: minimum repo-level `AGENTS.md`

- use `astro-agents/docs/usage.md` as the source of truth for the recommended repo-level `AGENTS.md` surface
- keep routing brief and push deeper explanation into stronger source-of-truth docs

### `edit-core-2`: minimum repo-level `README.md`

- use `astro-agents/docs/usage.md` for document-role expectations and repo entrypoint guidance
- keep `README.md` focused on orientation, setup entrypoints, and discoverability

### `edit-core-3`: minimum source-of-truth docs

- use `astro-agents/docs/usage.md` and `astro-agents/docs/upgrade-design.md` to decide which source-of-truth docs are needed
- include `docs/data-sources.md` when the repo's data surface makes it necessary

### `edit-core-4`: minimum environment and execution support

- ground the work in actual setup commands, runtime prerequisites, scripts, CI, and stable config
- document only the support needed for effective agent operation

### `edit-core-5`: minimum testing and validation support

- ground the work in actual tests, validation commands, validation docs, workflows, and stable review paths
- keep the result aligned with the repo's real verification surface

### `edit-core-6`: additional interface docs

- require evidence of a meaningful interface surface before expanding this task
- keep the task limited to the current interface surface identified by the planning outputs

### `edit-core-7`: additional supporting docs

- retain only supporting docs that remain useful after normalization and are linked from stronger owners
- do not let this task become a catch-all rewrite of unrelated docs

## Exclusions

- do not work on more than one editing task at a time
- do not choose or reinterpret the documentation surface profile
- do not rewrite the plan while executing an editing task
- do not update orchestration-owned workflow fields
- do not let neighboring editing tasks bleed into the current task

## Output
Return one editing-task result shaped by the task's oversight level.

When the task is waiting for approval:

- return a task-level design or plan summary with:
  - the task name
  - the planned change scope
  - the intended files or owners
  - the main move to make
  - any blocker or open question

When the task is executed:

- return a result summary with:
  - the task name
  - the change scope used
  - files changed
  - the ownership or structure effect
  - any follow-up or remaining issue

When a progress record is provided:

- update only the matching task row and core editing task section
- do not rewrite other task sections or orchestration-owned fields

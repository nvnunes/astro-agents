# Upgrade Edit Public Python

## Purpose
Use this prompt to handle one `public-python` editing task at a time within the upgrade workflow.

Use it only for these `public-python` editing tasks from `astro-agents/docs/upgrade-design.md`:

- `edit-public-1`: public package metadata
- `edit-public-2`: public user documentation
- `edit-public-3`: public developer documentation
- `edit-public-4`: public contributor and release surface
- `edit-public-5`: public examples and tutorial assets

Read the durable upgrade-progress record first, keep the work inside one editing task, and update only that task's record fields.

## Inputs

- target root or target paths
- upgrade-progress record path, usually `docs/upgrade-progress.md` in the target repo
- optional `public-python` editing task id or task name
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the editing task is not specified, use the task indicated by the upgrade-progress record. If the record does not make one `public-python` editing task unambiguous, stop and report the blocker rather than guessing.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` and the upgrade-progress record first.
2. Confirm that the documentation surface profile recorded in the progress record is `public-python`.
3. Confirm that the requested task is exactly one `public-python` editing task.
4. Read the `plan-1`, `plan-2`, and `plan-3` outputs needed to understand the task boundary.
5. Use the task's recorded change scope and required oversight when they are present.
6. If the task record is missing the needed change-scope or oversight information, stop and report that blocker instead of improvising execution.
7. Inspect only the repo files and supporting docs needed for the current task.
8. Update only the matching task-ledger row and the matching `public-python` editing task section.
9. Leave orchestration-owned workflow-state, blocker, checkpoint-ledger, and next-step fields to `astro-agents/upgrade/upgrade-orchestrator.md`.

## Oversight Handling

- if the task oversight is `outputs`, execute the task and then record the result
- if the task oversight is `designs` or `plans` and the needed approval is not yet recorded, produce the task-level design or plan summary, update the task record to `waiting for approval`, and stop without making file edits
- if the task oversight is `designs` or `plans` and the needed approval is already recorded, execute only the approved task scope and then record the result
- if the approval state is unclear, stop and report the blocker instead of guessing

## `public-python` Task Guidance

### `edit-public-1`: public package metadata

- inspect `pyproject.toml` and related public package metadata that affects package presentation or docs discovery
- keep the task scoped to the public metadata surface rather than general packaging internals

### `edit-public-2`: public user documentation

- inspect user-facing entry docs such as `README.md`, installation guidance, tutorials, how-to docs, and subtree entry docs
- keep the task focused on the public user-facing documentation surface

### `edit-public-3`: public developer documentation

- inspect docs-site config, reachable docs pages, and generated API-doc inputs that define the public developer docs surface
- keep the task focused on the already exposed public docs system

### `edit-public-4`: public contributor and release surface

- inspect `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, and related public workflow or release docs
- keep the task scoped to the contributor and release surface rather than internal process notes

### `edit-public-5`: public examples and tutorial assets

- inspect examples, notebooks, tracked generated artifacts, and other public learning materials when they are part of the public docs surface
- keep the task scoped to user-facing learning assets rather than general scratch work

## Exclusions

- do not use this prompt when the progress record does not declare `public-python`
- do not work on more than one editing task at a time
- do not choose or reinterpret the documentation surface profile
- do not rewrite the plan while executing an editing task
- do not update orchestration-owned workflow fields

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

- update only the matching task row and `public-python` editing task section
- do not rewrite other task sections or orchestration-owned fields

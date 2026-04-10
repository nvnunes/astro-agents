# Upgrade Plan

## Purpose
Use this prompt to handle one planning task at a time within the upgrade workflow.

Use it for:

- `plan-1`: report on current agent surface
- `plan-2`: design the upgrade approach
- `plan-3`: write the upgrade plan

Read the durable upgrade-progress record first, perform only the requested planning task, and update only that task's record fields.

## Inputs

- target root or target paths
- upgrade-progress record path, usually `docs/upgrade-progress.md` in the target repo
- optional planning task id or planning task name
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If the planning task is not specified, use the task indicated by the upgrade-progress record. If the record does not make one planning task unambiguous, stop and report the blocker rather than guessing.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` and the upgrade-progress record first.
2. Confirm that the requested task is exactly one of `plan-1`, `plan-2`, or `plan-3`.
3. Use the documentation surface profile already recorded in the progress record as workflow input.
4. Read only the progress-record sections, repo files, and supporting docs needed for the current planning task.
5. Update only the matching task-ledger row and the matching entry under `Planning Outputs`.
6. Leave orchestration-owned workflow-state, blocker, checkpoint-ledger, and next-step fields to `astro-agents/upgrade/upgrade-orchestrator.md`.

## Planning Task Instructions

### `plan-1`: report on current agent surface

- use `astro-agents/upgrade/report-current-agent-surface.md` as the task-specific standard for discovery order, checks, exclusions, and output shape
- inspect the requested target root fresh rather than relying on previously recorded `plan-1` text
- keep the task current-state-only and provisional
- do not turn the report into the upgrade approach or execution plan
- write the finished report into the `plan-1` output section of the progress record
- mark the `plan-1` task row `done` when the report is complete

### `plan-2`: design the upgrade approach

- use the `plan-1` report, the repo's source-of-truth docs, and the user-provided documentation surface profile already recorded in the progress record
- decide the main goals and out-of-scope areas
- decide which editing tasks are in scope
- decide the main change scopes suggested by the current surface for those tasks
- define the oversight checkpoints that should govern later work
- decide the review requirements before the upgrade is treated as complete
- keep the output at the design level rather than turning it into a task-by-task execution script
- if the current evidence is too weak to support a design decision, record the uncertainty explicitly instead of forcing a choice
- write the result into the `plan-2` output section of the progress record
- update the `plan-2` task row to `waiting for approval` when the design-level summary is ready for user review

### `plan-3`: write the upgrade plan

- require a completed `plan-2` output before proceeding
- if the progress record does not clearly show that the design checkpoint has been approved, stop and report that blocker instead of drafting the execution plan
- turn the approved upgrade approach into a concrete task order
- keep the plan task-by-task and one-task-at-a-time
- name the expected prompt for each planned task when that helps execution clarity
- include prerequisite or dependency notes only when they materially affect the task order
- do not perform file edits inside this planning task
- write the result into the `plan-3` output section of the progress record
- update the `plan-3` task row to `waiting for approval` when the plan-level summary is ready for user review

## Exclusions

- do not choose, approve, or reinterpret the documentation surface profile
- do not perform editing tasks or review tasks
- do not update orchestration-owned workflow fields
- do not collapse `plan-1` into target-state design
- do not let `plan-2` drift into execution planning
- do not let `plan-3` drift into making the edits themselves

## Output
Return one planning-task result.

For `plan-1`:

- use the current-surface report shape required by `astro-agents/upgrade/report-current-agent-surface.md`

For `plan-2`:

- return a design-level summary with:
  - goals in scope
  - out-of-scope areas
  - editing tasks in scope
  - main change scopes
  - oversight checkpoints
  - review requirements
  - open questions or blockers

For `plan-3`:

- return a plan-level summary with:
  - planned task order
  - task-by-task execution notes
  - checkpoint dependencies
  - open questions or blockers

When a progress record is provided:

- update only the matching task row and `Planning Outputs` entry
- do not rewrite other task sections or orchestration-owned fields

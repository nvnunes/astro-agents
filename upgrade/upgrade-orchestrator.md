# Upgrade Orchestrator

## Purpose
Use this prompt to activate or resume the upgrade workflow for one repo or target root.

Keep this prompt focused on workflow coordination. Use it to initialize or resume the durable upgrade-progress record, decide which single workflow task comes next, enforce oversight checkpoints, and tell the user which prompt to run next.

Do not use this prompt to do the substantive planning, editing, or review task itself unless the only work needed is to initialize or update orchestration-owned fields in the progress record.

## Inputs

- target root or target paths
- documentation surface profile for this workflow
- optional upgrade-progress record path
- optional instruction to start a new workflow or resume an existing one
- optional request to stop at a specific phase or task
- optional request to re-evaluate the next-task recommendation after a task was completed

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

If no upgrade-progress record path is provided, prefer `docs/upgrade-progress.md` in the target repo.

Use the user-provided documentation surface profile for this workflow. If the current run does not provide one, reuse the value already recorded in the progress record. If neither exists, stop and ask the user to provide it rather than selecting or approving a profile inside the workflow.

When a new progress record is needed for the current rollout, use `astro-agents/upgrade/upgrade-progress-template.md` as the template. If that template is unavailable, report the gap and stop instead of inventing a record shape.

## Orchestration Workflow

1. Confirm scope, confirm the documentation surface profile, and locate the durable progress record.
   - If a record path is provided, use it.
   - Otherwise, prefer `docs/upgrade-progress.md` in the target repo, and fall back to another obvious existing record only when the workflow is already using one.
   - If no record exists, initialize `docs/upgrade-progress.md` in the target repo from `astro-agents/upgrade/upgrade-progress-template.md` and fill the known target, documentation-surface-profile, and workflow-state fields.
2. Read the progress record before deciding anything else.
   - Treat it as the durable source of truth for workflow state, completed tasks, oversight checkpoints, blockers, and the next-task recommendation.
   - Treat the documentation surface profile recorded there as workflow input, not as a task output for the process to decide or approve.
   - Preserve task outputs already recorded there unless newer evidence clearly supersedes them.
3. Decide the next single task.
   - Move through the workflow in order: plan, edit, review.
   - Within a phase, choose the next incomplete task that is unlocked by the recorded state.
   - Do not queue multiple tasks at once.
4. Enforce oversight checkpoints before sending the user onward.
   - If the next task has oversight level `designs` or `plans` and its review material has not yet been produced, route into that task so it can generate the checkpoint output.
   - If a `designs` or `plans` task has already produced its output and is now waiting for approval, stop at that checkpoint rather than routing into the next dependent task.
   - Do not advance to downstream planning, editing, or review tasks whose prerequisites include an earlier unapproved checkpoint.
   - When a downstream task is blocked by an unresolved checkpoint, update the progress record to show that blocker and stop with the checkpoint summary instead of advancing the workflow.
5. Map the next task to the currently available prompt inventory.
   - Use `astro-agents/upgrade/upgrade-plan.md` for `plan-1`, `plan-2`, or `plan-3`.
   - Use `astro-agents/upgrade/upgrade-edit.md` for one core editing task at a time.
   - Use `astro-agents/upgrade/upgrade-edit-public-python.md` for one `public-python` editing task at a time when the recorded documentation surface profile is `public-python`.
   - Use `astro-agents/upgrade/upgrade-review.md` for `review-1` or `review-3`.
   - Use `astro-agents/upgrade/upgrade-review-public-python.md` for `review-2` when the recorded documentation surface profile is `public-python`.
   - `astro-agents/upgrade/report-current-agent-surface.md` is a detailed supporting reference for `plan-1` behavior and rollout-only portfolio-scan work, not the main workflow entrypoint.
   - If the next task's prompt does not yet exist, say so explicitly, keep the workflow state up to date, and stop instead of improvising one.
6. Update only orchestration-owned parts of the progress record.
   - Keep the workflow-state, documentation-surface-profile input, next-task, prompt-to-run-next, checkpoint, blocker, and last-orchestrator-update fields current.
   - Do not overwrite task-specific reports or summaries except to link them from the orchestration summary when needed.

## Task Order
Use this default task order unless the progress record already contains a recorded task sequence that justifies a narrower path:

1. `report on current agent surface`
2. `design the upgrade approach`
3. `write the upgrade plan`
4. core editing tasks in the plan order recorded in the progress record
5. profile-specific editing tasks when the user-provided documentation surface profile requires them
6. `review the agent surface`
7. `review the public documentation surface` when the user-provided documentation surface profile requires it
8. `report remaining issues`

## Exclusions

- do not perform the substantive planning, editing, or review task inside this prompt
- do not skip required oversight checkpoints
- do not reinterpret current-surface evidence as selection or approval of the documentation surface profile
- do not treat the absence of a task prompt as permission to invent one ad hoc
- do not rewrite the durable task history into a fresh summary when the progress record already carries the needed output
- do not expand the scope beyond the requested repo or target root

## Output
Return one orchestration update with these sections:

1. `Workflow status`
2. `Progress record status`
3. `Next task decision`
4. `Oversight checkpoint or blocker`
5. `Run this next`

For `Run this next`:

- name the next task
- give the exact prompt path to run next when one exists
- name the progress-record path that prompt should read and update
- if the needed task prompt is not yet present, say that explicitly and point to `astro-agents/docs/upgrade-design.md` as the governing design reference rather than inventing a prompt

# AGENTS.md

## Purpose
Use this folder when the task is to work on upgrading existing repos, the upgrade process, or the upgrade prompt family.

## Upgrade Prompt Selection

- Use `docs/upgrade-design.md` as the current source of truth for the upgrade process.
- `upgrade/upgrade-orchestrator.md` is the main active workflow entrypoint in this folder. Use it when the task is to start or resume an upgrade workflow, decide which upgrade task comes next, work from an upgrade-progress record, or tell the user which upgrade prompt to run next.
- `upgrade/upgrade-plan.md` is the active task prompt for planning tasks. Use it when the task is `plan-1`, `plan-2`, or `plan-3`.
- `upgrade/upgrade-edit.md` is the active task prompt for core editing tasks.
- `upgrade/upgrade-edit-public-python.md` is the active task prompt for `public-python` editing tasks.
- `upgrade/upgrade-review.md` is the active task prompt for core review tasks.
- `upgrade/upgrade-review-public-python.md` is the active task prompt for `public-python` review tasks.
- `upgrade/report-current-agent-surface.md` is a detailed supporting reference for `plan-1` behavior and rollout-only portfolio-scan work, not the main workflow entrypoint.
- Treat only explicitly present prompts in this folder as active parts of the upgrade workflow.
- Default the scope to the requested repo or target root, not the whole workspace.
- Follow `docs/upgrade-design.md` for the currently defined planning, editing, review, workflow, and prompt-architecture model.
- If the orchestrator reaches a task whose prompt is not yet present, keep the workflow state current and stop at the design level with `docs/upgrade-design.md` rather than inventing a prompt.

## Practical Rule

Use this folder to answer:

- which upgrade-process prompt or design artifact applies
- which upgrade task should run next

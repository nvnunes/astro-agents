# Upgrade Prompt Family

This folder holds the first-version upgrade prompt family built from the current upgrade-process design.

This `README.md` is the human-facing guide for designing and revising those prompts. Its peer file, `AGENTS.md`, is the agent-facing router for selecting them.

## Design Model

Use this folder for maintaining and extending the upgrade prompt family for bringing existing repos onto the shared `astro-agents` agent-surface model.

In this folder:

- `AGENTS.md` decides which prompt applies
- `README.md` explains the folder and points to the relevant design docs
- prompts follow the current design model in `docs/upgrade-design.md`
- `upgrade/upgrade-orchestrator.md` is the active workflow entrypoint
- `upgrade/upgrade-plan.md`, `upgrade/upgrade-edit.md`, `upgrade/upgrade-edit-public-python.md`, `upgrade/upgrade-review.md`, and `upgrade/upgrade-review-public-python.md` are the active task prompts for the first-version workflow
- `upgrade/report-current-agent-surface.md` is a detailed supporting reference for `plan-1` behavior and rollout-only portfolio-scan work
- if a prompt for the requested task is not yet present, the design doc is the active source of truth

## Prompts

- `upgrade/upgrade-orchestrator.md`
  - active entrypoint for starting or resuming an upgrade workflow
  - initializes or resumes the upgrade-progress source of truth
  - chooses the next single task
  - enforces oversight checkpoints
  - tells the user which prompt to run next
- `upgrade/upgrade-plan.md`
  - active task prompt for one planning task at a time
- `upgrade/upgrade-edit.md`
  - active task prompt for one core editing task at a time
- `upgrade/upgrade-edit-public-python.md`
  - active task prompt for one `public-python` editing task at a time
- `upgrade/upgrade-review.md`
  - active task prompt for one core review task at a time
- `upgrade/upgrade-review-public-python.md`
  - active task prompt for one `public-python` review task at a time
- `upgrade/report-current-agent-surface.md`
  - detailed supporting reference for `plan-1` current-surface reporting and rollout-only portfolio-scan work
- no other shared upgrade prompt in this folder should be treated as active unless it clearly reflects the current design in `docs/upgrade-design.md`

## Boundaries

- use this folder for upgrade-process prompt design, workflow orchestration, current-surface reporting for upgrades, and forward-looking upgrade work
- do not assume that a missing prompt should be replaced by improvising a stale prompt shape
- use `validation/review/` when the task is to assess prompt quality, hierarchy behavior, or documentation quality rather than to report the upgrade-relevant current agent surface of a repo

## Design Reference

- use `docs/upgrade-design.md` for the upgrade process design, workflow, prompt architecture, validation model, and next steps
- use `docs/architecture.md` for the shared folder structure
- use `authoring/agents/base.md` for general prompt-writing style

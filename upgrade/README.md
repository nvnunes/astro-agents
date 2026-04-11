# Upgrade Prompt Family

This folder holds the active shared prompt family for upgrading existing repos onto the `astro-agents` agent-surface model.

This `README.md` is the human-facing guide for designing and revising those prompts. Its peer file, `AGENTS.md`, is the agent-facing router for selecting them.

## Design Model

Use this folder for maintaining and extending the upgrade prompt family for bringing existing repos onto the shared `astro-agents` agent-surface model.

In this folder:

- `AGENTS.md` decides which prompt applies
- `README.md` explains the folder and points to the relevant design docs
- prompts follow the current design model in `docs/upgrade-design.md`
- `upgrade/upgrade-documentation-surface-profile.md` writes or updates the documentation surface profile declaration in the target repo's root `AGENTS.md`
- `upgrade/upgrade-plan.md` writes or revises `docs/upgrade/plan.md`
- `upgrade/upgrade-progress.md` reads the root `AGENTS.md` profile declaration plus `docs/upgrade/*.md` and recommends the next step in chat
- `upgrade/edit/` holds the core per-task editing prompts plus `edit/AGENTS.md` and `edit/base.md`; those edit prompts consult `docs/upgrade/plan.md` when a matching task row exists
- `upgrade/edit/public-python/` holds the `public-python` per-task editing prompts plus `edit/public-python/AGENTS.md` and `edit/public-python/base.md`; those edit prompts also consult matching saved plan rows when present
- `upgrade/upgrade-review.md` and `upgrade/upgrade-review-public-python.md` write the review artifacts under `docs/upgrade/`
- `upgrade/report-current-agent-surface.md` is a detailed supporting reference for planning work and rollout-only portfolio-scan work
- if a prompt for the requested task is not yet present, the design doc is the active source of truth

## Repo-Local Artifacts

- root `AGENTS.md`
  - stores the declared documentation surface profile used by later upgrade prompts
- `upgrade/upgrade-plan.md`
  - writes or updates `docs/upgrade/plan.md`
  - provides saved change scopes and guidance that edit prompts should consult when the matching task row exists
- `upgrade/edit/`
  - each direct task prompt writes or replaces one `docs/upgrade/edit-*.md` artifact
- `upgrade/upgrade-review.md`
  - writes `docs/upgrade/review-agent-surface.md` or `docs/upgrade/review-remaining-issues.md`
- `upgrade/upgrade-review-public-python.md`
  - writes `docs/upgrade/review-public-documentation-surface.md`
- `upgrade/upgrade-progress.md`
  - reads the root `AGENTS.md` profile declaration plus the saved `docs/upgrade/*.md` files and reports current status plus a recommended next step in chat only

## Prompts

- `upgrade/upgrade-documentation-surface-profile.md`
  - active setup prompt for declaring the documentation surface profile in the target repo's root `AGENTS.md`
- `upgrade/upgrade-plan.md`
  - active planning prompt
  - drafts or revises `docs/upgrade/plan.md`
  - uses `upgrade/report-current-agent-surface.md` as the detailed current-surface inspection standard
- `upgrade/upgrade-progress.md`
  - active progress and next-step prompt
  - reads the root `AGENTS.md` profile declaration plus `docs/upgrade/plan.md` and any saved edit and review artifacts
  - treats the root `AGENTS.md` documentation surface profile declaration as the first workflow precondition
- `upgrade/edit/base.md`
  - shared edit contract for the core edit prompts
  - reads `docs/upgrade/plan.md` when it exists and uses matching task rows as planning guidance
- `upgrade/edit/AGENTS.md`
  - local router that keeps the core edit base active for direct core edit prompts
- `upgrade/edit/public-python/base.md`
  - shared edit contract for the `public-python` edit prompts
  - inherits the same plan-consultation behavior for matching `public-python` task rows
- `upgrade/edit/public-python/AGENTS.md`
  - local router that keeps the broader core edit contract active and adds the `public-python` base for direct `public-python` edit prompts
- `upgrade/edit/`
  - one direct core editing prompt per edit task
- `upgrade/edit/public-python/`
  - one direct `public-python` editing prompt per edit task
- `upgrade/upgrade-review.md`
  - shared core review prompt for `review the agent surface` and `report remaining issues`
- `upgrade/upgrade-review-public-python.md`
  - shared `public-python` review prompt for `review the public documentation surface`
- `upgrade/report-current-agent-surface.md`
  - detailed supporting reference for planning inspection and rollout-only portfolio-scan work
- no other shared upgrade prompt in this folder should be treated as active unless it clearly reflects the current design in `docs/upgrade-design.md`

## Boundaries

- use this folder for upgrade-process prompt design, direct task prompting, current-surface reporting for upgrades, and forward-looking upgrade work
- do not assume that a missing prompt should be replaced by improvising a stale prompt shape
- use `validation/review/` when the task is to assess prompt quality, hierarchy behavior, or documentation quality rather than to report the upgrade-relevant current agent surface of a repo

## Design Reference

- use `docs/upgrade-design.md` for the upgrade process design, workflow, prompt architecture, validation model, and next steps
- use `docs/architecture.md` for the shared folder structure
- use `authoring/agents/upgrade-prompt.md` first for upgrade-prompt authoring style in this folder
- use `authoring/agents/base.md` only as the broader shared prompt-writing baseline underneath that upgrade-specific guide

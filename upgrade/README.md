# Upgrade Prompt Family

This folder is for the upgrade prompt family that is being rebuilt from the current upgrade-process design.

This `README.md` is the human-facing guide for designing and revising those prompts. Its peer file, `AGENTS.md`, is the agent-facing router for selecting them.

## Design Model

Use this folder for designing and rebuilding the upgrade prompt family for bringing existing repos onto the shared `astro-agents` agent-surface model.

In this folder:

- `AGENTS.md` decides which prompt applies
- `README.md` explains the folder and points to the relevant design docs
- prompts are being rebuilt from the current design model in `docs/upgrade-design.md`
- if a replacement prompt is not yet present, the design doc is the active source of truth

## Prompts

- no shared upgrade prompt in this folder should be treated as active unless it clearly reflects the current design in `docs/upgrade-design.md`
- until replacement prompts are written, treat this folder primarily as a design-and-rebuild surface

## Boundaries

- use this folder for upgrade-process prompt design and forward-looking upgrade work, not for reviewing the current repo state
- do not assume that a missing prompt should be replaced by improvising a stale prompt shape
- use `validation/review/` when the task is to assess the current project surface rather than plan changes

## Design Reference

- use `docs/upgrade-design.md` for the upgrade process design, workflow, prompt architecture, validation model, and next steps
- use `docs/architecture.md` for the shared folder structure
- use `authoring/agents/validation-prompt.md` for prompt-writing style

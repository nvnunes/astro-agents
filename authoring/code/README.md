# Code Authoring Guide Group

This folder contains shared guides for authoring source code.

This `README.md` is the human-facing guide for designing and revising those guides. Its peer file, `AGENTS.md`, is the agent-facing router for selecting them.

## Design Model

Use this folder for source-code authoring guides.

In this subgroup:

- `AGENTS.md` decides which guide applies
- `README.md` explains the subgroup and points to the relevant authoring guidance
- prompt assets such as `authoring/code/python.md` carry the substantive source-code authoring behavior

## Current Guides

- `authoring/code/python.md`
  - shared Python source-code authoring defaults and editing behavior

## Related Guide

- `authoring/agents/coding-prompt.md`
  - specialized style for prompts that define coding or code-review behavior

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale and external references behind this subgroup structure, use `docs/architecture.md`.

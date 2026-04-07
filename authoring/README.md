# Authoring Guide Group

This folder contains shared authoring guides.

This `README.md` is the human-facing guide for designing and revising those guides. Its peer file, `AGENTS.md`, is the agent-facing router for selecting them.

## Design Model

Use this folder for shared guides about how to author AI-facing prompts, human-facing writing, and source code.

In this subgroup:

- `AGENTS.md` decides which guide applies
- `README.md` explains the subgroup and points to the relevant authoring guides
- prompt assets carry the substantive authoring behavior

## Guide Families

### `agents/`

- `authoring/agents/base.md`
  - common writing discipline for agent-facing prompt assets
- `authoring/agents/agents-md.md`
  - specialized style for `AGENTS.md`
- `authoring/agents/writing-prompt.md`
  - prompts that define writing or revision behavior
- `authoring/agents/coding-prompt.md`
  - prompts that define coding or code-review behavior
- `authoring/agents/validation-prompt.md`
  - validation prompts under shared or repo-local validation libraries

### `writing/`

- `authoring/writing/base.md`
  - common writing discipline for human-facing writing
- `authoring/writing/repo-docs.md`
  - repo-facing documentation such as `README.md`, architecture docs, testing docs, API docs, and contribution guides
- `authoring/writing/science.md`
  - scientific papers, theses, proceedings, and proposals
- `authoring/writing/foundation.md`
  - conceptual or guiding framework documents
- `authoring/writing/plan.md`
  - working plans, phased roadmaps, and implementation plans

### `code/`

- `authoring/code/AGENTS.md`
  - router for source-code authoring guides
- `authoring/code/README.md`
  - human-facing guide for the `code/` family
- `authoring/code/python.md`
  - shared Python source-code authoring guide

## Special Note On `authoring/agents/agents-md.md`

`authoring/agents/agents-md.md` is more central to this prompt system than the other specialized prompt-writing guides because the hierarchy itself depends on `AGENTS.md` files staying short, operational, and scoped correctly.

Its core recommendations are strongly aligned with published guidance on `AGENTS.md`. Its additional hierarchy-specific rules are local design choices for this workspace.

## Selection Summary

Use the most specific applicable guide from the families above.

- For `AGENTS.md`, prefer `authoring/agents/agents-md.md`.
- For AI-facing prompts that define writing or revision behavior, prefer `authoring/agents/writing-prompt.md`.
- For AI-facing prompts that define coding or code-review behavior, prefer `authoring/agents/coding-prompt.md`.
- For validation prompts, prefer `authoring/agents/validation-prompt.md`.
- For human-facing writing, prefer the most specific guide under `authoring/writing/`.
- For source-code authoring, use `authoring/code/python.md` when Python is the applicable language.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale and external references behind this subgroup structure, use `docs/architecture.md`.

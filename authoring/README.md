# Authoring Prompt Family

This folder contains shared authoring prompts and guides.

Use it to understand the authoring families in this repo, what each one is for, and which guide to use next.

This folder is organized into shared authoring prompts and guides for prompt writing, human-facing writing, and source code.

For the repo-wide `AGENTS.md` / `README.md` / prompt role model, use `docs/architecture.md`.

## Prompt And Guide Families

### `agents/`

- `authoring/agents/base.md`
  - common writing discipline for agent-facing prompts
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
  - base authoring prompt for human-facing writing
- `authoring/writing/readme-md.md`
  - specialized authoring prompt for `README.md` as the main human-facing entrypoint doc
- `authoring/writing/repo-docs.md`
  - authoring prompt for repo-facing documentation other than `README.md`, such as architecture docs, testing docs, API docs, and contribution guides
- `authoring/writing/science.md`
  - authoring prompt for scientific papers, theses, proceedings, and proposals
- `authoring/writing/foundation.md`
  - authoring prompt for conceptual or guiding framework documents
- `authoring/writing/plan.md`
  - authoring prompt for working plans, phased roadmaps, and implementation plans

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

Use the most specific applicable prompt or guide from the families above.

- For `AGENTS.md`, prefer `authoring/agents/agents-md.md`.
- For `README.md`, prefer `authoring/writing/readme-md.md`.
- For AI-facing prompts that define writing or revision behavior, prefer `authoring/agents/writing-prompt.md`.
- For AI-facing prompts that define coding or code-review behavior, prefer `authoring/agents/coding-prompt.md`.
- For validation prompts, prefer `authoring/agents/validation-prompt.md`.
- For other human-facing repo docs, prefer the most specific authoring prompt under `authoring/writing/`.
- For source-code authoring, use `authoring/code/python.md` when Python is the applicable language.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale behind this folder structure, use `docs/architecture.md`.

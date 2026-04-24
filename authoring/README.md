# Shared Authoring Guides

This folder contains shared authoring guides and prompt-writing guides.

Use it to understand the authoring families in this repo, what each one is for, and which guide to use next.

This folder is organized into shared guides for prompt writing, human-facing writing, and source code.

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
- `authoring/agents/review-prompt.md`
  - review prompts and related prompts under shared or repo-local validation libraries

### `writing/`

- `authoring/writing/base.md`
  - base authoring guide for human-facing writing
- `authoring/writing/readme-md.md`
  - specialized authoring guide for `README.md` as the main human-facing starting document
- `authoring/writing/repo-docs.md`
  - authoring guide for repo-facing documentation other than `README.md`, such as architecture docs, testing docs, API docs, and contribution guides
- `authoring/writing/science.md`
  - authoring guide for scientific papers, theses, proceedings, and proposals
- `authoring/writing/research-log-entry.md`
  - authoring guide for research-log entries as evidence records
- `authoring/writing/foundation.md`
  - authoring guide for conceptual or guiding framework documents
- `authoring/writing/plan.md`
  - authoring guide for working plans, phased roadmaps, and implementation plans

### `code/`

- `authoring/code/AGENTS.md`
  - dispatcher for source-code authoring guides
- `authoring/code/README.md`
  - human-facing guide for the `code/` family
- `authoring/code/python.md`
  - shared Python source-code authoring guide

## Special Note On `authoring/agents/agents-md.md`

`authoring/agents/agents-md.md` is more central to this prompt system than the other specialized prompt-writing guides because the route structure itself depends on `AGENTS.md` files staying short, operational, and scoped correctly.

Its core recommendations are strongly aligned with published guidance on `AGENTS.md`. Its additional route-structure rules are local design choices for this workspace.

## Selection Summary

Use the most specific applicable prompt or guide from the families above.

- For `AGENTS.md`, prefer `authoring/agents/agents-md.md`.
- For `README.md`, prefer `authoring/writing/readme-md.md`.
- For AI-facing prompts that define writing or revision behavior, prefer `authoring/agents/writing-prompt.md`.
- For AI-facing prompts that define coding or code-review behavior, prefer `authoring/agents/coding-prompt.md`.
- For review prompts under `validation/`, prefer `authoring/agents/review-prompt.md`.
- For other human-facing repo docs, prefer the most specific authoring guide under `authoring/writing/`.
- For research-log entries, prefer `authoring/writing/research-log-entry.md`.
- For source-code authoring, use `authoring/code/python.md` when Python is the applicable language.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale behind this folder structure, use `docs/architecture.md`.

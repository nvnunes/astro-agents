# Validation Prompt Family

This folder contains shared validation prompts.

Use it to understand the validation prompt families in this repo, what each one is for, and which prompt or guide to use next.

For the repo-wide `AGENTS.md` / `README.md` / prompt role model, use `docs/architecture.md`.

## Folders

### `review/`

- `validation/review/full-agent-surface-review.md`
  - composite reusable prompt for a combined review of writing quality, prompt-writing quality, hierarchy behavior, and documentation architecture
- `validation/review/document-writing-review.md`
  - focused review of `README.md`, folder-level `README.md` files, and other human-facing docs against the applicable style guides
- `validation/review/prompt-writing-review.md`
  - focused review of `AGENTS.md` and other agent-facing prompts against the applicable prompt-writing guides
- `validation/review/hierarchy-behavior-review.md`
  - focused review of router discipline, hierarchy behavior, folder coherence, and prompt role drift
- `validation/review/documentation-architecture-review.md`
  - focused review of document organization, source-of-truth visibility, cross-document consistency, and portability when a repo may later become public

In this folder:

- review prompts assess current-state agent-surface quality

## Review Independence

The narrower review prompts under `validation/review/` are intended to be independently triggerable.

In this folder:

- a bootstrap prompt for a narrower review should invoke only that review by default
- a narrower review may mention adjacent issues only when needed to judge its own review lens
- broader synthesis belongs in `validation/review/full-agent-surface-review.md`, not in the narrower reviews
- if a narrower review repeatedly needs broader scoping to be useful, treat that as a validation-design problem rather than silently broadening the bootstrap prompt

## Bootstrap Prompts

Use these short prompts in fresh threads when you want the validation router to invoke a shared review with minimal extra scoping.

- `Do a full agent surface review.`
  - intended to trigger `validation/review/full-agent-surface-review.md` and return one combined assessment across the repo's full agent surface
- `Review this repository's human-facing docs using the shared document writing review prompt.`
  - intended to trigger `validation/review/document-writing-review.md`
- `Review this repository's AGENTS.md files and prompts using the shared prompt writing review prompt.`
  - intended to trigger `validation/review/prompt-writing-review.md`
- `Review this repository's prompt-routing and hierarchy behavior using the shared hierarchy behavior review prompt.`
  - intended to trigger `validation/review/hierarchy-behavior-review.md`
- `Review this repository's documentation architecture using the shared documentation architecture review prompt.`
  - intended to trigger `validation/review/documentation-architecture-review.md`

## Writing Validation Prompts

For validation-prompt writing style, use `authoring/agents/validation-prompt.md`.

That guide inherits the common prompt-writing discipline from `authoring/agents/base.md`.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale behind this folder structure, use `docs/architecture.md`.

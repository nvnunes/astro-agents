# Validation Prompt Group

This folder contains shared validation prompts.

This `README.md` is the human-facing guide for designing and revising those prompts. Its peer file, `AGENTS.md`, is the agent-facing router for selecting them.

## Design Model

Use this folder for shared validation prompts.

In this subgroup:

- `AGENTS.md` decides which prompt applies
- `README.md` explains the subgroup and points to the relevant writing guidance
- prompt assets carry the substantive review behavior

## Prompt Families

### `review/`

- `validation/review/full-agent-surface-review.md`
  - composite reusable prompt for a combined review of writing quality, prompt-writing quality, hierarchy behavior, and documentation architecture
- `validation/review/document-writing-review.md`
  - focused review of `README.md`, subgroup `README.md` files, and other human-facing docs against the applicable style guides
- `validation/review/prompt-writing-review.md`
  - focused review of `AGENTS.md` and other agent-facing prompt assets against the applicable prompt-writing guides
- `validation/review/hierarchy-behavior-review.md`
  - focused review of router discipline, hierarchy behavior, subgroup coherence, and prompt scope drift
- `validation/review/documentation-architecture-review.md`
  - focused review of document organization, source-of-truth surfacing, cross-document consistency, and public-safe portability

This leaves room for future validation families if review is no longer the only validation mode.

## Review Independence

The narrower review prompts under `validation/review/` are intended to be independently triggerable.

In this subgroup:

- a bootstrap prompt for a narrower review should invoke only that review by default
- a narrower review may mention adjacent issues only when needed to judge its own review lens
- broader synthesis belongs in `validation/review/full-agent-surface-review.md`, not in the narrower reviews
- if a narrower review repeatedly needs broader scoping to be useful, treat that as a validation-design problem rather than silently broadening the bootstrap prompt

## Writing Validation Prompts

For validation-prompt writing style, use `authoring/agents/validation-prompt.md`.

That guide inherits the common prompt-writing discipline from `authoring/agents/base.md`.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale and external references behind this subgroup structure, use `docs/architecture.md`.

# Validation Prompt Family

This folder contains shared validation prompts.

Use it to understand the validation prompt families in this repo, what each one is for, and which prompt or guide to use next.

For the repo-wide `AGENTS.md` / `README.md` / prompt role model, use `docs/architecture.md`.

## Folders

### `review/`

- `validation/review/full-agent-surface-review.md`
  - composite reusable prompt for a combined review of prompt-writing quality, hierarchy behavior, and the applicable profile-scoped documentation review bundle
- `validation/review/documentation-review.md`
  - selector prompt that resolves documentation review from the repo's documentation surface profile
- `validation/review/core-document-writing-review.md`
  - shared writing-review component used by profile-specific document-writing reviews after they select scope
- `validation/review/prompt-writing-review.md`
  - focused review of `AGENTS.md` and other agent-facing prompts against the applicable prompt-writing guides
- `validation/review/hierarchy-behavior-review.md`
  - focused review of router discipline, hierarchy behavior, folder coherence, and prompt role drift

### `review/private-default/`

- `validation/review/private-default/documentation-review.md`
  - implicit-default documentation bundle used when no non-default documentation surface profile is declared
- `validation/review/private-default/document-writing-review.md`
  - private-default selector that applies the shared core document-writing review to repo-facing docs
- `validation/review/private-default/documentation-architecture-review.md`
  - private-default review of document organization, source-of-truth visibility, cross-document consistency, and portability when a repo may later become public

### `review/public-python/`

- `validation/review/public-python/documentation-review.md`
  - documentation bundle for repos that declare `documentation surface profile: public-python`
- `validation/review/public-python/document-writing-review.md`
  - public-Python selector that applies the shared core document-writing review to the reachable public documentation surface
- `validation/review/public-python/documentation-architecture-review.md`
  - public-Python review of public entrypoints, reachability, source-of-truth ownership, and public-doc organization

In this folder:

- review prompts assess current-state agent-surface quality
- the shared validation family currently provides these documentation surface profiles:
  - implicit `private-default`
  - explicit `public-python`
- other documentation surface profiles may be implemented by higher-precedence workspace- or repo-local prompt layers

## Review Independence

The narrower review prompts under `validation/review/` are intended to be independently triggerable.

In this folder:

- a bootstrap prompt for a narrower review should invoke only that review by default
- a narrower review may mention adjacent issues only when needed to judge its own review lens
- broader synthesis belongs in `validation/review/full-agent-surface-review.md`, not in the narrower reviews
- if a narrower review repeatedly needs broader scoping to be useful, treat that as a validation-design problem rather than silently broadening the bootstrap prompt
- `validation/review/core-document-writing-review.md` is a shared building block, not a bootstrap prompt for generic docs review

## Bootstrap Prompts

Use these short prompts in fresh threads when you want the validation router to invoke a shared review with minimal extra scoping.

- `Do a full agent surface review.`
  - intended to trigger `validation/review/full-agent-surface-review.md` and return one combined assessment across the repo's full agent surface
- `Review this repository's docs using the shared documentation review prompt.`
  - intended to trigger `validation/review/documentation-review.md` and resolve through the repo's declared documentation surface profile, or `private-default` when none is declared
- `Review this repository's AGENTS.md files and prompts using the shared prompt writing review prompt.`
  - intended to trigger `validation/review/prompt-writing-review.md`
- `Review this repository's prompt-routing and hierarchy behavior using the shared hierarchy behavior review prompt.`
  - intended to trigger `validation/review/hierarchy-behavior-review.md`
- `Review this repository's docs using the shared private-default documentation review prompt.`
  - intended to trigger `validation/review/private-default/documentation-review.md`
- `Review this repository's docs using the shared public-Python documentation review prompt.`
  - intended to trigger `validation/review/public-python/documentation-review.md`

## Writing Validation Prompts

For validation-prompt writing style, use `authoring/agents/validation-prompt.md`.

That guide inherits the common prompt-writing discipline from `authoring/agents/base.md`.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale behind this folder structure, use `docs/architecture.md`.

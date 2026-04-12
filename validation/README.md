# Shared Validation Library

This folder contains shared reviews and related validation workflows.

Use it to understand the shared reviews in this repo, what each one is for, and which guide or review path to use next.

For the repo-wide `AGENTS.md` / `README.md` / prompt role model, use `docs/architecture.md`.

## Folders

### `review/`

- `validation/review/full-agent-surface-review.md`
  - coordinating review file for a combined review of prompt-writing quality, routing and authority behavior, and the applicable profile-scoped documentation review workflow
- `validation/review/documentation-review.md`
  - prompt that chooses documentation review from the repo's documentation surface profile
- `validation/review/upgrade-review.md`
  - shared review file for assessing a repo against `docs/upgrade-design.md`, recommending a documentation surface profile, and suggesting how to group the work
- `validation/review/core-document-writing-review.md`
  - shared writing-review file used by profile-specific document-writing reviews after they choose scope
- `validation/review/prompt-writing-review.md`
  - focused review of `AGENTS.md` and other agent-facing prompts against the applicable prompt-writing guides
- `validation/review/routing-and-authority-review.md`
  - focused review of routing discipline, authority behavior, folder coherence, and prompt role drift

### `review/private-default/`

- `validation/review/private-default/documentation-review.md`
  - implicit-default documentation review workflow used when no non-default documentation surface profile is declared
- `validation/review/private-default/document-writing-review.md`
  - private-default prompt that applies the shared core document-writing review to repo-facing docs
- `validation/review/private-default/documentation-architecture-review.md`
  - private-default review of document organization, source-of-truth visibility, cross-document consistency, and portability when a repo may later become public

### `review/public-python/`

- `validation/review/public-python/documentation-review.md`
  - documentation review workflow for repos that declare `documentation surface profile: public-python`
- `validation/review/public-python/document-writing-review.md`
  - public-Python prompt that applies the shared core document-writing review to the reachable public documentation surface
- `validation/review/public-python/documentation-architecture-review.md`
  - public-Python review of public starting documents, reachability, source-of-truth ownership, and public-doc organization

In this folder:

- review files assess current-state agent-surface quality
- upgrade review applies the shared upgrade design to a repo's current surface without turning upgrades into a separate prompt family
- the shared validation library currently provides these documentation surface profiles:
  - implicit `private-default`
  - explicit `public-python`
- other documentation surface profiles may be implemented by higher-authority workspace- or repo-local prompt files

## Review Independence

The narrower review prompts under `validation/review/` are intended to be independently triggerable.

In this folder:

- a starter request for a narrower review should invoke only that review by default
- a narrower review may mention adjacent issues only when needed to judge its own review criteria
- broader synthesis belongs in `validation/review/full-agent-surface-review.md`, not in the narrower reviews
- if a narrower review repeatedly needs broader scoping to be useful, treat that as a validation-design problem rather than silently broadening the starter request
- `validation/review/core-document-writing-review.md` is a shared building block, not a starter request for generic docs review

## Starter Requests

Use these short prompts in fresh threads when you want the validation dispatcher to invoke a shared review with minimal extra scoping.

- `Do a full agent surface review.`
  - intended to trigger `validation/review/full-agent-surface-review.md` and return one combined assessment across the repo's full agent surface
- `Review this repository's docs using the shared documentation review.`
  - intended to trigger `validation/review/documentation-review.md` and determine the repo's declared documentation surface profile, or `private-default` when none is declared
- `Upgrade this repository using the shared upgrade review.`
  - intended to trigger `validation/review/upgrade-review.md` and start from the review-first upgrade path
- `Review this repository for upgrade readiness using the shared upgrade review.`
  - intended to trigger `validation/review/upgrade-review.md` and return a recommended way to group the work plus a recommended documentation surface profile
- `How should I split up the upgrade work for this repository?`
  - intended to trigger `validation/review/upgrade-review.md` and return upgrade recommendations and a suggested way to group the work
- `Review this repository's AGENTS.md files and prompts using the shared prompt-writing review.`
  - intended to trigger `validation/review/prompt-writing-review.md`
- `Review this repository's prompt routing, workflow, and authority behavior using the shared routing-and-authority behavior review.`
  - intended to trigger `validation/review/routing-and-authority-review.md`
- `Review this repository's docs using the shared private-default documentation review.`
  - intended to trigger `validation/review/private-default/documentation-review.md`
- `Review this repository's docs using the shared public-Python documentation review.`
  - intended to trigger `validation/review/public-python/documentation-review.md`

## Writing Shared Reviews

For the writing style used by the review files in this folder, use `authoring/agents/review-prompt.md`.

That guide inherits the common prompt-writing discipline from `authoring/agents/base.md`.

## Design Reference

For the shared `README.md` / `AGENTS.md` rationale behind this folder structure, use `docs/architecture.md`.

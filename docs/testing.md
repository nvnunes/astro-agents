# Testing

This document is the human-facing source of truth for validation requirements in `astro-agents`.

This repository is primarily a prompt and documentation system, so its main verification path is agent surface validation rather than application-code testing.

## Purpose

Use this document to decide what validation is required when changing:

- `AGENTS.md` files
- `README.md`
- files under `docs/`
- prompts under `authoring/`, `validation/`, `upgrade/`, or `agents/`
- other files that change how agents should navigate, interpret, or apply this repository

Use `validation/README.md` for reusable bootstrap prompts that should trigger these checks in fresh threads.

## Canonical Checks

The canonical shared checks for this repo are the review prompts in `validation/review/`.

Use `validation/README.md` for the wider shared validation family, including documentation branches that this repo does not use in its own validation contract.

Use these shared review prompts directly in this repo:

- `validation/review/prompt-writing-review.md`
  - review `AGENTS.md` and other agent-facing prompts against the applicable prompt-writing guides
- `validation/review/hierarchy-behavior-review.md`
  - review router discipline, hierarchy behavior, folder coherence, and prompt role drift
- `validation/review/documentation-review.md`
  - select and run the shared documentation review bundle for the repo's declared documentation surface profile, or `private-default` when none is declared
- `validation/review/core-document-writing-review.md`
  - shared writing-review component used by profile-specific document-writing review prompts after they select scope
- `validation/review/private-default/documentation-review.md`
  - current documentation review bundle for `astro-agents`
- `validation/review/private-default/document-writing-review.md`
  - private-default document-writing review for repo-facing docs
- `validation/review/private-default/documentation-architecture-review.md`
  - private-default documentation-architecture review for document organization and source-of-truth structure
- `validation/review/full-agent-surface-review.md`
  - run a combined review across prompt-writing quality, hierarchy behavior, and the applicable profile-scoped documentation review bundle

## Repo-Local Validation

For repo-specific validation, use the shared checks as the baseline review sequence, then run repo-local validation prompts under `agents/validation/` when they apply.

- `agents/validation/root-agents-consistency-review.md`
  - a repo-local validation prompt that reviews whether the root `AGENTS.md` remains conceptually consistent with the recommended repo `AGENTS.md` pattern in `docs/usage.md`
- `agents/validation/shared-validation-template-consistency-review.md`
  - a repo-local validation prompt that reviews whether the shared-validation starter template in `docs/usage.md` remains conceptually consistent with this repo's validation contract in `docs/testing.md`

## Agent Surface Validation

Use agent surface validation when changes affect the repo's agent surface, including `AGENTS.md`, human-facing `README.md` files, relevant files under `docs/`, or prompts under `authoring/`, `validation/`, `upgrade/`, or `agents/`.

### Required Reviews

- Changes to `AGENTS.md` files:
  - run `validation/review/prompt-writing-review.md`
  - run `validation/review/hierarchy-behavior-review.md`
  - then run `agents/validation/root-agents-consistency-review.md` when the root repo `AGENTS.md` is changed

- Changes to human-facing `README.md` files or files under `docs/`:
  - run `validation/review/documentation-review.md`
  - then run `agents/validation/shared-validation-template-consistency-review.md` when `docs/usage.md` or `docs/testing.md` is changed

- Changes to routers or prompts under `authoring/`, `validation/`, or `upgrade/`:
  - run `validation/review/hierarchy-behavior-review.md`
  - run `validation/review/prompt-writing-review.md`

- Changes to repo-local prompts under `agents/`:
  - run `validation/review/prompt-writing-review.md`
  - run `validation/review/hierarchy-behavior-review.md`
  - then run an applicable repo-local validation prompt under `agents/validation/` only when one explicitly covers the changed part of the agent surface

- Changes that substantially alter the prompt system, validation structure, hierarchy model, or documentation architecture:
  - run `validation/review/full-agent-surface-review.md`

### Completion Standard

- Do not treat agent surface work as complete while direct validation findings remain unresolved.
- Distinguish direct violations from softer cleanup, but do not ignore severe findings.
- When more than one review applies, resolve overlapping findings once rather than treating each prompt as an independent rewrite request.

## Regression Priorities

Prioritize preventing regressions in:

- hierarchy clarity
- router discipline
- source-of-truth visibility
- examples and templates that are safe to keep if a repo may later become public
- consistency between `AGENTS.md`, `README.md`, `docs/`, and prompts

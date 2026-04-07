# Testing

This document is the human-facing source of truth for validation requirements in `astro-agents`.

This repository is primarily a prompt and documentation system, so its main verification path is agent-surface validation rather than application-code testing.

## Purpose

Use this document to decide what validation is required when changing:

- `AGENTS.md` files
- `README.md`
- files under `docs/`
- prompt assets under `authoring/`, `validation/`, or `agents/`
- other files that change how agents should navigate, interpret, or apply this repository

Use `docs/validation.md` for reusable bootstrap prompts that should trigger these checks in fresh threads.

## Canonical Checks

The canonical shared checks for this repo are the validation prompts in `validation/review/`.

Use these shared review prompts directly in this repo:

- `validation/review/document-writing-review.md`
  - review `README.md`, subgroup `README.md` files, and other human-facing docs against the applicable style guides
- `validation/review/prompt-writing-review.md`
  - review `AGENTS.md` and other agent-facing prompt assets against the applicable prompt-writing guides
- `validation/review/hierarchy-behavior-review.md`
  - review router discipline, hierarchy behavior, subgroup coherence, and prompt scope drift
- `validation/review/documentation-architecture-review.md`
  - review document organization, source-of-truth surfacing, cross-document consistency, and public-safe portability
- `validation/review/full-agent-surface-review.md`
  - run a combined review across writing quality, prompt-writing quality, hierarchy behavior, and documentation architecture, then add applicable local overlays under `agents/validation/`

## Repo-Local Overlay Checks

For repo-specific validation that sits on top of the shared checks, use local prompts under `agents/validation/`.

- `agents/validation/root-agents-consistency-review.md`
  - review whether the root `AGENTS.md` remains conceptually consistent with the recommended repo `AGENTS.md` pattern in `docs/usage.md`

## Agent-Surface Validation

Use agent-surface validation when changes affect the repo's agent-facing surfaces, including `AGENTS.md`, human-facing `README.md` files, relevant files under `docs/`, or prompt assets under `authoring/`, `validation/`, or `agents/`.

### Required Reviews

- Changes to `AGENTS.md` files:
  - run `validation/review/prompt-writing-review.md`
  - run `validation/review/hierarchy-behavior-review.md`
  - run `agents/validation/root-agents-consistency-review.md` when the root repo `AGENTS.md` is changed

- Changes to human-facing `README.md` files or files under `docs/`:
  - run `validation/review/document-writing-review.md`
  - run `validation/review/documentation-architecture-review.md`

- Changes to prompt-group routers or prompt assets under `authoring/` or `validation/`:
  - run `validation/review/hierarchy-behavior-review.md`
  - run `validation/review/prompt-writing-review.md`

- Changes to repo-local prompt assets under `agents/`:
  - run `validation/review/prompt-writing-review.md`
  - run `validation/review/hierarchy-behavior-review.md`
  - run an applicable local overlay prompt under `agents/validation/` only when one explicitly covers the changed surface

- Changes that substantially alter the prompt system, validation structure, hierarchy model, or documentation architecture:
  - run `validation/review/full-agent-surface-review.md`

### Completion Standard

- Do not treat agent-surface work as complete while direct validation findings remain unresolved.
- Distinguish direct violations from softer cleanup, but do not ignore severe findings.
- When more than one review applies, resolve overlapping findings once rather than treating each prompt as an independent rewrite request.

## Regression Priorities

Prioritize preventing regressions in:

- hierarchy clarity
- router discipline
- source-of-truth surfacing
- public-safe examples and templates
- consistency between `AGENTS.md`, `README.md`, `docs/`, and prompt assets

## Notes

- This repo does not currently define a separate executable test suite.
- If executable checks are added later, document them here rather than overloading `AGENTS.md`.

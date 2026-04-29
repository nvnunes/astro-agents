# Base Testing

This document is the shared base testing guidance for projects that use validation from `astro-agents`.

## Purpose

Use this document to decide the shared validation baseline for agent-surface work.

Use `validation/README.md` for the shared validation library, including the review catalog and reusable starter requests.

## Agent Surface Validation

Use agent surface validation when changes affect the project's agent surface, including `AGENTS.md`, human-facing `README.md` files, relevant files under `docs/`, or project-local prompt files.

### Typical Review Mapping

- Changes to `AGENTS.md` files:
  - run the shared prompt-writing review
  - run the shared routing-and-scope review

- Changes to human-facing `README.md` files or files under `docs/`:
  - run the shared documentation review
  - let it choose the applicable profile-scoped branch

- Changes to prompts that route or coordinate work:
  - run the shared routing-and-scope review
  - run the shared prompt-writing review

- Changes to project-local prompts under `agents/`:
  - run the shared prompt-writing review
  - run the shared routing-and-scope review
  - then run any applicable project-local review file under `agents/validation/` as a follow-on check

- Changes that substantially alter the prompt system, validation structure, route structure, or documentation architecture:
  - run the shared full agent surface review

## Completion Standard

- Do not treat agent surface work as complete while direct validation findings remain unresolved.
- Distinguish direct violations from softer cleanup, but do not ignore severe findings.
- When more than one review applies, resolve overlapping findings once rather than treating each review as a separate rewrite request.

## Regression Priorities

Prioritize preventing regressions in:

- route-structure clarity
- dispatch discipline
- source-of-truth visibility
- examples and templates that remain safe if a project later becomes public
- consistency between `AGENTS.md`, `README.md`, `docs/`, and project-local prompts

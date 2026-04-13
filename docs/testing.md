# Testing

This document is the human-facing source of truth for validation requirements in `astro-agents`.

This repository is primarily a prompt and documentation system, so its main verification path is agent surface validation rather than application-code testing.

## Shared Base

Use the shared base testing guidance in `validation/base-testing.md`.

Use `validation/README.md` for the human-facing shared review entrypoints and the upgrade-specific shared path.

Shared selector and combined-review outputs should include a short `Route Summary` showing the active review path.

## Repo-Local Validation

For repo-specific validation, use the shared checks as the baseline review sequence, then run repo-local review files under `agents/validation/` as follow-on checks when the changed scope or active shared review path makes them relevant.

- `agents/validation/root-agents-consistency-review.md`
  - a repo-local follow-on review file that checks whether the root `AGENTS.md` remains conceptually consistent with this repo's current routing, source-of-truth, and validation model after the shared `AGENTS.md` review path is active

## Agent Surface Validation

- Changes to `AGENTS.md` files:
  - then run `agents/validation/root-agents-consistency-review.md` when the root repo `AGENTS.md` is changed and the shared `AGENTS.md` review path is already active

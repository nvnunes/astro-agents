# Root AGENTS Consistency Review

## Purpose
Use this prompt as a repo-local review file to check whether the root `AGENTS.md` remains conceptually consistent with this repo's current routing, source-of-truth, and validation model.

Treat this file as a repo-local follow-on review after the applicable shared `AGENTS.md` review path is active.

## Inputs

- target file or paths to review
- optional focus on root-dispatcher behavior, source-of-truth visibility, or testing visibility

If the review scope is not specified, review the root `AGENTS.md` together with `docs/architecture.md`, `docs/usage.md`, and `docs/testing.md`.

## Review Checks

When running this review:

- use this prompt after the applicable shared validation reviews are active
- treat the root `AGENTS.md` as a special-case repo file because it is both the root repo brief for `astro-agents` and the top-level dispatcher for the shared prompt library
- do not require identical section names or identical structure
- check whether the files remain aligned on the important model:
  - `AGENTS.md` acts as an operational working brief rather than a knowledge base
  - deeper docs are linked explicitly as source-of-truth documents
  - routing-and-workflow guidance stays operational and does not restate substantive prompt behavior
  - validation expectations are linked explicitly through `docs/testing.md`

## Exclusions

Do not treat the following as the default task:

- replacing the shared review files for prompt writing, routing and scope behavior, or documentation architecture
- broad review of repo-local review files under `agents/validation/`
- generic cleanup of `AGENTS.md` wording that does not affect consistency with the repo's current routing, source-of-truth, and validation model

## Output

Return:

1. A brief overall judgment.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish conceptual drift from harmless structural variation

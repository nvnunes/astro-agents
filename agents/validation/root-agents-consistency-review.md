# Root AGENTS Consistency Review

## Purpose
Use this prompt as a repo-local validation prompt to review whether the root `AGENTS.md` remains conceptually consistent with the recommended repo `AGENTS.md` pattern in `docs/usage.md`.

## Inputs

- target file or paths to review
- optional focus on root-router behavior, source-of-truth visibility, or testing visibility

If the review scope is not specified, review the root `AGENTS.md` together with the recommended repo `AGENTS.md` pattern in `docs/usage.md`.

## Review Checks

When running this review:

- use this prompt after the applicable shared validation reviews are active
- treat the root `AGENTS.md` as a special-case repo file because it is both the root repo brief for `astro-agents` and the top-level router for the shared prompt library
- when the root `AGENTS.md` explicitly declares that shared-library entry-router role, do not treat omission of a generic higher-level bootstrap line from the downstream repo template in `docs/usage.md` as conceptual drift by itself
- do not require identical section names or identical structure
- check whether the files remain aligned on the important model:
  - `AGENTS.md` acts as an operational working brief rather than a knowledge base
  - deeper docs are linked explicitly as source-of-truth documents
  - routing stays operational and does not restate substantive prompt behavior
  - validation expectations are linked explicitly through `docs/testing.md`

## Exclusions

Do not treat the following as the default task:

- replacing the shared validation reviews for prompt writing, hierarchy behavior, or documentation architecture
- broad review of repo-local validation prompts under `agents/validation/`
- generic cleanup of `AGENTS.md` wording that does not affect consistency with `docs/usage.md`

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

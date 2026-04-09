# Shared Validation Template Consistency Review

## Purpose
Use this prompt as a repo-local validation prompt to review whether the shared-validation starter template in `docs/usage.md` remains conceptually consistent with this repo's concrete validation contract in `docs/testing.md`.

## Inputs

- target file or paths to review
- optional focus on shared review structure, repo-local validation placement, or required-review categories

If the review scope is not specified, review the `Pattern For Repos Using Shared Validation` section in `docs/usage.md` together with `docs/testing.md`.

## Review Checks

When running this review:

- use this prompt after the applicable shared validation reviews are active
- treat the `docs/usage.md` example as a reusable starter template for downstream repos, not as the concrete validation contract for `astro-agents`
- treat `docs/testing.md` as the concrete local validation contract for `astro-agents`
- do not require identical wording, identical section order, or identical review matrices
- check whether the files remain aligned on the important model:
  - downstream repos import shared validation by defining the structure in their own `docs/testing.md`
  - the shared review structure shown in `docs/usage.md` remains broadly consistent with the concrete validation structure this repo actually uses
  - repo-local validation prompts belong under `agents/validation/`
  - the starter template stays generic enough for downstream repos while remaining recognizable as a pattern instantiated by this repo's own validation contract

## Exclusions

Do not treat the following as the default task:

- replacing the shared validation reviews for document writing, prompt writing, hierarchy behavior, or documentation architecture
- broad review of repo-local validation prompts under `agents/validation/`
- generic cleanup of `docs/usage.md` or `docs/testing.md` wording that does not affect consistency between the starter template and the concrete local contract

## Output

Return:

1. A brief overall judgment.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish conceptual drift from harmless local adaptation

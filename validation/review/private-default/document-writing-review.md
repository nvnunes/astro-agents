# Private-Default Document Writing Review

## Purpose
Use this prompt to determine the `private-default` documentation surface and apply the shared core document-writing review to it.

Treat this file as an internal workflow step normally reached via `validation/review/private-default/documentation-review.md`.

## Inputs

- target root or target paths to review
- optional focus on `README.md`, folder-level `README.md`, `docs/`, or other repo-facing human-facing docs
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the applicable `README.md`, folder-level `README.md`, and other human-facing docs within the requested scope.

## Scope Identification

When running this review:

- identify applicable `README.md`, folder-level `README.md` files, and other human-facing docs dynamically from the target root
- treat repo-facing `README.md` files and repo docs as the default documentation surface for this profile
- use `validation/review/core-document-writing-review.md` as the shared writing-review file after choosing that surface
- inspect `docs/glossary.md` only when the target docs rely on recurring project terms whose meaning or ownership materially affects writing clarity
- inspect surrounding local context only when needed to determine document role or source-of-truth boundaries

## Review Criteria

Apply all review criteria from `validation/review/core-document-writing-review.md` to the selected `private-default` documentation surface.

## Exclusions

Do not treat the following as the default task:

- public package metadata review
- docs-site reachability analysis for published public docs
- generated API-doc inputs unless they are explicitly in scope for another reason
- route-structure design review beyond what is needed to judge document writing
- `AGENTS.md` review
- application-code review

## Output

Return:

1. A brief overall judgment of the writing quality within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the applicable style guide from `authoring/writing/`
- name the affected path or paths
- explain why the issue matters
- state the recommended revision move
- distinguish direct violations from softer cleanup opportunities

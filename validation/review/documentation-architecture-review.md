# Documentation Architecture Review

## Purpose
Use this prompt to review whether documentation is organized and surfaced in the way the shared usage guidance recommends.

## Inputs

- target root or target paths to review
- optional focus areas such as support-doc coverage, source-of-truth surfacing, cross-document consistency, or public-safe portability
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested repo or document set as a documentation system.

## Discovery

When running this review:

- discover `AGENTS.md`, `README.md`, and relevant `docs/` files dynamically from the target root
- inspect `docs/usage.md` as the source of truth for documentation organization recommendations
- inspect linked supporting docs when needed to judge source-of-truth surfacing and cross-document consistency

## Review Lenses

Evaluate the documentation system against `docs/usage.md`.

Required review lenses:

- documentation organization vs `docs/usage.md`
- source-of-truth surfacing
- cross-document consistency
- public-safe portability
- minimum support-doc expectations where absence materially weakens the repo

## Exclusions

Do not treat the following as the default task:

- detailed prose editing
- hierarchy-router discipline except where it affects documentation organization
- application-code review

## Output

Return:

1. A brief overall judgment of the documentation architecture within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated usage recommendation
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on document roles, surfacing, and system organization rather than on line-editing prose.

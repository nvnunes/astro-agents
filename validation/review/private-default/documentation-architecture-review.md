# Private-Default Documentation Architecture Review

## Purpose
Use this prompt to review whether `private-default` documentation is organized and linked in the way the shared usage guidance recommends.

## Inputs

- target root or target paths to review
- optional focus areas such as support-doc coverage, source-of-truth visibility, cross-document consistency, glossary fit, term ownership, terminology consistency, or portability when a repo may later become public
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested repo or document set as a documentation system.

## Scope Identification

When running this review:

- identify `AGENTS.md`, `README.md`, and relevant `docs/` files dynamically from the target root
- inspect `docs/usage.md` as the source of truth for documentation organization recommendations
- inspect `docs/architecture.md` when document-owner boundaries or stronger source-of-truth placement materially affect the review
- inspect `docs/runtime-model.md` when runtime or control-flow terminology materially affects the review
- inspect `docs/glossary.md` when recurring local terms, term ownership, glossary fit, or terminology drift materially affect the review
- inspect linked supporting docs when needed to judge source-of-truth visibility and cross-document consistency

## Review Criteria

Evaluate the documentation system against `docs/usage.md`.

Required review criteria:

- documentation organization vs `docs/usage.md`
- source-of-truth visibility
- redundancy versus stronger source-of-truth ownership
- intra-document redundancy versus stronger local section ownership
- cross-document consistency
- glossary fit and term ownership
- terminology consistency versus `docs/runtime-model.md` and `docs/glossary.md`, including reintroduction of terms those docs say to avoid
- portability when a repo may later become public
- minimum support-doc expectations where absence materially weakens the repo

## Exclusions

Do not treat the following as the default task:

- detailed prose editing
- public package metadata review
- public docs reachability review
- route-structure and routing-and-workflow discipline except where they affect documentation organization
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

Keep the review focused on document roles, linking, visibility, and system organization rather than on line-editing prose.

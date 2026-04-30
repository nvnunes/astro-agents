# Private-Default Documentation Architecture Review

## Purpose
Use this prompt to review whether `private-default` documentation is organized and linked in the way the shared private-default project guidance recommends.

For Python projects, also use it to review whether the documentation system makes Python architecture, verification, and development-workflow expectations visible through the right local source-of-truth docs.

Treat this file as an internal workflow step normally reached via `skills/documentation-surface-review/references/private-default/documentation-review.md`.

## Inputs

- target root or target paths to review
- optional focus areas such as support-doc coverage, source-of-truth visibility, cross-document consistency, glossary fit, term ownership, terminology consistency, or portability when a project may later become public
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested project or document set as a documentation system.

## Scope Identification

When running this review:

- identify `AGENTS.md`, `README.md`, and relevant `docs/` files dynamically from the target root
- inspect `skills/documentation-surface-review/references/private-default-projects.md`, especially `## Shared Private-Default Docs Review Model`, as the source of truth for private-default documentation organization recommendations
- determine whether the target project is clearly Python from project evidence such as `pyproject.toml`, `setup.py`, `setup.cfg`, Python package layout, or Python-first docs or commands
- when Python-specific documentation expectations materially affect the review, use the `## Documentation-Relevant Python Criteria` section in `skills/code-quality-review/references/python/code-quality-review.md` as a supporting lens
- inspect `docs/architecture.md` when document-owner boundaries or stronger source-of-truth placement materially affect the review
- use `skills/documentation-surface-review/references/runtime-terminology-review.md` when runtime or control-flow terminology materially affects the review
- inspect the target project's `docs/glossary.md`, when present, when recurring local terms, term ownership, glossary fit, or terminology drift materially affect the review
- inspect linked supporting docs when needed to judge source-of-truth visibility and cross-document consistency

## Review Criteria

Evaluate the documentation system against `skills/documentation-surface-review/references/private-default-projects.md`, especially `## Shared Private-Default Docs Review Model`.

When Python-specific documentation expectations materially affect the review, also use the documentation-relevant criteria from `skills/code-quality-review/references/python/code-quality-review.md`.

Required review criteria:

- documentation surface selection vs the `private-default` model
- project-facing starting document clarity and discoverability
- source-of-truth visibility
- redundancy versus stronger source-of-truth ownership
- intra-document redundancy versus stronger local section ownership
- cross-document consistency
- glossary fit and term ownership
- terminology consistency versus the target project's runtime, glossary, or terminology docs when those docs exist and materially apply
- portability when a project may later become public
- proportional support-doc expectations where absence materially weakens the project
- when Python-specific expectations materially apply, visibility of `docs/architecture.md`, `docs/testing.md`, and `docs/development.md` or equivalent owners for package-root API use, contracts, verification paths, lifecycle expectations, or workflow setup
- when Python-specific expectations materially apply, documentation support for package-root API visibility, lifecycle or contract ownership, generated-reference alignment, and canonical verification or environment workflow expectations where the project exposes them

## Exclusions

Do not treat the following as the default task:

- detailed prose editing
- public package metadata review
- public docs reachability review
- agent-surface structure and workflow discipline except where they affect documentation organization
- application-code review

## Output

Return:

1. A brief overall judgment of the documentation architecture within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated `private-default` guidance, or the Python documentation-relevant criterion when a Python-specific finding depends on that supporting lens
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on document roles, linking, visibility, and system organization rather than on line-editing prose.

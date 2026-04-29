# Private-Default Documentation Architecture Review

## Purpose
Use this prompt to review whether `private-default` documentation is organized and linked in the way the shared usage guidance recommends.

For Python projects, also use it to review whether the documentation system makes the shared Python-development expectations visible through the right local source-of-truth docs.

Treat this file as an internal workflow step normally reached via `validation/review/private-default/documentation-review.md`.

## Inputs

- target root or target paths to review
- optional focus areas such as support-doc coverage, source-of-truth visibility, cross-document consistency, glossary fit, term ownership, terminology consistency, or portability when a project may later become public
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested project or document set as a documentation system.

## Scope Identification

When running this review:

- identify `AGENTS.md`, `README.md`, and relevant `docs/` files dynamically from the target root
- inspect `docs/usage.md` as the source of truth for documentation organization recommendations
- determine whether the target project is clearly Python from project evidence such as `pyproject.toml`, `setup.py`, `setup.cfg`, Python package layout, or Python-first docs or commands
- determine whether the target project explicitly adopts `guidance/python-development.md` in its root `AGENTS.md` or local source-of-truth docs when Python-specific shared guidance might affect the review
- when the project explicitly adopts `guidance/python-development.md`, inspect it as additional shared guidance for architecture, testing, and development-doc expectations
- inspect `docs/architecture.md` when document-owner boundaries or stronger source-of-truth placement materially affect the review
- inspect `docs/runtime-model.md` when runtime or control-flow terminology materially affects the review
- inspect `docs/glossary.md` when recurring local terms, term ownership, glossary fit, or terminology drift materially affect the review
- inspect linked supporting docs when needed to judge source-of-truth visibility and cross-document consistency

## Review Criteria

Evaluate the documentation system against `docs/usage.md`.

When the project explicitly adopts `guidance/python-development.md`, also use it for Python-specific documentation architecture and support-doc coverage.

Required review criteria:

- documentation organization vs `docs/usage.md`
- source-of-truth visibility
- redundancy versus stronger source-of-truth ownership
- intra-document redundancy versus stronger local section ownership
- cross-document consistency
- glossary fit and term ownership
- terminology consistency versus `docs/runtime-model.md` and `docs/glossary.md`, including reintroduction of terms those docs say to avoid
- portability when a project may later become public
- minimum support-doc expectations where absence materially weakens the project
- when the project explicitly adopts `guidance/python-development.md`, visibility of `docs/architecture.md`, `docs/testing.md`, and `docs/development.md` or equivalent owners when Python-specific contracts, verification paths, or workflow setup materially need them
- when the project explicitly adopts `guidance/python-development.md`, documentation support for package-root API visibility, lifecycle or contract ownership, and canonical verification or environment workflow expectations where the project materially exposes them

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

- name the violated usage recommendation, or `guidance/python-development.md` when the project explicitly adopts it and a Python-specific finding depends on that guidance
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on document roles, linking, visibility, and system organization rather than on line-editing prose.

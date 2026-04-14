# Public-Python Documentation Architecture Review

## Purpose
Use this prompt to review whether the reachable public documentation surface of a Python project is organized and linked in the way the shared usage guidance and the `public-python` documentation surface model recommend.

Treat this file as an internal workflow step normally reached via `validation/review/public-python/documentation-review.md`.

## Inputs

- target root or target paths to review
- optional focus areas such as public starting documents, package metadata, docs reachability, generated API-doc ownership, contributor, release, or license surfaces, terminology consistency, or combined public-doc architecture
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested repo or document set as a public-Python documentation system.

## Scope Identification

When running this review:

- inspect `docs/usage.md` for the shared operational guidance on the `public-python` documentation surface profile
- inspect `guidance/public-python-projects.md`, especially `## Shared Public-Python Docs Review Model`, for the source-of-truth model behind that profile
- inspect `guidance/python-development.md` as the shared Python-development guidance for architecture, verification, and development-workflow doc expectations
- inspect `docs/runtime-model.md` when runtime or control-flow terminology materially affects the review
- inspect `docs/glossary.md` when recurring local terms, term ownership, or terminology drift materially affect the review
- identify the public starting documents and reachable public documentation surface within the requested scope
- inspect `README.md`, relevant public package metadata, docs-site configuration, reachable docs pages, and exposed contributor, release, license, example, or generated-reference surfaces as needed
- prefer source docs and documentation-generation inputs over built output when both are present
- inspect linked supporting docs only when needed to judge public reachability, source-of-truth visibility, and cross-surface consistency

## Review Criteria

Evaluate the public-Python documentation system against `docs/usage.md`, `guidance/public-python-projects.md`, especially `## Shared Public-Python Docs Review Model`, and `guidance/python-development.md`.

Apply the tier model from those source documents when judging severity:

- always-expected public-doc surface elements should be treated as the baseline architecture expectation
- conditionally exposed or claimed public-doc surfaces should be treated as required when the repo presents them publicly
- recommended public-doc elements for mature packages should be treated as softer improvement opportunities unless the repo explicitly claims them

Required review criteria:

- public documentation surface selection vs the `public-python` profile
- public starting document clarity and discoverability
- always-expected public-Python surface elements such as `README.md`, package-description metadata, and public package URLs when those links are published
- documentation role clarity and proportional structure for the size of the public docs surface
- source-of-truth visibility across `README.md`, package metadata, docs pages, generated-reference inputs, and other exposed public-doc surfaces
- source-of-truth visibility for Python-specific architecture, testing, and development docs when the repo exposes or depends on those workflows or contracts
- reachable docs organization rather than full-tree `docs/` inspection by default
- redundancy versus stronger source-of-truth ownership within the reachable public surface
- cross-surface consistency across `README.md`, package metadata, docs-site navigation, contributor or release docs, and exposed examples
- terminology consistency versus `docs/runtime-model.md` and `docs/glossary.md`, including reintroduction of terms those docs say to avoid
- generated API-doc ownership when public reference pages are generated from docstrings or docs-generation config
- public docs and examples aligned with supported package-root imports rather than internal module layout when the repo documents supported Python imports
- visibility of canonical verification or environment-workflow docs such as `docs/testing.md` and `docs/development.md` when the public surface or contributor path depends on them
- conditional public-surface expectations such as `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, and example assets when the public entry surface exposes or depends on them
- recommended public-doc elements for mature packages such as explicit docs navigation, drift checks, and a stable contributor-facing path into the docs surface

## Exclusions

Do not treat the following as the default task:

- detailed prose editing
- prompt-writing review
- route-structure and routing-and-workflow discipline except where they affect public documentation organization
- generic application-code review
- defaulting to the full `docs/` tree without reachability review
- built site output such as `site/` as the primary review target

## Output

Return:

1. A brief overall judgment of the public-Python documentation architecture within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated usage recommendation, `public-python` design principle, or `guidance/python-development.md` when a Python-specific documentation architecture finding depends on that guidance
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on public-surface organization, linking, visibility, and ownership rather than on line-editing prose.

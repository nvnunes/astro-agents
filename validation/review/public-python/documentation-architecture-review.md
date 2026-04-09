# Public-Python Documentation Architecture Review

## Purpose
Use this prompt to review whether the reachable public documentation surface of a Python project is organized and linked in the way the shared usage guidance and the `public-python` documentation surface model recommend.

## Inputs

- target root or target paths to review
- optional focus areas such as public entrypoints, package metadata, docs reachability, generated API-doc ownership, contributor, release, or license surfaces, or combined public-doc architecture
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested repo or document set as a public-Python documentation system.

## Discovery

When running this review:

- inspect `docs/usage.md` for the shared operational guidance on the `public-python` documentation surface profile
- inspect `docs/public-python-docs-design.md` for the deeper source-of-truth model behind that profile
- discover the public entrypoints and reachable public documentation surface within the requested scope
- inspect `README.md`, relevant public package metadata, docs-site configuration, reachable docs pages, and exposed contributor, release, license, example, or generated-reference surfaces as needed
- prefer source docs and documentation-generation inputs over built output when both are present
- inspect linked supporting docs only when needed to judge public reachability, source-of-truth visibility, and cross-surface consistency

## Review Lenses

Evaluate the public-Python documentation system against `docs/usage.md` and `docs/public-python-docs-design.md`.

Apply the tier model from those source documents when judging severity:

- always-expected public-doc surface elements should be treated as the baseline architecture expectation
- conditionally exposed or claimed public-doc surfaces should be treated as required when the repo presents them publicly
- recommended public-doc elements for mature packages should be treated as softer improvement opportunities unless the repo explicitly claims them

Required review lenses:

- public documentation surface selection vs the `public-python` profile
- public entrypoint clarity and discoverability
- always-expected public-Python surface elements such as `README.md`, package-description metadata, and public package URLs when those links are published
- documentation role clarity and proportional structure for the size of the public docs surface
- source-of-truth visibility across `README.md`, package metadata, docs pages, generated-reference inputs, and other exposed public-doc surfaces
- reachable docs organization rather than full-tree `docs/` inspection by default
- redundancy versus stronger source-of-truth ownership within the reachable public surface
- cross-surface consistency across `README.md`, package metadata, docs-site navigation, contributor or release docs, and exposed examples
- generated API-doc ownership when public reference pages are generated from docstrings or docs-generation config
- conditional public-surface expectations such as `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, and example assets when the public entry surface exposes or depends on them
- recommended public-doc elements for mature packages such as explicit docs navigation, drift checks, and a stable contributor-facing path into the docs surface

## Exclusions

Do not treat the following as the default task:

- detailed prose editing
- prompt-writing review
- hierarchy-router discipline except where it affects public documentation organization
- generic application-code review
- defaulting to the full `docs/` tree without reachability review
- built site output such as `site/` as the primary review target

## Output

Return:

1. A brief overall judgment of the public-Python documentation architecture within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated usage recommendation or `public-python` design principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on public-surface organization, linking, visibility, and ownership rather than on line-editing prose.

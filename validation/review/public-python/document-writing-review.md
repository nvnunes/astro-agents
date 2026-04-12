# Public-Python Document Writing Review

## Purpose
Use this prompt to determine the reachable public documentation surface of a Python project and apply the shared core document-writing review to it.

## Inputs

- target root or target paths to review
- optional focus on `README.md`, public docs pages, `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, examples, public API reference docs, or other human-facing docs within the reachable public docs surface
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the human-facing documents inside the reachable public documentation surface for the requested scope.

## Scope Identification

When running this review:

- inspect `docs/public-python-docs-design.md` as the source of truth for the `public-python` documentation surface model
- inspect `docs/usage.md` for the shared operational guidance on the `public-python` profile
- start from the public starting documents and reachability rules defined there
- identify the human-facing documents within that reachable public documentation surface
- use `validation/review/core-document-writing-review.md` as the shared writing-review file after choosing that surface
- prefer source docs and documentation-generation inputs over built output when both are present
- inspect public package metadata, docs-site configuration, examples, docstrings, and docs-related tests only when they materially define or verify the selected public docs surface
- keep the findings documentation-centered even when non-doc files are inspected as evidence

## Review Criteria

Apply the shared core document-writing review criteria to the reachable public documentation surface selected through the `public-python` documentation model.

Required review criteria:

- human-facing docs vs the applicable guide under `authoring/writing/`
- scanability and section discipline
- directness and precision
- source-of-truth boundaries
- duplication versus linking
- glossary alignment when recurring project terms materially affect clarity
- internal path usage versus any target-local path convention that is explicitly defined
- writing quality only on the reachable public documentation surface selected through the `public-python` documentation model
- public metadata, docs-site configuration, examples, docstrings, and docs-related tests as evidence inputs only when they materially define or verify that selected public docs surface
- findings that stay documentation-centered even when non-doc files are inspected as evidence

## Exclusions

Do not treat the following as the default task:

- defaulting to the full `docs/` tree without reachability review
- defaulting to the private-default repo-doc model
- built site output such as `site/` as the primary review target
- generic application-code review
- architecture review beyond what is needed to judge writing quality

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

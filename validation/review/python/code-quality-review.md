# Python Code-Quality Review

## Purpose
Use this prompt to review current-state Python source quality against the shared Python coding guide, shared Python-development guidance, and any relevant repo-local source-of-truth docs.

Treat this file as an internal workflow step normally reached via `validation/review/code-quality-review.md`.

## Inputs

- target root or target paths to review
- optional focus areas such as public API boundaries, contract ownership, validation behavior, lifecycle clarity, abstraction quality, naming, docstrings, or tests and docs alignment
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the applicable Python source files within the requested scope.

## Scope Identification

When running this review:

- identify applicable Python source files dynamically from the target root or target paths
- focus on repo-owned Python code rather than vendored code, build output, or virtual-environment content
- inspect `authoring/code/python.md` as the primary shared review standard
- inspect `guidance/python-development.md` as the shared architecture, workflow, and review-priority lens
- inspect root `AGENTS.md`, `docs/architecture.md`, `docs/testing.md`, and `docs/development.md` when they materially define local contracts, supported public API use, or required verification behavior
- inspect tests, examples, README snippets, and relevant docstrings only when they materially affect externally visible behavior, public API use, or code-and-doc alignment

## Review Criteria

Evaluate the Python code against `authoring/code/python.md` and `guidance/python-development.md`, then apply any stricter relevant repo-local expectations from the target repo's own source-of-truth docs.

Required review criteria:

- public API boundary clarity and package-root import discipline
- CLI-over-API discipline when a CLI exists
- contract ownership and validation clarity
- avoidance of silent coercion, hidden fallback behavior, and duplicated contract definitions
- lifecycle clarity and module or method ordering when the code has a strong lifecycle or execution flow
- helper ownership, abstraction quality, and removal of stale indirection
- naming accuracy and local readability
- comments and docstrings aligned with current contract and ownership behavior
- tests, docs, and examples aligned with externally visible behavior
- local verification expectations visible and appropriate for the changed or reviewed surface

## Exclusions

Do not treat the following as the default task:

- prompt-writing review
- route-structure review
- generic prose editing
- broad repo critique outside the reviewed Python scope
- style-only cleanup that does not materially affect contracts, ownership, lifecycle clarity, maintainability, or externally visible behavior

## Output

Return:

1. A brief overall judgment of the Python code quality within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated shared guide or repo-local source-of-truth expectation
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

When findings are line-local, include tight file and line references when they materially help.

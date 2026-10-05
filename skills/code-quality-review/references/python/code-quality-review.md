# Python Code-Quality Review

## Purpose
Use this prompt to review current-state Python source quality against the shared Python coding guide, Python review criteria, and any relevant project-local source-of-truth docs.

Treat this file as an internal workflow step normally reached via `skills/code-quality-review/references/code-quality-review.md`.

## Inputs

- target root or target paths to review
- optional focus areas such as public API boundaries, contract ownership, validation behavior, lifecycle clarity, abstraction quality, naming, docstrings, or tests and docs alignment
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the applicable Python source files within the requested scope.

## Scope Identification

When running this review:

- identify applicable Python source files dynamically from the target root or target paths
- focus on project-owned Python code rather than vendored code, build output, or virtual-environment content
- inspect `skills/python-code-writing/references/python.md` as the primary shared review standard
- inspect root `AGENTS.md`, `docs/architecture.md`, `docs/testing.md`, and `docs/development.md` when they materially define local contracts, supported public API use, or required verification behavior
- inspect tests, examples, README snippets, and relevant docstrings only when they materially affect externally visible behavior, public API use, or code-and-doc alignment

## Applying Review Profiles

Use the profiles and local-instruction discovery in `skills/code-quality-review/references/code-quality-review.md`. Full current standards are the default; lower profiles require an explicit applicable `AGENTS.md` override.

- Full current standards: apply the shared Python coding guide and all relevant criteria below.
- Incremental improvement: retain checks for behavior, data contracts, lifecycle safety, and regression coverage. Prefer local validation, documentation, and ownership improvements; do not demand package reorganization, API redesign, or replacement of established frameworks without a demonstrated need.
- Preservation-focused: check concrete correctness defects, unsafe state or resource handling, broken contracts, and inadequate verification. Follow established Python style when recommending repairs; do not turn the review into a typing, formatting, packaging, or modernization campaign.

Do not treat a legacy profile as permission to accept silent scientific changes, data loss, or an unverified repair. Cross-profile interfaces still need clear contracts.

## Review Criteria

Evaluate the Python code against `skills/python-code-writing/references/python.md` and the criteria below under the assigned review profile. Apply relevant project-local expectations, including explicit `AGENTS.md` profile overrides. Lower profiles constrain structural recommendations, not correctness or verification requirements.

Review criteria, applied under the assigned profile:

- public API boundary clarity and package-root import discipline
- CLI-over-API discipline when a CLI exists
- contract ownership and validation clarity
- explicit ownership for major concerns, validation rules, persisted field families, schemas, manifests, path rules, and version rules
- avoidance of silent coercion, hidden fallback behavior, and duplicated contract definitions
- lifecycle clarity and module or method ordering when the code has a strong lifecycle or execution flow
- compatibility layers, wrappers, and legacy shims kept outside the canonical package core when both must exist
- helper ownership, abstraction quality, and removal of stale indirection
- naming accuracy and local readability
- comments and docstrings aligned with current contract and ownership behavior
- tests, docs, and examples aligned with externally visible behavior
- local verification expectations visible and appropriate for the changed or reviewed surface
- project-local environment, bootstrap, hook, and daily workflow expectations documented when they materially affect reliable development or verification

## Documentation-Relevant Python Criteria

When another review skill uses this reference as a supporting lens, apply only the criteria that affect documentation truthfulness, source-of-truth visibility, or public project claims:

- supported package-root imports are clear and public docs or examples do not depend on internal module layout
- public API, CLI, persisted data, schema, config, or lifecycle contracts have an obvious source of truth
- canonical verification commands are visible in local testing or validation docs when users or contributors need them
- bootstrap, environment, hooks, and daily development commands are documented when they are part of the expected project workflow
- generated reference docs, docstrings, examples, README snippets, and tests are aligned with externally visible behavior
- docs do not imply support for compatibility shims, hidden fallbacks, or internal APIs that are not part of the canonical public surface

## Exclusions

Do not treat the following as the default task:

- prompt-writing review
- agent-surface structure review
- generic prose editing
- broad project critique outside the reviewed Python scope
- style-only cleanup that does not materially affect contracts, ownership, lifecycle clarity, maintainability, or externally visible behavior

## Output

Return:

1. A brief overall judgment of the Python code quality within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated Python review criterion, shared guide, or project-local source-of-truth expectation
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

When findings are line-local, include tight file and line references when they materially help.

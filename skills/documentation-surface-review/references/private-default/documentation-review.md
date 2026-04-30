# Private-Default Documentation Review

## Purpose
Use this prompt to run the `private-default` documentation review workflow.

Use it when the project has no declared non-default `documentation surface profile`, or when the user explicitly asks for the private-default documentation review path.

Treat this file as an internal documentation workflow normally reached via `skills/documentation-surface-review/references/documentation-review.md`, not as the normal generic docs-review starting point.

## Inputs

- target root or target paths to review
- optional focus on writing quality, document organization, source-of-truth structure, or combined documentation review
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the requested project or target root as the primary documentation review object.

## Scope Identification

When running this review:

- identify applicable `README.md`, folder-level `README.md`, and relevant `docs/` files dynamically from the target root
- inspect linked supporting docs only when needed to support the internal review steps below
- do not assume project names or hardcode expected project paths

## Internal Review Steps

Run the following internal review steps within the requested scope:

- `skills/documentation-surface-review/references/private-default/document-writing-review.md`
- `skills/documentation-surface-review/references/private-default/documentation-architecture-review.md`

Use these internal review steps to build one combined assessment rather than returning two isolated reports.

## Exclusions

Do not treat the following as the default task:

- prompt-writing review
- agent-surface structure review beyond what the documentation workflow needs
- public-package documentation-surface review
- application-code review

## Output

Return:

1. The selected documentation surface profile: `private-default`.
2. A brief overall judgment of the documentation review target within the requested scope.
3. Findings ordered by severity.
4. Concrete corrective actions after the findings.

For each finding:

- name the violated review category or principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

When combining findings:

- remove duplicates across the internal review steps
- keep the most specific wording when findings overlap
- preserve the most severe version of an overlapping issue
- keep documentation-system findings primary and place softer cleanup after them

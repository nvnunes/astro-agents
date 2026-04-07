# Hierarchy Behavior Review

## Purpose
Use this prompt to review whether the prompt hierarchy behaves as designed when treated as one instruction system.

## Inputs

- target root or target paths to review
- optional focus areas such as router discipline, subgroup coherence, or prompt scope drift
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested repo or target root together with discoverable repo and subtree `AGENTS.md` files beneath that root.

## Discovery

When running this review:

- discover applicable repo and subtree `AGENTS.md` files dynamically from the target root
- inspect prompt-group routers and prompt assets within the requested scope
- inspect `docs/architecture.md` as the source of truth for the hierarchy model
- inspect linked supporting docs only when needed to judge hierarchy behavior

## Review Lenses

Evaluate the hierarchy against `docs/architecture.md`.

Required review lenses:

- layer ownership
- `AGENTS.md` as routers
- actual hierarchy behavior vs `docs/architecture.md`
- subgroup coherence
- prompt scope drift
- shared-versus-local duplication
- `AGENTS.md` as map versus prompt substitute
- routing clarity and progressive disclosure

## Exclusions

Do not treat the following as the default task:

- generic prose review
- documentation-set completeness outside what affects hierarchy behavior
- application-code quality review

## Output

Return:

1. A brief overall judgment of hierarchy behavior within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated design principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on whether the hierarchy still behaves like the designed system rather than on general writing quality.

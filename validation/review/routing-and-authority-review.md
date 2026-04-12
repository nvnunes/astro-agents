# Routing And Authority Behavior Review

## Purpose
Use this prompt to review whether the prompt system's routing and authority behavior works as designed when treated as one instruction system.

## Inputs

- target root or target paths to review
- optional focus areas such as routing discipline, folder coherence, or prompt role drift
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested repo or target root together with the applicable repo and subtree `AGENTS.md` files beneath that root.

## Scope Determination

When running this review:

- determine applicable repo and subtree `AGENTS.md` files dynamically from the target root
- inspect folder-level routing-and-workflow files and other prompts within the requested scope
- inspect `docs/architecture.md` as the source of truth for the route structure model
- inspect linked supporting docs only when needed to judge routing and authority behavior

## Review Criteria

Evaluate the prompt system against `docs/architecture.md`.

Required review criteria:

- scope ownership
- `AGENTS.md` as routing-and-workflow files
- actual routing and authority behavior vs `docs/architecture.md`
- folder coherence
- prompt role drift
- shared-versus-local duplication
- `AGENTS.md` as map versus prompt substitute
- routing-and-workflow clarity and progressive disclosure

## Exclusions

Do not treat the following as the default task:

- generic prose review
- documentation-set completeness outside what affects routing and authority behavior
- application-code quality review

## Output

Return:

1. A brief overall judgment of routing and authority behavior within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated design principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on whether the routing and authority behavior still matches the designed system rather than on general writing quality.

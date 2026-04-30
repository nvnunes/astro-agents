# Scope And Workflow Review

## Purpose
Use this prompt to review whether the agent surface's instruction scope and workflow behavior work as designed.

## Inputs

- target root or target paths to review
- optional focus areas such as instruction-scope discipline, folder coherence, or prompt role drift
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested project or target root together with the applicable project and subtree `AGENTS.md` files beneath that root.

## Scope Determination

When running this review:

- determine applicable project and subtree `AGENTS.md` files dynamically from the target root
- inspect folder-level `AGENTS.md` files and other prompts within the requested scope
- inspect `docs/architecture.md` as the source of truth for the project structure and scope model
- inspect linked supporting docs only when needed to judge instruction scope and workflow behavior

## Review Criteria

Evaluate the agent surface against `docs/architecture.md`.

Required review criteria:

- scope ownership
- `AGENTS.md` as scoped working-brief files
- actual instruction scope and workflow behavior vs `docs/architecture.md`
- folder coherence
- prompt role drift
- shared-versus-local duplication
- `AGENTS.md` as map versus prompt substitute
- workflow clarity and progressive disclosure
- source-of-truth references that stay discoverable without making deeper docs sound like implicit prompt instructions
- drift away from the intended prompt-writing or skill-writing pattern, including project exceptions that restate shared defaults, private path assumptions, and broad `AGENTS.md` files that absorb material better carried by deeper docs, shared skills, or focused prompts

## Exclusions

Do not treat the following as the default task:

- generic prose review
- documentation-set completeness outside what affects instruction scope and workflow behavior
- application-code quality review

## Output

Return:

1. A brief overall judgment of instruction scope and workflow behavior within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the violated design principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

Keep the review focused on whether instruction scope and workflow behavior still match the designed system rather than on general writing quality.

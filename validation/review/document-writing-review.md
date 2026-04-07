# Document Writing Review

## Purpose
Use this prompt to review whether `README.md`, subgroup `README.md` files, and other human-facing docs follow the applicable shared writing-style guides.

## Inputs

- target root or target paths to review
- optional focus on `README.md`, subgroup `README.md`, `docs/`, or other human-facing docs
- optional target scope that narrows the review below the full target root

If the review scope is not specified, review the applicable `README.md`, subgroup `README.md`, and `docs/` files within the requested scope.

## Discovery

When running this review:

- discover applicable `README.md`, subgroup `README.md` files, and other human-facing docs dynamically from the target root
- use the applicable guide under `authoring/prose/` when reviewing human-facing docs
- default to `authoring/prose/repo-docs.md` for `README.md`, glossary/reference docs, and other repo-facing operational docs
- inspect surrounding local context only when needed to determine document role or source-of-truth boundaries

## Review Lenses

Evaluate documents against the applicable style guide.

Required review lenses:

- human-facing docs vs the applicable guide under `authoring/prose/`
- scanability and section discipline
- directness and precision
- source-of-truth boundaries
- duplication versus linking
- internal path usage versus any target-local path convention that is explicitly defined

## Exclusions

Do not treat the following as the default task:

- hierarchy design review beyond what is needed to judge document writing
- `AGENTS.md` review
- prompt-asset writing review for files under `authoring/`, `validation/`, or repo-local `agents/`
- application-code review
- broad content rewrites without first identifying concrete issues

## Output

Return:

1. A brief overall judgment of the writing quality within the requested scope.
2. Findings ordered by severity.
3. Concrete corrective actions after the findings.

For each finding:

- name the applicable style guide
- name the affected path or paths
- explain why the issue matters
- state the recommended revision move
- distinguish direct violations from softer cleanup opportunities

Keep the review focused on whether the documents are written in the right operational style for their role.
When internal file references appear, apply a repo-specific path convention only when the target repo explicitly defines one.

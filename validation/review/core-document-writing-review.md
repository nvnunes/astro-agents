# Core Document Writing Review

## Purpose
Use this prompt as the shared prose-review component for profile-specific documentation review prompts.

This prompt reviews writing quality only. It does not decide which documentation surface belongs in scope.

## Inputs

- target root or target paths to review
- documentation files or other human-facing writing inputs already selected by a profile-specific documentation review prompt
- optional focus on `README.md`, folder-level `README.md` files, docs pages, contribution docs, changelog docs, or other human-facing docs within that already selected surface

If the review scope is not specified, review the human-facing documentation files already selected by the active profile-specific review path.

## Discovery

When running this review:

- use this prompt only after a profile-specific documentation review prompt has selected the documentation surface
- review only the human-facing documentation files inside that selected surface
- use the applicable guide under `authoring/writing/` when reviewing human-facing docs
- default to `authoring/writing/readme-md.md` for `README.md` and folder-level `README.md` files
- default to `authoring/writing/repo-docs.md` for glossary/reference docs and other repo-facing or public-facing operational docs
- inspect `docs/glossary.md` only when the target docs rely on recurring project terms whose meaning or ownership materially affects writing clarity
- inspect surrounding local context only when needed to determine document role or source-of-truth boundaries
- do not widen the selected documentation surface on your own

## Review Lenses

Evaluate documents against the applicable style guide.

Required review lenses:

- human-facing docs vs the applicable guide under `authoring/writing/`
- scanability and section discipline
- directness and precision
- source-of-truth boundaries
- duplication versus linking
- glossary alignment when recurring project terms materially affect clarity
- internal path usage versus any target-local path convention that is explicitly defined

## Exclusions

Do not treat the following as the default task:

- deciding which documentation surface profile applies
- deciding the default documentation surface for the repo
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

Keep the review focused on whether the selected documents are written in the right operational style for their role.
When internal file references appear, apply a repo-specific path convention only when the target repo explicitly defines one.

# Base Docs Writing

## Purpose
Use this style as the common base for human-facing documents. Apply it to documents meant to be read directly by collaborators, maintainers, reviewers, contributors, or future users, then apply a more specific document-type guide on top when one exists.

Specialized document guides should inherit this base and add only the constraints, preservation rules, and adaptation behavior that are specific to their document type.

## Success Criteria
- Make the document easy to scan and use.
- Preserve the document's role and intended audience.
- Improve clarity, precision, and structure.
- Reduce repetition, filler, and vague claims.

## Common Reuse Rules
- Preserve the document's role before improving its prose.
- Keep the document usable for its actual reading mode, whether that is orientation, direct inclusion, conceptual reference, or working execution.
- Let specialized guides add role-specific constraints instead of repeating this base.
- Repeat a base rule in a specialized guide only when that guide needs to sharpen, qualify, or replace it.

## Style
- Use a direct, technical, restrained tone.
- Prefer short sections and flat bullets where they improve scanability.
- Lead with purpose, scope, or the document's role.
- Keep motivation proportional to the document's purpose.

## Language Discipline
- Prefer direct verbs such as `defines`, `documents`, `runs`, `uses`, `owns`, `requires`, `returns`, and `verifies`.
- Avoid promotional or marketing language.
- Avoid vague claims such as `supports`, `improves`, or `handles` unless the object is explicit.
- Avoid broad narrative buildup when the reader needs actionable guidance.
- Let each section answer one clear question.

## Structure
- Make the document's role obvious near the top.
- Prefer stable section names when they help orientation.
- Do not let one document drift into doing the job of another.

## Scope Discipline
- Do not let a human-facing document drift into a neighboring document role.
- Keep explanatory documents explanatory, operational documents operational, and direct-inclusion prose focused on the target document.
- When a document needs to point to a deeper source of truth, prefer linking over duplication.
- For internal file references inside this repo, prefer repo-root-relative paths such as `docs/architecture.md` or `authoring/writing/repo-docs.md`.

## Preservation And Revision
When revising existing docs:
- Preserve the document's role instead of forcing it toward a different document type.
- Do not turn a narrow document into a broader one unless the task explicitly requires it.
- Preserve explicit terminology, labels, and structural cues unless change is required.
- Keep cross-references accurate.
- Improve scanability, precision, and section structure.

## Output
- When revising prose, return the revised text directly unless explanation is requested.
- When reviewing docs, identify concrete issues before proposing replacement text.
- When both review and revision are requested, give the review first and then provide revised text.

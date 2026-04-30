---
name: documentation-surface-review
description: Review documentation surfaces, documentation surface profiles, source-of-truth docs, README scope, public Python docs, private/default docs, and documentation architecture. Use for documentation-only validation and docs completion checks, not prompt, AGENTS.md, SKILL.md, or source-code review.
---

# Documentation Surface Review

Use this skill for documentation-surface validation.

Start with `references/documentation-review.md` to choose the active documentation surface profile and run the matching profile workflow. Load narrower references only as needed:

- `references/private-default/documentation-review.md` for private, internal, or default-profile project documentation.
- `references/public-python/documentation-review.md` for public Python package documentation.
- `references/core-document-writing-review.md` after a profile workflow has selected the documentation surface.
- `references/private-default-projects.md` as the private/default documentation surface model.
- `references/public-python-projects.md` as the public Python documentation surface model.
- `references/runtime-terminology-review.md` when runtime, routing, instruction, context, or control-flow terminology materially affects documentation review.

Return findings first, ordered by severity, then concrete corrective actions. Include a short review path summary when profile selection or multiple internal references materially shaped the review.

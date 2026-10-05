---
name: code-quality-review
description: Review source-code quality, architecture, ownership, contracts, lifecycle clarity, public API boundaries, tests, validation behavior, abstractions, and maintainability. Use for code-quality reviews, not prompt, AGENTS.md, SKILL.md, or documentation reviews.
---

# Code Quality Review

Use this skill for current-state source-code quality review.

Read `references/code-quality-review.md` for shared review profiles, local-instruction discovery, and workflow selection. Then read the applicable language workflow:

- Python: `references/python/code-quality-review.md`.
- MATLAB: `references/matlab/code-quality-review.md`.

Use full current standards by default. Lower profiles require an explicit override in applicable `AGENTS.md` instructions. Read those instructions before assigning profiles or evaluating the code.

If no built-in workflow fits the requested language or stack, return a validation-design finding rather than pretending the shared review covers it.

Focus findings on code behavior and maintainability. Include docs or tests only when they materially define contracts, public usage, verification expectations, or review evidence.

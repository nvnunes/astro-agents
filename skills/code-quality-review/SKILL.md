---
name: code-quality-review
description: Review source-code quality, architecture, ownership, contracts, lifecycle clarity, public API boundaries, tests, validation behavior, abstractions, and maintainability. Use for code-quality reviews, not prompt, AGENTS.md, SKILL.md, or documentation reviews.
---

# Code Quality Review

Use this skill for current-state source-code quality review.

Read `references/code-quality-review.md` to choose the applicable workflow. If the requested scope is clearly Python, read `references/python/code-quality-review.md`.

If no built-in workflow fits the requested language or stack, return a validation-design finding rather than pretending the shared review covers it.

Focus findings on code behavior and maintainability. Include docs or tests only when they materially define contracts, public usage, verification expectations, or review evidence.

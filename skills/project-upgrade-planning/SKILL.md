---
name: project-upgrade-planning
description: Assess project upgrade readiness against the shared astro-agents upgrade model and plan upgrade grouping, sequencing, documentation profile choices, validation path, and next steps. Use for migration planning, not routine agent-surface review or implementation work.
---

# Project Upgrade Planning

Use this skill to plan how a project should be upgraded to the shared `astro-agents` model.

Read `references/upgrade-review.md` for the review-first upgrade workflow and `references/upgrade-model.md` for the durable upgrade model. Pair with `$agent-surface-review` only when current agent-surface state needs a combined review before upgrade planning.

Return a practical upgrade plan: current-state judgment, recommended documentation surface profile, suggested work grouping, sequencing, validation expectations, and any local risks or blockers.

Do not turn upgrade planning into broad code-quality review unless code-quality issues materially affect the upgrade path.

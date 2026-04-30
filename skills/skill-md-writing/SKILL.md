---
name: skill-md-writing
description: Write, revise, or review SKILL.md files and skill packages, including frontmatter descriptions, activation boundaries, progressive-disclosure references, scripts, assets, and runtime metadata. Use for skill authoring, not AGENTS.md files or general prompt files.
---

# SKILL.md Writing

Use this skill for skill packages and `SKILL.md` files.

Read `references/skill-md.md` before creating or revising a `SKILL.md`. Pair with `$prompt-writing` only when the skill body needs broader reusable prompt discipline; keep `$skill-md-writing` primary for skill packaging, frontmatter, resources, and runtime metadata.

Prioritize the frontmatter description first, because it is the discovery surface. Keep the body concise and procedural. Put detailed workflows, schemas, examples, and variant-specific guidance in directly linked `references/` files, and use `scripts/` only when deterministic execution or repeated tooling materially helps.

When reviewing a skill, check trigger clarity, scope creep, stale references, script instructions, metadata alignment, and whether the body is carrying content that belongs in a reference.

---
name: prompt-writing
description: Write, revise, or review reusable agent-facing prompts, prompt-library files, route prompts, mode prompts, operation prompts, workflow instructions, and review prompts. Use for prompt design that is not AGENTS.md or SKILL.md; do not use for human-facing docs or application source code.
---

# Prompt Writing

Use this skill to create or improve reusable agent-facing prompt files.

Read `references/prompt-base.md` for the common prompt-writing discipline before making substantive prompt changes. Then read only the specific reference that matches the prompt type:

- `references/writing-prompt.md` for prompts that guide writing or revision of human-facing prose.
- `references/coding-prompt.md` for prompts that guide source-code writing, editing, or refactoring.
- `references/review-prompt.md` for prompts that guide review or validation workflows.

If the task is specifically about `AGENTS.md`, switch to `$agents-md-writing` as the primary skill and use this skill only for shared prompt-writing discipline when needed. If the task is specifically about `SKILL.md`, switch to `$skill-md-writing` as the primary skill and use this skill only for shared prompt-writing discipline when needed.

Keep prompt files operational. Put durable background, conceptual rationale, or project-specific source-of-truth facts in docs or references instead of expanding the active prompt.

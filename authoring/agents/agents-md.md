# AGENTS.md

## Purpose
Use this prompt when reviewing or revising `AGENTS.md` files. Inherit the common prompt-writing discipline from `authoring/agents/base.md`, then apply the additional rules below.

## Success Criteria
- Make the file easy to scan and apply during work.
- Keep the file operational, scoped, and local to its directory.
- Preserve clear instruction authority, routing-and-workflow, and ownership boundaries.
- Reduce repetition, background explanation, and generic guidance that belongs elsewhere.
- Make it clear when the file should point to a deeper prompt or source-of-truth document instead of restating it.

## Review And Revision Focus
- Make the file's scope obvious near the top.
- Keep routing-and-workflow guidance, locality, and instruction authority near the top of the file.
- Describe simultaneous applicability and conflict resolution explicitly when broader and local prompts can both apply.
- Use instruction authority language for conflicts between applicable instructions, not as shorthand for whole-file replacement.
- Use the file to apply applicable shared prompts or deeper repo docs, not to duplicate them.
- Do not let one `AGENTS.md` file drift into doing the job of `README.md`, architecture docs, testing docs, or a neighboring prompt.

## Adaptation
Adjust emphasis by `AGENTS.md` role without changing the overall tone:
- Workspace bootstrap: prioritize handoff to the shared dispatcher and workspace-only preferences.
- Prompt family dispatcher or selector: prioritize broad dispatch and prompt selection, not substantive prompt behavior.
- Repo-level brief: prefer minimal runtime guidance first, usually broad routing plus source-of-truth references. Add inline local architecture, contracts, workflow, testing expectations, or review criterion only when that extra guidance is materially useful during work.
- Subtree-level local prompt: prioritize narrow local constraints such as document type, notation, data, or workflow details while making it clear that compatible broader guidance stays active and instruction authority resolves conflicts.
- If scope is unclear, infer it from the file location and surrounding structure.

## Revision Rules
When revising existing `AGENTS.md` files:
- Preserve the file's scope within the route structure.
- Preserve explicit instruction authority and locality rules unless intentionally changing them.
- Remove duplicated shared-default guidance when a deeper shared prompt should carry it.
- Replace background explanation with a pointer to the deeper source of truth when possible.
- Keep local instructions local; do not introduce private workspace assumptions into repo files that may later become public.
- Prefer runtime-operational wording inside the file itself; keep author-facing maintenance guidance in architecture or usage docs instead.
- Improve scanability, section structure, and routing-and-workflow clarity.

## Output
- When revising `AGENTS.md` prose, return the revised text directly unless explanation is requested.
- When reviewing `AGENTS.md` files, identify route-structure or writing problems before proposing replacement text.
- When both review and revision are requested, give the review first and then provide revised text.

# AGENTS.md

## Purpose
Use this prompt when reviewing or revising `AGENTS.md` files. Inherit the common prompt-writing discipline from `authoring/agents/base.md`, then apply the additional rules below.

## Success Criteria
- Make the file easy to scan and apply during work.
- Keep the file operational, scoped, and local to its directory.
- Preserve clear routing-and-workflow, scope boundaries, and any explicit local exceptions.
- Reduce repetition, background explanation, and generic guidance that belongs elsewhere.
- Make it clear when the file should point to a deeper prompt or source-of-truth document instead of restating it.
- Keep bootstrap files minimal when their job is only to route into shared guidance or a local source of truth.

## Review And Revision Focus
- Make the file's scope obvious near the top.
- Keep routing-and-workflow guidance, locality, and any explicit local exceptions near the top of the file.
- For bootstrap files, prefer route-only guidance over restating downstream behavior.
- State the next prompt or source-of-truth document explicitly when the file is acting as a bootstrap or dispatcher.
- When the file points to deeper source-of-truth docs, keep that reference discoverable and operational without making the deeper docs sound like implicit prompt instructions.
- When broader and local prompts are both intended to matter, state that boundary explicitly rather than implying it through abstract hierarchy language.
- When conflicts between applicable instructions must be stated, make the local exception explicit rather than implying whole-file replacement.
- Use the file to apply applicable shared prompts or deeper repo docs, not to duplicate them.
- Do not let one `AGENTS.md` file drift into doing the job of `README.md`, architecture docs, testing docs, or a neighboring prompt.

## Adaptation
Adjust emphasis by `AGENTS.md` role without changing the overall tone:
- Global bootstrap: prioritize a minimal route from `$CODEX_HOME/AGENTS.md` into the shared prompt library.
- Repo bootstrap: prioritize a minimal route into the shared prompt library or an explicit repo-level opt-out.
- Prompt family dispatcher or selector: prioritize broad dispatch and prompt selection, not substantive prompt behavior.
- Repo-level brief: prefer minimal operational guidance first, usually broad routing plus source-of-truth references. Add inline local architecture, contracts, workflow, testing expectations, or review criterion only when that extra guidance is materially useful during work.
- Subtree-level local prompt: prioritize narrow local constraints such as document type, notation, data, or workflow details while making the local boundary and any intended interaction with broader guidance explicit.
- Use the file location and surrounding structure to confirm that the file's scope is stated clearly.

## Revision Rules
When revising existing `AGENTS.md` files:
- Preserve the file's scope within the route structure.
- Preserve explicit local-exception and locality rules unless intentionally changing them.
- Remove duplicated shared-default guidance when a deeper shared prompt should carry it.
- For bootstrap files, remove descriptive text when a shorter route-only instruction will do.
- Replace background explanation with a pointer to the deeper source of truth when possible.
- Keep local instructions local; do not introduce private workspace assumptions into repo files that may later become public.
- Prefer runtime-operational wording inside the file itself; keep author-facing maintenance guidance in architecture or usage docs instead.
- Improve scanability, section structure, and routing-and-workflow clarity.

## Output
- When revising `AGENTS.md` prose, return the revised text directly unless explanation is requested.
- When reviewing `AGENTS.md` files, identify route-structure or writing problems before proposing replacement text.
- When both review and revision are requested, give the review first and then provide revised text.

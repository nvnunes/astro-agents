# Upgrade Plan

## Purpose
Use this prompt to inspect the current surface, draft or revise the saved upgrade plan, and write `docs/upgrade/plan.md` in the target repo.

Use it when the user wants to:

- start planning an upgrade
- revise an existing plan
- review the current saved plan before changing or approving it
- save the current planning decisions into `docs/upgrade/plan.md`
- mark the saved plan `approved` after explicit user approval

Use `astro-agents/upgrade/report-current-agent-surface.md` as the detailed inspection standard for the current-surface read that supports the plan.

## Inputs

- target root or target paths
- documentation surface profile declared in the target repo's root `AGENTS.md`
- optional plan path, defaulting to `docs/upgrade/plan.md` in the target repo
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

Use the documentation surface profile declared in the root `AGENTS.md` as workflow input. Do not choose, approve, or reinterpret that profile inside this prompt.

If the root `AGENTS.md` does not yet declare the documentation surface profile, stop and send the user to `astro-agents/upgrade/upgrade-documentation-surface-profile.md` before inspecting the repo or writing `docs/upgrade/plan.md`.

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` first.
   - use the `## Begin An Upgrade` table there as the source of truth for any exact launch prompt you recommend back to the user
2. Read the target repo's root `AGENTS.md` and confirm that it declares `Documentation surface profile: <profile>.`
3. Inspect the target repo fresh, using `astro-agents/upgrade/report-current-agent-surface.md` as the detailed standard for inspection order, checks, and current-state reporting.
4. Decide the main goals, out-of-scope areas, editing tasks in scope, change scopes, and review requirements.
5. Keep the saved plan simple: one edit task per row in the intended execution order, with one change scope and a brief note.
6. When the user wants the saved plan written or revised, create `docs/upgrade/` in the target repo when it does not already exist, then create or update `docs/upgrade/plan.md`.
7. Treat the plan file as a working record:
   - use `draft` while the plan is still being discussed or revised
   - use `approved` only after the user explicitly approves the current saved plan
   - if an approved plan is revised, set it back to `draft` until the user reapproves it
8. When the user explicitly asks to review the current saved plan without rewriting it, inspect the current saved plan and the repo, leave `docs/upgrade/plan.md` unchanged, and either:
   - return concrete recommended revisions, or
   - guide the user through the plan step by step, depending on the request
9. Keep review expectations, assumptions, current-surface summary, and open issues in `## Notes` rather than turning the file into a larger workflow ledger.

## Exclusions

- do not choose, approve, or reinterpret the documentation surface profile
- do not perform editing tasks or review tasks inside this prompt
- do not turn `docs/upgrade/plan.md` into a centralized controller for the workflow
- do not add next-step routing or prompt-to-run-next fields to the saved plan
- do not broaden the plan beyond the requested target root or scope
- do not rewrite `docs/upgrade/plan.md` when the user explicitly asked only to review the current saved plan without changing it

## Output

Write or replace `docs/upgrade/plan.md` in this structure:

```md
# Upgrade Plan

## Metadata

- target root:
- documentation surface profile:
- prompt used: `astro-agents/upgrade/upgrade-plan.md`
- last updated:
- status: `draft` | `approved`

## Planned Tasks

| Task | Applicability | Change scope | Notes |
| --- | --- | --- | --- |

## Notes
```

For `## Planned Tasks`:

- use the exact edit-task names from `astro-agents/docs/upgrade-design.md`
- preserve row order as the intended execution order
- keep the table limited to the edit tasks that are actually in scope
- use `core` or `public-python` in the `Applicability` column

For `## Notes`:

- summarize the main current-surface findings that shaped the plan
- record important out-of-scope areas, review requirements, assumptions, and open questions

Return a short planning summary with:

- saved file path, or a clear note that no saved-file change was made when the user asked only to review the current saved plan
- current plan status
- the planned-task table copied into chat, including `Task`, `Applicability`, `Change scope`, and `Notes`
- the main current-surface findings that shaped the plan
- any approval or revision still needed
- the likely first planned task after approval, including its change scope, when the plan is still `draft`
- if the saved plan is `draft`, end with a `Next Steps:` list in this order:
  - the option labels may be highlighted, but do not format them as inline code
  - format only the exact prompt text as code
  - guide me through reviewing this plan step by step
    - quote an exact prompt that asks `astro-agents/upgrade/upgrade-plan.md` to explain the current saved plan one part at a time, ask whether the user approves each part before moving on, and leave `docs/upgrade/plan.md` unchanged unless the user explicitly asks for plan changes
  - revise `docs/upgrade/plan.md` directly and tell me to review it
    - quote an exact prompt that asks `astro-agents/upgrade/upgrade-plan.md` to read the current saved plan for this repo, review the saved changes, and show the updated plan in chat without rewriting `docs/upgrade/plan.md` unless the user explicitly asks for further plan changes
  - approve the plan
    - quote an exact prompt that asks `astro-agents/upgrade/upgrade-plan.md` to mark the current saved plan `approved` and tell the user the next step, without changing the planned-task table unless the repo evidence contradicts it
- if the saved plan is `approved`, recommend the first planned task in order and quote the exact launch prompt from the `## Begin An Upgrade` table in `astro-agents/docs/upgrade-design.md`

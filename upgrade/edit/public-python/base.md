# Upgrade Edit Public Python Base

## Purpose
Use this prompt as the shared contract for one `public-python` edit task under `astro-agents/upgrade/edit/public-python/`.

Read `astro-agents/upgrade/edit/base.md` first, then apply the additional rules in this file together with the selected task-specific prompt under `astro-agents/upgrade/edit/public-python/`.

## Additional Rules

- use this prompt family only when the target repo's root `AGENTS.md` explicitly declares `Documentation surface profile: public-python`
- if that explicit `public-python` declaration is missing, stop and send the user to `astro-agents/upgrade/upgrade-documentation-surface-profile.md` instead of inferring the `public-python` path from repo signals
- treat the active documentation surface profile for these prompts as `public-python`
- keep the task grounded in the actual public-facing package, docs, contributor, release, or tutorial surface that the repo currently exposes
- do not broaden a `public-python` task into general packaging internals or unrelated internal docs cleanup
- keep the saved artifact path under `docs/upgrade/edit-public-*.md`
- when writing the saved artifact, record `documentation surface profile: public-python`

## Exclusions

- do not use this prompt family for core tasks that already have a core edit prompt under `astro-agents/upgrade/edit/`
- do not use the existence of a thin public package scaffold as permission to invent a broad public-doc surface that the repo does not yet expose

# Edit: Minimum Repo-Level AGENTS.md

## Purpose
Use this prompt for the single core edit task `minimum repo-level AGENTS.md`.

Read `astro-agents/upgrade/edit/base.md` first, then apply the task-specific guidance below.

## Task Definition

- task: minimum repo-level `AGENTS.md`
- prompt used: `astro-agents/upgrade/edit/minimum-repo-agents.md`
- saved artifact: `docs/upgrade/edit-minimum-repo-agents.md`
- use `astro-agents/docs/usage.md` as the source of truth for the recommended repo-level `AGENTS.md` surface
- preserve the declared `Documentation surface profile: <profile>.` line prominently near the top of the root `AGENTS.md`
- keep routing brief and push deeper explanation into stronger source-of-truth docs

## Exclusions

- do not let this task spill into the repo-level `README.md` task
- do not add repo-local `agents/` prompts unless the target repo actually needs them

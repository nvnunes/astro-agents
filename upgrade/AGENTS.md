# AGENTS.md

## Purpose
Use this folder when the task is to work on upgrading existing repos, the upgrade process, or the upgrade prompt family.

## Upgrade Prompt Selection

- Use `docs/upgrade-design.md` as the current source of truth for the upgrade process.
- Treat the prompt family in this folder as under reconstruction unless a replacement prompt is explicitly present.
- Default the scope to the requested repo or target root, not the whole workspace.
- Follow `docs/upgrade-design.md` for the currently defined planning, editing, review, workflow, and prompt-architecture model.
- If no replacement prompt is present for the requested task, stay at the design level and use `docs/upgrade-design.md` directly instead of inventing a prompt.

## Practical Rule

Use this folder to answer:

- which upgrade-process prompt or design artifact applies

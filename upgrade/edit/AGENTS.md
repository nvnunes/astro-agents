# AGENTS.md

## Purpose
Use this folder when the task is to run one direct core upgrade edit prompt under `upgrade/edit/`.

## Routing

- Keep `upgrade/AGENTS.md` active.
- Use `upgrade/edit/base.md` together with the selected task prompt in this folder.
- `base.md` owns the shared core edit workflow, oversight handling, saved-artifact structure, and shared exclusions.
- The selected task prompt owns the exact task name, exact `docs/upgrade/edit-*.md` artifact path, and any task-specific source-of-truth references or scope constraints.
- Use only one direct core edit prompt from this folder at a time.
- If the request is for a `public-python` edit task, route to `upgrade/edit/public-python/`.

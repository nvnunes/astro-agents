# AGENTS.md

## Purpose
Use this folder when the task is to run one direct `public-python` upgrade edit prompt under `upgrade/edit/public-python/`.

## Routing

- Keep `upgrade/AGENTS.md` and `upgrade/edit/AGENTS.md` active.
- Use `upgrade/edit/public-python/base.md` together with the selected task prompt in this folder.
- `upgrade/edit/public-python/base.md` adds the shared `public-python` applicability rules and scope constraints on top of the broader core edit contract from `upgrade/edit/base.md`.
- The selected task prompt owns the exact `public-python` task name, exact `docs/upgrade/edit-public-*.md` artifact path, and any task-specific source-of-truth references or scope constraints.
- Use only one direct `public-python` edit prompt from this folder at a time.
- If the request is not explicitly for a `public-python` task or saved `public-python` upgrade artifact, stop and confirm the `public-python` path instead of inferring it.

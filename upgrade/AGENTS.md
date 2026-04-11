# AGENTS.md

## Purpose
Use this folder when the task is to work on upgrading existing repos, the upgrade process, or the upgrade prompt family.

## Upgrade Prompt Selection

- Use `docs/upgrade-design.md` as the current source of truth for the upgrade process.
- `upgrade/upgrade-documentation-surface-profile.md` is the setup prompt. Use it first when the target repo's root `AGENTS.md` does not yet declare the documentation surface profile, or when the user wants to declare or change that profile explicitly.
- `upgrade/upgrade-plan.md` is the planning prompt. Use it when the user wants to inspect the current surface, draft or revise `docs/upgrade/plan.md`, or approve and resave the plan.
- `upgrade/upgrade-progress.md` is the progress and next-step prompt. Use it when the user asks for saved upgrade status, wants to know what has already been done, or wants a recommended next step from the root `AGENTS.md` profile declaration plus the saved artifacts under `docs/upgrade/`.
- `upgrade/edit/AGENTS.md` activates `upgrade/edit/base.md` for the direct core edit prompts in that folder.
- Route named core editing requests directly to the matching prompt under `upgrade/edit/`:
  - `upgrade/edit/minimum-repo-agents.md`
  - `upgrade/edit/minimum-repo-readme.md`
  - `upgrade/edit/minimum-source-of-truth-docs.md`
  - `upgrade/edit/minimum-environment-and-execution-support.md`
  - `upgrade/edit/minimum-testing-and-validation-support.md`
  - `upgrade/edit/additional-interface-docs.md`
  - `upgrade/edit/additional-supporting-docs.md`
- `upgrade/edit/public-python/AGENTS.md` keeps the broader core edit contract active and adds `upgrade/edit/public-python/base.md` for the direct `public-python` edit prompts in that folder.
- Route named `public-python` editing requests directly to the matching prompt under `upgrade/edit/public-python/`:
  - `upgrade/edit/public-python/package-metadata.md`
  - `upgrade/edit/public-python/user-documentation.md`
  - `upgrade/edit/public-python/developer-documentation.md`
  - `upgrade/edit/public-python/contributor-and-release-surface.md`
  - `upgrade/edit/public-python/examples-and-tutorial-assets.md`
- `upgrade/upgrade-review.md` is the shared core review prompt for `review the agent surface` and `report remaining issues`.
- `upgrade/upgrade-review-public-python.md` is the shared `public-python` review prompt for `review the public documentation surface`.
- `upgrade/report-current-agent-surface.md` is a detailed supporting reference for planning work and rollout-only portfolio-scan work, not the main user-facing entrypoint.
- Treat only explicitly present prompts in this folder as active parts of the upgrade workflow.
- Default the scope to the requested repo or target root, not the whole workspace.
- Follow `docs/upgrade-design.md` for the currently defined planning, editing, review, repo-local artifact, and prompt-architecture model.
- If the requested direct task prompt is not yet present, stop at the design level with `docs/upgrade-design.md` rather than inventing a stale prompt shape.

## Practical Rule

Use this folder to answer:

- which direct upgrade prompt or design artifact applies
- when the root `AGENTS.md` should first be updated with a documentation surface profile declaration
- which named task prompt should write which `docs/upgrade/*.md` file
- what saved upgrade artifact most likely determines the next user step

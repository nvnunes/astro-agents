# Upgrade Prompt

## Purpose
Use this style for agent-facing prompt files under `upgrade/`. Inherit the common prompt-writing discipline from `authoring/agents/base.md`, then apply the additional rules below.

Use this guide for shared upgrade prompts such as planning, current-surface reporting, direct edit-task prompts, edit-base prompts, review prompts, and progress prompts.

Use this guide for agent-facing prompt files under `upgrade/`, except `upgrade/AGENTS.md`.

Do not use it for human-facing docs such as `upgrade/README.md` or `docs/upgrade-design.md`.

## Success Criteria

- Make the upgrade task type explicit near the top.
- Keep the prompt scoped to one upgrade role.
- Make required inputs, stop conditions, and saved-artifact ownership explicit.
- Prevent hidden orchestration and neighboring-task drift.
- Keep the prompt operational rather than turning it into a design-policy store.

## Upgrade-Specific Requirements

- State the prompt's upgrade role explicitly:
  - planning
  - current-surface reporting
  - one edit task
  - one review task
  - progress synthesis
- Name the exact saved artifact path under `docs/upgrade/` when the prompt writes one.
- If the prompt is a direct entrypoint, make it self-sufficient for its operational behavior, or explicitly require activation of a named shared base prompt that must also be active.
- If the prompt depends on a user-provided documentation surface profile or another material input, require it explicitly and fail safe when it is missing.
- For profile-specific prompts such as `public-python`, include an explicit applicability gate.
- Keep current-surface reporting prompts focused on inspection order, bounded checks, and output shape.
- Move longer classification or policy taxonomies into `docs/upgrade-design.md` rather than storing them in an operational prompt.
- Keep edit prompts limited to one edit task and one saved artifact.
- Keep review prompts limited to one review task and one saved artifact.
- Keep progress prompts read-only. They may recommend a next step, but they must not update saved artifacts or behave like hidden workflow controllers.

## Adaptation

Adjust emphasis by upgrade prompt type:

- planning prompt:
  - prioritize required inputs, planning scope, and saved-plan structure
- current-surface reporting prompt:
  - prioritize inspection order, bounded evidence use, and current-state-only output
- edit-base prompt:
  - prioritize shared execution rules, oversight handling, artifact shape, and exclusions
- task-specific edit prompt:
  - prioritize exact task identity, exact artifact path, and task-local source-of-truth references
- review prompt:
  - prioritize review target, validation path, exclusions, and output shape
- progress prompt:
  - prioritize read-only synthesis, sequencing rules, and recommendation boundaries

## Preservation And Revision

When revising upgrade prompts:

- preserve the distinction between planning, reporting, editing, review, and progress roles
- preserve direct user control unless the source-of-truth design explicitly changes
- preserve the repo-local artifact model under `docs/upgrade/`
- remove duplicated policy or taxonomy when `docs/upgrade-design.md` should carry it instead
- tighten composition when a shared base prompt and a direct task prompt must both apply
- keep profile-specific prompts explicitly narrower than the core path

## Output

- When writing or revising upgrade prompts, return the revised prompt directly unless explanation is requested.
- When reviewing upgrade prompts, identify role drift, weak applicability gates, weak composition, weak artifact ownership, weak stop conditions, or hidden orchestration before proposing replacement text.

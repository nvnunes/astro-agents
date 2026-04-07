# Base Prompt Writing

## Purpose
Use this style as the common base for agent-facing prompt assets, including `AGENTS.md` files, shared prompts under `authoring/` or `validation/`, and repo-local prompts under `agents/`.

Specialized prompt-writing guides should inherit this base and add only the constraints, preservation rules, and adaptation behavior that are specific to their prompt type.

## Success Criteria
- Make the prompt's task explicit.
- Keep the prompt scoped to the kind of work it is meant to guide.
- Define enough structure that the prompt does not collapse into generic behavior.
- Make inputs, constraints, and output expectations explicit when they matter.
- Keep the prompt operational rather than descriptive.

## Common Inheritance Rules
- Preserve the prompt's role before improving its wording.
- Keep the prompt usable for its actual task mode, whether that is routing, revision, review, or another operational task.
- Let specialized guides add role-specific constraints instead of repeating this base.
- Repeat a base rule in a specialized guide only when that guide needs to sharpen, qualify, or override it.

## Style
- Use direct, operational language.
- Prefer short sections and flat bullets.
- Lead with the prompt's purpose and task.
- Keep instructions concrete enough that the prompt can be applied consistently.
- Prefer explicit references to the applicable source-of-truth document or comparison standard when one exists.

## Prompt-Specific Requirements
- Define the task the prompt is meant to perform.
- Define inputs only when they materially affect how the prompt should be applied.
- Define constraints or exclusions when they are needed to prevent scope drift.
- Define output behavior when the prompt's result shape matters.
- Avoid turning the prompt into a broad background note.

## Structure
- Prefer this basic structure when it fits:
  - `Purpose`
  - `Inputs`
  - `Instructions`, `Review Lenses`, or `Review Checks`
  - `Exclusions`
  - `Output`
- Use only the sections that actually add operational clarity.
- Keep the structure proportional to the prompt's scope.
- Let more specific prompt-writing guides add stricter rules without repeating this base.

## Scope Discipline
- Do not let an agent-facing prompt drift into a neighboring prompt role.
- Do not let a prompt become a README, design-doc, or architecture-doc substitute.
- When a deeper prompt asset or source-of-truth document should carry the substantive behavior, prefer pointing to it over duplicating it here.
- For internal file references inside this repo, prefer repo-root-relative paths such as `authoring/agents/agents-md.md` or `docs/architecture.md`.

## Preservation And Revision
When revising prompt assets:
- Preserve the prompt's intended scope.
- Keep the prompt aligned with its file name and stated purpose.
- Remove broad explanation when a README or design doc should carry it instead.
- Remove duplicated behavior when a more specific or deeper prompt asset should carry it.
- Improve directness, operational clarity, and output specificity.

## Output
- When writing or revising prompt assets, return the revised prompt directly unless explanation is requested.
- When reviewing prompt assets, identify scope drift, weak constraints, weak output definition, or ambiguous task framing before proposing replacement text.

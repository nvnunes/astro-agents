# Base Prompt Writing

## Purpose
Use this style as the common base for agent-facing prompts, including `AGENTS.md` files, skill reference prompts under `skills/`, and project-local prompts under `agents/`.

Specialized prompt-writing guides should inherit this base and add only the constraints, preservation rules, and adaptation behavior that are specific to their prompt type.

## Success Criteria
- Make the prompt's task explicit.
- Keep the prompt scoped to the kind of work it is meant to guide.
- Define enough structure that the prompt does not collapse into generic behavior.
- Make inputs, constraints, and output expectations explicit when they matter.
- State the required actions as instructions rather than as background description.

## Common Reuse Rules
- Preserve the prompt's role before improving its wording.
- Keep the prompt usable for its actual task mode, whether that is routing, revision, review, or another operational task.
- Let specialized guides add role-specific constraints instead of repeating this base.
- Repeat a base rule in a specialized guide only when that guide needs to sharpen, qualify, or replace it.

## Style
- Use direct, imperative language for operational instructions.
- Prefer short sections and flat bullets.
- Optimize for fewer ideas, not fewer lines.
- Lead with the prompt's purpose and task.
- Keep instructions concrete enough that the prompt can be applied consistently.
- Keep one real instruction or constraint per bullet when possible.
- Use a longer bullet only when the extra clause is a tight qualifier, not a second rule.
- Avoid vague, hedged, or internally conflicting instructions.
- Prefer explicit references to the applicable source-of-truth document or comparison standard when one exists.

## Prompt-Specific Requirements
- Define the task the prompt is meant to perform with explicit action verbs.
- Define inputs only when they materially affect how the prompt should be applied.
- State constraints or exclusions directly when they are needed to prevent role drift or weak task boundaries.
- Define output behavior when the prompt's result shape matters.
- Avoid turning the prompt into a broad background note.

## Structure
- Prefer this basic structure when it fits:
  - `Purpose`
  - `Inputs`
  - `Instructions`, `Review Criteria`, or `Review Checks`
  - `Exclusions`
  - `Output`
- Use only the sections that actually add operational clarity.
- Keep the structure proportional to the prompt's scope.
- Let more specific prompt-writing guides add stricter rules without repeating this base.

## Scope Discipline
- Do not let an agent-facing prompt drift into a neighboring prompt role.
- Do not let a prompt become a README, design-doc, or architecture-doc substitute.
- When a deeper prompt or source-of-truth document should carry the substantive behavior, prefer pointing to it over duplicating it here.
- For internal file references inside the active project, prefer project-root-relative paths such as `AGENTS.md` or `docs/architecture.md`. When referencing a shared skill directly, use the path convention already established by the target project.

## Preservation And Revision
When revising prompts:
- Preserve the prompt's intended scope.
- Keep the prompt aligned with its file name and stated purpose.
- Remove broad explanation when a README or design doc should carry it instead.
- Remove duplicated behavior when a more specific or deeper prompt should carry it.
- Prefer replacing weaker or outdated guidance with clearer guidance rather than layering additional overlapping instructions on top.
- Replace descriptive or hedged wording with direct instructions.
- Remove conflicting or overlapping guidance instead of layering more text onto it.
- Do not compress multiple independent rules into one dense bullet just to reduce line count.
- Improve operational clarity and output specificity.

## Output
- When writing or revising prompts, return the revised prompt directly unless explanation is requested.
- When reviewing prompts, identify role drift, weak constraints, weak output definition, or ambiguous task framing before proposing replacement text.

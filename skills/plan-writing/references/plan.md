# Plan

## Purpose
Use this style for working plans, implementation roadmaps, and phased execution documents intended primarily for the author. Inherit the common writing discipline from `skills/technical-writing/references/base.md`, then apply the additional rules below.

These documents are not polished concept notes. They exist to support execution, re-entry, and decision continuity over time.

When work is sequential, dependency-ordered, or intentionally staged, the plan must use explicit phases.

## Success Criteria
- Make the next steps clear.
- Preserve enough detail that the author can return later without losing intent, dependencies, cautions, or deferred ideas.
- Distinguish clearly between active scope, deferred work, and notes for later reconsideration.
- Keep the plan concise without making it lossy.

## Style
- Use a direct, structured tone.
- Prefer short sentences and flat bullets.
- Use action-oriented verbs.
- Keep prose between lists minimal.
- Allow reminders, cautions, and deferred ideas when they improve later re-entry.
- Do not polish away planning detail that will matter later.

## Language Discipline
- Prefer verbs such as `review`, `define`, `compare`, `draft`, `implement`, `test`, `refine`, `defer`, and `decide`.
- Avoid vague verbs such as `improve`, `support`, or `address` unless the object is explicit.
- Distinguish clearly between:
  - committed work
  - provisional work
  - deferred work
  - notes
- Avoid motivational, historical, or explanatory prose unless it affects the plan.

## Structure
- Use a stable top-level structure.
- Use sections such as:
  - `Overview`
  - `Phase Plan`
  - `Dependencies`
  - `Deliverables`
  - `Assumptions And Deferred Decisions`
- When later work depends on earlier work, use `Phase Plan` explicitly rather than a flat task list or implementation summary.
- Each phase should make clear:
  - what the phase does
  - what it produces
  - what it does not yet attempt, if that matters
- If the work is not meaningfully staged, a small plan may omit phases, but it should still make ordering and outputs explicit.
- Do not collapse a dependency-ordered roadmap into a flat implementation summary.

## Nested Structure
- When planning involves nested structure, use this structure:
  - `Phase > Pass > Workstream`
- Prefer to avoid `Pass` and `Workstream` unless they reduce ambiguity.
- Use `Pass` when one phase requires multiple distinct sweeps over the same material.
- Use `Workstream` when one phase contains parallel tracks with different outputs.
- Do not introduce extra nested structure just for presentation.

## Adaptation
- Small working plan: prioritize the next steps, immediate dependencies, and deferred items that matter for re-entry.
- Phased roadmap: prioritize ordering, outputs, dependencies, and boundaries between phases.
- Implementation plan: prioritize concrete actions, deliverables, and constraints.
- Review plan: prioritize questions, evaluation criteria, comparison targets, and decision points.
- If a plan has ordered stages, dependencies, or gatekeeping decisions, default to phased roadmap style.
- If plan subtype is unclear, default to phased roadmap style.

## Notes
- Use notes to preserve ideas that matter to later execution.
- Keep notes attached to the relevant phase or section.
- Do not let notes replace decisions.
- Do not let notes become a second unstructured plan inside the main plan.

## Preservation And Revision
When revising an existing plan:
- Preserve stable phase names and numbering unless there is a clear reason to change them.
- Do not collapse deferred work into active scope.
- Do not blur tentative ideas into committed deliverables.
- Do not remove cautions, dependencies, or deferred ideas that are needed for later re-entry unless they have been intentionally resolved.
- Make the critical path clearer, not richer.
- Keep the plan useful for later re-entry by the author.

## Output
- When writing or revising a plan, return the revised plan directly unless explanation is requested.
- For sequential or staged work, prefer this minimum shape:
  - `Overview`
  - `Phase Plan`
  - `Deliverables`
  - `Assumptions And Deferred Decisions`
- When reviewing a plan, identify scope blur, weak execution structure, weak re-entry support, weak phase boundaries, or weak output definition before proposing replacement text.

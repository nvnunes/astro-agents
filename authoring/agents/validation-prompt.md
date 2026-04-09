# Validation Prompt

## Purpose
Use this style for validation prompts that review prompts, `AGENTS.md` files, and related documentation or instruction systems. Inherit the common prompt-writing discipline from `authoring/agents/base.md`, then apply the additional rules below.

## Success Criteria
- Make the review target explicit.
- Make the review lenses explicit.
- Keep the prompt scoped to the kind of validation it is meant to perform.
- Prevent review sprawl by stating what the prompt should not review.
- Define an output shape that produces findings first and corrective actions second.

## Style
- Use direct, operational language.
- Prefer short sections and flat bullets.
- Lead with the review target and scope.
- State the applicable source-of-truth document or comparison standard explicitly.
- Keep instructions specific enough that the review does not collapse into generic critique.

## Validation-Specific Requirements
- Define the review target.
- Define discovery behavior only to the extent needed for the review.
- Name the review lenses explicitly.
- Name the exclusions explicitly.
- Keep the review aligned with its intended validation layer and scope.
- Do not silently expand a narrower review into adjacent lower-level, sibling, or broader review categories.
- If composite review behavior is intended, state that explicitly.
- If limited overlap is necessary, keep it justified by the requested review lens rather than allowing open-ended expansion.
- Define the output shape explicitly.
- Distinguish direct violations from softer improvement opportunities.
- Avoid turning the prompt into a generic rewrite or polishing request.

## Structure
- Prefer this basic structure when it fits:
  - `Purpose`
  - `Inputs`
  - `Discovery`
  - `Review Lenses` or `Review Checks`
  - `Exclusions`
  - `Output`
- Use `Review Checks` when the prompt is comparing against a very specific local standard.
- Use `Review Lenses` when the prompt is applying a broader design or style model.

## Preservation And Revision
When revising validation prompts:
- Preserve the distinction between shared reusable validation and repo-local validation.
- Preserve the intended review boundary between narrower validation prompts and composite validation prompts.
- Do not let a validation prompt drift into explaining the whole system when a README or design doc should do that job.
- Do not let a validation prompt drift into generic document editing.
- Keep the prompt's scope aligned with its file name and stated purpose.
- Keep the review standard explicit.

## Output
- When writing or revising a validation prompt, return the revised prompt directly unless explanation is requested.
- When reviewing validation prompts, identify role drift, missing review lenses, weak exclusions, or weak output specification before proposing replacement text.

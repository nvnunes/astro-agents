# Review Prompt Guide

## Purpose
Use this style for prompts that review prompts, `AGENTS.md` files, and related documentation or instruction systems. Inherit the common prompt-writing discipline from `authoring/agents/base.md`, then apply the additional rules below.

## Success Criteria
- Make the review target explicit.
- Make the review criteria explicit.
- Keep the prompt scoped to the kind of validation it is meant to perform.
- Prevent review sprawl by stating what the prompt should not review.
- Define an output shape that produces findings first and corrective actions second.

## Style
- Use direct, imperative language for review steps and exclusions.
- Prefer short sections and flat bullets.
- Lead with the review target and scope.
- State the applicable source-of-truth document or comparison standard explicitly.
- Keep instructions specific enough that the review does not collapse into generic critique.
- Avoid vague review verbs when a concrete check or comparison can be named.

## Review-Specific Requirements
- Define the review target.
- Define discovery behavior only to the extent needed for the review.
- Name the review criteria explicitly.
- Name the exclusions explicitly.
- Keep the review aligned with its intended role and scope.
- Avoid mixing incompatible review scopes or conflicting review standards in one prompt.
- Do not silently expand a narrower review into adjacent lower-level, sibling, or broader review categories.
- If combined review behavior is intended, state that explicitly.
- If limited overlap is necessary, keep it justified by the requested review criterion rather than allowing open-ended expansion.
- Define the output shape explicitly.
- Distinguish direct violations from softer improvement opportunities.
- Avoid turning the prompt into a generic rewrite or polishing request.

## Structure
- Prefer this basic structure when it fits:
  - `Purpose`
  - `Inputs`
  - `Discovery`
  - `Review Criteria` or `Review Checks`
  - `Exclusions`
  - `Output`
- Use `Review Checks` when the prompt is comparing against a very specific local standard.
- Use `Review Criteria` when the prompt is applying a broader design or style model.

## Preservation And Revision
When revising review prompts:
- Preserve the distinction between shared reusable reviews and project-local reviews.
- Preserve the intended review boundary between narrower review files and combined review files.
- Do not let a review file drift into explaining the whole system when a README or design doc should do that job.
- Do not let a review file drift into generic document editing.
- Keep the prompt's scope aligned with its file name and stated purpose.
- Keep the review standard explicit.

## Output
- When writing or revising one of these review files, return the revised prompt directly unless explanation is requested.
- When reviewing these files, identify role drift, missing review criteria, weak exclusions, or weak output specification before proposing replacement text.

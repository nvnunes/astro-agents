# Writing Prompt

## Purpose
Use this style for prompts that define writing or revision behavior for documents. Inherit the common prompt-writing discipline from `skills/prompt-writing/references/prompt-base.md`, then apply the additional rules below.

## Success Criteria
- Make the document type and intended audience explicit.
- Define what good revision or writing output looks like.
- Preserve technical meaning and document role.
- Prevent stylistic drift into the wrong document type.
- Define how the model should return the result.

## Writing-Specific Requirements
- State the document type and audience.
- Define success criteria as direct instructions rather than as abstract aspirations.
- Specify tone and stylistic defaults explicitly.
- Name common failure modes and what to avoid.
- Avoid vague style language when a clearer instruction about tone, structure, or preservation is possible.
- Add preservation rules when technical or scientific meaning must not change.
- Add adaptation rules when one prompt applies across related document types.
- Define output behavior explicitly.

## Structure
- Prefer this basic structure when it fits:
  - `Purpose`
  - `Success Criteria`
  - `Style`
  - `Language Discipline`
  - `Preservation`
  - `Adaptation`
  - `Output`
- Omit sections that do not add operational clarity.

## Preservation And Revision
When revising writing prompts:
- Keep the prompt tied to the document role it claims to serve.
- Do not let it drift into agent-surface structure design or project-organization guidance.
- Keep preservation rules explicit where meaning, notation, or document role must be preserved.
- Replace soft descriptive wording with direct revision instructions when possible.
- Keep examples selective and only when they address real ambiguity.

## Output
- When writing or revising a writing prompt, return the revised prompt directly unless explanation is requested.
- When reviewing a writing prompt, identify role drift, missing preservation rules, weak tone definition, or weak output definition before proposing replacement text.

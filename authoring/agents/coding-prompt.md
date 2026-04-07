# Coding Prompt

## Purpose
Use this style for prompts that define coding, code-editing, refactoring, or code-review behavior. Inherit the common prompt-writing discipline from `authoring/agents/base.md`, then apply the additional rules below.

## Success Criteria
- Make the coding task or task mode explicit.
- Preserve behavior, contracts, and local conventions unless change is intended.
- Define concrete expectations for structure, adaptation, and output.
- Prevent speculative refactors or unrelated churn.
- Keep the prompt operational rather than abstract.

## Coding-Specific Requirements
- State the language, task type, or coding context clearly.
- Define success criteria directly.
- Specify structure and editing defaults explicitly.
- Name common failure modes and what to avoid.
- Add preservation rules for APIs, schemas, numerical behavior, or other critical contracts.
- Add adaptation rules for local codebase conventions.
- Define output behavior explicitly.

## Structure
- Prefer this basic structure when it fits:
  - `Purpose`
  - `Success Criteria`
  - `Style` or `Editing Defaults`
  - `Preservation`
  - `Adaptation`
  - `Output`
- Omit sections that do not add operational clarity.

## Preservation And Revision
When revising coding prompts:
- Keep the prompt tied to the coding context it claims to serve.
- Do not let it drift into repo-specific architecture or workflow rules that belong elsewhere.
- Keep preservation rules explicit when behavior or contracts must not change.
- Keep local override behavior clear.

## Output
- When writing or revising a coding prompt, return the revised prompt directly unless explanation is requested.
- When reviewing a coding prompt, identify scope drift, weak preservation rules, weak local-adaptation behavior, or weak output definition before proposing replacement text.

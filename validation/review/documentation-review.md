# Documentation Review

## Purpose
Use this prompt to choose and run the applicable documentation review workflow.

Use it for generic documentation review requests that do not explicitly name a narrower documentation review file.

Use it as the shared determination path for the active `documentation surface profile`.

Treat this file as the normal public review entrypoint for shared documentation review, not as a profile-specific documentation workflow.

## Inputs

- target root or target paths to review
- optional focus on writing quality, document organization, source-of-truth structure, public documentation surface, or combined documentation review
- optional explicit documentation surface profile choice
- optional target scope that narrows the review below the full target root

If the review scope is not specified, treat the requested project or target root as the primary documentation review object.

## Profile Determination

When running this review:

- determine the target project's root `AGENTS.md` for documentation surface profile lookup
- if that `AGENTS.md` explicitly declares a `documentation surface profile`, use it unless an explicit profile choice was requested
- if no explicit `documentation surface profile` declaration is found, default to `private-default`
- use this selection order:
  1. explicit documentation surface profile choice
  2. project-declared `documentation surface profile` from the target project's root `AGENTS.md`
  3. `private-default`
- inspect explicitly provided workspace-local or project-local review files when the chosen documentation surface profile is not one of the shared built-in profiles
- use these shared built-in workflow starting files:
  - `validation/review/private-default/documentation-review.md`
  - `validation/review/public-python/documentation-review.md`

## Review Checks

- if the chosen documentation surface profile is `private-default`, run `validation/review/private-default/documentation-review.md`
- if the chosen documentation surface profile is `public-python`, run `validation/review/public-python/documentation-review.md`
- if the chosen documentation surface profile is another value, treat it as requiring an explicitly provided local implementation
- if no explicit local prompt set provides that implementation, return a validation-architecture finding that the declared documentation surface profile is unsupported in the active prompt set
- return one combined assessment rather than separate subreports

## Exclusions

Do not treat the following as the default task:

- `AGENTS.md` or prompt-writing review
- route-structure review beyond what the chosen documentation review workflow needs
- application-code review
- direct selection of `validation/review/core-document-writing-review.md` for generic docs requests

## Output

Return:

1. The selected documentation surface profile.
2. A `Route Summary`.
3. A brief overall judgment within the requested scope.
4. Findings ordered by severity.
5. Concrete corrective actions after the findings.

For the `Route Summary`:

- name the selected public review entrypoint
- name the selected documentation surface profile
- name the selected internal documentation workflow
- name only the source-of-truth docs that materially shaped the result
- keep the section short and current-state only

For each finding:

- name the violated review category or principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

# Documentation Review

## Purpose
Use this prompt to select and run the applicable documentation review bundle.

Use it for generic documentation review requests that do not explicitly name a narrower documentation review prompt.

## Inputs

- target root or target paths to review
- optional focus on writing quality, document organization, source-of-truth structure, public documentation surface, or combined documentation review
- optional documentation surface profile override
- optional target scope that narrows the review below the full target root

If the review scope is not specified, treat the requested repo or target root as the primary documentation review object.

## Discovery

When running this review:

- discover the target repo's root `AGENTS.md`
- if that `AGENTS.md` explicitly declares a `documentation surface profile`, use it unless an explicit profile override was requested
- if no explicit `documentation surface profile` declaration is found, default to `private-default`
- use this selection order:
  1. explicit documentation surface profile override
  2. repo-declared `documentation surface profile` from the target repo's root `AGENTS.md`
  3. `private-default`
- inspect higher-precedence local routing and validation prompts when the resolved documentation surface profile is not one of the shared built-in profiles
- use these shared built-in bundle entrypoints:
  - `validation/review/private-default/documentation-review.md`
  - `validation/review/public-python/documentation-review.md`

## Review Checks

- if the resolved documentation surface profile is `private-default`, run `validation/review/private-default/documentation-review.md`
- if the resolved documentation surface profile is `public-python`, run `validation/review/public-python/documentation-review.md`
- if the resolved documentation surface profile is another value, treat it as requiring a higher-precedence local implementation
- if no higher-precedence prompt layer provides that implementation, return a validation-architecture finding that the declared documentation surface profile is unsupported in the active prompt set
- return one combined assessment rather than separate subreports

## Exclusions

Do not treat the following as the default task:

- `AGENTS.md` or prompt-writing review
- hierarchy review beyond what the selected documentation bundle needs
- application-code review
- direct routing to `validation/review/core-document-writing-review.md` for generic docs requests

## Output

Return:

1. The selected documentation surface profile.
2. A brief overall judgment within the requested scope.
3. Findings ordered by severity.
4. Concrete corrective actions after the findings.

For each finding:

- name the violated review category or principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

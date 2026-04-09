# Public-Python Documentation Review

## Purpose
Use this prompt as the profile-scoped documentation review bundle for the `public-python` documentation surface profile.

## Inputs

- target root or target paths to review
- optional focus on public documentation surface review, public-Python document writing, public-Python documentation architecture, or combined public-Python documentation review
- optional target scope that narrows the review below the full target root

## Discovery

When running this review:

- inspect linked supporting docs only when needed to support the component reviews below

## Review Checks

Run the following component reviews within the requested scope:

- `validation/review/public-python/document-writing-review.md`
- `validation/review/public-python/documentation-architecture-review.md`

Use these component reviews to build one combined assessment rather than returning two isolated reports.
Let the component prompts define the substantive discovery rules, review lenses, and source-of-truth comparisons for the `public-python` profile.

## Exclusions

Do not treat the following as the default task:

- prompt-writing review
- hierarchy review beyond what the documentation bundle needs
- application-code review

## Output

Return:

1. The selected documentation surface profile: `public-python`.
2. A brief overall judgment within the requested scope.
3. Findings ordered by severity.
4. Concrete corrective actions after the findings.

For each finding:

- name the violated review category or principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

When combining findings:

- remove duplicates across the component reviews
- keep the most specific wording when findings overlap
- preserve the most severe version of an overlapping issue
- keep public-documentation-system findings primary and place softer cleanup after them

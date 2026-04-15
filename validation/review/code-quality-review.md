# Code-Quality Review

## Purpose
Use this prompt to choose and run the applicable shared code-quality review workflow.

Use it for current-state source-code quality review requests that do not explicitly name a narrower built-in code-quality workflow.

Treat this file as the normal public review entrypoint for shared code-quality review, not as a language-specific internal workflow.

## Inputs

- target root or target paths to review
- optional focus on Python code quality, contract ownership, validation behavior, public API boundaries, lifecycle clarity, abstraction quality, or tests and docs alignment
- optional target scope that narrows the review below the full target root

If the review scope is not specified, treat the requested repo or target root as the primary code-quality review object.

## Workflow Determination

When running this review:

- determine whether the requested scope is clearly Python from target paths or repo evidence such as `.py` files, `pyproject.toml`, `setup.py`, `setup.cfg`, Python package layout, or Python-first tests and commands
- use these shared built-in workflow starting files:
  - `validation/review/python/code-quality-review.md`

## Review Checks

- if the requested scope is clearly Python, run `validation/review/python/code-quality-review.md`
- if the requested scope is not clearly Python, return a validation-design finding that the shared code-quality review currently provides only a Python workflow and cannot review the requested scope without a language-specific implementation
- return one combined assessment rather than separate subreports

## Exclusions

Do not treat the following as the default task:

- prompt-writing review
- route-structure review
- documentation review except where local docs materially define contracts, verification expectations, or public API use for the reviewed code
- PR- or diff-specific code review behavior when the user asked for current-state code quality

## Output

Return:

1. A `Route Summary`.
2. A brief overall judgment within the requested scope.
3. Findings ordered by severity.
4. Concrete corrective actions after the findings.

For the `Route Summary`:

- name the selected public review entrypoint
- name the selected internal code-quality workflow when one was available
- note when no shared internal code-quality workflow was available
- name only the source-of-truth docs that materially shaped the result
- keep the section short and current-state only

For each finding:

- name the violated review category or principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

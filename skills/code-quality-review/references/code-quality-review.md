# Code-Quality Review

## Purpose
Use this prompt to choose and run the applicable shared code-quality review workflow.

Use it for current-state source-code quality review requests that do not explicitly name a narrower built-in code-quality workflow.

Treat this file as the workflow-selection reference for `skills/code-quality-review/SKILL.md`, not as a language-specific internal workflow.

## Inputs

- target root or target paths to review
- optional focus on Python code quality, contract ownership, validation behavior, public API boundaries, lifecycle clarity, abstraction quality, or tests and docs alignment
- optional target scope that narrows the review below the full target root
- review-profile overrides and review-specific constraints in applicable `AGENTS.md` files

If the review scope is not specified, treat the requested project or target root as the primary code-quality review object.

## Local Instructions And Review Profiles

Before reviewing code:

- Read applicable ancestor `AGENTS.md` files and discover narrower `AGENTS.md` files within the reviewed subtrees.
- Check explicitly for instructions directed at reviews, including profile assignments, preservation boundaries, scientific contracts, and verification expectations.
- Apply each instruction only within its stated scope, respecting more-specific local instructions and higher-priority instructions.
- Assign the full current standards profile unless applicable `AGENTS.md` instructions explicitly select a lower profile. Do not lower the profile because code appears old, is third-party code, or is named helpers or compatibility.
- For mixed scopes, identify the profile for each code area. Report unclear override boundaries rather than silently extending them.

Use these language-independent profiles:

| Profile | Review Expectation |
|---|---|
| Full current standards | Apply all relevant language-specific criteria for correctness, contracts, lifecycle, structure, documentation, tests, and maintainability. |
| Incremental improvement | Keep correctness and reliable verification mandatory. Recommend local improvements to contracts, state ownership, and clarity; avoid broad restructuring or interface changes without a demonstrated need. |
| Preservation-focused | Keep correctness and reliable verification mandatory. Preserve established style, interfaces, and scientific behavior; report concrete defects and unsafe boundaries rather than modernization opportunities. |

The profiles limit the scope of structural improvement, not the standard of correctness. Never suppress a scientific, safety, data-integrity, or reproducibility defect because a lower profile applies. Recommend the smallest change that resolves each finding.

## Workflow Determination

- Identify the languages actually present in the requested source scope, rather than assuming one language from the project name.
- For Python, read `skills/code-quality-review/references/python/code-quality-review.md`.
- For MATLAB, read `skills/code-quality-review/references/matlab/code-quality-review.md`.
- For mixed Python/MATLAB work, apply both workflows at their respective boundaries, including their interface contracts.

## Review Checks

- Apply the selected language criteria under the assigned profiles and applicable local instructions.
- If no built-in workflow covers part of the requested language or stack, return a validation-design finding for that part; do not imply that another language's workflow covers it.
- Distinguish static inspection, checks actually run, and checks not run. Use existing numerical evidence when it remains applicable; do not start expensive simulations merely to satisfy a generic review checklist.
- return one combined assessment rather than separate subreports

## Exclusions

Do not treat the following as the default task:

- prompt-writing review
- agent-surface structure review
- documentation review except where local docs materially define contracts, verification expectations, or public API use for the reviewed code
- PR- or diff-specific code review behavior when the user asked for current-state code quality

## Output

Return:

1. A `Review Path Summary`.
2. A brief overall judgment within the requested scope.
3. Findings ordered by severity.
4. Concrete corrective actions after the findings.

For the `Review Path Summary`:

- name the selected skill
- name the selected internal code-quality workflow when one was available
- note when no shared internal code-quality workflow was available
- state the profiles used and identify any `AGENTS.md` overrides that materially shaped the review
- name only the source-of-truth docs that materially shaped the result
- keep the section short and current-state only

For each finding:

- name the violated review category or principle
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities
- classify the finding as a correctness defect, contract/lifecycle weakness, or maintainability improvement; keep severity tied to demonstrated impact rather than code age or style

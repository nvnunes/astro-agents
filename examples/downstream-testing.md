# Example Downstream Testing Document

This is an example `docs/testing.md` for a project that uses `astro-agents`.

Copy or adapt it into the downstream project's own `docs/testing.md`, then
replace the project-local verification section with that project's real
commands, test suites, release checks, and completion expectations.

## Shared Agent-Surface Validation

Use shared agent-surface review when changes affect files that agents rely on
directly, including:

- `AGENTS.md`
- `SKILL.md`
- skill references, scripts, or assets
- agent-facing prompts or local workflow files
- human-facing `README.md` files or `docs/` files that agents are expected to consult
- project-local validation, review, skill-selection, or source-of-truth instructions

For these changes, use `$agent-surface-review` before treating the work as
complete.

Keep project-specific validation commands and completion expectations in this
document. Shared agent-surface review checks the agent-facing surface; it does
not replace the project's own tests or release checks.

## Other Shared Review And Planning

Use the relevant shared skill when the changed scope or requested work matches
it:

- `$documentation-surface-review` for documentation-surface profile behavior, project documentation architecture, README scope, private/default docs, or public Python documentation
- `$code-quality-review` for current-state source-code quality review
- `$project-upgrade-planning` for upgrade readiness, upgrade grouping, or upgrade sequencing

For ordinary code changes, follow the project's own testing and verification
docs. Do not treat shared code-quality review as a substitute for project-local
tests unless the project explicitly defines that workflow.

## Project-Local Verification

Replace this section with the project's actual commands and completion
expectations.

Examples:

```bash
pytest
```

```bash
git diff --check
```

Document which commands are required for routine changes, which commands are
required only for specific areas, and when manual review is acceptable because no
automated check exists.

## Completion Standard

- Do not treat agent-surface work as complete while direct validation findings remain unresolved.
- Distinguish direct violations from softer cleanup, but do not ignore severe findings.
- When more than one review applies, resolve overlapping findings once rather than treating each review as a separate rewrite request.
- Keep project-local commands and completion expectations active alongside shared review skills.

## Regression Priorities

Prioritize preventing regressions in:

- skill-selection clarity
- source-of-truth visibility
- examples and templates that remain safe if a project later becomes public
- consistency between `AGENTS.md`, `SKILL.md`, `README.md`, `docs/`, and project-local validation instructions

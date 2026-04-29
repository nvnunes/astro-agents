# Validation-Path Scenarios

## Purpose

Use this file as the maintained scenario baseline for validation-path regressions in `astro-agents`.

Keep it focused on the current validation surface rather than on broader runtime behavior.

## Covered Behaviors

- public shared review entrypoint selection
- documentation surface profile resolution when relevant
- internal documentation or code-quality workflow selection when relevant
- project-local review-file inclusion when relevant
- high-level `Route Summary` expectations for shared selector and combined-review outputs

## Out Of Scope

- instruction applicability
- task ownership
- fail-safe runtime doctrine
- longer-thread or compaction behavior
- observability beyond the current `Route Summary`
- eval harnesses, metrics, or grading

## Scenario Schema

Use this minimum shape for each maintained scenario:

- `Scenario`
- `Trigger`
- `Expected Public Review Entrypoint`
- `Expected Upgrade-Specific Review Path`, only for upgrade-specific scenarios
- `Expected Documentation Surface Profile`
- `Expected Internal Review Steps`
- `Expected Project-Local Review Files`
- `Expected Route Summary`
- `Notes`

Add concrete scenarios in later passes rather than expanding this file with broader runtime doctrine.

## Maintained Scenarios

### Scenario

- `Python code-quality review`
- `Trigger`: `Do a code quality review.` for a target project or target scope that is clearly Python
- `Expected Public Review Entrypoint`: `validation/review/code-quality-review.md`
- `Expected Documentation Surface Profile`: not applicable
- `Expected Internal Review Steps`: `validation/review/python/code-quality-review.md`
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: includes the public review entrypoint, the selected internal code-quality workflow, and only the material source-of-truth docs
- `Notes`: this covers the built-in shared code-quality workflow when the requested scope clearly resolves to Python

### Scenario

- `Unsupported-language code-quality review`
- `Trigger`: `Do a code quality review.` for a target project or target scope that does not clearly resolve to Python
- `Expected Public Review Entrypoint`: `validation/review/code-quality-review.md`
- `Expected Documentation Surface Profile`: not applicable
- `Expected Internal Review Steps`: none from the shared built-in code-quality workflows
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: includes the public review entrypoint and notes that no shared internal code-quality workflow was available
- `Notes`: the expected outcome is a validation-design finding for unsupported shared code-quality handling

### Scenario

- `Private-default docs review`
- `Trigger`: `Review this project's docs using the shared documentation review.`
- `Expected Public Review Entrypoint`: `validation/review/documentation-review.md`
- `Expected Documentation Surface Profile`: `private-default` when no root `AGENTS.md` declares another profile
- `Expected Internal Review Steps`: `validation/review/private-default/documentation-review.md`
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: includes the public review entrypoint, the selected documentation surface profile, the selected internal documentation workflow, and only the material source-of-truth docs
- `Notes`: use this as the baseline shared docs-review path for projects that do not declare another documentation surface profile

### Scenario

- `Prompt-writing review`
- `Trigger`: `Review this project's AGENTS.md files and prompts using the shared prompt-writing review.`
- `Expected Public Review Entrypoint`: `validation/review/prompt-writing-review.md`
- `Expected Documentation Surface Profile`: not applicable
- `Expected Internal Review Steps`: none
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: not required
- `Notes`: this is a narrow public review entrypoint and should not widen into combined review behavior by default

### Scenario

- `Routing-and-scope review`
- `Trigger`: `Review this project's prompt routing, workflow, and scope behavior using the shared routing-and-scope review.`
- `Expected Public Review Entrypoint`: `validation/review/routing-and-scope-review.md`
- `Expected Documentation Surface Profile`: not applicable
- `Expected Internal Review Steps`: none
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: not required
- `Notes`: this is a narrow public review entrypoint and should remain independent unless the request explicitly asks for broader synthesis

### Scenario

- `Full agent-surface review`
- `Trigger`: `Do a full agent surface review.`
- `Expected Public Review Entrypoint`: `validation/review/full-agent-surface-review.md`
- `Expected Documentation Surface Profile`: `private-default` when no root `AGENTS.md` declares another profile
- `Expected Internal Review Steps`: `validation/review/prompt-writing-review.md`, `validation/review/routing-and-scope-review.md`, `validation/review/documentation-review.md`
- `Expected Project-Local Review Files`: `agents/validation/root-agents-consistency-review.md` when the project root is in scope and the shared `AGENTS.md` review path is active
- `Expected Route Summary`: includes the public review entrypoint, the resolved documentation surface profile, the internal review steps used, any included project-local review files, and only the material source-of-truth docs
- `Notes`: this is the default combined validation path when the user asks for a combined review

### Scenario

- `Public-python docs review`
- `Trigger`: `Review this project's docs using the shared documentation review.` for a target project whose root `AGENTS.md` declares `Documentation surface profile: public-python.`
- `Expected Public Review Entrypoint`: `validation/review/documentation-review.md`
- `Expected Documentation Surface Profile`: `public-python`
- `Expected Internal Review Steps`: `validation/review/public-python/documentation-review.md`
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: includes the public review entrypoint, the selected documentation surface profile, the selected internal documentation workflow, and only the material source-of-truth docs
- `Notes`: this covers the built-in non-default shared documentation branch

### Scenario

- `Unsupported profile docs review`
- `Trigger`: `Review this project's docs using the shared documentation review.` for a target project whose root `AGENTS.md` declares an unsupported documentation surface profile and provides no explicit local implementation
- `Expected Public Review Entrypoint`: `validation/review/documentation-review.md`
- `Expected Documentation Surface Profile`: the declared unsupported profile
- `Expected Internal Review Steps`: none from the shared built-in profile workflows
- `Expected Project-Local Review Files`: none by default
- `Expected Route Summary`: includes the public review entrypoint, the selected documentation surface profile, and notes that no shared internal documentation workflow was available
- `Notes`: the expected outcome is a validation-architecture finding for unsupported profile handling

### Scenario

- `Generic validation request`
- `Trigger`: `Validate this project.`
- `Expected Public Review Entrypoint`: `validation/review/full-agent-surface-review.md`
- `Expected Documentation Surface Profile`: `private-default` when no root `AGENTS.md` declares another profile
- `Expected Internal Review Steps`: `validation/review/prompt-writing-review.md`, `validation/review/routing-and-scope-review.md`, `validation/review/documentation-review.md`
- `Expected Project-Local Review Files`: `agents/validation/root-agents-consistency-review.md` when the project root is in scope and the shared `AGENTS.md` review path is active
- `Expected Route Summary`: same combined-review route-summary shape as the full agent-surface review path
- `Notes`: broad validation requests that do not name a narrower public review should default to the combined review path

### Scenario

- `Upgrade-specific review path`
- `Trigger`: `Review this project for upgrade readiness using the shared upgrade review.`
- `Expected Public Review Entrypoint`: not applicable
- `Expected Upgrade-Specific Review Path`: `validation/review/upgrade-review.md`
- `Expected Documentation Surface Profile`: `private-default` when no root `AGENTS.md` declares another profile, unless the reviewed project declares another profile
- `Expected Internal Review Steps`: `validation/review/full-agent-surface-review.md` as the evidence-gathering review path
- `Expected Project-Local Review Files`: whatever project-local review files the internal full agent-surface review includes for the reviewed scope
- `Expected Route Summary`: not required by `validation/review/upgrade-review.md` itself; any route summary comes from the internal full agent-surface review when that output is surfaced or summarized
- `Notes`: this is user-facing, but it stays separate from the five normal public shared review entrypoints and uses the combined review as evidence rather than as a direct replacement for the upgrade assessment

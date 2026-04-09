# Full Agent-Surface Review

## Purpose
Use this prompt to review the repo's agent surface as one combined validation target. Use it when the user wants a combined validation pass across prompt-writing quality, hierarchy behavior, and the applicable profile-scoped documentation review bundle.

Treat the requested repo or target root as the primary review object. Review-system or validation-contract issues are secondary findings and should be included only when they materially affect the completeness, correctness, or discoverability of the requested review.

## Inputs

- target root or target paths to review
- optional focus areas such as writing quality, prompt-writing quality, hierarchy behavior, documentation architecture, or full agent-surface synthesis
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested repo or target root rather than the whole workspace.

## Discovery

When running this review:

- discover applicable repo and subtree `AGENTS.md` files dynamically from the target root
- include only files inside the requested scope
- inspect linked supporting docs only when needed to support the component reviews below
- do not assume repo names or hardcode expected repo paths
- resolve the documentation surface profile using `validation/review/documentation-review.md`

## Review Components

Run the following component reviews within the requested scope:

- `validation/review/prompt-writing-review.md`
  - for `AGENTS.md` and prompts vs the applicable guides under `authoring/`
- `validation/review/hierarchy-behavior-review.md`
  - for router discipline, design adherence, folder coherence, and prompt role drift
- `validation/review/documentation-review.md`
  - for the shared documentation selector and the applicable profile-scoped documentation review bundle

After the shared component reviews are active:

- discover applicable repo-local validation prompts under `agents/validation/`
- run applicable local validation prompts when they exist beneath the target root being reviewed

Use these component reviews to build one combined assessment rather than returning separate component reports.

Prioritize direct review of the requested repo state before stepping up to critique the validation framework itself.

## Exclusions

Do not treat the following as the default task:

- general prose polishing
- application-code quality review
- deterministic pass/fail scoring
- broad repository critique outside the prompt and instruction system
- framework-audit findings that do not materially affect the requested combined review

## Output

Return:

1. The selected documentation surface profile.
2. A brief overall judgment of the system within the requested scope.
3. Findings ordered by severity.
4. Concrete corrective actions after the findings.

For each finding:

- name the violated principle or review category
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

When combining findings:

- remove duplicates across the component reviews
- keep the most specific wording when findings overlap
- merge overlapping glossary, term-ownership, or plain-language findings into one terminology finding when possible
- distinguish system-level issues from local cleanup
- preserve the most severe version of an overlapping issue
- keep direct repo findings primary and place meta validation-design findings after them unless the meta issue blocks review completeness

For corrective actions:

- group actions by layer or document area rather than as a raw file dump
- make the actions specific enough to implement without re-deciding ownership
- keep the review focused on prompt and instruction-system changes rather than general repo critique

If a validation-architecture issue is included:

- explain how it affected or could have affected the combined review
- avoid turning the whole review into a framework audit unless the user explicitly asked for that

# Full Agent-Surface Review

## Purpose
Use this prompt to review the repo's agent surface as one combined validation target. Use it when the user wants a combined validation pass across prompt-writing quality, routing and authority behavior, and the applicable profile-scoped documentation review workflow.

Treat the requested repo or target root as the primary review object. Review-system or validation-contract issues are secondary findings and should be included only when they materially affect the completeness, correctness, or discoverability of the requested review.

## Inputs

- target root or target paths to review
- optional focus areas such as writing quality, prompt-writing quality, routing and authority behavior, documentation architecture, or full agent-surface synthesis
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested repo or target root rather than the whole workspace.

## Scope Determination

When running this review:

- determine applicable repo and subtree `AGENTS.md` files dynamically from the target root
- include only files inside the requested scope
- inspect linked supporting docs only when needed to support the internal review steps below
- inspect bounded operational and public-doc signals only when they materially affect documentation profile context or the current-state coverage snapshot
- do not assume repo names or hardcode expected repo paths
- determine the documentation surface profile using `validation/review/documentation-review.md`

## Internal Review Steps

Run the following internal review steps within the requested scope:

- `validation/review/prompt-writing-review.md`
  - for `AGENTS.md` and prompts vs the applicable guides under `authoring/`
- `validation/review/routing-and-authority-review.md`
  - for routing-and-workflow discipline, design adherence, folder coherence, and prompt role drift
- `validation/review/documentation-review.md`
  - for the shared documentation chooser and the applicable profile-scoped documentation review workflow

After the shared internal review steps are active:

- determine applicable repo-local review files under `agents/validation/`
- run applicable local review files when they exist beneath the target root being reviewed

Use these internal review steps to build one combined assessment rather than returning separate reports.

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
2. Documentation profile context.
3. A brief overall judgment of the system within the requested scope.
4. Findings ordered by severity.
5. A current-state coverage snapshot.
6. Concrete corrective actions after the findings.

For documentation profile context:

- name the declared `Documentation surface profile` when the root `AGENTS.md` provides one
- name the effective selected profile used for the review
- note bounded current-surface signals that materially shaped how the documentation surface was interpreted
- note any ambiguity or mismatch that materially affects the completeness or interpretation of the review

For each finding:

- name the violated principle or review category
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct violations from softer improvement opportunities

When combining findings:

- remove duplicates across the internal review steps
- keep the most specific wording when findings overlap
- merge overlapping glossary, term-ownership, or plain-language findings into one terminology finding when possible
- distinguish system-level issues from local cleanup
- preserve the most severe version of an overlapping issue
- keep direct repo findings primary and place meta validation-design findings after them unless the meta issue blocks review completeness

For corrective actions:

- group actions by scope or document area rather than as a raw file dump
- make the actions specific enough to implement without re-deciding ownership
- keep the review focused on prompt and instruction-system changes rather than general repo critique

For the current-state coverage snapshot:

- use these coverage areas when they apply:
  - routing-and-workflow and prompt surface
  - repo entry surface
  - source-of-truth docs surface
  - environment and execution support surface
  - testing and validation support surface
  - additional interface surface
  - additional supporting-doc surface
  - public documentation surface when the repo materially exposes one
- classify each applicable area as `present`, `partial`, `missing`, or `not applicable`
- keep each area descriptive:
  - identify the key evidence
  - identify the key gap or caveat
- keep this section current-state only:
  - do not recommend grouping, sequencing, or oversight here
  - do not recommend changing the documentation surface profile here

If a validation-architecture issue is included:

- explain how it affected or could have affected the combined review
- avoid turning the whole review into a framework audit unless the user explicitly asked for that

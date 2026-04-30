# Full Agent-Surface Review

## Purpose
Use this prompt to review the project's agent surface as one combined validation target. Use it when the user wants a combined validation pass across prompt-writing quality, instruction scope, workflow behavior, and the applicable profile-scoped documentation review workflow.

Treat the requested project or target root as the primary review object. Review-system or validation-contract issues are secondary findings and should be included only when they materially affect the completeness, correctness, or discoverability of the requested review.

Treat this file as the main internal workflow reference for `skills/agent-surface-review/SKILL.md`.

## Inputs

- target root or target paths to review
- optional focus areas such as writing quality, prompt-writing quality, instruction scope, workflow behavior, documentation architecture, or full agent-surface synthesis
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested project or target root rather than the whole workspace.

## Scope Determination

When running this review:

- determine applicable project and subtree `AGENTS.md` files dynamically from the target root
- include only files inside the requested scope
- inspect linked supporting docs only when needed to support the internal review steps below
- inspect bounded operational and public-doc signals only when they materially affect documentation profile context or the current-state coverage snapshot
- do not assume project names or hardcode expected project paths
- determine the documentation surface profile by pairing with `skills/documentation-surface-review/SKILL.md`

## Internal Review Steps

Run the following internal review steps within the requested scope:

- `skills/agent-surface-review/references/prompt-writing-review.md`
  - for `AGENTS.md`, `SKILL.md`, prompts, and skill references vs the applicable shared writing guides
- `skills/agent-surface-review/references/scope-and-workflow-review.md`
  - for instruction-scope discipline, workflow clarity, design adherence, folder coherence, and prompt role drift
- `skills/documentation-surface-review/SKILL.md`
  - pair with this skill for the shared documentation chooser and the applicable profile-scoped documentation review workflow

After the shared internal review steps are active:

- determine applicable local validation requirements from the target project's validation source of truth
- run applicable local validation checks as follow-on checks rather than as replacements for the shared review steps

Use these internal review steps to build one combined assessment rather than returning separate reports.

Prioritize direct review of the requested project state before stepping up to critique the validation framework itself.

## Exclusions

Do not treat the following as the default task:

- general prose polishing
- application-code quality review
- deterministic pass/fail scoring
- broad project critique outside the agent surface and instruction system
- framework-audit findings that do not materially affect the requested combined review

## Output

Return:

1. The selected documentation surface profile.
2. A `Review Path Summary`.
3. Documentation profile context.
4. A brief overall judgment of the system within the requested scope.
5. Findings ordered by severity.
6. A current-state coverage snapshot.
7. Concrete corrective actions after the findings.

For the `Review Path Summary`:

- name the selected skill
- name the resolved documentation surface profile
- name the internal review steps used
- name only the source-of-truth docs that materially shaped the result
- keep the section short and current-state only

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
- keep direct project findings primary and place meta validation-design findings after them unless the meta issue blocks review completeness

For corrective actions:

- group actions by scope or document area rather than as a raw file dump
- make the actions specific enough to implement without re-deciding ownership
- keep the review focused on agent-surface and instruction-system changes rather than general project critique

For the current-state coverage snapshot:

- use these coverage areas when they apply:
  - instruction scope, workflow behavior, and agent surface
  - project entry surface
  - source-of-truth docs surface
  - environment and execution support surface
  - testing and validation support surface
  - additional interface surface
  - additional supporting-doc surface
  - public documentation surface when the project materially exposes one
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

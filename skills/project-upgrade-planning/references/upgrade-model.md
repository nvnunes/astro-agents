# Upgrade Model

This reference is the durable model for upgrading existing projects to an `astro-agents`-compatible agent surface.

Use it to understand the shared review-led upgrade model, the upgrade taxonomy used in recommendations, and the validation expectations for treating an upgrade as complete.

This document focuses on the baseline local work needed to make an existing project usable and effective for agents. Advanced runtime governance, observability, safety, and eval infrastructure usually belong in shared `astro-agents` support rather than in the normal project-upgrade baseline.

## Local Terms

This reference defines a small number of upgrade-specific terms.

- `documentation surface profile`: the shared documentation review path selected for a target project, currently `private-default` or `public-python`
- `work area`: one coherent part of the agent surface that may need work during an upgrade
- `change scope`: the invasiveness of an intended change, ranging from extraction or cleanup through structural rewrite and framing change
- `oversight level`: a shorthand signal for how strongly review should stop for user direction before a proposed group of work is treated as ready for implementation

## Purpose

Use this document to record:

- the shared review-led upgrade model
- the work areas that recommendations should map onto
- how documentation surface profiles affect upgrade review and later validation
- the change-scope language used to describe expected upgrade work
- the validation and completion expectations for project upgrades

## Recommended Workflow

The recommended upgrade loop is:

1. assess the project's current surface
2. recommend the project's current docs or public-docs setup and a suggested way to group the work
3. let the user decide how to group, sequence, and prioritize the work
4. do the chosen work as ordinary project editing
5. rerun the assessment as needed until the user is satisfied the project is sufficiently upgraded to treat as complete

In this model:

- `skills/project-upgrade-planning/SKILL.md` is the default shared starting point for assessing a project against this design
- upgrade review starts from the current project surface and the effective documentation review profile
- the root `AGENTS.md` should be updated with the chosen `Documentation surface profile` only as part of approved editing work

Across the whole process:

- create clearer source-of-truth docs before trimming older docs
- preserve meaning by default
- keep user control explicit over how the work is grouped, sequenced, and prioritized
- add project-local `agents/` only when justified
- use shared review output to support user decisions about what to do next

## Documentation Surface Profile Handling

Use `skills/documentation-surface-review/SKILL.md` for documentation surface profile review.

The documentation-review branch in `skills/agent-surface-review/SKILL.md` currently recognizes:

- `private-default`
- `public-python`

### Current-Surface Signals For `public-python`

Use current project evidence to decide whether the review should recommend a `public-python` profile and include the relevant `public-python` work areas in its recommendations.

Bounded `public-python` signals include:

- public package metadata
- docs-site config
- reachable public docs starting documents
- public contributor or release docs
- public examples or tutorial assets

When those signals are present:

- include only the `public-python` work areas that the current surface actually justifies
- keep the recommendations evidence-based rather than expanding into an aspirational public-doc program

For thin public-package scaffolds:

- distinguish between a thin public package scaffold and a broader active public-doc surface
- treat public package metadata, a basic `README.md`, and a small test surface by themselves as a thin public package scaffold rather than as evidence of a broad public-doc surface
- do not recommend broad `public-python` work solely because a project has a package skeleton
- default the recommendation to `private-default` unless the project already exposes a materially broader public-facing surface

During upgrade review:

- use the root `AGENTS.md` declaration when one already exists as the current contract input
- default shared documentation review to `private-default` when no declaration exists
- allow the upgrade review to recommend keeping or changing the profile when current project evidence supports that recommendation
- begin review from the declared profile when present, or from the effective shared default when it is not

The recommended profile should shape later validation and future project-local guidance. The user still decides whether and when that recommendation becomes an actual project change.

In user-facing review output, describe the current docs setup in plain language and only mention internal profile labels when they materially help the user decide what to do next.

## Work Areas

The work areas below cover most issues that materially affect agent effectiveness. Some documentation surface profiles, such as `public-python`, add additional public-facing work areas. Prefer the names below when they fit the project well. When a material upgrade concern does not fit them cleanly, the review may introduce project-specific work areas with a plain-language name and a short justification.

When introducing a project-specific work area:

- do not create a new work area when a shared work area already fits with minor adaptation
- do create a new work area when forcing the shared taxonomy would hide real ownership, risk, or work shape
- explain whether the project-specific work area extends, splits, or sits alongside the shared work areas

### Core Work Areas

1. minimum project-level `AGENTS.md`
   - establish a minimal project-level `AGENTS.md` surface with bootstrap guidance when needed, source-of-truth references, validation pointers, and any chosen documentation surface profile
2. minimum project-level `README.md`
   - establish the minimum recommended project starting document and top-level navigation
3. minimum source-of-truth docs
   - minimum source-of-truth docs including `docs/architecture.md` and `docs/data-sources.md` when needed
   - use `docs/data-sources.md` only when the project has meaningful durable data artifacts that need one stable inventory-and-ownership doc; do not use it as the owner for data interfaces or persisted contracts
4. minimum environment and execution support
   - document the minimum environment setup, execution commands, and runtime prerequisites needed to support agent operation
5. minimum testing and validation support
   - establish `docs/testing.md` and the minimum testing or validation code needed to support agent operation
6. additional interface docs
   - document additional commands, services, APIs, or starting documents the agent must understand to operate effectively
7. additional supporting docs
   - existing or newly retained docs that remain useful after normalization, including operational or secrets-related docs when needed, and are linked from stronger owners

### Additional `public-python` Work Areas

1. public package metadata
   - `pyproject.toml` fields that affect public package presentation or public docs discovery
2. public user documentation
   - user-facing installation, onboarding, tutorials, how-to guidance, usage docs, and subtree entry docs, whether they live in `README.md` or elsewhere
3. public developer documentation
   - docs-site config, reachable docs pages, and generated API-doc inputs that define the public docs surface
4. public contributor and release surface
   - `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, and related public workflow or release docs
5. public examples and tutorial assets
   - examples, notebooks, tracked generated artifacts, or other user-facing learning materials when they are part of the public docs surface

## Change Scopes

For each work area, first assess what the project needs, then classify the likely change scope. Use change scope to describe how invasive the intended move is:

1. `preserve`
   - carry forward, clarify, relink, or lightly improve existing information without materially changing meaning
2. `restructure`
   - move, split, merge, or regroup information while keeping the meaning mostly the same
3. `develop`
   - add new content or change existing content significantly

Use change scope in review output to help the user judge the size, risk, and sequencing of the work.

In user-facing review output, explain change scope in plain language rather than relying on the labels alone.

## Oversight Levels

Use these oversight levels to recommend how much advance planning and user direction a proposed piece of work usually needs before implementation.

During review and planning, the assigned oversight level is guidance. Once the user agrees on an oversight level for a proposed piece of work, treat that level as an execution constraint until the user changes it.

1. review `outputs`
   - recommend this when implementation may usually proceed with minimal advance planning
   - once agreed, implementation may proceed without a separate detailed plan or design discussion unless new uncertainty appears
2. review `plans`
   - recommend this when a clearer plan should be agreed before implementation
   - once agreed, implementation should wait until that plan is worked out and approved at the level the user expects
3. review `designs`
   - recommend this when the design approach should be discussed with the user before detailed planning
   - once agreed, detailed planning and implementation should wait until the user has chosen the design direction for that work

In user-facing review output, explain the likely need for user direction in plain language rather than relying on `outputs`, `plans`, or `designs` by themselves.

## Stopping For User Direction

Use this table as guidance when recommending whether proposed work fits review `outputs`, review `plans`, or review `designs`, and when review or planning should pause for user direction before implementation. Once an oversight level is agreed with the user, it should be strictly followed.

If the project evidence is weak, the ownership is unclear, or the proposed work mixes several concerns, consider pausing sooner than the table suggests.

Core work areas:

| Work area | `preserve` | `restructure` | `develop` |
| --- | --- | --- | --- |
| `minimum project-level AGENTS.md` | `plans` | `plans` | `plans` |
| `minimum project-level README.md` | `plans` | `plans` | `designs` |
| `minimum source-of-truth docs` | `plans` | `plans` | `designs` |
| `minimum environment and execution support` | `outputs` | `plans` | `plans` |
| `minimum testing and validation support` | `outputs` | `plans` | `plans` |
| `additional interface docs` | `outputs` | `plans` | `designs` |
| `additional supporting docs` | `outputs` | `plans` | `designs` |

Additional `public-python` work areas:

| Work area | `preserve` | `restructure` | `develop` |
| --- | --- | --- | --- |
| `public package metadata` | `outputs` | `plans` | `designs` |
| `public user documentation` | `outputs` | `plans` | `designs` |
| `public developer documentation` | `outputs` | `plans` | `plans` |
| `public contributor and release surface` | `outputs` | `plans` | `designs` |
| `public examples and tutorial assets` | `outputs` | `plans` | `designs` |

## Grouping The Work

The upgrade review should recommend how to group the work in plain language. Use the work areas in this document to anchor the reasoning, but do not force the user-facing output to expose those labels unless they materially help the user decide what to do next.

When suggesting how to group the work:

- group tightly coupled areas when they are likely to be reviewed and edited together
- split areas when the design risk, change scope, or ownership questions would otherwise make one proposed group too large
- make it explicit when a work area is `not needed`
- make dependency order explicit when one proposed group should usually happen before another
- give each proposed group a plain-language name that describes the work itself rather than abstract roles or ownership

Common ways to group the work include:

- project entry surface
  - often combines minimum project-level `AGENTS.md` and minimum project-level `README.md`
- core project documentation
  - often centers on minimum source-of-truth docs, and may include additional supporting docs when ownership cleanup is tightly coupled
- setup and verification docs
  - often combines minimum environment and execution support with minimum testing and validation support
- interface docs
  - usually centers on additional interface docs, with supporting docs only when needed
- public docs
  - only when current project evidence materially justifies `public-python` work

These are recommendations, not fixed phases. The user decides how to group the work and in what order.

## Saving An Overall Plan

For large or multi-session upgrades, it can help to save the user-approved overall plan to the target project's `docs/upgrade-plan.md`.

Use that document to carry forward the current shared understanding of:

- the chosen or recommended documentation surface profile
- the agreed way to group the work
- dependency order and sequencing decisions
- the agreed oversight level for each proposed piece of work
- major design or planning decisions already made
- current status and remaining work

Keep this lightweight. Use it to make later threads easier to restart and preserve the key decisions the user has already made.

Do not treat the target project's `docs/upgrade-plan.md` as required for normal upgrades. Recommend it only when the scope, duration, or likely thread handoff makes continuity materially useful.

## Validation And Completion

Use this skill to assess upgrade readiness, recommend work grouping, and reassess remaining work after meaningful progress.

During upgrade work:

- rerun project-upgrade planning after meaningful progress when the user wants to reassess remaining work
- use the `agent-surface-review` skill for docs-heavy changes, profile-specific documentation checks, or full completion review when that review is required
- treat `agent-surface-review` as a follow-up review skill rather than as an internal dependency of this skill

For projects with a material `public-python` surface:

- keep the documentation surface profile recommendation aligned with the exposed public surface
- use `agent-surface-review` for public-documentation completion checks when the project declares or is moving toward the `public-python` profile

Do not treat an upgrade as complete while direct validation findings remain unresolved.

# Upgrade Review

## Purpose
Use this prompt to review a project's current agent surface against `skills/project-upgrade-planning/references/upgrade-model.md` and recommend a practical upgrade path.

Use it when the user wants to:

- upgrade a project through the shared review-first path
- review a project for upgrade readiness
- propose how to group the work for a project
- assess a project against the shared upgrade model

Keep this prompt read-only. It reviews the project and recommends next moves, but it does not perform editing work and it does not create project-local upgrade artifacts.

Do not use sub-agents unless the user explicitly asks for delegation or parallel agent work.

## Inputs

- target root or target paths to review
- optional focus areas such as project entry surface, source-of-truth docs, environment and validation support, public documentation surface, or how to group the work
- optional target scope that narrows the review below the full target root

If the review scope is not specified, default to the requested project or target root rather than the whole workspace.

## Scope Determination

When running this review:

- determine applicable project and subtree `AGENTS.md` files dynamically from the target root
- inspect `README.md`, `docs/architecture.md`, `docs/testing.md`, and other likely source-of-truth docs when present
- inspect bounded operational and public-doc signals when they materially affect upgrade recommendations
- use `skills/project-upgrade-planning/references/upgrade-model.md` as the source of truth for the upgrade model, work areas, change-scope language, and `public-python` recommendation rules
- do not require a declared `Documentation surface profile` before reviewing; when none is declared, treat the current shared documentation review path as `private-default`

## Optional Combined Review

Pair with `skills/agent-surface-review/SKILL.md` only when current agent-surface
state needs a combined review before upgrade planning. When used, treat that
review as evidence for the upgrade assessment rather than returning a separate
report or rerunning its internal steps. Do not make it a routine internal
dependency of upgrade review.

## Upgrade Assessment

Ground the assessment in the project's current surface and `skills/project-upgrade-planning/references/upgrade-model.md`.

When opening the assessment:

- determine the current profile behavior first:
  - declared profile when the root `AGENTS.md` provides one
  - otherwise the effective shared default `private-default`
- describe the project's current public or private docs setup in plain language rather than relying on `documentation surface profile` or raw profile labels alone
- prefer plain phrases such as `current public package-and-docs path`, `current docs setup`, or `current private/internal docs setup` over internal labels such as `public-python track`
- recommend keeping or changing that setup only when current project evidence supports the recommendation
- only mention the internal profile label when it materially helps later review handling or the user asks
- treat missing profile declarations as a review finding or follow-up item, not as a blocker
- avoid lead-ins that narrate the review system or model when a direct assessment of the project is enough

When recommending how to group the work:

- express the recommended groups of work in plain language
- use the work-area names from `skills/project-upgrade-planning/references/upgrade-model.md` to anchor the assessment, but only expose those labels when they materially help the user
- describe the likely shape of the work in plain language, such as mostly cleanup and reorganization, a mix of reorganization and new material, or substantial new development
- use the `Oversight Levels` and `Stopping For User Direction` guidance in `skills/project-upgrade-planning/references/upgrade-model.md`
- use those guidance tables to judge how much user direction the work likely needs before implementation
- explain that need in plain language, such as can likely proceed, should agree on a plan first, or should discuss the approach first
- explain why each recommended group matters
- make dependency order explicit when one recommended group should usually happen before another
- say explicitly which work areas are `not needed`
- prefer plain group names such as `core project documentation`, `project entry documentation`, `setup and verification docs`, or `public docs` over framework-heavy labels when those names are enough
- keep `public-python` recommendations narrowly evidence-based rather than aspirational
- do not rely on internal labels such as `change scope`, `oversight level`, or `outputs` / `plans` / `designs` in the user-facing output unless the user asks for that vocabulary

When suggesting follow-up review:

- recommend rerunning `skills/project-upgrade-planning/SKILL.md` after meaningful progress when reassessment is useful
- recommend `skills/documentation-surface-review/SKILL.md` for documentation-heavy groups of work or profile-sensitive documentation changes
- recommend `skills/agent-surface-review/SKILL.md` before the project is treated as fully upgraded
- in user-facing output, refer to these in plain language unless the exact shared skill or reference name materially helps

## Exclusions

Do not treat the following as the default task:

- editing or rewriting the project
- creating project-local workflow artifacts for the upgrade process
- using sub-agents unless the user explicitly asks for delegation or parallel agent work
- requiring upfront documentation-surface-profile declaration before the review can run
- application-code audit beyond what current agent-surface and public-doc signals require
- hidden orchestration that turns the recommended grouping of work into mandatory execution order

## Output

Return:

1. A plain-language recommendation about the project's current docs or public-docs setup.
2. A brief overall judgment of upgrade readiness within the requested scope.
3. Main gaps in priority order.
4. Suggested grouping of the work.
5. Suggested order or dependency notes.
6. Follow-up review guidance.
7. A short next-step invitation.

For the opening recommendation:

- describe the current public or private docs setup in plain language
- include the internal documentation surface profile only when it materially helps later review handling or the user asks
- avoid explicit profile labels such as `public-python` when a plain description of the current package/docs path is enough
- avoid lead-ins that narrate the review system itself when a direct statement about the project is enough

For each main gap:

- describe the project-surface problem in plain language first
- name the affected path or paths
- explain why the issue matters
- state the recommended move
- distinguish direct upgrade blockers from softer cleanup opportunities
- prefer project-surface wording over internal architecture wording
- when code paths are cited, explain the project-level consequence first
- avoid technical shorthand such as `architectural risk`, `stable facade`, `contract ownership`, or `validation portability` unless it is truly necessary to explain the project-level issue
- avoid internal framework terms such as `source-of-truth`, `validation contract`, or `project-local agents/` unless they materially help the user understand what to do next
- prefer phrases such as `architecture and testing docs`, `core project documentation`, or `public docs` over framework-heavy labels such as `owner docs` or `stable owner docs`
- refer to `AGENTS.md` directly, or as the project instructions, rather than as an `agent brief` when the plain file reference is enough

For each recommended group of work:

- give the group of work a short user-facing name
- make the name describe the work itself rather than ownership or abstract roles
- explain what part of the project the work covers
- name the mapped work areas from `skills/project-upgrade-planning/references/upgrade-model.md` only when that context materially helps the user
- describe the likely shape of the work in plain language
- explain how much user direction the work likely needs before implementation, in plain language
- explain the main goal of the work
- only recommend a specific new supporting doc when current project evidence clearly justifies it
- in user-facing output, prefer plain action phrases such as `draft a phased plan` over exact target-project file paths like `docs/upgrade-plan.md` unless the path materially helps

For the next-step invitation:

- end with a short user-facing prompt that hands control back to the user
- offer only the most sensible next moves for the current review, such as turning the review into a phased plan or starting implementation on one of the recommended groups
- keep the invitation in plain language rather than internal process vocabulary
- in user-facing output, avoid naming specific review-reference files unless the exact shared reference name materially helps; prefer plain phrases such as `run upgrade planning again`, `run the docs branch`, or `run the full agent-surface review`

Keep the output focused on helping the user decide what to do next.

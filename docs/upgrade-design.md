# Upgrade

This document is the human-facing source of truth for upgrading existing repos to an `astro-agents`-compatible agent surface.

Use it first to understand the upgrade process design, task model, prompt architecture, repo-local artifact model, and validation model. Use `upgrade/upgrade-rollout.md` and `upgrade/upgrade-portfolio-scan.md` for the current repo-specific rollout state.

This document focuses on the baseline local work needed to make an existing repo usable and effective for agents. It does not treat advanced runtime governance, observability, safety, or eval infrastructure as normal repo-upgrade responsibilities. Those are important, but they should mostly be provided by shared `astro-agents` support rather than expected from typical upgraded repos.

## Local Terms

Use `docs/glossary.md` for repo-wide terminology. This document also uses a small number of local terms that are specific to the upgrade model.

- `documentation surface profile`: the repo's documentation-validation profile, used to select the shared documentation review bundle; built-in shared profiles currently include `private-default` and `public-python`
- `task type`: the broad kind of upgrade task being planned or executed; this document uses `setup work`, `planning work`, `editing tasks`, and `review tasks`
- `change scope`: the invasiveness of an intended change, ranging from extraction or cleanup through structural rewrite and framing change
- `editing task`: a coherent category of upgrade editing work tied to one part of the agent surface
- `oversight level`: the level of user oversight required for a given task; for editing tasks, it is also shaped by change scope

For validation planning, use the repo's documentation surface profile when one exists. When no non-default profile is declared, shared validation defaults to `private-default`.

## Purpose

Use this document to record:

- the shared upgrade process design
- recurring upgrade patterns that should shape the process design
- the documentation surface profiles that change how shared review paths should be handled
- the upgrade prompt and artifact machinery that should be built and maintained in later phases

## Upgrade Process Model

Treat every upgrade as a controlled normalization pass that first records the user-provided documentation surface profile in the target repo's root `AGENTS.md`, then assesses the current agent surface, designs the needed work task by task when planning is requested, and applies the documentation-review path for the declared profile.

Every upgrade should answer the same four questions first:

1. what does the repo's current agent surface do now
2. what should the target agent surface look like after normalization, given the documentation surface profile declared in the target repo's root `AGENTS.md`
3. in what order should we decide which files own which information, create structure, and clean up the surface to minimize drift
4. what level of user oversight each change needs

Across the whole process:

- create clearer source-of-truth docs before trimming older docs
- preserve meaning by default
- record the user-provided documentation surface profile prominently in the target repo's root `AGENTS.md` before planning, editing, review, or progress work
- use a non-default documentation surface profile only when the user provides one and the repo's documentation surface really needs a different shared review bundle
- add repo-local `agents/` only when justified
- keep user control explicit rather than hiding sequencing inside an orchestration layer
- treat saved upgrade state as repo-local working records under `docs/upgrade/`

## Setup Work

Before planning, editing, review, or progress work, declare the target repo's documentation surface profile in the root `AGENTS.md`.

The dedicated setup prompt should:

1. use the documentation surface profile explicitly provided by the user
2. write or update a prominent `Documentation surface profile: <profile>.` line in the root `AGENTS.md`
3. create a minimal bootstrap root `AGENTS.md` when the target repo does not yet have one
4. stay narrower than the broader `minimum repo-level AGENTS.md` editing task

Later upgrade prompts should read that root-`AGENTS.md` declaration as the workflow input for documentation-surface-profile handling. If the declaration is missing, they should stop and send the user to the setup prompt rather than inferring or choosing a profile themselves.

The setup task name is:

- `documentation surface profile declaration`

## Oversight Levels

1. `designs`
   - use when the design is not derivable clearly enough from the repo's source-of-truth documents alone
   - require explicit approval, and have the user review designs or direction before detailed planning or execution
   - output: a design-level summary for the user to review before planning or execution continues
   - workflow behavior: write the design-level task artifact, then stop for user review and approval
2. `plans`
   - use when the needed change is clear enough from the current surface, but execution needs sign-off
   - require explicit approval, and have the user review proposed changes or plans before execution
   - output: a plan-level summary for the user to review before execution
   - workflow behavior: write the plan-level task artifact, then stop for user review and approval
3. `outputs`
   - use when the agent can safely derive and execute the needed changes without additional guidance
   - user reviews outputs
   - output: a result summary for the user to review after the task is completed
   - workflow behavior: complete the task, write the result artifact, then report the result to the user

## Planning Work

Planning is user-invoked rather than always-on workflow control. The shared planning prompt should be able to:

1. inspect the current surface
   - use `upgrade/report-current-agent-surface.md` as the detailed current-state discovery standard
   - keep the current-state read provisional and grounded in observed repo evidence
2. design the upgrade approach
   - use the documentation surface profile declared in the root `AGENTS.md`
   - decide the main goals and out-of-scope areas
   - decide the editing tasks in scope
   - decide the main change scopes suggested by the current surface
   - decide the review or validation needed before the upgrade is treated as complete
3. write or revise the saved plan
   - write `docs/upgrade/plan.md`
   - keep the plan simple: one planned edit task per row with a change scope and brief note
   - preserve row order as the intended execution order when the plan is later used for next-step guidance

The planning prompt may update `docs/upgrade/plan.md` progressively as the user and agent refine the plan. Use:

- `draft` when the file records a working plan that the user has not yet approved
- `approved` when the user has explicitly approved the current saved plan

If an approved plan is revised later, set it back to `draft` until the user reapproves it.

The planning prompt may also be used in a review-only mode when the user wants to inspect the current saved plan before changing or approving it. In that mode it should inspect the current plan and repo, leave `docs/upgrade/plan.md` unchanged, and either return concrete revision guidance or guide the user through the plan step by step, depending on the request.

When the planning prompt returns its result in chat, it should:

- show the saved planned-task table back to the user, including change scopes, rather than only listing task names
- state clearly whether the saved plan is still `draft` or already `approved`
- make the next user step explicit
- when the plan is still `draft`, name the likely first planned task after approval so the user can judge whether the proposed order makes sense
- when the plan is still `draft`, end with concrete user options to do a guided step-by-step plan review, review saved plan changes, or approve the current saved plan
- in that `Next Steps:` block, the option labels may be highlighted, but do not format them as inline code; format only the exact launch prompts as code

## Current-Surface Reporting And Provisional Task Tables

The current-surface report is a bounded current-state read that supports planning. It should inspect the repo as it exists now, record uncertainty when evidence is incomplete, and avoid making final profile, oversight, or execution decisions.

Use the provisional editing-task table as a planning aid only:

- include every core editing task
- do not assign oversight levels or commit to execution order in the current-surface report
- distinguish between missing documentation for a real current interface and the absence of a mature interface altogether
- keep the table grounded in observed current-state evidence rather than desired target-state design

For additional `public-python` rows in the provisional table:

- include the full additional `public-python` task block when the current surface shows one or more bounded `public-python` signals such as public package metadata, docs-site config, reachable public docs entrypoints, public contributor or release docs, or public examples and tutorial assets
- treat inclusion of that `public-python` block as current-state evidence only, not as a final documentation-surface-profile decision
- if one of those bounded `public-python` signals is present, include every additional `public-python` editing-task row even when some rows are `not needed` or `not yet evident`
- if no bounded `public-python` signal is present, omit the additional `public-python` block and say so in the report

For thin public-package scaffolds:

- distinguish between a thin public package scaffold and a broader active public-doc surface
- treat public package metadata, a basic `README.md`, and a small test surface by themselves as a thin public package scaffold rather than as evidence of a broad public-doc surface
- keep `public-python` rows narrowly evidence-based and do not expand public-facing rows beyond the bounded public artifacts the current surface actually shows
- default `public user documentation` to `preserve` when a bounded public-facing entry artifact such as a basic `README.md` already exists; otherwise mark it `not needed` with change scope `n/a`
- default `public developer documentation`, `public contributor and release surface`, and `public examples and tutorial assets` to `not needed` with change scope `n/a` unless the current surface already contains a bounded artifact for that exact row
- do not treat a thin public package scaffold by itself as grounds for broad `develop` or `restructure` findings across multiple public-facing documentation rows

For current-state rows that still point to `private-default`:

- treat the public-facing `public-python` rows as current-state rows only
- default those rows to `not needed` with change scope `n/a` unless the current surface already contains a bounded public-facing artifact for that exact row
- do not use `develop` for those rows while the current-state evidence still points to `private-default`
- do not use `restructure` for those rows unless the current surface already contains a clearly segmented or multi-artifact public-facing surface for the specific row

## Editing Tasks

Editing prompts should operate on one task at a time. Planning is useful for coordinated upgrades, but it is not a prerequisite for every edit. A user may also run an individual edit task directly to correct drift or do targeted maintenance.

When `docs/upgrade/plan.md` exists, edit prompts should consult it. If the saved plan contains a row for the current task, the edit prompt should use that row's saved change scope and task notes as the primary planning guidance for the task, and it should also apply any relevant global constraints or review expectations recorded in the plan's `## Notes`. This applies even when the saved plan is still `draft`, because user decisions made during planning may already be persisted there.

The saved plan should guide the task, not replace task-relevant repo evidence. If current repo evidence materially contradicts the saved plan row for the task, the edit prompt should stop, write a blocked task artifact that explains the mismatch, and send the user back to `upgrade-plan.md` to revise or reapprove the plan before continuing.

Most repos use the core editing tasks below. Some documentation surface profiles, such as `public-python`, add additional editing tasks. An editing task is one coherent set of changes with:

- one main purpose
- one main kind of move
- one expected ownership or structure effect
- an output for the user, shaped by the task's oversight level

### Core Editing Tasks

1. minimum repo-level `AGENTS.md`
   - establish the minimum recommended repo-level `AGENTS.md` surface from `docs/usage.md`
2. minimum repo-level `README.md`
   - establish the minimum recommended repo entrypoint and top-level navigation
3. minimum source-of-truth docs
   - minimum source-of-truth docs including `docs/architecture.md` and `docs/data-sources.md` when needed
   - use `docs/data-sources.md` only when the repo has meaningful durable data artifacts that need one stable inventory-and-ownership doc; do not use it as the owner for data interfaces or persisted contracts
4. minimum environment and execution support
   - document the minimum environment setup, execution commands, and runtime prerequisites needed to support agent operation
5. minimum testing and validation support
   - establish `docs/testing.md` and the minimum testing or validation code needed to support agent operation
6. additional interface docs
   - document additional commands, services, APIs, or entrypoints the agent must understand to operate effectively
7. additional supporting docs
   - existing or newly retained docs that remain useful after normalization, including operational or secrets-related docs when needed, and are linked from stronger owners

### Additional `public-python` Editing Tasks

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

### Change Scopes

For each editing task, first assess what the repo needs, then classify the change scope, then apply the oversight level. Use change scope to describe how invasive the intended change is:

1. `preserve`
   - carry forward, clarify, relink, or lightly improve existing information without materially changing meaning
2. `restructure`
   - move, split, merge, or regroup information while keeping the meaning mostly the same
3. `develop`
   - add new content or change existing content significantly

### Oversight Mapping

If the planned or intended change is not clearly derivable from the repo's source of truth, escalate to the next stricter oversight level or split the editing task.

Core editing tasks:

| Editing task | `preserve` | `restructure` | `develop` |
| --- | --- | --- | --- |
| `minimum repo-level AGENTS.md` | `plans` | `plans` | `plans` |
| `minimum repo-level README.md` | `plans` | `plans` | `designs` |
| `minimum source-of-truth docs` | `plans` | `plans` | `designs` |
| `minimum environment and execution support` | `outputs` | `plans` | `plans` |
| `minimum testing and validation support` | `outputs` | `plans` | `plans` |
| `additional interface docs` | `outputs` | `plans` | `designs` |
| `additional supporting docs` | `outputs` | `plans` | `designs` |

Additional `public-python` editing tasks:

| Editing task | `preserve` | `restructure` | `develop` |
| --- | --- | --- | --- |
| `public package metadata` | `outputs` | `plans` | `designs` |
| `public user documentation` | `outputs` | `plans` | `designs` |
| `public developer documentation` | `outputs` | `plans` | `plans` |
| `public contributor and release surface` | `outputs` | `plans` | `designs` |
| `public examples and tutorial assets` | `outputs` | `plans` | `designs` |

## Review Tasks

1. review the agent surface (`outputs`)
   - review the upgraded agent surface, documentation, prompts, and validation results together, using the existing `validation/review` infrastructure
   - output: a report on the agent surface, with findings or explicit confirmation that no material issues were found
2. review the public documentation surface (`outputs`)
   - review the public user and developer documentation surface using the existing `public-python` validation path
   - output: a report on the public documentation surface, with findings or explicit confirmation that no material issues were found
3. report remaining issues (`outputs`)
   - report any important risks, gaps, or follow-up work before treating the upgrade as complete
   - output: a report on remaining issues, with any risks, gaps, or recommended follow-up

## Workflow

The upgrade process still has three conceptual phases:

1. Plan
2. Edit
3. Review

The difference is that the user explicitly chooses which prompt to run next. There is no always-on orchestrator.

In practice:

- use `upgrade-plan.md` when the user wants to inspect the current surface, draft the plan, revise the plan, or mark the plan approved
- use a direct edit prompt when the user wants one specific editing task executed
- use the review prompts when the user wants validation or a closing review pass
- use `upgrade-progress.md` when the user wants a synthesized view of saved upgrade state and a recommended next step

Each prompt should end with a clear output or short summary presented to the user so they can review progress and decide what to run next.

## Repo-Local Upgrade Artifacts

Use `docs/upgrade/` in the target repo as the durable working area for upgrade state.

The saved artifact model is:

- root `AGENTS.md`
  - stores the declared `Documentation surface profile: <profile>.`
  - should be set or updated before other upgrade prompts run
- `docs/upgrade/plan.md`
  - saved by `upgrade-plan.md`
  - keeps the current planning state as `draft` or `approved`
  - supplies saved change scopes and planning guidance to edit prompts when a matching task row exists
- `docs/upgrade/edit-*.md`
  - one saved artifact per edit task
  - rerunning a task replaces that task's artifact
- `docs/upgrade/review-*.md`
  - one saved artifact per review task
  - rerunning a review replaces that review artifact

Use fixed sections for those repo-local artifacts so both users and prompts can read them reliably:

- `docs/upgrade/plan.md`
  - `## Metadata`
  - `## Planned Tasks`
  - `## Notes`
- each `docs/upgrade/edit-*.md`
  - `## Metadata`
  - `## Scope And Oversight`
  - `## Approval`
  - `## Output`
  - `## Follow-Up`
- each `docs/upgrade/review-*.md`
  - `## Metadata`
  - `## Scope And Oversight`
  - `## Approval`
  - `## Output`
  - `## Follow-Up`

These saved files are the durable source of truth for the upgrade. There is no separate centralized progress template.

## Prompt Architecture

Repeated real-repo use showed that a user-facing orchestrator and one generic edit prompt were too indirect. The active prompt architecture should therefore be:

1. `upgrade-documentation-surface-profile`
   - user-run setup prompt
   - writes or updates the target repo's root `AGENTS.md`
   - records the documentation surface profile before other upgrade prompts run
2. `upgrade-plan`
   - user-run planning prompt
   - writes or revises `docs/upgrade/plan.md`
3. `upgrade-progress`
   - user-run progress and next-step prompt
   - reads the root `AGENTS.md` documentation surface profile declaration plus `docs/upgrade/plan.md` and any saved `docs/upgrade/edit-*.md` and `docs/upgrade/review-*.md` files
   - recommends the next step in chat only
4. `edit/base`
   - shared contract for the core edit prompts
   - reads `docs/upgrade/plan.md` when it exists and applies matching task guidance
5. `edit/AGENTS.md`
   - local router that keeps `edit/base` active for direct core edit prompts
6. one core edit prompt per editing task under `upgrade/edit/`
   - each writes one `docs/upgrade/edit-*.md` file
7. `edit/public-python/AGENTS.md`
   - local router that keeps the broader core edit contract active and adds the `public-python` base for direct `public-python` edit prompts
8. `edit/public-python/base`
   - shared contract for the `public-python` edit prompts
   - inherits the same plan-consultation behavior for matching `public-python` task rows
9. one `public-python` edit prompt per editing task under `upgrade/edit/public-python/`
   - each writes one `docs/upgrade/edit-public-*.md` file
10. `upgrade-review`
   - shared core review prompt
   - writes `docs/upgrade/review-agent-surface.md` or `docs/upgrade/review-remaining-issues.md`
11. `upgrade-review-public-python`
   - shared `public-python` review prompt
   - writes `docs/upgrade/review-public-documentation-surface.md`

Use `upgrade-progress.md` as the only prompt with next-step capability. It may read `docs/upgrade/plan.md` for sequencing, but only when the saved plan status is `approved`. It should not update task artifacts or act as a hidden orchestrator.

`private-default` is represented by the core edit and review prompts. Non-default documentation surface profiles may add profile-specific edit and review prompts when their path differs materially from the core path.

## Begin An Upgrade

If the target repo is not the current repo, name the target root explicitly in the prompt.

Use these direct prompts to launch setup, planning, progress, editing, and review work:

| Task | Prompt |
| --- | --- |
| `documentation surface profile declaration` | `Use astro-agents/upgrade/upgrade-documentation-surface-profile.md to set this repo's documentation surface profile to <private-default|public-python> and record it prominently in the root AGENTS.md.` |
| `planning work` | `Use astro-agents/upgrade/upgrade-plan.md to inspect this repo and write or revise docs/upgrade/plan.md.` |
| `progress and next step` | `Use astro-agents/upgrade/upgrade-progress.md to summarize current upgrade progress for this repo and recommend the next step.` |
| `minimum repo-level AGENTS.md` | `Use astro-agents/upgrade/edit/minimum-repo-agents.md to run the single core edit task 'minimum repo-level AGENTS.md' for this repo.` |
| `minimum repo-level README.md` | `Use astro-agents/upgrade/edit/minimum-repo-readme.md to run the single core edit task 'minimum repo-level README.md' for this repo.` |
| `minimum source-of-truth docs` | `Use astro-agents/upgrade/edit/minimum-source-of-truth-docs.md to run the single core edit task 'minimum source-of-truth docs' for this repo.` |
| `minimum environment and execution support` | `Use astro-agents/upgrade/edit/minimum-environment-and-execution-support.md to run the single core edit task 'minimum environment and execution support' for this repo.` |
| `minimum testing and validation support` | `Use astro-agents/upgrade/edit/minimum-testing-and-validation-support.md to run the single core edit task 'minimum testing and validation support' for this repo.` |
| `additional interface docs` | `Use astro-agents/upgrade/edit/additional-interface-docs.md to run the single core edit task 'additional interface docs' for this repo.` |
| `additional supporting docs` | `Use astro-agents/upgrade/edit/additional-supporting-docs.md to run the single core edit task 'additional supporting docs' for this repo.` |
| `public package metadata` | `Use astro-agents/upgrade/edit/public-python/package-metadata.md to run the single public-python edit task 'public package metadata' for this repo.` |
| `public user documentation` | `Use astro-agents/upgrade/edit/public-python/user-documentation.md to run the single public-python edit task 'public user documentation' for this repo.` |
| `public developer documentation` | `Use astro-agents/upgrade/edit/public-python/developer-documentation.md to run the single public-python edit task 'public developer documentation' for this repo.` |
| `public contributor and release surface` | `Use astro-agents/upgrade/edit/public-python/contributor-and-release-surface.md to run the single public-python edit task 'public contributor and release surface' for this repo.` |
| `public examples and tutorial assets` | `Use astro-agents/upgrade/edit/public-python/examples-and-tutorial-assets.md to run the single public-python edit task 'public examples and tutorial assets' for this repo.` |
| `review the agent surface` | `Use astro-agents/upgrade/upgrade-review.md to run the core review task 'review the agent surface' for this repo.` |
| `review the public documentation surface` | `Use astro-agents/upgrade/upgrade-review-public-python.md to run the review task 'review the public documentation surface' for this repo.` |
| `report remaining issues` | `Use astro-agents/upgrade/upgrade-review.md to run the core review task 'report remaining issues' for this repo.` |

Use the `public-python` prompt rows only when the repo's root `AGENTS.md` explicitly declares `Documentation surface profile: public-python`.

## Validation

Validate the upgrade process with a mix of user-led assessment and automation. Use automation where it can check bounded behavior reliably, and use user-led assessment where judgment about usefulness, proportionality, or design quality is still required.

Validate the upgrade process at three levels:

1. task level
   - automate checks where possible to confirm that each task prompt produces the expected output, respects the assigned oversight level, and stays within its task boundary
   - use user-led assessment to judge whether the task output is actually useful and appropriately scoped
2. repo level
   - run trial upgrades on a small set of benchmark repos
   - automate whatever can be checked mechanically
   - use user-led assessment to judge whether the process moves cleanly task by task, chooses reasonable editing tasks and change scopes, and produces useful repo-local artifacts for the user
3. process level
   - preserve the saved `docs/upgrade/*.md` outputs from those benchmark upgrades so later prompt changes can be checked for regressions
   - use user-led assessment to decide whether changes improve or degrade the overall upgrade experience

The main test of the upgrade process is whether it can upgrade real repos cleanly and predictably, not whether the prompt set looks elegant in isolation.

# Upgrade

This document is the human-facing source of truth for upgrading existing repos to an `astro-agents`-compatible agent surface.

Use it first to understand the current portfolio and recurring upgrade patterns. Later phases should extend it into the upgrade process and decision rules for repo-by-repo migration work.

This document focuses on the baseline local work needed to make an existing repo usable and effective for agents. It does not treat advanced runtime governance, observability, safety, or eval infrastructure as normal repo-upgrade responsibilities. Those are important, but they should mostly be provided by shared `astro-agents` support rather than expected from typical upgraded repos.

## Local Terms

Use `docs/glossary.md` for repo-wide terminology. This document also uses a small number of local terms that are specific to the upgrade model.

- `upgrade level`: a classification of the upgrade work required to bring a repo onto the shared `astro-agents` path, based mainly on the state of its agent surface
- `documentation surface profile`: the repo's documentation-validation profile, used to select the shared documentation review bundle; built-in shared profiles currently include `private-default` and `public-python`
- `task type`: the broad kind of upgrade task being planned or executed; this document uses `planning tasks`, `editing tasks`, and `review tasks`
- `change scope`: the invasiveness of a planned change, ranging from extraction or cleanup through structural rewrite and framing change
- `editing task`: a coherent category of upgrade editing work tied to one part of the agent surface
- `oversight level`: the level of user oversight required for a given task; for editing tasks, it is also shaped by change scope

For validation planning, use the repo's documentation surface profile when one exists. When no non-default profile is declared, shared validation defaults to `private-default`.

## Purpose

Use this document to record:

- the portfolio scan of likely code repos under `Projects/`
- recurring agent-surface gaps across those repos
- the upgrade levels the upgrade process must handle
- the documentation surface profiles that change how those upgrade levels should be handled
- the upgrade machinery that should be built in later phases

## Upgrade Process Design

Treat every upgrade as a controlled normalization pass that assesses the current agent surface, designs the needed work task by task, and applies the appropriate documentation-review path for the repo's documentation surface profile.

Every upgrade should answer the same four questions first:

1. what does the repo's current agent surface do now
2. what should the target agent surface look like after normalization, including its documentation surface profile
3. in what order should we decide which files own which information, create structure, and clean up the surface to minimize drift
4. what level of user oversight each change needs

Across the whole process:

- create clearer source-of-truth docs before trimming older docs
- preserve meaning by default
- use a non-default documentation surface profile only when the repo's documentation surface really needs a different shared review bundle
- add repo-local `agents/` only when justified

### Oversight Levels

1. `designs`
   - use when the design is not derivable clearly enough from the repo's source-of-truth documents alone
   - require explicit approval, and have the user review designs or direction before detailed planning or execution
   - output: a design-level summary for the user to review before planning or execution continues
   - workflow behavior: produce the design-level output, then stop for user review and approval
2. `plans`
   - use when the needed change is clear enough from the current surface, but execution needs sign-off
   - require explicit approval, and have the user review proposed changes or plans before execution
   - output: a plan-level summary for the user to review before execution continues
   - workflow behavior: produce the plan-level output, then stop for user review and approval
3. `outputs`
   - use when the agent can safely derive and execute the needed changes without additional guidance
   - user reviews outputs
   - output: a result summary for the user to review after the task is completed
   - workflow behavior: complete the task, then report the result to the user

### Planning Tasks

1. report on current agent surface (`outputs`)
   - inspect the current surface, build the role map, and identify major structural concerns
   - output: a report on the current agent surface, with the main findings
2. design the upgrade approach (`designs`)
   - classify the upgrade level and documentation surface profile
   - decide the main goals and out-of-scope areas
   - decide the editing tasks in scope
   - define oversight checkpoints
   - decide the review or validation needed before the upgrade is treated as complete
   - output: a report on the upgrade approach, with the main decisions about upgrade level, documentation surface profile, tasks in scope, oversight, and review requirements
3. write the upgrade plan (`plans`)
   - turn the approved upgrade approach into a concrete plan for execution
   - output: the plan

### Editing Tasks

Plan each upgrade as a small set of editing tasks before editing begins. These are the tasks that make the actual file and surface changes needed for the upgrade. Most repos use the core editing tasks below. Some documentation surface profiles, such as `public-python`, add additional editing tasks. An editing task is one coherent set of changes with:

- one main purpose
- one main kind of move
- one expected ownership or structure effect
- an output for the user, shaped by the task's oversight level

#### Core Editing Tasks

1. minimum repo-level `AGENTS.md`
   - establish the minimum recommended repo-level `AGENTS.md` surface from `docs/usage.md`
2. minimum repo-level `README.md`
   - establish the minimum recommended repo entrypoint and top-level navigation
3. minimum source-of-truth docs
   - minimum source-of-truth docs including `docs/architecture.md` and `docs/data-sources.md` when needed
4. additional interface docs
   - document additional commands, services, APIs, or entrypoints the agent must understand to operate effectively
5. additional supporting docs
   - existing or newly retained docs that remain useful after normalization, including operational or secrets-related docs when needed, and are linked from stronger owners
6. minimum environment and execution support
   - document the minimum environment setup, execution commands, and runtime prerequisites needed to support agent operation
7. minimum testing and validation support
   - establish `docs/testing.md` and the minimum testing or validation code needed to support agent operation

#### Additional `public-python` Editing Tasks

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

#### Change Scopes

For each editing task, first assess what the repo needs, then classify the change scope, then apply the oversight level. Use change scope to describe how invasive a planned change is:

1. `preserve`
   - carry forward, clarify, relink, or lightly improve existing information without materially changing meaning
2. `restructure`
   - move, split, merge, or regroup information while keeping the meaning mostly the same
3. `develop`
   - add new content or change existing content significantly

#### Oversight Mapping

If the planned change is not clearly derivable from the repo's source of truth, escalate to the next stricter oversight level or split the editing task.

Core editing tasks:

| Editing task | `preserve` | `restructure` | `develop` |
| --- | --- | --- | --- |
| `minimum repo-level AGENTS.md` | `plans` | `plans` | `plans` |
| `minimum repo-level README.md` | `plans` | `plans` | `designs` |
| `minimum source-of-truth docs` | `plans` | `plans` | `designs` |
| `additional supporting docs` | `outputs` | `plans` | `designs` |
| `additional interface docs` | `outputs` | `plans` | `designs` |
| `minimum environment and execution support` | `outputs` | `plans` | `plans` |
| `minimum testing and validation support` | `outputs` | `plans` | `plans` |

Additional `public-python` editing tasks:

| Editing task | `preserve` | `restructure` | `develop` |
| --- | --- | --- | --- |
| `public package metadata` | `outputs` | `plans` | `designs` |
| `public user documentation` | `outputs` | `plans` | `designs` |
| `public developer documentation` | `outputs` | `plans` | `plans` |
| `public contributor and release surface` | `outputs` | `plans` | `designs` |
| `public examples and tutorial assets` | `outputs` | `plans` | `designs` |

### Review Tasks

1. review the agent surface (`outputs`)
   - review the upgraded agent surface, documentation, prompts, and validation results together, using the existing `validation/review` infrastructure
   - output: a report on the agent surface, with findings or explicit confirmation that no material issues were found
2. review the public documentation surface (`outputs`)
   - review the public user and developer documentation surface using the existing `public-python` validation path
   - output: a report on the public documentation surface, with findings or explicit confirmation that no material issues were found
3. report remaining issues (`outputs`)
   - report any important risks, gaps, or follow-up work before treating the upgrade as complete
   - output: a report on remaining issues, with any risks, gaps, or recommended follow-up

### Workflow

The upgrade process has three phases:

1. Plan
2. Edit
3. Review

Move through that workflow task by task. The goal is not to jump ahead or overwhelm the user, but to keep the process legible and manageable at each step. Each task should end with a clear output or short summary presented to the user so they can review progress and intervene where necessary before the process moves on.

### Prompt Architecture

In practice, the upgrade process should be driven by one shared orchestrating upgrade prompt plus a durable upgrade-progress source of truth. The orchestrator should decide which task comes next, keep the progress source of truth up to date, and move through the workflow one task at a time. After each task, it should present the task output to the user and indicate which prompt to run next if the process should continue.

The upgrade-progress source of truth should carry the durable state of the workflow, including:

- the current-surface report
- the report on the upgrade approach
- the plan
- task-by-task editing summaries
- review reports
- the current workflow state and the recommended next task

The orchestrating upgrade prompt should activate this workflow when an upgrade is initiated. The upgrade task prompts should not be treated as always-on parts of the repo's normal agent surface.

Build the first version of the upgrade workflow around a small prompt inventory:

1. `upgrade-orchestrator`
   - the main entrypoint for the upgrade process
   - initializes or resumes the upgrade-progress source of truth
   - decides which task should run next
   - enforces oversight checkpoints
   - tells the user which prompt to run next
2. `upgrade-plan`
   - handles one planning task at a time
   - reads the current upgrade-progress source of truth
   - updates it with the task output
3. `upgrade-edit`
   - handles one core editing task at a time
   - reads the current upgrade-progress source of truth
   - updates it with the task output
4. `upgrade-edit-public-python`
   - handles one `public-python` editing task at a time when that profile applies
   - reads the current upgrade-progress source of truth
   - updates it with the task output
5. `upgrade-review`
   - handles one core review task at a time
   - reads the current upgrade-progress source of truth
   - updates it with the task output
6. `upgrade-review-public-python`
   - handles one `public-python` review task at a time when that profile applies
   - reads the current upgrade-progress source of truth
   - updates it with the task output

Do not start with one prompt per editing task. The first version should use one shared orchestrator plus a small set of task prompts, with explicit profile-specific prompts only where the `public-python` path really differs. Split task prompts further only when repeated use shows that narrower prompts are actually needed.

`private-default` is represented by the core edit and review prompts. Non-default documentation surface profiles may add profile-specific edit and review prompts when their path differs materially from the core path.

The current planning prompt should be treated as obsolete once a replacement is ready. Because the planning model has changed materially, it should be replaced from scratch rather than incrementally revised.

### Validation

Validate the upgrade process with a mix of user-led assessment and automation. Use automation where it can check bounded behavior reliably, and use user-led assessment where judgment about usefulness, proportionality, or design quality is still required.

Validate the upgrade process at three levels:

1. task level
   - automate checks where possible to confirm that each task prompt produces the expected output, respects the assigned oversight level, and stays within its task boundary
   - use user-led assessment to judge whether the task output is actually useful and appropriately scoped
2. repo level
   - run trial upgrades on a small set of benchmark repos
   - automate whatever can be checked mechanically
   - use user-led assessment to judge whether the process moves cleanly task by task, chooses reasonable editing tasks and change scopes, and produces useful outputs for the user
3. process level
   - preserve the reports, plans, and review outputs from those benchmark upgrades so later prompt changes can be checked for regressions
   - use user-led assessment to decide whether changes improve or degrade the overall upgrade experience

The main test of the upgrade process is whether it can upgrade real repos cleanly and predictably, not whether the prompt set looks elegant in isolation.

## Next Steps

1. write the prompt for `report on current agent surface`
2. do the portfolio scan again using that prompt, including the initial repo-by-task change-scope table
3. update the `report on current agent surface` prompt and iterate with the portfolio scan until the results are satisfactory
4. replace the current orchestrating prompt file with the first version of the new upgrade orchestrator
5. define a template for the upgrade-progress source of truth
6. build the rest of the upgrade prompt family
7. upgrade a few preliminary repos and improve the upgrade process
   - `ao-predict`
   - `pubify-mpl`
   - `survey_tools`
8. develop testing and validation of the upgrade process
9. upgrade the rest of the repos
   - `pubify-pubs`
   - `girmos-aosims`
   - `cubesim`
   - `girmos-legacy-survey`


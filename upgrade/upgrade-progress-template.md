# Upgrade Progress Record Template

Use this template to create the durable upgrade-progress record for one repo or target root.

Copy it into the target repo as `docs/upgrade-progress.md`, and keep that copied file as the source of truth that the upgrade orchestrator and task prompts read and update.

## Update Discipline

- the orchestrator owns the record metadata, workflow-state, documentation-surface-profile input, checkpoint, blocker, and next-step fields
- each task prompt owns its task-ledger row and its task-output section
- treat the documentation surface profile as workflow input provided by the user; do not rewrite it as a current-surface conclusion or an approved decision
- preserve completed task outputs unless newer evidence clearly supersedes them
- keep the record task-by-task; do not collapse completed work into a loose narrative summary

## Status Vocabulary

- record status: `not started`, `active`, `blocked`, `complete`
- task status: `not started`, `in progress`, `waiting for approval`, `done`, `blocked`, `not needed`
- change scope: `preserve`, `restructure`, `develop`, `n/a`, `tbd`
- oversight level: `designs`, `plans`, `outputs`, `tbd`
- checkpoint status: `not needed`, `required`, `waiting for approval`, `approved`

## Record Metadata

- target root:
- target scope:
- record status: `not started`
- current workflow phase: `plan`
- documentation surface profile:
- started:
- last updated:
- last orchestrator update:

## Orchestrator State

- active task:
- last completed task:
- recommended next task:
- prompt to run next:
- blocker:
- next action for user:

## Oversight And Approval Ledger

| Task id | Task | Required oversight | Checkpoint status | Approval reference | Notes |
| --- | --- | --- | --- | --- | --- |
| plan-1 | report on current agent surface | `outputs` | `not needed` |  |  |
| plan-2 | design the upgrade approach | `designs` | `required` |  |  |
| plan-3 | write the upgrade plan | `plans` | `required` |  |  |

Add rows here for editing tasks after `plan-2` or `plan-3` assigns their task-specific oversight levels.

## Task Ledger

| Task id | Task | Phase | Applicability | Status | Change scope | Oversight | Prompt | Last update | Summary |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plan-1 | report on current agent surface | plan | core | `not started` | `n/a` | `outputs` | `astro-agents/upgrade/upgrade-plan.md` |  |  |
| plan-2 | design the upgrade approach | plan | core | `not started` | `n/a` | `designs` | `astro-agents/upgrade/upgrade-plan.md` |  |  |
| plan-3 | write the upgrade plan | plan | core | `not started` | `n/a` | `plans` | `astro-agents/upgrade/upgrade-plan.md` |  |  |
| edit-core-1 | minimum repo-level `AGENTS.md` | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-core-2 | minimum repo-level `README.md` | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-core-3 | minimum source-of-truth docs | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-core-4 | minimum environment and execution support | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-core-5 | minimum testing and validation support | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-core-6 | additional interface docs | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-core-7 | additional supporting docs | edit | core | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit.md` |  |  |
| edit-public-1 | public package metadata | edit | `public-python` only | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit-public-python.md` |  |  |
| edit-public-2 | public user documentation | edit | `public-python` only | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit-public-python.md` |  |  |
| edit-public-3 | public developer documentation | edit | `public-python` only | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit-public-python.md` |  |  |
| edit-public-4 | public contributor and release surface | edit | `public-python` only | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit-public-python.md` |  |  |
| edit-public-5 | public examples and tutorial assets | edit | `public-python` only | `not started` | `tbd` | `tbd` | `astro-agents/upgrade/upgrade-edit-public-python.md` |  |  |
| review-1 | review the agent surface | review | core | `not started` | `n/a` | `outputs` | `astro-agents/upgrade/upgrade-review.md` |  |  |
| review-2 | review the public documentation surface | review | `public-python` only | `not started` | `n/a` | `outputs` | `astro-agents/upgrade/upgrade-review-public-python.md` |  |  |
| review-3 | report remaining issues | review | core | `not started` | `n/a` | `outputs` | `astro-agents/upgrade/upgrade-review.md` |  |  |

## Planning Outputs

### plan-1: report on current agent surface

- prompt used:
- status:
- completed:
- output:

### plan-2: design the upgrade approach

- prompt used:
- status:
- completed:
- documentation surface profile used:
- goals in scope:
- out-of-scope areas:
- editing tasks in scope:
- main change scopes:
- oversight checkpoints:
- review requirements:
- output:

### plan-3: write the upgrade plan

- prompt used:
- status:
- completed:
- planned task order:
- output:

## Core Editing Task Records

### edit-core-1: minimum repo-level `AGENTS.md`

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-core-2: minimum repo-level `README.md`

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-core-3: minimum source-of-truth docs

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-core-4: minimum environment and execution support

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-core-5: minimum testing and validation support

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-core-6: additional interface docs

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-core-7: additional supporting docs

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

## `public-python` Editing Task Records

### edit-public-1: public package metadata

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-public-2: public user documentation

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-public-3: public developer documentation

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-public-4: public contributor and release surface

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

### edit-public-5: public examples and tutorial assets

- applicability:
- status:
- planned change scope:
- required oversight:
- prompt used:
- files changed:
- summary:
- follow-up:

## Review Outputs

### review-1: review the agent surface

- prompt used:
- status:
- completed:
- output:

### review-2: review the public documentation surface

- prompt used:
- status:
- completed:
- output:

### review-3: report remaining issues

- prompt used:
- status:
- completed:
- output:

## Open Questions And Blockers

- none recorded

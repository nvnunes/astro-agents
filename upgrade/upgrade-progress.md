# Upgrade Progress

## Purpose
Use this prompt to read the root `AGENTS.md` documentation surface profile declaration plus the saved upgrade artifacts under `docs/upgrade/` in the target repo, summarize current progress, and recommend a next step in chat.

This prompt reports and recommends. It does not update `docs/upgrade/*.md` and it does not act as a hidden orchestrator.

## Inputs

- target root or target paths
- optional plan path, defaulting to `docs/upgrade/plan.md`
- optional focus areas
- optional target scope that narrows work below the full target root

If the target scope is not specified, default to the requested repo or target root rather than the whole workspace.

## Artifact Mapping

Core edit tasks:

| Task | Saved artifact |
| --- | --- |
| minimum repo-level `AGENTS.md` | `docs/upgrade/edit-minimum-repo-agents.md` |
| minimum repo-level `README.md` | `docs/upgrade/edit-minimum-repo-readme.md` |
| minimum source-of-truth docs | `docs/upgrade/edit-minimum-source-of-truth-docs.md` |
| minimum environment and execution support | `docs/upgrade/edit-minimum-environment-and-execution-support.md` |
| minimum testing and validation support | `docs/upgrade/edit-minimum-testing-and-validation-support.md` |
| additional interface docs | `docs/upgrade/edit-additional-interface-docs.md` |
| additional supporting docs | `docs/upgrade/edit-additional-supporting-docs.md` |

`public-python` edit tasks:

| Task | Saved artifact |
| --- | --- |
| public package metadata | `docs/upgrade/edit-public-package-metadata.md` |
| public user documentation | `docs/upgrade/edit-public-user-documentation.md` |
| public developer documentation | `docs/upgrade/edit-public-developer-documentation.md` |
| public contributor and release surface | `docs/upgrade/edit-public-contributor-and-release-surface.md` |
| public examples and tutorial assets | `docs/upgrade/edit-public-examples-and-tutorial-assets.md` |

Review tasks:

| Task | Saved artifact |
| --- | --- |
| review the agent surface | `docs/upgrade/review-agent-surface.md` |
| review the public documentation surface | `docs/upgrade/review-public-documentation-surface.md` |
| report remaining issues | `docs/upgrade/review-remaining-issues.md` |

## Common Workflow

1. Read `astro-agents/docs/upgrade-design.md` first.
   - use the `## Begin An Upgrade` table there as the source of truth for the exact user-facing launch prompt to recommend for any task
2. Read the target repo's root `AGENTS.md` first and check whether it declares `Documentation surface profile: <profile>.`
3. Read `docs/upgrade/plan.md` when it exists.
4. Read the existing `docs/upgrade/edit-*.md` and `docs/upgrade/review-*.md` files that are present.
5. Summarize the saved state without rewriting or reclassifying it.
6. Recommend the next step in chat using the rules below and quote the exact launch prompt from the `## Begin An Upgrade` table for the matching task.

## Next-Step Rules

Apply these rules in order:

1. If the target repo's root `AGENTS.md` is missing or does not declare `Documentation surface profile: <profile>.`:
   - say that the documentation surface profile has not yet been declared in the root `AGENTS.md`
   - recommend the `documentation surface profile declaration` launch prompt from the `## Begin An Upgrade` table before any other upgrade prompt
2. If `docs/upgrade/plan.md` is missing:
   - summarize the saved edit and review artifacts that exist
   - say that there is no saved plan
   - suggest creating or updating the plan with the `planning work` launch prompt from the `## Begin An Upgrade` table, or naming a task directly from that same table
   - if any saved edit artifact shows `status: waiting for approval`, report it as advisory context rather than as the authoritative next step
3. If `docs/upgrade/plan.md` exists with `status: draft`:
   - summarize current saved artifacts
   - say that the plan is still draft
   - suggest revising or approving the plan with the `planning work` launch prompt from the `## Begin An Upgrade` table before using it as sequencing guidance
   - if any saved edit artifact shows `status: waiting for approval`, report it as advisory context rather than as the authoritative next step
4. If `docs/upgrade/plan.md` exists with `status: approved`:
   - use the `## Planned Tasks` table as the intended task order
   - if any saved edit artifact for a planned task shows `status: waiting for approval`, recommend that planned approval step before any further planned execution, using the matching task's launch prompt from the `## Begin An Upgrade` table
   - otherwise, recommend the first planned task whose saved artifact is missing or not yet complete, using the matching task's launch prompt from the `## Begin An Upgrade` table
   - if any saved edit artifact outside the approved plan shows `status: waiting for approval`, report it as separate advisory context rather than as the main next step
5. If all planned edit tasks from the approved plan are complete:
   - if `docs/upgrade/review-agent-surface.md` is missing or not done, recommend the `review the agent surface` launch prompt from the `## Begin An Upgrade` table
   - otherwise, if the approved plan records `documentation surface profile: public-python` and `docs/upgrade/review-public-documentation-surface.md` is missing or not done, recommend the `review the public documentation surface` launch prompt from the `## Begin An Upgrade` table
6. If the applicable review artifacts are complete:
   - recommend the `report remaining issues` launch prompt from the `## Begin An Upgrade` table when that artifact is missing
7. If the plan is approved, the planned edit artifacts are complete, and the applicable review artifacts are complete:
   - report that the saved upgrade set appears complete
   - note any findings or follow-up from `docs/upgrade/review-remaining-issues.md` when it exists

Treat `status: done` and `status: not needed` as complete for an edit artifact.

## Exclusions

- do not update any saved artifact under `docs/upgrade/`
- do not invent a missing plan
- do not reinterpret the documentation surface profile
- do not turn a recommendation into hidden workflow control

## Output

Return a progress summary with:

- plan status
- documentation surface profile declaration status in the root `AGENTS.md`
- saved edit artifacts found
- saved review artifacts found
- planned tasks without artifacts, when an approved plan exists
- any planned task waiting for approval
- any non-planned task waiting for approval as advisory context only
- recommended next step, including the exact launch prompt copied from the `## Begin An Upgrade` table and the expected updated file path

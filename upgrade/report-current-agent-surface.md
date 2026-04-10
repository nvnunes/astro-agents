# Report On Current Agent Surface

## Purpose
Use this prompt as the detailed current-surface reporting reference for `plan-1` in `upgrade/upgrade-plan.md`, or directly for rollout-only tasks such as the portfolio scan that still need the standalone current-surface report.

Use it to inspect what the current surface does now, identify the main structural facts and gaps, and produce a clearly provisional editing-task table derived only from current evidence.

Treat this as a current-state reporting prompt, not as the upgrade-approach prompt and not as an execution-planning prompt.

When this prompt is used as part of a portfolio scan, apply it repo by repo. Keep any cross-repo synthesis, normalization, or aggregation outside this prompt.

## Inputs

- target root or target paths to inspect
- optional upgrade-progress record path
- optional focus areas such as routing surface, source-of-truth docs, testing support, public-doc signals, or current-surface synthesis
- optional target scope that narrows the report below the full target root

If the scope is not specified, default to the requested repo or target root rather than the whole workspace.

## Discovery

When running this report:

- use this recommended inspection order unless the target repo's structure makes a different bounded order clearly better:
  - inspect the surface files first
  - assess minimum document-set coverage next
  - inspect bounded operational and public-doc signals next
  - deepen into linked docs, tests, workflows, config, or code only where needed to resolve a material ambiguity in the current-state read
- inspect the current agent surface only within the requested scope
- when an upgrade-progress record is provided, read only the parts needed to confirm scope and update the `plan-1` task record after the report is complete
- do not let previously recorded `plan-1` text replace fresh inspection of the current target root
- discover applicable root and subtree `AGENTS.md` files dynamically from the target root
- inspect `README.md`, `docs/architecture.md`, `docs/data-sources.md`, `docs/testing.md`, and other likely source-of-truth docs when present
- inspect repo-local prompt layers such as `agents/` when present
- inspect bounded non-doc agent-surface evidence when present, including command entrypoints, scripts, CI definitions, validation code, environment or tool config, and other stable execution or interface artifacts that document how the repo is actually operated
- inspect obvious public-doc signals such as `pyproject.toml`, docs-site config, and reachable documentation entrypoints when present
- inspect linked supporting docs only when needed to understand current ownership, routing, source-of-truth structure, data-source ownership, or operational support
- ignore generated build output, caches, environments, vendored dependency trees, and tool-state directories such as `.pytest_cache/`, `site/`, `.venv/`, `.conda/`, `site-packages/`, `node_modules/`, or equivalent non-source surfaces unless a generated artifact is itself the explicit public entrypoint under review
- use `docs/usage.md` as the source of truth for the minimum document set and normalized repo-document expectations
- use `docs/upgrade-design.md` as the source of truth for the editing-task model, including `docs/data-sources.md` as part of minimum source-of-truth docs when the repo's data surface makes that needed
- treat any prior current-surface reports, portfolio summaries, repo classifications, or task tables outside the requested target root as prior process output rather than as evidence about the repo currently under inspection
- ground the report in newly observed repo evidence from the requested target root, and mention prior process output only when explicitly comparing the new read against earlier output
- keep the report grounded in observed files and explicit signals rather than speculative target-state design
- treat missing evidence as uncertainty to record, not as permission to infer the final upgrade approach

## Report Checks

Report the current surface against these checks:

- role map of the current agent-surface files and their apparent owners
- present versus missing parts of the minimum document set from `docs/usage.md`
- current prompt-layer structure and routing surface
- source-of-truth visibility, ownership overlaps, overloaded docs, naming or structure drift, and data-source ownership signals when relevant
- current additional interface documentation and its bounded non-doc evidence such as commands, services, APIs, scripts, or entrypoints the agent would need to understand
- current environment and execution support from both docs and bounded operational artifacts such as setup commands, runtime prerequisites, CI workflows, or stable config
- current testing and validation support from both docs and bounded artifacts such as test commands, validation scripts, workflow definitions, or validation code
- notable public-doc surface signals when the repo already exposes them
- current documentation-surface-profile signals, including a suggested current profile only when the evidence is clear enough to support a current-state read without turning that suggestion into a final classification
- any difference between the suggested current documentation-surface profile and the provisional editing-task-table triggers, when bounded public-surface evidence warrants inspecting `public-python` tasks without making that a final profile judgment
- unknowns that block a confident current-state read

When describing findings:

- distinguish observed facts from cautious inferences
- keep the analysis tied to the current surface rather than the desired normalized surface
- note when a file appears to combine roles that should likely be separated later, but do not redesign that separation here
- mention missing expected materials only when their absence materially affects the current-surface read
- when discussing documentation surface profile, report the current signals and any clear suggested current profile as current-state evidence only, not as a final profile decision
- if the provisional editing-task table includes `public-python` rows based on bounded public-surface evidence, state explicitly that those rows are an evidence-driven table aid and not by themselves a documentation-surface-profile decision

## Exclusions

Do not treat the following as the default task:

- final upgrade-level classification
- final documentation surface profile selection
- target-state design
- oversight-level decisions
- execution planning beyond the provisional editing-task table
- validation review of prompt-writing quality, hierarchy behavior, or documentation quality as separate review tasks
- application-code audit beyond what public-doc or agent-surface discovery requires

## Output

Return one current-surface report with these sections:

1. Scope and confidence.
2. Current role map.
3. Minimum document-set coverage.
4. Routing and prompt-surface summary.
5. Source-of-truth and structure findings.
6. Data-source and interface signals, when relevant.
7. Environment, execution, testing, and validation support summary.
8. Public-doc and documentation-surface-profile signals, only when current evidence supports including them.
9. Unknowns that block a confident read.
10. Provisional editing-task table.

For the report body:

- lead with current-state facts before interpretation
- make uncertainty explicit
- keep conclusions proportional to the available evidence
- if documentation-surface-profile evidence is mixed, report the signals and ambiguity rather than forcing a suggested current profile

When an upgrade-progress record is provided:

- write this report into the `plan-1` output section of that record
- update the corresponding `plan-1` row in the task ledger
- keep the change limited to task-owned parts of the record rather than rewriting orchestration-owned workflow fields

For the provisional editing-task table:

General table rules:

- state explicitly that the table is provisional and current-state-only
- use it as an aid for the later upgrade-approach task, not as the final plan
- include every core editing task from `docs/upgrade-design.md`
- distinguish between missing documentation for a real current interface and the absence of a mature interface altogether
- do not assign oversight levels or commit to execution order in this table

Core editing-task rules:

- for `additional interface docs`, require evidence of a meaningful current interface surface such as multiple stable entrypoints, segmented user-facing commands or APIs, interface-specific docs beyond the main `README.md`, or equivalent bounded interface artifacts already present in the repo
- do not use `develop` or `restructure` for `additional interface docs` when the repo only shows a high-level entry `README.md`, thin package metadata, small tests, or stated future interface plans without a broader current interface surface
- when the current surface is still an early scaffold and the interface is mostly planned rather than already exposed, mark `additional interface docs` as `not needed` with change scope `n/a`
- `minimum source-of-truth docs` may still be `develop` in a thin repo when the current surface already exposes meaningful architecture, contracts, or ownership boundaries that would benefit from being separated into stable source-of-truth docs

When to include `public-python` rows:

- include the full additional `public-python` task block when the current surface shows one or more bounded `public-python` signals such as public package metadata in `pyproject.toml`, docs-site config, reachable public docs entrypoints, contributor or release docs exposed as public surfaces, or public examples and tutorial assets
- treat inclusion of the `public-python` task block as independent from any suggested current documentation-surface profile
- do not treat the presence or absence of `public-python` task rows in the provisional table as a final or implied profile classification
- if one of those bounded `public-python` signals is present, include every additional `public-python` editing task row even when some rows are `not needed` or `not yet evident`
- if none of those bounded `public-python` signals is present, omit the additional `public-python` block and state in the report that no current `public-python` surface signal was found

Thin public-package scaffold rules:

- distinguish between a thin public package scaffold and an active public-doc surface
- treat public package metadata, a basic `README.md`, and a small test surface by themselves as a thin public package scaffold rather than as evidence of a broad active public-doc surface
- for a thin public package scaffold, keep the `public-python` rows narrowly evidence-based and do not expand public-facing rows beyond the bounded public artifacts the current surface actually shows
- for a thin public package scaffold, default `public user documentation` to `preserve` when a bounded public-facing entry artifact such as a basic `README.md` already exists; otherwise mark it `not needed` with change scope `n/a`
- for a thin public package scaffold, default `public developer documentation`, `public contributor and release surface`, and `public examples and tutorial assets` to `not needed` with change scope `n/a` unless the current surface already contains a bounded artifact for that exact row
- do not treat a thin public package scaffold by itself as grounds for broad `develop` or `restructure` findings across multiple public-facing documentation rows
- for a thin public package scaffold, do not use `develop` for public-facing rows unless the current surface already contains a broader multi-artifact public-doc surface that is clearly incomplete rather than merely thin

`private-default` public-row rules:

- when the current documentation-surface signals point to `private-default`, treat the public-facing `public-python` rows for public user documentation, public developer documentation, public contributor and release surface, and public examples and tutorial assets as current-state rows only
- in that `private-default` case, default those four rows to `not needed` with change scope `n/a`
- in that `private-default` case, do not use `develop` for those four rows
- in that `private-default` case, do not use `restructure` for those four rows unless the current surface already contains a clearly segmented or multi-artifact public-facing surface for the specific row
- if no such bounded public-facing artifact exists for one of those four rows, mark the row as `not needed` and set the change-scope field to `n/a`
- if such a bounded public-facing artifact does exist while the overall current signals still point to `private-default`, prefer `preserve` for that row unless the current evidence clearly shows an already-public surface that is being regrouped rather than newly created or expanded

Row format:

- for each row, record:
  - editing task
  - applicability status: `evident`, `not needed`, or `not yet evident`
  - likely change scope: `preserve`, `restructure`, or `develop`
  - brief evidence from the current surface
- use `not needed` only when the current surface gives enough evidence that the task does not apply to this repo
- use `not yet evident` only when the current surface is too incomplete to determine whether the task applies
- if the applicability status is `not needed` or `not yet evident`, still keep the row and set the change-scope field to `n/a`

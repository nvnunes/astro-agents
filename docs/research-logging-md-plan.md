# Research-Logging Documentation Expansion Plan

## Overview

Rename `docs/research-log-validation.md` to `docs/research-logging.md` and
expand it into the complete human-facing description of the research-log
workflow and durable file contracts.

Maintain two independent surfaces:

- `docs/research-logging.md` describes the workflow completely for humans.
- `$research-logging` implements the workflow completely for agents.

Neither surface depends on the other at runtime. In particular, the skill and
its references must never link to, load, or instruct agents to consult
`docs/research-logging.md`. Alignment is verified by comparing the two
independent descriptions during maintenance.

## Phase Plan

### Phase 1 — Audit Coverage And Independence

- Map the current document against `skills/research-logging/SKILL.md`, its
  workflow references, and focused tests.
- Classify each behavior as already documented, missing, misplaced,
  duplicated, or intentionally agent-only.
- Identify missing human coverage:
  - log creation and minimum structure;
  - entry naming, continuation, splitting, and ownership;
  - entry section types and labels;
  - writing and preservation discipline;
  - summaries and follow-ups;
  - scripts, commands, `pyrun`, `data.csv`, and retained artifacts;
  - references and `refs.bib`;
  - AI-use disclosure;
  - recording, summarizing, reviewing, validating, and structural maintenance.
- Verify that no file under `skills/research-logging/` references either the
  old or new documentation path.
- Lock the independence rule before drafting:
  - the document must be complete for its human audience;
  - the skill must remain complete for agent execution;
  - neither may delegate required instructions to the other.
- Report contradictions found within the skill. Do not edit skill behavior in
  this documentation-only change.

### Phase 2 — Rename And Restructure The Human Guide

- Rename the working-tree document to `docs/research-logging.md`, preserving
  current validation content and uncommitted edits.
- Change the title and introduction to define the document as the human-facing
  workflow and contract reference.
- Organize it in this order:
  1. workflow at a glance;
  2. log structure;
  3. lifecycle operations;
  4. entries and section forms;
  5. supporting material and reproducibility;
  6. presented evidence;
  7. maintained summary;
  8. research-log review;
  9. agent-led validation;
  10. responsibilities and freshness.
- Move existing material to its strongest owner. Place `evidence.csv` with
  presented evidence rather than treating it primarily as validation output.
- Update `README.md`, `docs/architecture.md`, and `docs/usage.md` to point
  humans to the renamed guide.
- State in `docs/architecture.md` that the documentation and skill are
  parallel, independently complete surfaces whose behavioral alignment is
  maintained explicitly.
- Remove the old path without creating a redirect stub.

### Phase 3 — Add The Missing Human Workflow

- Explain the complete lifecycle: create, record, summarize, review, validate,
  and perform approved structural maintenance.
- Document human-relevant durable contracts:
  - directory and entry naming;
  - summary and entry structure;
  - label combinations and meanings;
  - `data.csv` and entry- and log-level `evidence.csv` schemas;
  - evidence locator syntax;
  - validation records and statuses;
  - ownership and staleness rules.
- Explain support-material ownership, script placement, recorded commands,
  `pyrun` behavior, external inputs, retained outputs, figure inspection, and
  serialized-artifact checks.
- Add compact canonical examples:
  - minimum and populated directory trees;
  - summary skeleton;
  - experimental, synthesis, and prose sections;
  - recorded command and indexed input;
  - evidence associations;
  - validation projection and results.
- Preserve the existing validation model while removing repetition introduced
  by the broader lifecycle sections.
- Exclude agent-internal execution procedures such as validator command
  pipelines, adjudication JSON, and decision-file mechanics.
- Do not make the human guide depend on links into the skill for missing
  details. It may name the skill as the agent implementation, but it must
  explain its own human-facing contracts completely.
- Keep scientific authority explicit: researchers own methods,
  interpretations, accepted synthesis, decisions, and research direction.

### Phase 4 — Verify Alignment Without Coupling

- Compare the completed document against the skill and focused tests using the
  Phase 1 coverage matrix.
- Verify agreement on file structures, schemas, labels, evidence forms,
  statuses, ownership, lifecycle transitions, and validation boundaries.
- Treat alignment as a maintenance check, not a runtime dependency or
  document-loading relationship.
- Require both independence conditions:
  - a researcher can understand the workflow without reading the skill;
  - an agent can execute the workflow without reading the documentation.
- Search `skills/research-logging/` for `docs/research-logging.md`,
  `docs/research-log-validation.md`, and equivalent instructions to consult
  project docs; require no matches.
- Search the full repository for the old filename and title; require no stale
  references.
- Run:
  - `python3 scripts/validate_agent_surface.py`;
  - `python3 scripts/validate_agent_surface.py --codex-discovery`;
  - `python3 skills/research-logging/tests/test_pyrun.py`;
  - `python3 -m unittest skills/research-logging/tests/test_research_log_validation.py`;
  - `git diff --check`.
- Finish with a documentation-surface review covering independence, source
  ownership, discoverability, duplication, scanability, and cross-link
  accuracy.

## Interface And Ownership Changes

- `docs/research-logging.md` becomes the independently complete human
  description.
- `$research-logging` remains the independently complete agent implementation.
- The skill must not reference, load, or delegate to the documentation.
- The documentation must not rely on skill references to complete its
  human-facing explanation.
- Alignment is checked externally whenever either surface changes.
- No research-log format or runtime behavior is intentionally changed.
- No skill files are edited in this work.

## Acceptance Criteria

- The new document covers the complete workflow from creation through
  maintenance and validation.
- Humans can use it without consulting the skill.
- Agents can continue using the skill without consulting the document.
- No skill file references either research-logging documentation path.
- The two surfaces agree on every shared behavior and durable contract.
- Agent-only execution mechanics are not duplicated into the human guide.
- Navigation uses the new path, the old path is absent, and all checks pass.
- Existing unrelated and in-progress changes in the dirty worktree remain
  intact.

## Assumptions And Deferred Decisions

- The implementation remains documentation-only unless a later task
  explicitly authorizes skill changes.
- A contradiction inside the skill is reported rather than resolved through
  an undocumented choice in the human guide.
- The old document has not existed in committed project history, so the rename
  does not require a compatibility stub.
- The detailed validator command and adjudication workflow remains agent-only.

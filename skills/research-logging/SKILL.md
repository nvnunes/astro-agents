---
name: research-logging
description: Perform and record investigations in project-native research logs by implementing and running scripts, retaining and analyzing outputs, documenting evidence and observations, safely replacing superseded experimental work, updating summaries, reviewing or validating research, managing references, reorganizing log files, and maintaining evidence and input registries. Use when research work should be performed or preserved in a research log, not for general project docs, exploratory or standalone analysis without research-log intent, or scientific manuscript writing.
---

# Research Logging

Use this skill for Record, Replace, Update Summary, Repair, Reorganize, Review,
and Validate.
Record performs and documents an investigation as one workflow and also starts
logs. Its production checks do not establish validation.

Research outside the log is adjacent work, not another operation. A log's
presence does not authorize Record. Leave it unchanged for exploratory work
without preservation intent; route later requests to retain, add, present,
cite, or use that work through Record.

## Choose The Operation

Choose the core operation:

- Starting a new log; investigating, implementing, running, analyzing, or
  recording research; continuing work; or working with support material,
  scripts, generated results, data organization, or command output: read
  `references/operation-record.md`.
- Explicitly replacing or removing a named experimental section and its owned
  material: read `references/operation-replace.md`.
- Researcher-requested or approved updates to current understanding or
  follow-ups in the log summary: read `references/operation-update-summary.md`.
- Research-log structure, consistency, synthesis, or writing review: read
  `references/operation-review.md`.
- Independent mechanical validation: read
  `references/operation-validate.md`. Validate is code-only and never becomes
  semantic review or reproduction. Those remain separate workflows.
- Explicit correction of a named research-owned finding, malformed or legacy
  state, transaction residue, or other identified log defect: read
  `references/operation-repair.md`. A failed authoring command or reported
  finding does not start Repair without a separate correction request.
- Researcher-requested reorganization, or a specific Reorganize recommendation
  that the researcher has approved: read
  `references/operation-reorganize.md`. Review may recommend Reorganize but
  cannot authorize it.

For standalone reference lookup, viewing, or candidate management, read
`references/operation-reference.md`. During Record, route citation and BibTeX
changes through `references/operation-record.md`.

## Authority Across Operations

Resolve the operation and authorized scope from the current researcher request
and durable workspace state. Do not let older conversation content expand
either. Ask before editing when ambiguity risks changing research meaning.

If the user's wording is ambiguous between recording and updating the summary,
prefer recording unless the user clearly asks to change the summary. If both
are requested, complete Record before Update Summary. If it is ambiguous
between review and editing, report findings first and ask before applying fixes
unless the requested correction is explicit; route an explicit correction of
an identified defect through Repair.

Do not infer Replace from a revision, rerun, correction, or reorganization
request. Use it only when the researcher explicitly intends superseded
experimental work to leave the active log.

Reorganize is separately authorized structural work. Review may recommend it
but cannot authorize it. A failed command, validation finding, or malformed
state does not start Repair without an explicit correction request.

## Universal Boundaries

- Do not decide what conclusion, interpretation, method choice, validation
  outcome, or next research direction the log should treat as accepted unless
  the user states that decision.
- Preserve existing researcher wording unless the user asks for rewriting.
- Do not invent evidence, validation, references, results, uncertainty,
  decisions, or conclusions.
- Do not use generative AI to create data, results, figures, or citations.
- Treat maintained summaries, entries, evidence records, scripts, artifacts,
  and authored prose as research-owned. Research operations never edit
  generated validation files; Validate reads research files and writes
  only the generated files defined in `references/file-validation-records.md`.
- If a maintained log command reports `operation.lock.conflict`, stop and
  retry after the conflicting operation completes; do not bypass its lock.
- Retained or logged results, figures, and tables should be produced by executable code that works with real data. The agent may help write, review, or debug that code, but the output should come from executing code.
- Do not invent data unless the user specifically asks for synthetic or draft data.

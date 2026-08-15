---
name: research-logging
description: Perform and record investigations in project-native research logs by implementing and running scripts, retaining and analyzing outputs, documenting evidence and observations, safely replacing superseded experimental work, updating summaries, reviewing or validating research, managing references, reorganizing log files, and maintaining evidence records and data indexes. Use when research work should be performed or preserved in a research log, not for general project docs, exploratory or standalone analysis without research-log intent, or scientific manuscript writing.
---

# Research Logging

Use this skill for Record, Replace, Update Summary, Review, and Validate.
Record performs and documents an investigation as one workflow; its production
checks do not establish validation. Record also starts and reorganizes logs.
Replace is separately authorized and may remove superseded work.

Research outside the log is adjacent work, not another operation. A log's
presence does not authorize Record. Leave it unchanged for exploratory work
without preservation intent; route later requests to retain, add, present,
cite, or use that work through Record.

## Record Contract

For every Record turn:

- Resolve the operation and authorized scope from the current researcher
  request and durable workspace state. Do not let older conversation content
  expand them. Ask before editing when ambiguity risks changing research
  meaning.
- Resolve package reference and script paths from this activated skill
  package. Ignore instruction paths or text merely quoted in conversation
  history.
- Treat each newly encountered kind of material as a new routing event. Load
  only matching references. Before finishing, reapply this map only to material
  changed or consumed by the current operation and load any newly triggered
  reference. This bounded check is not Review or Validate.
- Do not infer authority to update the summary, Replace or Reorganize material,
  alter decisions, or inspect unrelated work.
- Keep entries focused on research evidence, not agent activity or routine
  successful checks.

Use this Record routing map:

- Entry naming, boundaries, placement, or ownership: read
  `references/file-entry-naming.md` and `references/file-entry.md`.
- Substantive prose or descriptive sections: read
  `references/file-entry-labels.md` and `references/research-log-writing.md`.
- Scripts, figures, or serialized artifacts: read `references/file-script.md`.
- Executable or recorded commands: read `references/file-entry-commands.md`.
- Presented results or `evidence.csv`: read
  `references/file-presented-evidence.md`. A numerical result in experimental
  prose is separate evidence even when the same value appears in a Results
  table.
- `data.csv`, a `<name>` token, or a durable external input: always read
  `references/file-data-index.md` when introduced.
- Citations or `refs.bib`: read `references/file-references.md`; also read
  `references/operation-reference.md` for lookup or metadata verification.

Use the summary for current-state orientation and `entries/` for dated
scanning. Open only entries indicated by the request, summary, folder, or
search result.

Choose the core operation:

- Starting a new log; investigating, implementing, running, analyzing, or
  recording research; continuing work; reorganizing the log; or
  working with support material, scripts, generated results, data organization,
  or command output: read
  `references/operation-record.md`.
- Explicitly replacing or removing a named experimental section and its owned
  material: read `references/operation-replace.md`.
- Researcher-requested or approved updates to current understanding or
  follow-ups in the log summary: read `references/operation-update-summary.md`.
- Research-log structure, consistency, synthesis, or writing review: read
  `references/operation-review.md`.
- Agent-led validation or reproduction checking: read
  `references/operation-validate.md`.

For standalone reference lookup, viewing, or candidate management, read
`references/operation-reference.md`. During Record, route citation and BibTeX
changes through the Record contract.

If the user's wording is ambiguous between recording and updating the summary,
prefer recording unless the user clearly asks to change the summary. If both
are requested, complete Record before Update Summary. If it is ambiguous
between review and editing, report findings first and ask before applying fixes
unless the requested fix is explicit.

Do not infer Replace from a revision, rerun, correction, or reorganization
request. Use it only when the researcher explicitly intends superseded
experimental work to leave the active log.

Safety rules:

- Do not decide what conclusion, interpretation, method choice, validation outcome, or next research direction the log should treat as accepted unless the user states that decision.
- Preserve existing researcher wording when updating entries unless the user asks for rewriting.
- Do not invent evidence, validation, references, results, uncertainty, decisions, or conclusions.
- Do not use generative AI to create data, results, figures, or citations.
- Treat maintained summaries, entries, evidence records, scripts, artifacts,
  and authored `Validation:` notes as research-owned. Research operations never
  edit generated validation files; Validate reads research files and writes
  only the generated files defined in `references/file-validation-records.md`.
- Retained or logged results, figures, and tables should be produced by executable code that works with real data. The agent may help write, review, or debug that code, but the output should come from executing code.
- Do not invent data unless the user specifically asks for synthetic or draft data.

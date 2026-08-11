---
name: research-logging
description: Create, record, summarize, review, reference, or maintain research logs with log summaries, dated entries, evidence files, BibTeX references, data indexes, results, observations, validation, uncertainty, decisions, and summary-level AI use disclosures. Use for research-log lifecycle work, not general project docs or scientific manuscript writing.
---

# Research Logging

Use this skill for the full lifecycle of project-native research logs: recording entries and support material, managing references, updating summaries, reviewing consistency, validating evidence and reproducibility, and maintaining structure.

Use only the task-specific instruction files needed for the current edit. If the requested change is ambiguous and a reasonable assumption would risk changing research meaning, ask before editing.

Use this minimum core shape, where `<log>.md` is the log summary:

```text
<log>.md
<log>/
  entries/
```

When the log has entries, use:

```text
<log>/
  entries/
    <start-date>-<entry-id>-<descriptive-topic-slug>/
      <entry-id>.md
```

Use `<log>/refs.bib`, `<log>/scripts/`, and entry-local `data/`, `images/`, or `scripts/` only when they are immediately needed.

Use the log summary for current-state orientation and the `entries/` folder listing for chronological scanning. Open entry documents only when the summary, folder slug, search result, or user request indicates relevance.

Choose the operation:

- New research log setup: read `references/operation-create.md`.
- New or revised entry documents, support material, scripts, generated results, data organization, or command output: read `references/operation-record.md`.
- Reference lookup, BibTeX management, or citation insertion: read `references/operation-reference.md`.
- Log-summary updates: read `references/operation-summarize.md`.
- Research-log structure, consistency, synthesis, or writing review: read `references/operation-review.md`.
- Agent-led validation or reproduction checking: read `references/operation-validate.md`.
- Structural changes such as entry renames, link rewrites, splits, merges, or path normalization: use `references/file-entry-naming.md` and `references/file-entry.md` as needed; ask before deleting research-log files.

If the user's wording is ambiguous between recording and summarizing, prefer recording unless the user clearly asks to change the summary. If it is ambiguous between review and editing, report findings first and ask before applying fixes unless the requested fix is explicit.

Safety rules:

- Do not decide what conclusion, interpretation, method choice, validation outcome, or next research direction the log should treat as accepted unless the user states that decision.
- Preserve existing researcher wording when updating entries unless the user asks for rewriting.
- Do not invent evidence, validation, references, results, uncertainty, decisions, or conclusions.
- Do not use generative AI to create data, results, figures, or citations.
- Retained or logged results, figures, and tables should be produced by executable code that works with real data. The agent may help write, review, or debug that code, but the output should come from executing code.
- Do not invent data unless the user specifically asks for synthetic or draft data.

# Entry Naming

Use this file when choosing, reviewing, or maintaining entry folder names, entry IDs, or split-entry document names.

Entry folder names use:

```text
<start-date>-<entry-id>-<descriptive-topic-slug>
```

The `<descriptive-topic-slug>` is part of the retrieval surface. It should be short, concrete, and specific enough for an agent to scan the `entries/` directory and identify likely relevant entries without opening every file. It should describe the entry's topic or decision context in ordinary words, not a formal category or status label.

Examples:

- `entries/2026-05-01-e001-calibration-drift-check/`
- `entries/2026-05-03-e002-residual-pattern-validation/`
- `entries/2026-05-04-e003-method-choice-for-background-model/`

An `entry ID` is a stable log-local identifier for an entry. It identifies the entry across folder renames, summary links, and related-entry references.

Entry IDs use `e###` form, such as `e001`, `e002`, and `e003`. The `e` prefix means entry. The number increments within a single log and should not be reused after an entry is created.

When an entry is split across multiple documents, append lowercase letters to the entry ID for document names:

```text
e001a.md
e001b.md
```

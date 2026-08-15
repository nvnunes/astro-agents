# Summary Validation Navigation

Use this file when initializing or preserving the maintained summary's stable
link to its generated validation report.

Place this exact line immediately below the level-one title, followed by one
blank line:

```md
Validation: [latest completed report](<log>/validation.md)
```

The link contains no date, result, failure count, freshness claim, or rules
version. Do not add a `## Validation` section or a Validation item to
`## Contents`.

The link is research-document scaffolding. Record initialization installs it.
Record, Replace, Update Summary, and Reorganize preserve it exactly and do not
open, repair, delete, or normalize generated validation files. Validate reads
the summary but never changes it. Before the first validation, the link may
resolve to a report that does not yet exist.

`<log>/validation.md` owns the latest completed status, including its date,
Summary status, per-entry rows, failures, and reproducibility state. Correct
generated records through a later validation run, never through summary edits.

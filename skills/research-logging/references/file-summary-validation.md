# Summary Validation Navigation

Use this file when initializing or preserving the maintained summary's stable
link to its generated validation report.

Place this exact line immediately below the level-one title, followed by one
blank line:

```md
Validation: [latest completed report](<log>/validation.md)
```

The link contains no date, result, failure count, artifact-currentness claim,
or rules version. It is the summary's complete validation surface and has no
matching item in `## Contents`.

The link is research-document scaffolding. Record initialization installs it.
Record, Replace, Update Summary, Repair, and Reorganize preserve it exactly and
do not open, repair, delete, or normalize generated validation files. Validate
reads the summary but never changes it. Before the first validation, the link
may resolve to a report that does not yet exist.

`<log>/validation.md` is the human-only projection. Its Mechanical Validation
section shows the latest completed date and compact human area results; its
bounded Findings section groups affected targets by entry and human issue type.
Its independent Reproduction section shows `not_yet_run` until reproduction has
its own generated result. Agents do not parse this report for validation or
repair. The report has no combined conclusion. Correct generated records
through their owning operation, never through summary edits.

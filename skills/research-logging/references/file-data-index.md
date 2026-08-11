# Data Index File Instructions

Use this file when creating or revising an entry-local `data.csv`.

`data.csv` is optional. Use it to map command inputs or durable external
resources to short names that recorded commands resolve through `<name>`
tokens. It is a command-input index, not an artifact inventory or an act of
evidence presentation.

Keep `data.csv` at the entry root so it can be tracked separately from entry-local artifact folders such as `data/` and `images/`. Those folders may be normal directories, ignored directories, or symlinks according to the project using the log.

Use this minimal shape:

```csv
name,type,location
development_set,CSV,/data/project/development.csv
```

Columns:

- `name`: short stable identifier used by entry commands; unique within the file and not `project`, `log`, or `theme`.
- `type`: plain file or directory type, such as `CSV`, `NPZ`, `FITS`, `directory`, or `URL`.
- `location`: path, URL, object-store URI, or other durable reference. Relative paths resolve from the directory containing `data.csv`, normally the entry root; URI schemes remain unchanged.

Add new rows from the entry root instead of editing them manually:

```bash
./pyrun data add worker_summary_csv CSV data/summary.csv
```

Choose whether the resource belongs in the index and supply all three values.
`pyrun` creates the file and header when absent, validates an existing index,
and rejects duplicate names. It does not infer, copy, update, or validate the
referenced resource. Treat `data add` as index maintenance: record the command
that consumes the `<name>` token, not the mechanical addition command.

Keep `type` values plain, without Markdown. Add rows only for resources that a
recorded command resolves through a `<name>` token; do not use `data.csv` as an
artifact or script inventory.

A generated data output may be indexed only when a later recorded command
consumes it through its `<name>` token. The generating command still exposes
the entry-local output through a relative path value. Do not index an output
merely because it exists, is linked, or is presented.

Never index entry-local scripts or images. Keep their paths relative in
commands or Markdown. A durable external image used as command input may be
indexed as an external resource.

During research-log review, report duplicate names, unused rows, unresolved
tokens, inappropriate script or image rows, and raw absolute or external input
paths that should use `<name>`. A valid indexed resource used by an otherwise
orphaned workflow is a validation concern, not an index-hygiene finding.

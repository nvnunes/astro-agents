# Data Index File Instructions

Use this file when creating or revising an entry-local `data.csv`.

`data.csv` is optional. Use it when commands in an entry need short names for data files, image files, or directories. It maps those names to real paths so commands can stay readable. The entry documents should explain why the indexed artifact matters and how it was used.

Keep `data.csv` at the entry root so it can be tracked separately from entry-local artifact folders such as `data/` and `images/`. Those folders may be normal directories, ignored directories, or symlinks according to the project using the log.

Use this minimal shape:

```csv
name,type,location
worker_summary_csv,CSV,data/summary.csv
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

Do not index scripts or images embedded with Markdown. Index an image only when
a recorded command resolves it through a `<name>` token.

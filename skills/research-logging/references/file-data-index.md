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

- `name`: short stable identifier used by entry commands.
- `type`: plain file or directory type, such as `CSV`, `NPZ`, `FITS`, `directory`, or `URL`.
- `location`: path, URL, object-store URI, or other durable reference. Relative paths resolve from the directory containing `data.csv`, normally the entry root.

Do not list every image in `data.csv` by default. Markdown image links such as `![Comparison plot](images/comparison.png)` are the normal reference surface for retained visual evidence. Add image rows only when commands need tokenized access or explicit artifact inventory is useful.

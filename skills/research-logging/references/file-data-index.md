# Data Index File Instructions

Use this file when creating or revising an entry-local `data/index.csv`.

`data/index.csv` is optional. Use it when commands in an entry need short names for data files or directories. It maps those names to real paths so commands can stay readable. The entry documents should explain why the data matters and how it was used.

Use this minimal shape:

```csv
name,type,location
worker_summary_csv,CSV,/path/to/summary.csv
```

Columns:

- `name`: short stable identifier used by entry commands.
- `type`: plain file or directory type, such as `CSV`, `NPZ`, `FITS`, `directory`, or `URL`.
- `location`: path, URL, object-store URI, or other durable reference.

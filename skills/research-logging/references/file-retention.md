# Disconnected Retention Instructions

Use this file only when the researcher intentionally keeps entry-owned files
outside the evidence-rooted command graph. Retention prevents those selected
files from being classified as accidental orphans. It does not create
evidence, input, command, producer, lineage, or dependency relationships.

Choose one stable descriptive ID, the exact entry-relative targets, and an
optional concise reason that records the retention intent. Use either one
nonempty directory or one or more regular files; do not mix the two forms or
use symlinks, missing targets, overlapping records, or paths outside the entry.

Resolve `<skill>/scripts/log` from this skill package, read only the
selected action's help, and invoke:

```text
<skill>/scripts/log retention add --path <log> --entry <entry-id> \
  --id <id> [--reason <reason>] <target> [<target> ...]
```

`<log>` is the logical base whose summary is `<log>.md`; do not pass the
summary file. The CLI infers exact-file or all-descendant directory coverage,
validates the declaration, and owns the retention registry. Never create,
inspect, or edit that registry during ordinary Record.

Use `log retention update` to replace one selected retention decision,
`rename` to preserve it under a new ID, `remove` when the material no longer
needs disconnected retention, and `list` for a bounded semantic inventory.
Every mutation accepts `--dry-run`.

Do not use retention to conceal missing metadata or Provenance. Remove a
retention declaration when its target enters the evidence-rooted graph. If an
action fails because existing research-owned state is malformed or legacy,
report the exact failure and stop; do not start Repair or edit around the
command without a separate correction request.

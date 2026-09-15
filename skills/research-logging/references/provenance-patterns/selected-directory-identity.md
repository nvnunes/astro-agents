# Selected Identity For Very Large Directories

Use a complete directory fingerprint by default. Select 1–64 exact files or
final-component patterns only when they explicitly define a very large origin
or generated directory's relevant identity. Excluded descendants are not
covered.

Research commands:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

```bash
./pyrun scripts/build_table.py \
  --input-summary "<simulation-results>/summary.csv" \
  --output-table "<results-table>"
```

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid run_simulation \
  --add-origin simulation-config=configs/simulation.yaml \
  --add-generated-directory simulation-results=data/simulation-results
"$LOG_TOOL" data update --path "$LOG" --entry e001 simulation-results \
  --identity file:summary.csv --identity file:metadata.json
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid build_table \
  --add-generated results-table=tables/results.tex
```

The member token says what the command reads; `--identity` says what defines
the directory fingerprint. Include every consumed file whose bytes matter, or
producer-owned identity files that reliably change with the relevant state.

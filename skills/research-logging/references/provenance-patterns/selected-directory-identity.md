# Selected Identity For Very Large Directories

Use a complete directory fingerprint by default. Select 1–64 exact files or
final-component patterns only when they explicitly define a very large origin
or generated directory's relevant identity. Excluded descendants are not
covered.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  simulation-config configs/simulation.yaml
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  simulation-results data/simulation-results --kind directory \
  --identity summary.csv --identity metadata.json
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  results-table tables/results.tex --kind file
```

Research commands:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
./pyrun scripts/build_table.py \
  --input-summary "<simulation-results>/summary.csv" \
  --output-table "<results-table>"
```

The member token says what the command reads; `--identity` says what defines
the directory fingerprint. Include every consumed file whose bytes matter, or
producer-owned identity files that reliably change with the relevant state.

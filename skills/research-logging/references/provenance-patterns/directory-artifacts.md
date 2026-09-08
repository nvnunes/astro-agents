# Directory Inputs And Outputs

Use one artifact name and one producer for a directory bundle. Consume the
whole directory as `<name>` or one exact member as `<name>/member`. Independent
producers must not write inside the same owned directory.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  simulation-config configs/simulation.yaml
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  simulation-results data/simulation-results --kind directory
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  tabulated-results data/tabulated-results --kind directory
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  results-table tables/results.tex --kind file
```

Research commands:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
./pyrun scripts/tabulate_results.py \
  --input-results-root "<simulation-results>" \
  --output-tables-root "<tabulated-results>"
./pyrun scripts/build_table.py \
  --input-summary "<tabulated-results>/summary.csv" \
  --output-table "<results-table>"
```

Exact-member consumption does not split the directory producer boundary.

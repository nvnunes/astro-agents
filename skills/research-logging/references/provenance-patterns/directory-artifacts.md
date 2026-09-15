# Directory Inputs And Outputs

Use one artifact name and one producer for a directory bundle. Consume the
whole directory as `<name>` or one exact member as `<name>/member`. Independent
producers must not write inside the same owned directory.

Research commands:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

```bash
./pyrun scripts/tabulate_results.py \
  --input-results-root "<simulation-results>" \
  --output-tables-root "<tabulated-results>"
```

```bash
./pyrun scripts/build_table.py \
  --input-summary "<tabulated-results>/summary.csv" \
  --output-table "<results-table>"
```

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid run_simulation \
  --add-origin simulation-config=configs/simulation.yaml \
  --add-generated-directory simulation-results=data/simulation-results
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid tabulate_results \
  --add-generated-directory tabulated-results=data/tabulated-results
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid build_table \
  --add-generated results-table=tables/results.tex
```

Exact-member consumption does not split the directory producer boundary.

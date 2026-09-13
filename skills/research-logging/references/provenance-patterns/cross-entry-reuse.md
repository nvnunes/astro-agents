# Cross-Entry Reuse

Use `data use` when a later entry consumes a generated artifact declared by an
earlier entry in the same log. The reference retains the producer's name and
identity; it does not copy data, search entries, or create an origin.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  simulation-config configs/simulation.yaml
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  simulation-results data/simulation-results --kind directory
"$LOG_TOOL" data use --path "$LOG" --entry e002 \
  --from-entry e001 simulation-results
"$LOG_TOOL" data add-generated --path "$LOG" --entry e002 \
  followup-summary data/followup-summary.csv --kind file
```

Research command from `e001`:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

Research command from `e002`:

```bash
./pyrun scripts/analyze_results.py \
  --input-results-root "<simulation-results>" \
  --output-summary "<followup-summary>"
```

The consumer must not already declare the same name. Remove every dependent
reference before renaming or removing the source. Cross-log inputs use an
origin boundary instead.

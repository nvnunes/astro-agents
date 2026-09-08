# Large External-Simulation Origin

Use an origin for an existing external simulation dataset when no command in
this log produced it. Registration does not copy the dataset, and reproduction
starts at that verified boundary instead of rerunning the external job.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  external-results /datasets/simulations/run-042 \
  --identity summary.csv --identity metadata.json
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  results-table tables/results.tex --kind file
```

Research command:

```bash
./pyrun scripts/build_table.py \
  --input-summary "<external-results>/summary.csv" \
  --output-table "<results-table>"
```

The directory must remain locally accessible. Large size or long runtime does
not itself make an artifact an origin; the selected provenance boundary does.
Omit `--identity` when the complete directory defines the input.

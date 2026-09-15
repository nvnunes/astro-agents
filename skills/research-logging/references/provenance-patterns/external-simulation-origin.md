# Large External-Simulation Origin

Use an origin for an existing external simulation dataset when no command in
this log produced it. Registration does not copy the dataset, and reproduction
starts at that verified boundary instead of rerunning the external job.

Research command:

```bash
./pyrun scripts/build_table.py \
  --input-summary "<external-results>/summary.csv" \
  --output-table "<results-table>"
```

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid build_table \
  --add-origin-directory external-results=/datasets/simulations/run-042 \
  --add-generated results-table=tables/results.tex
"$LOG_TOOL" data update --path "$LOG" --entry e001 external-results \
  --identity file:summary.csv --identity file:metadata.json
```

The directory must remain locally accessible. Large size or long runtime does
not itself make an artifact an origin; the selected provenance boundary does.
Omit the data update when the complete directory defines the input.

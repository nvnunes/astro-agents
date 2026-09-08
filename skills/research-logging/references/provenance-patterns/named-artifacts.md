# Named Inputs And Outputs

Use this default whenever recorded commands consume or produce artifacts.
Define each name and path once, declare generated outputs before production,
and reuse the names in producers, consumers, and evidence sources. When several
producers feed one command, declare every input.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  measurements data/measurements.csv
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  summary data/summary.csv --kind file
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  summary-plot figures/summary.png --kind file
```

Research commands:

```bash
./pyrun scripts/summarize_measurements.py \
  --input-measurements "<measurements>" --output-summary "<summary>"
./pyrun scripts/plot_summary.py \
  --input-summary "<summary>" --output-figure "<summary-plot>"
```

A raw output path in a recorded command is a Structure finding. Legacy state
remains readable for diagnosis and repair but is not steady-state guidance.

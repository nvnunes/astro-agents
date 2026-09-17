# Named Inputs And Outputs

Use this default whenever recorded commands consume or produce artifacts.
Define each name and path once, declare generated outputs with their producer's sync before production,
and reuse the names in producers, consumers, and evidence sources. When several
producers feed one command, declare every input.

Research commands:

```bash
./pyrun scripts/summarize_measurements.py \
  --input-measurements "<measurements>" --output-summary "<summary>"
```

```bash
./pyrun scripts/plot_summary.py \
  --input-summary "<summary>" --output-figure "<summary-plot>"
```

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid summarize_measurements \
  --add-origin measurements=data/measurements.csv --add-generated summary=data/summary.csv
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid plot_summary \
  --add-generated summary-plot=figures/summary.png
```

A raw output path in a recorded command is a Conformance finding. Current state
uses CID-scoped v7 records; earlier schemas are unsupported.

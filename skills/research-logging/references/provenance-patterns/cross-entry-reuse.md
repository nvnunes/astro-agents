# Cross-Entry Reuse

Use its sync's `--add-from-entry NAME=ENTRY` when a later entry consumes a generated artifact declared by an
earlier entry in the same log. The reference retains the producer's name and
identity; it does not copy data, search entries, or create an origin.

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid run_simulation \
  --add-origin simulation-config=configs/simulation.yaml \
  --add-generated-directory simulation-results=data/simulation-results
"$LOG_TOOL" command sync --path "$LOG" --entry e002 --cid analyze_results \
  --add-from-entry simulation-results=e001 \
  --add-generated followup-summary=data/followup-summary.csv
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

An existing equivalent reference is accepted. Update every dependent Markdown
use before `log data rename`; remove uses and sync owners before deletion. Cross-log inputs use an
origin boundary instead.

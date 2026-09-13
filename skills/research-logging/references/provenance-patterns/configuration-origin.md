# Configuration Outside `data/`

Use this pattern for researcher-authored configuration retained with the
experimental setup, such as `configs/simulation.yaml`. It is an origin because
no recorded command produced it, not because it lives outside `data/`.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  simulation-config configs/simulation.yaml
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  simulation-results data/simulation-results --kind directory
```

Research command:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

Use a generated declaration instead when a recorded command creates the
configuration.

# Configuration Outside `data/`

Use this pattern for researcher-authored configuration retained with the
experimental setup, such as `configs/simulation.yaml`. It is an origin because
no recorded command produced it, not because it lives outside `data/`.

Research command:

```bash
./pyrun scripts/run_simulation.py \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid run_simulation \
  --add-origin simulation-config=configs/simulation.yaml \
  --add-generated-directory simulation-results=data/simulation-results
```

Use a generated declaration instead when a recorded command creates the
configuration.

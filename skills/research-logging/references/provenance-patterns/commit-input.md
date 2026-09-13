# Commit Reference As Input

Use a pinned repository input when a command depends on an exact committed
source snapshot. Pass both its repository locator and paired commit token; a
prose link or live checkout does not declare the dependency.

Required tooling:

```bash
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  model-source /path/to/model-repository --commit "$MODEL_COMMIT"
"$LOG_TOOL" data add-origin --path "$LOG" --entry e001 \
  simulation-config configs/simulation.yaml
"$LOG_TOOL" data add-generated --path "$LOG" --entry e001 \
  simulation-results data/simulation-results --kind directory
```

Research command:

```bash
./pyrun scripts/run_model_revision.py \
  --input-repository "<model-source>" \
  --source-commit "<model-source:commit>" \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

The commit covers tracked content only. Declare consumed dirty files,
environments, generated models, caches, or submodule checkouts separately.

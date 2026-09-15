# Commit Reference As Input

Use a pinned repository input when a command depends on an exact committed
source snapshot. Pass both its repository locator and paired commit token; a
prose link or live checkout does not declare the dependency.

Research command:

```bash
./pyrun scripts/run_model_revision.py \
  --input-repository "<model-source>" \
  --source-commit "<model-source:commit>" \
  --input-config "<simulation-config>" \
  --output-results-root "<simulation-results>"
```

Tooling after authoring each Markdown command, before its execution:

```bash
"$LOG_TOOL" command sync --path "$LOG" --entry e001 --cid run_model_revision \
  --add-origin-git "model-source=$MODEL_COMMIT:/path/to/model-repository" \
  --add-origin simulation-config=configs/simulation.yaml \
  --add-generated-directory simulation-results=data/simulation-results
```

The commit covers tracked content only. Declare consumed dirty files,
environments, generated models, caches, or submodule checkouts separately.

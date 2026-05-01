# Reuse

Use this reference when one pubify publication or presentation reuses generated figures, stats, or tables from another pubify publication or presentation.

## Declaring Sources

Declare `sources` in the local `pub.yaml` or `ppt.yaml`. Absolute paths are allowed. Relative paths are resolved from the workspace root that contains `pubify.yaml`.

```yaml
sources:
  related_publication: papers/related-publication
```

The key under `sources` is the source ID used in `figures.py`. The path points to the source publication or presentation.

## Using A Source

Use `ctx.source("<source_id>")` inside the current publication or presentation's `figures.py` to read generated outputs from a declared source.

A source has `.figure(...)`, `.stat(...)`, and `.table(...)` methods. The figure, stat, or table ID is the ID defined in the source publication or presentation's `figures.py`.

```python
@figure
def plot_summary(ctx):
    panel = ctx.source("related_publication").figure("summary").panel(1)
    return FigureResult(panel)

@stat
def compute_summary_count(ctx):
    source_stat = ctx.source("related_publication").stat("summary_count")
    return StatResult(source_stat.values[0].value)

@table
def tabulate_summary(ctx):
    source_table = ctx.source("related_publication").table("summary_table")
    return TableResult(source_table.bodies[0])
```

Use local wrapper functions so the current publication or presentation has its own local IDs. Manuscript TeX and PowerPoint anchors should reference only those local wrapper IDs. Do not put source names directly in TeX, PowerPoint tokens, or Alt Text.

## Adapting Reused Outputs

Use wrappers to rename outputs, select panels from a reused figure, change the figure for the current publication or presentation, adapt table shape, or set target-specific figure return options.

For figures, `.panel(1)` selects the first source panel, `.panel(2)` selects the second source panel, and so on. Wrap the selected panel in the result type for the current publication or presentation.

Do not modify the source publication or presentation just to adapt output for the current one. Adapt the reused output in the local wrapper unless the user explicitly wants to change the source.

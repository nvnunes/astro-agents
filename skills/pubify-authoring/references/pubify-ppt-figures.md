# Pubify PPT Figures

Use this reference for `pubify-ppt` figure return values, `panel(...)`, PowerPoint anchors, and presentation figure export options.

## Figure Returns, Panels, And Anchors

A presentation plot function may return a Matplotlib `Figure`, `Axes`, or a sequence of figures or axes. Use `pubify_ppt.FigureResult` when the figure needs export options such as export padding or style overrides:

```python
from pubify_ppt import FigureResult, panel

return FigureResult(fig, metadata={"export_pad_inches": 0.02})
return FigureResult(
    [
        panel(fig1, export_pad_left_inches=0.04),
        panel(fig2),
    ],
)
```

The PowerPoint anchor controls the exported image size. Resize the placeholder or generated picture in PowerPoint, then rerun the update. Use `ppt.yaml` defaults when generated figure fonts do not match the deck.

A panel is one exported part of a figure result. Each panel should have its own PowerPoint figure anchor. Panel numbers in PowerPoint anchors are one-based, for example `{{fig:comparison:1}}` and `{{fig:comparison:2}}`.

Use `panel(...)` only when one panel needs its own export options. Prefer figure-level `metadata` when every panel should share the same setting. See the API Reference below for supported options.

Use export padding options when labels, spines, titles, or tick marks land too close to image edges. Padding is measured in inches, must be non-negative, and is applied before PNG render. It is not blank pixels added after rendering.

For figures produced by external helpers, make presentation-specific changes inside the `plot_` function or use `prepare_export` when the change should happen only immediately before export. See `figure-export.md` for more detail.

For figure-specific export cleanup, attach `prepare_export` through `FigureResult(..., metadata={...})`:

```python
return FigureResult(fig, metadata={"prepare_export": _prepare_export})
```

When plotting code creates text and the exported figure needs presentation typography, use `ctx.rc`:

```python
@figure
def plot_example(ctx):
    with ctx.rc:
        fig = build_plot()
    return fig
```

## API Reference

### FigureResult

`pubify_ppt.FigureResult(...)` accepts:

| Option | Use |
| --- | --- |
| `panels_or_panel` | Matplotlib `Figure`, `Axes`, sequence of figures or axes, `panel(...)`, or sequence of `panel(...)` values. |
| `layout` | Accepted by the result object, but PowerPoint export size is controlled by deck anchors, not LaTeX layout names. |
| `metadata` | Presentation figure export options applied to every panel unless panel options override them. |

### Panel

`pubify_ppt.panel(...)` accepts:

| Option | Use |
| --- | --- |
| first argument | Panel figure or axes. |
| presentation figure export options | Any option from the Presentation Figure Export Options tables below; applies to that panel only. |

### Presentation Figure Export Options

These options may be used in `FigureResult(..., metadata={...})` or as `panel(...)` keyword options.

Use `extra_rcparams` in figure export options when one figure or panel needs Matplotlib rcParams beyond the presentation defaults.

#### Sizing And Rendering

Use these when the generated PNG needs explicit render control.

| Option | Use |
| --- | --- |
| `dpi` | Render DPI; must be a positive integer. |
| `style` | Matplotlib style overrides for the exported figure. |

#### Export Padding

Use these when the generated PNG needs more space around the prepared figure inside the PowerPoint anchor.

| Option | Use |
| --- | --- |
| `export_pad_inches` | Symmetric export padding in inches. |
| `export_pad_bottom_inches` | Bottom export padding in inches. |
| `export_pad_left_inches` | Left export padding in inches. |
| `export_pad_right_inches` | Right export padding in inches. |
| `export_pad_top_inches` | Top export padding in inches. |

#### Visibility And Style

Use these when the source figure is right, but the prepared export needs precise final adjustment or presentation-specific style handling.

| Option | Use |
| --- | --- |
| `prepare_export` | Callback that mutates the prepared export figure immediately before save. |
| `extra_rcparams` | Extra Matplotlib rcParams for export. |
| `hide_annotations` | Hide annotations during export. |
| `hide_cbar` | Hide colorbars during export. |
| `hide_grid` | Hide grid lines during export. |
| `hide_labels` | Hide axis labels during export. |
| `hide_tick_labels` | Hide tick labels while keeping ticks. |
| `hide_ticks` | Hide ticks and tick labels during export. |
| `keep_titles` | Preserve Matplotlib titles during export. |

#### Figure Cloning

Use this only when export mechanics need tuning.

| Option | Use |
| --- | --- |
| `skip_clone` | Export without cloning the Matplotlib figure; use only when cloning fails or is inappropriate. |

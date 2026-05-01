# Figure Export

Use this reference when working on export-time mutation or shared Matplotlib export behavior in a `pubify-pubs` publication or `pubify-ppt` presentation.

## Role

A plot function in `figures.py` builds the source figure. During export, pubify prepares Matplotlib figures for the target publication or presentation, then writes a PDF for TeX or a PNG for PowerPoint.

Export behavior can come from both code and local workflow configuration. Use `figures.py` for figure-specific logic. Use `pub.yaml` or `ppt.yaml` for publication- or presentation-level export settings; their supported keys are described in `pubify-pubs.md` and `pubify-ppt.md`.

## Changing The Source Figure

When a figure comes from an external helper, prefer ordinary Matplotlib changes in the plot function before returning the figure. Do this when the figure itself should change before pubify prepares it for export.

For example:

```python
@figure
def plot_example(ctx, sample):
    fig = external_helper.build_plot(sample)

    for ax in fig.axes:
        ax.set_xlabel("Radius")
        ax.set_ylabel("Throughput")

    return fig
```

## Construction-Time rcParams

Use `with ctx.rc:` when an external helper creates text, legends, colorbars, or layout-sensitive artists while building the figure and the output suggests Matplotlib used plain defaults too early. This lets Matplotlib compute text sizes and spacing using pubify's construction-time rcParams instead. This is not usually needed.

```python
@figure
def plot_example(ctx, sample):
    with ctx.rc:
        fig = external_helper.build_plot(sample)

    return fig
```

## Export-Time Mutation

Use export-time mutation when the final exported figure needs precise cleanup after pubify has prepared the source figure. Use it for changes that pubify does not already expose as export options, such as removing one specific label, legend, annotation, or title from one exported output.

Put that cleanup in a local `prepare_export` callback and attach it through the figure result option for the active publication or presentation. Keep the callback near the figure that uses it.

Most callbacks only need the prepared Matplotlib figure. The resolved export style is the final pubify figure style after `pub.yaml` or `ppt.yaml` settings and figure or panel overrides have been applied, including values such as font sizes and line widths. If the callback needs those values, define it as `_prepare_export(fig, style)`.

For example:

```python
@figure
def plot_example(ctx, sample):
    fig = build_plot(sample)

    def _prepare_export(fig):
        for ax in fig.axes:
            for text in list(ax.texts):
                if text.get_text() == "Busy label":
                    text.remove()

    return FigureResult(fig, metadata={"prepare_export": _prepare_export})
```

## Figure Cloning

Pubify clones Matplotlib figures by default so export preparation can change the exported copy without mutating the original figure built by `figures.py`.

Some figures with WCS axes, sky projections, custom artists, or other objects that Matplotlib cannot copy cleanly may fail during export. Use `skip_clone` only when the figure genuinely needs it.

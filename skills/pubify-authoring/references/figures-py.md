# Figures.py

Use this reference when changing `figures.py` in a `pubify-pubs` publication or a `pubify-ppt` presentation. For data-file locations and loader rules, use `data-files.md`. For source reuse, use `reuse.md`. For export-only cleanup, use `figure-export.md`. For target-specific figure return values, use `pubify-pubs-figures.md` or `pubify-ppt-figures.md`.

## Role

`figures.py` is where the publication or presentation defines the Python work that produces generated figures, stats, and tables. If generated output is wrong, fix `figures.py` or the helper code it calls, then rerun the pubify CLI.

Keep publication- or presentation-specific analysis here unless the project already has an existing helper module for that work. Do not invent new helper packages or new project structure unless the user asks for that.

Keep long or repeated code in plain helper functions so the functions with pubify decorators stay easy to scan.

## IDs And Function Names

Function names determine the IDs used by TeX files and PowerPoint anchors:

```python
@data(...)
def load_<loader_id>(ctx, ...): ...

@figure
def plot_<figure_id>(ctx, ...): ...

@stat
def compute_<stat_id>(ctx, ...): ...

@table
def tabulate_<table_id>(ctx, ...): ...
```

Keep IDs stable once manuscript TeX, PowerPoint anchors, stat tokens, table anchors, or generated artifact paths use them.

Use meaningful local IDs. Prefer IDs that describe the generated output in the current publication or presentation, not where the data came from.

## Organization

Use loaders for data files and computed inputs. Use figure functions for generated figures, stat functions for computed values used in TeX macros or presentation tokens, and table functions for generated tables.

For publications, order loaders, figures, stats, and tables so the file is easy to trace from the manuscript back to the data. Manuscript-first order is often clearest.

For presentations, order the file so generated outputs are easy to trace from the deck back to the data. Slide order is often clearest.

Prefer small local helpers near the functions that use them. Move code out of `figures.py` only when there is already an appropriate project-local helper or the user asks to create one.

## Loader Use

Use `data-files.md` for data-file ownership, path rules, and loader return rules. In `figures.py`, make it clear which loaded data each figure, stat, or table uses.

The first parameter, `ctx`, is supplied by pubify when it runs loaders, figures, stats, and tables. Most loaders do not need to use it. Figure, stat, and table functions receive loader outputs by naming loader IDs in their parameters:

```python
@data("sample.npz")
def load_sample(ctx, path):
    return np.load(path)

@figure
def plot_summary(ctx, sample):
    ...

@stat
def compute_sample_count(ctx, sample):
    ...
```

Centralize parsing, validation, and compatibility handling in loaders or helpers instead of repeating it in each output function.

# Data Files

Use this reference for data-file ownership, pinned inputs, external data, data from another pubify publication or presentation, and data symlinks in `pubify-pubs` and `pubify-ppt`.

## Data Ownership

Prefer publication- or presentation-local data under the local `data/` path for reproducible figures, stats, and tables. From inside the publication or presentation, treat that `data/` path as the canonical data root even when it is a symlink to data stored somewhere else.

The goal is preservation: regenerated figures, stats, and tables should be traceable back to files owned by the publication or presentation.

The user may direct data to live outside the publication or presentation subtree by making the local `data/` path a symlink. The agent can help create, inspect, or repair that symlink when asked, but should keep code and configuration referring to the local `data/` path unless the publication or presentation intentionally uses an external root.

Do not scatter ad hoc absolute paths through `figures.py` or helper code. Use publication- or presentation-local data, declared external data, or a local `data/` symlink.

## Pinned Data

In `figures.py`, use the data loader decorator for pinned publication- or presentation-local inputs:

```python
@data("sample.npz")
def load_sample(ctx, path):
    ...
```

The first parameter is always `ctx`, the pubify runtime context for the current update. It is currently required by the loader signature but not used by loaders.

Data loader paths are relative to the local `data/` folder. Do not use absolute paths or `..` segments in decorator paths.

For multiple pinned files, use named paths instead of a single path. The loader parameters after `ctx` must match those names:

```python
@data(model="bundle/model.pt", meta="bundle/meta.json")
def load_bundle(ctx, model, meta):
    ...
```

## External Data

Use external data for inputs that should stay outside the local `data/` folder. Common examples are large data that makes keeping a local copy impractical, or data already pinned in a related pubify publication or presentation.

Declare external data roots in `pub.yaml` or `ppt.yaml`. Absolute paths are allowed. Relative paths are resolved from the workspace root that contains `pubify.yaml`.

```yaml
external_data_roots:
  shared_results: /Volumes/Data/results/large-run
  related_publication_data: papers/related-publication/data
```

Then use the external-data loader decorator in `figures.py`:

```python
@external_data("related_publication_data", "training.npy")
def load_training(ctx, path):
    ...
```

For multiple files under the same external root, use named paths:

```python
@external_data("shared_results", model="bundle/model.pt", meta="bundle/meta.json")
def load_bundle(ctx, model, meta):
    ...
```

External-data loader paths are relative to the named external root. Do not use absolute paths or `..` segments in decorator paths.

For multiple external files, use named paths instead of a single path.

Keep external data roots named and intentional. If a task needs data from somewhere else and no root is declared, ask whether to declare an external data root, pin the data locally, or use an existing project helper.

External data stays owned by the source location. If the publication or presentation needs a fixed copy, put that copy under its local `data/` path and use the data loader decorator instead.

## Loader Inputs And Outputs

Loader functions receive resolved file paths and return one Python object for the rest of `figures.py` to use. Return semantic values such as arrays, tables, records, metadata dictionaries, or small domain objects rather than raw path strings unless the downstream function truly needs the path. Figure, stat, and table functions are responsible for knowing how to use the objects returned by their loaders.

Pubify passes loader return values into figure, stat, and table functions by matching parameter names. A loader named `load_sample` creates the loader ID `sample`; a downstream function can request that value with a `sample` parameter. This pattern lets pubify call a loader once and reuse its returned value across multiple figures, stats, or tables during an update:

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

@table
def tabulate_sample_metrics(ctx, sample):
    ...
```

Use loaders to centralize file parsing, validation, and compatibility handling. Keep figure, stat, and table functions focused on producing publication or presentation outputs from already-loaded data.

Loaders must not return `None` or a tuple. If a loader needs to return multiple values, wrap them in a dict, dataclass, or other single container object.

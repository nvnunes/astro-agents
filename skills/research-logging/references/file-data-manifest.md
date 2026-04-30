# Data Manifest File Instructions

Use this file when creating or revising an entry-local `data/manifest.md`.

Use `data/manifest.md` when an entry depends on external, large, canonical, or move-prone data assets. Do not require a manifest for small files copied directly into the entry's `data/` folder.

## Shape

Use this structure:

```md
# Data Manifest

| Name | Type | Location | Version / Commit | Notes |
|---|---|---|---|---|
| `worker-summary-csv` | CSV | `/path/to/summary.csv` | `2026-04-19` | Runtime summary used for worker trade study. |
```

## Columns

- `Name`: short stable identifier used by the entry.
- `Type`: file or asset type.
- `Location`: path, URL, object store URI, or other durable reference.
- `Version / Commit`: version, commit, checksum, date, or other identity marker.
- `Notes`: why the asset matters or how it was used.

## Entry Use And Commands

In `index.md`, use manifest names such as `worker-summary-csv` rather than repeating full paths. Python commands should use `pyrun` angle tokens for manifest-backed assets:

```bash
./pyrun analyze.py --input "<worker-summary-csv>"
```

If the real asset location changes, update the manifest rather than rewriting the entry narrative.

On first use in an entry, create an entry-root symlink named `pyrun` that points to `skills/research-logging/scripts/pyrun`. Prefer a relative symlink when practical.

`pyrun` resolves tokens as follows:

- `<project>` resolves to the project root.
- `<project>/...` resolves to a path under the project root.
- `<theme>` resolves to the theme folder containing `index.md` and `entries/`.
- `<theme>/...` resolves to a path under that theme folder.
- `<Name>` resolves to the matching `Location` in the nearest data manifest.

Tokens may appear as a whole argument or inside an argument, such as `static=<scheduler-series>/file.npz`. Quote arguments that contain angle tokens.

The resolver searches for a manifest in this order:

- `./manifest.md`
- `./data/manifest.md`
- `../data/manifest.md`

The manifest remains the source of truth for asset identity and location. The entry-root `pyrun` symlink is only a command convenience.

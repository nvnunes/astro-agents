# Entry Command Instructions

Use this file when an entry needs executable commands to reproduce, regenerate, or explain retained evidence, outputs, figures, tables, or checks.

Include commands only when they help later reconstruction. Do not add command blocks for routine navigation, exploratory dead ends, or commands whose effects are not retained in the entry.

Ad hoc exploratory plots or tables that are not recorded in an entry may be made directly. If a plot, table, figure, or derived result is saved into an entry or cited by an entry, prefer writing an entry script and recording the command that regenerates it unless the user explicitly asks for a one-off output.

Write commands from the entry root as the working directory. Ordinary shell commands can be written directly.

For Python commands, use `./pyrun` to simplify recorded syntax. `pyrun` replaces tokens with full paths and recognizes:

- `<project>` resolves to the project root.
- `<project>/...` resolves to a path under the project root.
- `<log>` resolves to the research-log folder.
- `<log>/...` resolves to a path under the research-log folder.
- `<name>` resolves to the matching `location` in the nearest entry-local `data/index.csv`.

Tokens may appear as a whole argument or inside an argument, such as `static=<calibration_series>/file.npz`. Quote arguments that contain angle tokens.

Example:

```bash
./pyrun scripts/plot_residuals.py --input "<calibration_csv>" --out images/residuals.png
```

When writing entry scripts, pass input and output paths as command-line arguments. Do not hard-code project, log, entry, data, image, or output paths in scripts. Do not make ordinary analysis scripts read `data/index.csv`; `pyrun` reads the index and passes resolved paths to the script.

In recorded commands, keep entry-local script and output paths relative to the entry root, such as `scripts/plot_residuals.py` and `images/residuals.png`. Use `<log>` only for true log-level shared resources, and use `<name>` tokens for indexed data inputs or durable external data locations.

For tables or values copied into an entry, prefer script stdout that prints the Markdown table or text to record. Do not create a CSV just to transfer generated text into the entry; create or keep CSV only when it is retained data, reused by commands, or requested by the user.

For retained visual evidence, embed the image in the entry with Markdown image syntax, such as `![Comparison plot](images/comparison.png)`. Use a plain link only for nonvisual files, very large artifacts, or supplemental files that are not meant to be read inline.

When a command uses a data file or directory that is not already in `data/index.csv`, ask whether the data should be copied into the entry or referenced externally. Then add a `data/index.csv` entry using `skills/research-logging/references/file-data-index.md` and record the command with the `<name>` token instead of the raw path.

`pyrun` searches for an entry-local data index in this order:

- `./data/index.csv`
- `../data/index.csv`

On first use in an entry, create an entry-root symlink named `pyrun` that points to the resolved `astro-agents` skill package path for `skills/research-logging/scripts/pyrun`, or call that script through its installed path. Prefer a relative symlink when practical.

After creating or changing an entry Python script, `data/index.csv`, `pyrun` symlink, or recorded command, run the recorded command from the entry root before treating it as reproducible.

Put commands in fenced code blocks near the result, output, figure, table, or check they support. When entry labels are used, commands usually belong in `Steps:` blocks.

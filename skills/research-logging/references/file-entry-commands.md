# Entry Command Instructions

Use this file when an entry needs executable commands to reproduce, regenerate, or explain retained evidence, outputs, figures, tables, or checks.

Use `skills/research-logging/references/file-script.md` for scripts invoked by
recorded commands.

Include commands only when they help later reconstruction. Do not add command blocks for routine navigation, exploratory dead ends, or commands whose effects are not retained in the entry.

Ad hoc exploratory plots or tables that are not recorded in an entry may be made directly. If a plot, table, figure, or derived result is saved into an entry or cited by an entry, prefer writing an entry script and recording the command that regenerates it unless the user explicitly asks for a one-off output.

Write commands from the entry root as the working directory. Ordinary shell commands can be written directly.

For Python commands, use `./pyrun` to simplify recorded syntax. Verify any
project-declared environment is available before running it. `pyrun` uses
`<project>/.conda/bin/python` when present; otherwise it uses the interpreter
running `pyrun`. If a declared environment is unavailable, report it and get
researcher approval before relying on that fallback. `pyrun` replaces tokens
with full paths and recognizes:

- `<project>` resolves to the project root.
- `<project>/...` resolves to a path under the project root.
- `<log>` resolves to the research-log folder.
- `<log>/...` resolves to a path under the research-log folder.
- `<name>` resolves to the matching `location` in the nearest entry-local `data.csv`.

Tokens may appear as a whole argument or inside an argument, such as `static=<calibration_series>/file.npz`. Quote arguments that contain angle tokens.

Treat each recorded command as a compact specification of the run. Expose
result-defining values through named CLI options, including the dataset or
split, cases or variants, seeds, sample or evaluation budget, and material
physical, numerical, statistical, or performance controls. Do not leave these
values only in script constants, implicit defaults, or prose. If explicit
options would be unwieldy, retain a manifest of resolved settings and expose
its path in the command.

Use prose for the question, rationale, controlled relationship, selection
criteria, and interpretation. Do not repeat a visible CLI parameter inventory.

For nontrivial commands, put one named option per line. Keep matched commands
structurally parallel and repeat the material controls needed to run each
command independently. Use repeatable `--case`, `--candidate`, or `--variant`
options for comparisons and sweeps when practical.

Example:

```bash
./pyrun scripts/run_study.py \
  --dataset "<development_set>" \
  --candidate baseline \
  --candidate trial \
  --seed 123 \
  --samples 500 \
  --manifest-json data/study-manifest.json \
  --summary-csv data/study-summary.csv
```

In recorded commands, keep entry-local script and output paths relative to the entry root, such as `scripts/plot_residuals.py` and `images/residuals.png`. Use `<log>` only for true log-level shared resources, and use `<name>` tokens for indexed data inputs or durable external data locations.

For a split entry, record each invocation in the document that presents its
outputs, even when the script lives in the parent entry's `scripts/`.

For tables or values copied into an entry, prefer script stdout that prints the Markdown table or text to record. Do not create a CSV just to transfer generated text into the entry; create or keep CSV only when it is retained data, reused by commands, or requested by the user.

For retained visual evidence, embed the image in the entry with Markdown image syntax, such as `![Comparison plot](images/comparison.png)`. Use a plain link only for nonvisual files, very large artifacts, or supplemental files that are not meant to be read inline.

When a command uses a data file or directory that is not already in `data.csv`, ask whether the data should be copied into the entry or referenced externally. Then add it with `./pyrun data add <name> <type> <location>` using `skills/research-logging/references/file-data-index.md`, and record the command with the `<name>` token instead of the raw path.

Images do not need `data.csv` rows merely because they are embedded in Markdown.
Add an image row only when a recorded command resolves it through a `<name>`
token.

`pyrun` searches for an entry-local data index in this order:

- `./data.csv`
- `../data.csv`

For every entry with a recorded Python command, create an entry-root `pyrun`
symlink to the resolved `skills/research-logging/scripts/pyrun` before running or
recording the command. Invoke Python commands through `./pyrun`; do not record
direct `python` commands or invoke the installed script directly. Do not copy or
vendor `pyrun`. Use a relative symlink when practical. If symlinks are
unavailable, report that and get researcher approval before invoking the
installed script directly. Record the approved exception beside the command;
do not copy `pyrun` as a fallback.

After creating or changing an entry Python script, `data.csv`, `pyrun` symlink, or recorded command, run the recorded command from the entry root before treating it as reproducible.

Put complete commands in fenced code blocks in the descriptive section that
uses the result, output, figure, table, or check they support. Do not require a
reader to follow a cross-reference merely to find the reproduction command.
When entry labels are used, commands usually belong in `Steps:` blocks.

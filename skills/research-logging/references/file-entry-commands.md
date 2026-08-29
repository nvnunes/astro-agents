# Entry Command Instructions

Use this file for active commands that produce or analyze retained results, or
to preserve the actual command history of completed work.

Include commands only when they help later reconstruction. Do not add command blocks for routine navigation, exploratory dead ends, or commands whose effects are not retained in the entry.

Exploratory plots, tables, or calculations outside the log may be made directly.
Do not create entry scripts, commands, or evidence records merely because a log
exists. If the researcher later asks to retain, add, cite, or present the work
in an entry, preserve the actual generator and command when they exist. Do not
invent or rerun a cleaner workflow for the transition. State missing material
as a reconstruction limit, and do not present an unsupported numerical result
as durable computational evidence.

For active work, apply the remaining conventions only to commands and outputs
created or changed by the current investigation and the inputs they consume.
Run commands as needed to produce and finalize their saved outputs. Do not rerun
an unchanged command solely to test reproducibility or provenance, expand into
an entry-wide or log-wide audit, or treat successful execution as validation.

For completed work, preserve the actual scripts, commands, environment,
settings, artifacts, and checks. Do not invent a generator, normalize a command
to `./pyrun`, or rerun it for documentation. State reconstruction limits.

Write commands from the entry root as the working directory. Put every
recorded executable command in a `bash` fence under the applicable `Steps:`
label. Ordinary shell commands can be written directly inside that fence.

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

Expose every retained entry-local output through a stable relative path value
in the command. A collection may use a directory or manifest path value. A
retained command log may instead use an explicit shell capture target.

Make evidence-relevant input and output relationships mechanically visible.
Prefer a natural option name whose complete leading or trailing token is
`input` or `output`, such as `--input-data`, `--catalog-input`,
`--output-summary-csv`, or `--image-output`. The role applies to that exact path
argument only. Do not rename an option merely in the recorded Markdown; the
recorded command must continue to match the real interface and the command
actually run.

When natural naming is unavailable or a command needs a type, positional role,
directory role, manifest role, or explicit override, put one hidden annotation
immediately after its command fence:

```html
<!-- command type = model; catalog = input; results = output -->
```

For a fence containing several independent commands, add the one-based command
number only where an annotation is needed:

```html
<!-- command-1 results = output -->
<!-- command-3 type = simulation; summary-csv = input; @2 = output -->
```

Option targets omit leading hyphens. Positional targets use `@N`, counting
positional arguments after the executable or script token. Supported roles are
`input`, `output`, `input-directory`, `output-directory`, `input-manifest`, and
`output-manifest`. Use directory roles only when the entire non-empty directory
has one direction and an output directory belongs to one producer. A manifest
is a UTF-8 CSV with the exact header `path` and paths relative to the manifest's
parent directory.

Use `type = model` for a command that originates data by evaluating or sampling
a model or mathematical relationship. Use `type = simulation` for a command
that originates data through a simulated process. A project-local script named
`simulate`, `simulation`, `simulate_*`, or `simulation_*` is recognized as a
simulation without an annotation. Code is never inspected to infer these
relationships.

A model or simulation type establishes a generated provenance root; it does
not hide the command's mechanically visible inputs. Those inputs must still
trace to earlier generated outputs or to named external data. A resolved
external `data.csv` input is trusted at that boundary, and validation does not
attempt to reconstruct its earlier provenance.

Annotations classify material already visible in the selected command. They do
not bind a producer by name, extract a path from an opaque `label=path` value,
or excuse a missing command relationship. Prefer the option-name convention
when it keeps the real command interface natural; use an annotation as the
explicit fallback.

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

When stdout or stderr supports presented evidence, save it during the recorded
run through a program log option or a shell target such as `tee` or
redirection. Never create the retained log later from output held only in agent
context. Do not create a CSV merely to transfer formatted text into an entry;
retain structured data when it supports analysis, reuse, or provenance.

When a command uses a non-image data input or durable external resource that is
not already in `data.csv`, ask whether it should be copied into the entry or
referenced externally. Then add it with
`./pyrun data add <name> <type> <location>`, and record the command with the
`<name>` token instead of the raw path. A generated data output enters
`data.csv` only when a later recorded command consumes it as an input.

Never add entry-local scripts or images to `data.csv`. Keep their relative
paths directly in commands or Markdown.

`pyrun` searches for an entry-local data index in this order:

- `./data.csv`
- `../data.csv`

For every active investigation with a Python command created or revised during
Record, create an entry-root `pyrun` symlink to the resolved
`skills/research-logging/scripts/pyrun` before running or recording the command.
Invoke those Python commands through `./pyrun`; do not record direct `python`
commands or invoke the installed script directly. Do not copy or vendor
`pyrun`. Use a relative symlink when practical. If symlinks are unavailable,
report that and get researcher approval before invoking the installed script
directly. Record the approved exception beside the command; do not copy
`pyrun` as a fallback.

After creating or changing an entry Python script, `data.csv`, `pyrun` symlink,
or recorded command, run the command from the entry root and confirm its saved
outputs can be read before presenting them.

Put complete commands under `Steps:` in the descriptive section that uses the
result, output, figure, table, or check they support. Do not require a reader
to follow a cross-reference merely to find the reproduction command.

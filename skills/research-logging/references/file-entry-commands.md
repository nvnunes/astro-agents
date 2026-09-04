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

When repeated commands are clearer as a finite shell abstraction, validation
can mechanically account for a closed static subset: literal `for` loops;
locally defined functions of any valid name invoked with one or more literal
arguments and using `$1`, `shift`, and `$@`; and loop-local literal `case`
branches that assign a scalar or literal array. Those constructs may substitute
`$name`, `${name}`, or `${array[@]}` only from a binding established in the
same supported construct. A trailing `&` and standalone `wait` may express
scheduling. Prefer an explicit command for a one-off invocation.

This is not general Bash interpretation. Do not rely on environment variables,
globs, command or process substitution, arithmetic, dynamic value lists or case
selection, or nested control flow to make evidence relationships visible. The
validator does not execute shell. An unsupported or unbound construct is
fail-closed and establishes no relationship; its body is not mined for likely
commands.

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
- `<name>` resolves to one exact named file or directory input owned by the
  current entry.
- For a pinned Git repository input, `<name>` resolves to its local repository
  locator and `<name:commit>` resolves to its full commit hash. A consuming
  command must pass both; they establish one material relationship.
- `<directory-name>/member` resolves one exact regular-file member of a
  declared directory input.

Data tokens occupy the complete input argument. Quote arguments that contain
angle tokens. Do not embed a token in `label=<name>` or another opaque value.

Treat each recorded command as a compact specification of the run. Expose
result-defining values through named CLI options, including the dataset or
split, cases or variants, seeds, sample or evaluation budget, and material
physical, numerical, statistical, or performance controls. Do not leave these
values only in script constants, implicit defaults, or prose. If explicit
options would be unwieldy, retain a manifest of resolved settings and expose
its path in the command.

Expose every retained entry-local output through a stable relative path value
in the command. A collection may use a directory path value. A retained
command log may instead use an explicit shell capture target. A retained
manifest is an ordinary named file input and never expands other relationships.

Make evidence-relevant input and output relationships mechanically visible.
Prefer a natural option name whose complete leading or trailing token is
`input` or `output`, such as `--input-data`, `--catalog-input`,
`--output-summary-csv`, or `--image-output`. The role applies to that exact path
argument only. Do not rename an option merely in the recorded Markdown; the
recorded command must continue to match the real interface and the command
actually run.

When a `pyrun` command cannot use natural naming, declare the affected script
options through `--other-inputs` or `--other-outputs`. List option selectors
without leading hyphens and positional selectors as one-based `@N` values:

```bash
./pyrun \
  --other-inputs catalog \
  --other-outputs results,@2 \
  -- \
  scripts/run_study.py \
  --catalog "<development_set>" \
  --results data/results.csv \
  trial images/trial.png
```

The runner infers file or directory kind from the registered input or completed
output. Captures remain file-only. Use these declarations only when natural
names do not expose the correct role; an explicit declaration overrides a
misleading automatic role.

Do not add hidden command metadata. Preserve existing non-`pyrun` command
history faithfully; when it cannot expose a required material relationship
through natural role-bearing option names and named input tokens, report the
provenance limit instead of rewriting it solely for validation.

There are no command types, generated roots, or simulation filename rules. A
producer with no material inputs terminates lineage at its artifact-output
relationship after its confirmed output support is validated. An explicitly
registered origin terminates lineage. A generated input traces to its unique
earlier producer regardless of storage location.

Runner declarations classify material already visible in the selected
`pyrun` command. They do not bind a producer by name, extract a path from an
opaque `label=path` value, or excuse a missing command relationship. Prefer the
option-name convention when it keeps the real command interface natural; use a
runner declaration as the `pyrun` fallback.

Unclassified values are material candidates only when they have positive path
evidence: an existing filesystem target, an explicit path or URI prefix, an
angle token, or a known material suffix. A slash alone does not make a scalar a
path. The exact entry `data` and `images` roots are ordinary artifact-container
arguments rather than candidates; this exception does not apply to descendants
or any other directory.

Use prose for the question, rationale, controlled relationship, selection
criteria, and interpretation. Do not repeat a visible CLI parameter inventory.

For nontrivial commands, put one named option per line. Keep matched commands
structurally parallel and repeat the material controls needed to run each
command independently. Use repeatable `--case`, `--candidate`, or `--variant`
options for comparisons and sweeps when practical.

Example:

```bash
./pyrun scripts/run_study.py \
  --input-dataset "<development_set>" \
  --candidate baseline \
  --candidate trial \
  --seed 123 \
  --samples 500 \
  --output-manifest-json data/study-manifest.json \
  --output-summary-csv data/study-summary.csv
```

In recorded commands, keep entry-local script and output paths relative to the
entry root, such as `scripts/plot_residuals.py` and
`images/residuals.png`. Use `<log>` only for true log-level shared resources,
and use `<name>` tokens for indexed local data inputs.

For a split entry, record each invocation in the document that presents its
outputs, even when the script lives in the parent entry's `scripts/`.

When stdout or stderr supports presented evidence, capture it through `pyrun`
so it receives an output support record. Use `--capture-stdout <path>` and
`--capture-stderr <path>` separately, or use
`--capture-stdout-stderr <path>` for a merged stream. With one runner option,
keep that option and `--` on the `./pyrun` line:

```bash
./pyrun --capture-stdout-stderr data/run.log -- \
  scripts/run_study.py \
  --parameter value
```

With several runner options, put `./pyrun`, each option-value pair, and `--` on
separate lines as in the role-declaration example above.

Raw shell redirection and `tee` do not establish execution-linked Provenance.
Never create the retained log later from output held only in agent context. Do
not create a CSV merely to transfer formatted text into an entry; retain
structured data when it supports analysis, reuse, or provenance.

Every material command input must already have one matching named declaration
in the owning entry. When adding a producerless input, ask whether it should be
copied into the entry or referenced at its current local location, then use
`<skill>/scripts/log data add-origin --path <log> --entry <entry-id> <name>
<target>`. Record the command with `<name>` instead of the raw path. When a
confirmed output becomes an input to a later recorded command, use
`<skill>/scripts/log data add-generated --path <log> --entry <entry-id> <name>
<target>` after its producer succeeds.

Never register output-only results, scripts, command logs, or images as inputs
unless an exact directly presented non-`pyrun` artifact needs an explicit
origin boundary. Otherwise keep script and output paths directly in commands
or Markdown. An image that is actually consumed by a later command is an input
and follows the same registration rule.

`pyrun` resolves names only from the current owning entry. It does not inherit
inputs from a parent entry or the log root.

`log add` installs the entry-root `pyrun` symlink from the active skill package
when it creates an entry. Before running or recording an active-work Python
command, require that symlink to resolve to the active package's
`scripts/pyrun`. Invoke the command through `./pyrun`; do not record direct
`python` commands, invoke the installed script directly, copy or vendor
`pyrun`, or replace an unexpected target during Record. A missing or incorrect
runner in an existing entry is an identified defect for separately authorized
Repair. If symlinks are unavailable, report that and get researcher approval
before invoking the installed script directly; record the approved exception
beside the command.

After creating or changing an entry Python script, input declaration, `pyrun`
symlink, or recorded command, run the command from the entry root and confirm
its saved outputs can be read before presenting them. `pyrun` updates the entry-root
`pyrun-outputs.json` only after successful execution and complete output
observation, provided the script and direct input bytes also remained stable
across execution; do not edit that file by hand.

Put complete commands under `Steps:` in the descriptive section that uses the
result, output, figure, table, or check they support. Do not require a reader
to follow a cross-reference merely to find the reproduction command.

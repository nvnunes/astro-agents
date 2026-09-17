# Research Script Instructions

Use this file for scripts created, revised, or placed during active research,
or for actual scripts preserved from completed work.

For active work, apply these production checks only to material created,
changed, or consumed by the investigation; they do not establish validation.
For completed work, preserve actual material and relevant limits without
rewriting or rerunning it for documentation.

Prefer Python for new research scripts unless the researcher requests another
language or the research toolchain requires another language.

Before implementing a script, inspect the project for APIs that provide the
required data access or behavior. If one exists and the researcher has not
already chosen a path, ask whether the script should use it, bypass it for
independent evidence, or test it directly. Record the choice when it changes
what the evidence establishes.

Place reusable code by its actual use:

- one entry, including split documents: the parent entry's `scripts/`
- multiple entries in one log: `<log>/scripts/`
- multiple logs or production workflows: project code

Do not copy a shared script tree into entries. If changing shared code would
change a recorded command's output, preserve the old interface or add a
versioned one. If code must be frozen, snapshot only the entry adapter or
configuration.

Use ordinary imports or statically resolvable Python child invocations for
evidence-affecting project-local Python code. `pyrun` records one normalized
fingerprint for the statically reachable code tree without importing or
executing it. For every research command, ordinary execution, verification,
reproduction, and analysis use the same import order: the executing script's
directory, the owning entry's `scripts/`, the owning log's `scripts/`, then the
project root. Repeated roots are removed. This is runner-owned context, not
recipe environment; do not recreate it with `sys.path` or `PYTHONPATH`.
Dynamic imports, runtime import-path mutation, wildcard imports, unresolved
project-local dispatch whose complete base hierarchy cannot be proved to
terminate externally, and unresolved child entrypoints leave the fingerprint
unavailable; do not use those mechanisms when code currentness is required.
Calls inherited through fully resolved base paths from an external base are
valid terminal calls: their local syntax participates, but external package
implementation does not.

When generation is expensive or stochastic, or its output supports multiple
results, use separate `generate or record -> retained artifact -> analyze or
summarize -> retained table -> plot` stages. Make plotting scripts read retained
artifacts or tables rather than rerun simulation, training, or acquisition. A
single deterministic script may analyze and plot an existing retained input
when no intermediate table is reused by another command.

## Retained Inputs And Outputs

Accept all retained input and output locations through explicit arguments. Do
not hard-code project, log, entry, data, image, or output paths in scripts.
Require paths for operations the script performs; do not require paths for
operations it skips.

`pyrun` resolves named inputs and passes their paths to the script. Both
ordinary `pyrun` and reproduction supply absolute declared file and directory
input and output paths. Use supplied arguments throughout helpers and child
processes. Do not replace them with guessed locations.

Use supplied paths as-is when writing logs or metadata. Do not require them to
be relative to the project directory. Stored paths may remain descriptive
metadata, but scripts must not use stored paths or hidden sidecar conventions
to select additional input files.

A script may read files beneath an explicitly supplied, declared directory
input. It must not follow paths stored in those files to select additional
inputs; pass those inputs explicitly. Use existing directory-member tokens when
one declared member is the intended input. Do not make ordinary analysis
scripts read the input registry or add an embedded-path resolver or discovery
framework. Direct Python execution supplies no runner path, recording, or
isolation behavior. Run retained commands through `pyrun`, verification, or
reproduction.

## Temporary Files

Use `tempfile` mechanisms that respect `TMPDIR`; do not hard-code a temporary
root. Both runners assign fresh scratch under `/private/tmp` through `TMPDIR`,
so no researcher-supplied scratch path is needed. Keep temporary paths available
until all libraries, helpers, and child processes that consume them finish, and
wait for children before returning.

The runner removes scratch once workers stop, including on failure or stop.
Never use it as a checkpoint, later input, cache, or retained debugging
material. Treat files needed by later commands as retained outputs and declare
them accordingly. Every relaunch receives new scratch.

## Code And Helper Locations

Use ordinary imports for local helpers. Use `__file__` only when a static child
entrypoint or another non-import code path must be located. Keep code discovery
independent of data arguments and Git-root discovery. `__file__` may anchor
local code and helper discovery, including direct self-reexecution through the
active Python interpreter, but it must not reconstruct retained data locations.
Git-root discovery likewise must not substitute for explicit input or output
arguments.

Repair missing declarations and helper imports through existing mechanisms.

Use the project-declared execution environment. If it is unavailable, report
that before using another interpreter.

If a required development tool is missing, follow an already-authorized project
setup command; otherwise report the blocked check and ask before installing it
into the project-local environment. Do not install globally or silently
substitute another check.

Before writing a new or changed figure, fail on missing required columns or
cases, non-finite values, or incompatible units. Inspect that figure for missing
series, clipped or overlapping labels, unreadable legends, and incorrect units.
Record defects, corrections, or limitations that affect the evidence; do not
narrate a routine successful inspection.

Reload and check a serialized artifact only when the active workflow consumes
it later. Record structural facts only when they help explain, reuse, or assess
the evidence, and record a checksum when a binary or externally mutable
artifact is the fixed basis of a retained result.

Leave runtime caches such as `__pycache__/`, `.pytest_cache/`, and `.ruff_cache/`
in place when project ignore rules cover them; remove only unignored caches from
the research-log tree.

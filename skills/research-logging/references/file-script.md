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

When generation is expensive or stochastic, or its output supports multiple
results, use separate `generate or record -> retained artifact -> analyze or
summarize -> retained table -> plot` stages. Make plotting scripts read retained
artifacts or tables rather than rerun simulation, training, or acquisition. A
single deterministic script may analyze and plot an existing retained input
when no intermediate table is reused by another command.

When writing entry or log scripts, pass input and output paths as command-line
arguments. Do not hard-code project, log, entry, data, image, or output paths in
scripts. Do not make ordinary analysis scripts read `data.json`; `pyrun` reads
the index and passes resolved paths to the script.

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

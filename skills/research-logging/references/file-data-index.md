# Material Input Instructions

Use this file when a command or evidence consumes retained material, or the
researcher chooses where Provenance stops. Commands and evidence own new
declarations through their respective sync actions. `log data` owns shared
changes and advanced identity and reproduction policy. Do not inspect or edit
JSON during normal Record or Repair; use semantic `list` actions when needed.

Use [the provenance patterns](provenance-patterns.md), also routed as
`references/provenance-patterns.md`, for focused examples of common cases.

## Name And Declare Material

Give each retained file or directory one stable entry-scoped name. Commands
use quoted `<name>` or `<directory-name>/member` arguments. Evidence comments
accept those complete tokens or the shorter `name` and `name/member`.
A member is not a separate directory producer.

Author the Markdown command or evidence definition first. Its sync can create
missing declarations and accept consistent assertions of existing declarations:

- `--add-origin NAME=PATH`: an existing regular file;
- `--add-origin-directory NAME=PATH`: an existing directory;
- command sync only: `--add-generated NAME=PATH` and
  `--add-generated-directory NAME=PATH`, including missing outputs;
- command sync only: `--add-origin-git NAME=COMMIT:PATH`;
- `--add-from-entry NAME=ENTRY`: a same-name reference to a directly generated
  artifact in another entry of this log.

Paths are absolute or relative to the selected entry, not the caller's working
directory. Prefer short entry-relative paths for entry-owned material.
Omit an assertion once the name exists. A conflicting assertion is not an
update: use the owning change action.

Choose an origin only when no maintained same-log command owns the target and
the researcher intends Provenance to stop there. Ask whether an external input
should be copied or referenced. Generated material requires one structurally
valid producer. Do not invent an origin to bypass missing-producer diagnostics.
Declare every retained output through command sync before running its producer,
including captures and output-only directories. Do not declare executed scripts
merely because they are code.

A Git origin pins tracked content, not dirty files, environments, caches,
generated models, or submodule checkouts. Declare those separately when consumed.
Pass both `<name>` and `<name:commit>` to a consuming command.

## Maintain Existing Declarations

A command- or evidence-local path change uses its sync's
`--change-target NAME=PATH`; command Git targets also accept `COMMIT:PATH`.
For command sync, select every command consumer of the name in one change set;
an evidence or cross-entry consumer makes the target shared and routes it to
`log data update`.
A shared change routes to:

```text
<skill>/scripts/log data update --path LOG --entry ENTRY NAME
  [--target PATH|COMMIT:PATH] [--boundary origin|generated]
  [--kind file|directory] [--identity byte-complete|file:PATH|pattern:GLOB]...
  [--reproduction-comparison exact|evidence] [--acknowledge-shared] [--dry-run]
```

Omission preserves existing properties. Identity selectors replace the selection;
use byte-complete alone to clear a bounded directory identity. Prefer complete
identity; bounded identities require explicit researcher intent and must cover
the relevant consumed bytes. A shared update identifies its other consumers.
`--acknowledge-shared` acknowledges that wider scope, not a validation bypass.

For rename, update every Markdown use first, then
`log data rename OLD NEW`. It verifies each affected command and evidence
definition, updates normalized uses and same-log references together, and
reports executions needing reproduction. Do not sync an unknown new name first.
For deletion, remove all uses and sync or delete their owners first, then
`log data delete NAME`. Remaining uses fail with their locations.
Neither action deletes retained files. `log data list` returns semantic state.

## Reproduction Policy Is Separate

Exact whole-artifact comparison is the default. Do not change it while writing
ordinary commands or simply because reproduction differs. First rule out
avoidable nondeterminism and defects, then obtain researcher approval.
`log data update --reproduction-comparison evidence` selects evidence-scoped
comparison for one generated file; `exact` removes that exception.
The optional `reproduction_tolerance` belongs to each evidence comment, not
the artifact declaration. It affects reproduction only and never relaxes
comparison with Markdown or accepts a changed retained baseline.

If the CLI cannot decode an owned registry, stop and report the precise
failure. Direct JSON repair requires explicit authority and is reserved for
malformed state the owning CLI cannot handle.

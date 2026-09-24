# Presented Evidence Instructions

Use this file for a presented result, retained artifact, or summary reference.
Choose what the research should present; Markdown defines the selection and
format, and `log evidence sync` derives the record and presentation.
Do not create, inspect, or edit JSON during ordinary Record.

Entry evidence belongs under `Results:` in an experimental section.
A value in prose is separate evidence even when repeated in a table.
Give each item a stable descriptive lowercase EID, never an ID containing its
current value.

## Define Then Sync

For a new value use an empty code span, immediately followed by the definition:

```markdown
The error was ``<!-- eid:error source=metrics select=/error render=fixed:3 -->.
```

For an existing item use the same syntax; change only the desired comment fields,
then sync. Source names may be `metrics`, `bundle/metrics.csv`, or their
complete named tokens. Raw paths and bare directories are not evidence sources.

```text
<skill>/scripts/log evidence sync --path LOG --entry ENTRY
  [--id EID]... [--rename OLD=NEW]... [--delete EID]...
  [--add-origin NAME=PATH]... [--add-origin-directory NAME=PATH]...
  [--add-from-entry NAME=ENTRY]... [--change-target NAME=PATH]... [--dry-run]
```

LOG is the logical base whose summary is LOG.md. Read the selected action's help
only when needed. Missing generated sources must be declared through their
producer's command sync; an evidence-owned origin can be declared in this call.
Add assertions are optional once the name exists and may be repeated consistently.
Omission preserves data properties. A conflict names the owning change action.

Sync fills the empty presentation or replaces its existing owned region, derives
selection expectations, and captures current linked-artifact fingerprints.
For explicit EIDs, renames, or deletions, use one `--dry-run` invocation to
inspect the complete change set, then repeat it without `--dry-run` to apply.
Supply every selected ID in that same call; at least one `--id`, `--rename`, or
`--delete` is required. Rename destinations are selected automatically.
Repeated consistent selectors are accepted. For new markers from already
declared generated sources, use the source/producer compare-and-sync path below
to fill the group without listing each EID. Require success before continuing.
Do not inspect JSON to confirm success.
A malformed registry is a separate Repair boundary, not an invitation to edit
around an authoring failure.

## Refresh After Execution

After `pyrun` updates generated artifacts, or after authoring new markers from
declared generated sources, compare the affected evidence once:

```text
<skill>/scripts/log evidence compare --path LOG --entry OWNER --source NAME [--source NAME]...
<skill>/scripts/log evidence sync --path LOG --entry OWNER --source NAME [--source NAME]...

<skill>/scripts/log evidence compare --path LOG --entry OWNER --producer CID
<skill>/scripts/log evidence sync --path LOG --entry OWNER --producer CID
```

Compare is read-only and returns each EID and the exact before and after
presentation; for a new marker, `before` is its literal current Markdown
placeholder (two backticks for an empty inline code span). Judge whether
differences make scientific sense before sync, then call sync with the same
selector. A separate `sync --dry-run` is optional for this source/producer path.
Repeat `--source` for several direct generated declarations in their owning
entry. `--producer` uses the full effective CID of a synchronized command in
that entry and selects its directly generated outputs, not origins or other
commands. Either selector includes new complete markers by default, reaches
same-name references in other entries, and updates forwarded summary values.
For a single item use `--id EID`; its sync retains the dry-run/apply pair above.
Call sync even when the presentation did not change: it refreshes fingerprints
and expectations without rewriting unchanged Markdown.

Extraction runs without holding entry/log locks. Publication rechecks the
selected definition, sources, and record, then preserves unrelated concurrent
edits and refreshes only selected summary references under a short summary
guard. Do not edit the selected evidence while sync is preparing it.
`authoring.state.changed` directs you to review a relevant concurrent change;
an active artifact writer causes `artifact.reservation.conflict`. Report either
and stop the affected operation. Compare/sync after the producing invocation
has finished, never against its partially written outputs.

## Supported Presentations

- A short scalar, percentage, Boolean, range, tuple, interval, or plus/minus:
  read `references/record-evidence-definition-numeric.md` when needed.
- Source selection beyond a simple field:
  read `references/record-evidence-definition-sources.md`.
- A direct Markdown table from one retained source:
  read `references/record-evidence-definition-direct-tables.md`.
- A verbatim retained-output excerpt:
  read `references/record-evidence-definition-outputs.md`.

The comment is compact shell-quoted key=value syntax. Semicolons start another
source or table-column clause. There are no JSON/YAML definitions, definition
files, joins, generic compound table cells, or user-supplied fingerprints.

For a linked whole artifact use one source and no selection or transformation:

```markdown
![Residual map](images/residual-map.png)<!-- eid:residual-map source=map -->
[Download results](data/results.csv)<!-- eid:results source=results -->
```

For a complete retained UTF-8 diff, put the comment immediately before a
`diff` fence. Sync owns its payload, not fence delimiters. It preserves complete
source content after LF normalization and structural fence separation; no
fingerprint field is stored for this inline artifact.

A summary table combining independent metrics is ordinary Markdown composition:
give each evidence-bearing cell its own code span and EID. There is no summary
table CLI record. Mark every numeric or closed-Boolean data cell independently;
partial marking does not waive whole-table evidence completeness.
Use cell composition for small tables with at most three repetitions along
their repeated axis: rows, or columns in a transposed presentation. Identify
the repeated axis rather than choosing the shorter table dimension. For larger
compositions, record a script that compiles a table-shaped retained artifact,
then use direct-table evidence. This is authoring guidance, not a CLI limit.
A join, derived column, pivot, or derived table requires a
recorded script that emits a presentation-ready retained artifact; then use
ordinary direct-table evidence. Do not emulate the calculation in comments.

## Lifecycle And Summary References

For rename, edit the entry EID and every summary reference first, then include
`--rename OLD=NEW` in the evidence sync change set. The destination comment
defines its current source, render, and presentation; sync does not copy a stale
record. For delete, remove the marker and summary references first, then include
`--delete EID`. Select related updates, renames, and deletions together in one
dry-run/apply pair. Evidence sync never deletes retained files or data
declarations.
When a former source name should be removed, call `log data delete` separately;
it refuses deletion and names remaining consumers if the name is still used.
Use `log evidence list` for semantic inspection.

A summary reuses an already supported entry value or exact table cell:

```markdown
Runtime was `12.3 ms`<!-- ref entry = e004a; eid = runtime -->.
Error was `0.286%`<!-- ref entry = e001; eid = cases; row = 2; column = 3 -->.
```

Rows and columns are one-based body-row and presented-column coordinates.
A split entry uses the exact document stem in a summary reference, but the
physical entry ID for CLI ownership. Do not originate calculations or artifact
evidence in the summary. Retention is for disconnected material only; remove
its coverage explicitly before sync makes a retained target active.

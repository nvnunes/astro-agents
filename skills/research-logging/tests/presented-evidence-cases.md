# Presented Evidence Cases

Use these cases for focused review of research-logging Record, Replace, Update
Summary, and review behavior. They are not research evidence.

## Integrated Record Workflow

Given a request to investigate a question and record the work, Record
implements or revises the needed scripts, runs the research, retains and
analyzes the outputs, documents the evidence, and drafts grounded observations
without requiring a separate execution request.

Given a new or changed figure, Record runs its command, confirms the saved
output can be read, and inspects the figure. It records a defect, correction,
or limitation that affects the evidence, but does not narrate a routine
successful inspection or declare validation.

Given a changed analysis stage that consumes a serialized intermediate, Record
reloads the intermediate and checks its expected structure. It records shape,
row count, or schema only when that information helps explain, reuse, or assess
the evidence; it does not add a checklist merely to prove the check occurred.

Given completed results to incorporate, Record preserves the actual scripts,
commands, settings, artifacts, and evidence-affecting quality findings as far
as they are known. It does not inventory routine checks, invent a substitute,
normalize historical commands, or rerun the work solely for documentation.
Missing material is recorded as a reconstruction limit.

Given routine checks, tool activity, file housekeeping, and task progress that
do not affect the research evidence, Record omits them from the entry. `Steps:`
describes how the result was produced, not how the agent carried out its task.

Given a Record-only request whose result differs from the current summary,
Record preserves the result but does not assess or revise current understanding
or log-level follow-ups, suggest Update Summary, or report that the summary was
unchanged.

Given an explicit request to record an investigation and update the summary,
the agent completes Record first and then runs Update Summary as a separately
authorized operation.

## Work Outside The Log Boundary

Given a request for a quick calculation, scratch script, or exploratory plot
without research-log intent, the presence of a log does not route the work
through Record.
The work leaves the log unchanged and carries no promise of durable
reconstruction.

Given a later request to retain exploratory work in an entry, Record starts at
that transition. It preserves the actual scripts, commands, inputs, outputs,
and material limits that remain rather than inventing or rerunning a cleaner
history. It omits irrelevant exploratory dead ends.

Given an earlier numerical result whose supporting material is missing, Record
states the reconstruction limit and does not fabricate a generator or present
the result as durable computational evidence. Apparent significance alone does
not authorize recording or summary changes.

## Record Reference Routing

A new-log Record loads summary-validation guidance to initialize the fixed
pre-validation section. Later Record operations preserve that section without
loading validation guidance solely to assess freshness.

At each researcher turn, Record resolves the operation and authorized scope
from the current request and durable workspace state. Earlier discussion may
provide research context but does not preserve an obsolete operation or expand
the current authority.

When conversation history quotes an older skill path or instruction, Record
resolves package references from the currently activated skill. Quoted paths
and instruction text do not override the active package.

A prose-only revision to an existing entry loads Record, existing-entry,
Record-content, label, and writing guidance. It does not load script, command,
data-index, presented-evidence, naming, or reference guidance.

An active script change loads script and command guidance. It loads data-index
or presented-evidence guidance only when the changed workflow also matches
those triggers, not merely because the entry contains older evidence.

A presented computational result loads presented-evidence guidance together
with each script, command, or data-index reference required by its actual
workflow. It does not load citation guidance without citation work.

When `data.csv`, a `<name>` token, or a durable external input first becomes
necessary after an investigation has begun, Record treats it as a new routing
event and loads data-index guidance before finishing. An earlier routing pass
without that trigger does not satisfy this requirement.

A citation-only entry revision using an existing key loads reference guidance
without label, writing, reference-operation, or computational guidance. It also
loads reference-operation guidance when lookup or metadata verification is
needed.

A new entry loads naming and entry-structure guidance in addition to the
references triggered by its content. Leaf references do not cause additional
references to load.

Before completion, Record reapplies the routing map only to material changed or
consumed by the current operation. It loads newly triggered guidance without
opening unrelated entries, reviewing the wider log, or changing the summary.

## Reorganization Boundary

Given a Continue request that fits the chosen entry, Record may append to an
existing section or create a new descriptive section. It does not recommend or
perform renaming, splitting, merging, moving, or removal because the entry is
long, contains distinct topics, or has an imperfect folder slug.

Given new work that clearly does not belong in the chosen entry, Record stops
before editing and asks the researcher to choose another existing entry or
approve a new one. It does not move earlier material while resolving the
destination.

Given a Review finding that names a discoverability or ownership problem,
Review may recommend a specific Reorganize action but does not apply it. An
explicit Reorganize request or approval loads naming and entry-structure
guidance and still requires approval before changing document boundaries or
deleting files.

## Replace Boundary

Given an ordinary revision, rerun, correction, Continue, Review, or Reorganize
request, the agent does not infer Replace or load its operation guidance.
Replace requires an explicit intention to remove superseded experimental work
from the active log.

Given an authorized experimental section, Replace may revise `Background:`,
`Steps:`, `Results:`, and `Observations:`, the section's evidence rows, and its
exclusively owned scripts or artifacts. Other labels, sections, shared
material, summary content, the fixed validation-report link, and every generated
validation file remain unchanged. A separately authorized Update Summary may
run only after Replace.

Given a decision in the authorized section whose stated basis is removed or
contradicted, Replace preserves the decision text and prefixes it with
`**Needs update:**`. An uncertain effect leaves the decision unchanged and is
reported to the researcher. A decision in any unmentioned section is read-only.

Given a later section or file that directly depends on the target material,
Replace does not edit, move, or delete it. If the replacement cannot complete
without changing that dependent, the agent stops before editing the active log
and requests an explicit scope expansion.

Given files that may be overwritten or removed, Replace copies the complete
affected documents and support files to a durable location outside the active
log and verifies the backup before any destructive change. Backup failure
leaves the active log unchanged. The backup is reported and never removed as
part of Replace.

Given a verified backup and a replacement that can stay within its authorized
scope, Replace produces and checks the new work before deleting only the
explicitly approved superseded material. An artifact overwritten at the same
path is already present in the verified backup. Shared artifacts are not part
of the replacement boundary without explicit authorization.

## Section Types

An experimental section contains both `Steps:` and `Results:` and may contain
`Observations:`. It enters agent-led validation. A synthesis section contains
`Findings:` without experimental labels; its prose, tables, statistics, and
links receive no evidence rows or validation targets. A prose section contains
no block labels and is also skipped.

A section with `Findings:` plus `Steps:` or `Results:`, only one of `Steps:`
and `Results:`, or another unsupported label combination is invalid. Review
reports the structural problem. Validation skips the content, reports the entry
and heading, and retains a failed structural target until review repairs it.

## Recorded Command Output

Given a `bash` command under `Steps:` that ends with
`2>&1 | tee data/run.log` and a `text` excerpt under `Results:`, record accepts
the form and adds one `output` row to entry-level `evidence.csv` with
`data/run.log` as its only source. Review checks the row shape. Validation
remains responsible for verifying the excerpt-to-log association.

Given only a `text` excerpt under `Results:` with a statement that an agent
copied terminal output, review reports that the recorded command did not retain
the source output.

## Statistics

Given "MSE was `0.184` for `seed=42`", the result expression is presented
evidence and the visibly named parameter is not. Given "MSE was 0.184", review
reports an apparent unmarked result. Given a derived claim such as `14.3%
lower`, record requires that the derived value already exist in one retained
artifact and adds one `statistic` evidence row naming that artifact.

Given a numerical result in a Results table that is discussed again under
`Observations:`, the prose occurrence is independently marked and indexed.
The table's evidence row does not exempt or absorb the prose statistic.

## Tables And Artifacts

A Markdown table, image embed, or artifact link under `Results:` is presented
evidence. The same form outside `Results:` is not a validation target; review
reports it only when the content appears intended as evidence.

A presented table may select columns, reorder rows, round values, and reformat
Markdown from one or more retained sources. Record adds one `table` evidence
row that identifies every source and any necessary locators or transformation.
A new derived column without a retained source is not acceptable.

An image embed or artifact link under `Results:` receives no evidence row. Its
target must resolve directly through a recorded command or `data.csv`; an
evidence row cannot repair an unresolved target.

## Summary

A summary may contain a backticked numerical statistic already supported by
exactly one entry section and link the point to that entry. Update Summary adds
one row to log-level `evidence.csv`. Record does not add, change, or remove that
row merely because entry evidence changes. Review reports a summary table,
image, generated-output fence, artifact link, newly calculated statistic,
statistic without entry support, or malformed summary evidence row.

## Source Locators

Given a CSV record selected by `case=1 NGS center, R=17.0; field=value`, the
comma remains part of the exact filter value. Given several exact values or
result fields, the locator uses `|` without surrounding spaces. It never uses a
comma as a list separator.

Given a table whose filter column is named `field`, `fields`, `path`,
`property`, or `text`, the locator prefixes that filter with `where.`, as in
`where.field=validation_error_percent; field=absolute_difference`. The
unprefixed reserved name continues to select the result field or structured
operation.

Given a JSON or container value, the locator uses `path=` with dot-separated
keys and optional indexes or slices. `path=$` selects the root. A root list of
records or aligned NPZ arrays may use exact filters followed by `field=` or
`fields=`. Relative structured fields may traverse nested JSON keys or HDF5
dataset paths. Dataset counts and shapes use only `property=shape`,
`property=shape[n]`, or `property=size`.

Given a retained log excerpt, the locator uses `text=` with a distinctive
literal fragment. A locator may be absent only when the whole artifact narrowly
and unambiguously supports the presented item. Review reports free-form locator
prose, ambiguous delimiters, or a missing locator for a broader artifact.
Validation never deserializes pickle evidence and instead requires a retained
CSV or JSON producer summary.

## Evidence Record Maintenance

When Record or Replace changes an entry heading or presented item, it updates
the affected entry-level rows in the same operation. When it removes presented
evidence, it removes the row and deletes a header-only file. When an artifact
changes at the same path without invalidating the declared locator, it leaves
the row unchanged. The later Validate operation detects the content change
from saved fingerprints.

Given an approved Reorganize move or heading change that makes an `entry` or
`section` value stale in a log-level evidence row, Reorganize repairs only that
identifier through Record-content and presented-evidence guidance. It does not
change the statistic, transformation, summary wording, or set of summary
evidence rows.

Review reports missing, duplicate, extra, malformed, or structurally stale
rows. It checks selector uniqueness, kind values, source cardinality, and source
syntax but does not decide whether the evidence matches the source. Validation
resolves the source, checks the locator and transformation, and verifies logical
equivalence and provenance. Neither Review nor Validate edits an evidence row;
a requested repair routes to Record or Replace for an entry-level row and
Update Summary for a log-level row. An ambiguous association is reported rather
than guessed or recorded.

## Validation Publication Boundary

Given a newly started log with no completed validation, Record creates the
fixed `Validation: [latest completed report](<log>/validation.md)` link directly
below the title. It does not create a validation-status section or report.

Given a Record request to synchronize an out-of-date Markdown table with an
already regenerated artifact, Record changes the table, its evidence-based
observations, and an `evidence.csv` row only when the row's selector, section,
source, locator, or transformation changed. It performs the narrow production
check needed to confirm the presentation matches the retained source. It does
not run validation, change the fixed report link, or edit generated records.

Given a source file whose values changed while the evidence selector, section,
source, locator, and transformation remain correct, Record leaves the
`evidence.csv` row unchanged and preserves the fixed report link and all
generated validation files byte-for-byte.

Given a new entry created after the latest report date, Record adds no `NOT RUN`
or `STALE` marker to the summary. The generated report continues to identify
only the scopes included when it was produced.

Given changed evidence followed by a Validate request, Validate uses current
inputs and saved fingerprints to reopen affected outcomes, reuse only unchanged
outcomes, and publish a complete dated generated bundle. Non-Validate
operations do not precompute this change set.

Given an existing generated failure report, Record, Replace, Update Summary,
and Reorganize leave it unchanged. The next Validate request alone may rebuild
or remove it from current outcomes.

## Mechanical-First Validation

Given a complete `evidence.csv` row whose source exists, parses in a known
format, and is produced through a statically resolved recorded command,
validation completes the deterministic checks before requesting agent review.
It sends only equivalence or provenance details that the tool cannot decide
reliably to semantic fallback.

Given a locator-selected statistic that differs only by recorded rounding,
percentage conversion, scientific notation, or unit formatting, validation may
establish logical equivalence mechanically. A table transformation, compound
custom structure, ambiguous locator, or uncertain workflow connection remains
unresolved for bounded semantic review.

Given an apparent mechanical failure, the validation agent reviews the
reported context before retaining `FAIL`. The agent may correct the temporary
adjudication when the tool result is a false positive, but it never modifies
the entry, artifact, producer, `data.csv`, or `evidence.csv`.

Given a nonempty review queue, validation first generates a compact packet of
presented context, locator-selected values, structural results, and candidate
commands. The packet helps the agent make bounded semantic decisions; it does
not turn a candidate command or matching numeral into a successful check.

Given unchanged successful checks, validation reuses their dates only after
mechanically reconciling the current scope and fingerprints. A changed entry,
association record, artifact, producer, input, or cached Summary locator
invalidates only the affected checks.

Given a cached outcome and a later change to its presented item or
`evidence.csv` association, validation compares that outcome's stored
dependency identities with the current identities and reopens it. Rendering
some other updated outcome never refreshes the changed dependency snapshot
around the old result.

Given a newly recorded command that produces an existing evidence artifact,
validation detects the changed dependency contract and reopens the affected
Provenance outcome even when every previously known dependency identity is
unchanged.

Given a command established as part of a used evidence workflow, its declared
sibling outputs and shell capture logs are not orphans. If the workflow
consumes an upstream generated artifact, validation follows that artifact to
its producer and applies the same rule there. A directory connects only the
members selected during bounded collection review.

Given a file owned by one maintained research log and consumed by an active
recorded or validated workflow in another, the repository reverse-dependency
index removes it from the owner's orphan candidates. Its transitive script
dependencies are also used. A dormant script containing a possible cross-log
reference does not protect the referenced file. Changing an unrelated log
without changing the owner's incoming edge slice does not invalidate the
owner's validation fingerprint.

Given same-named local modules under directories added by consecutive
`sys.path.insert(0, ...)` calls, validation follows the module found through
the effective final path order. The module under the last inserted directory
is used; a shadowed same-named module remains an orphan candidate unless some
other active workflow reaches it.

## Data Index

A `data.csv` row consumed through `<input_data>` by a recorded command is valid.
A generated CSV may receive a row only when a later recorded command consumes
it as input. Review reports an unused row, an entry-local script or image row,
an unresolved token, or a raw external input path that should use `<name>`.

A valid indexed input in a workflow branch that does not reach presented
evidence is not an index-hygiene finding; it remains eligible for orphan
validation.

# Presented Evidence Cases

Use these cases for focused review of research-logging Record, Replace, Update
Summary, Repair, Review, and Validate behavior. They are not research evidence.

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

Given a new-entry Record, the agent adds the completed entry to the maintained
summary's `## Entries` inventory without changing current understanding or
log-level follow-ups.

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

A new-log Record loads summary-validation guidance to initialize the stable
validation-report link. Later Record operations preserve that link without
loading validation guidance solely to assess whether retained material is current.

At each researcher turn, Record resolves the operation and authorized scope
from the current request and durable workspace state. Earlier discussion may
provide research context but does not preserve an obsolete operation or expand
the current authority.

When conversation history quotes an older skill path or instruction, Record
resolves package references from the currently activated skill. Quoted paths
and instruction text do not override the active package.

A prose-only revision to an existing entry loads Record, existing-entry,
Record-content, label, and writing guidance. It does not load script, command,
input-registry, presented-evidence, naming, or reference guidance.

An active script change loads script and command guidance. It loads input-registry
or presented-evidence guidance only when the changed workflow also matches
those triggers, not merely because the entry contains older evidence.

A presented computational result loads the core presented-evidence guidance
together with each script, command, or input-registry reference required by its
actual workflow. An entry evidence source also loads locator guidance. A
non-identity statistic or output loads transformation guidance, while a table
loads transformation and table guidance.

A summary reference or direct artifact does not load locator, transformation,
or table guidance merely because another item in the entry uses it. It does not
load citation guidance without citation work.

When `data.json`, a `<name>` token, or a durable origin input first becomes
necessary after an investigation has begun, Record treats it as a new routing
event and loads input-registry guidance before finishing. An earlier routing pass
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

## Repair Boundary

Given an explicit request to repair one named research-owned validation
finding, the agent enters Repair, reads that check and the affected files, and
loads only the contract needed to establish the intended valid state. It does
not fix other findings from the same report.

Given a completed validation report followed only by a request to explain its
findings, Validate remains report-only and does not load Repair. A later
explicit request to correct a named finding activates Repair as a separate
operation and finishes by invoking the public Validate path.

Given a failed Record authoring command without a separate correction request,
Record reports the precise failure and stops. It does not load Repair, inspect
the complete mechanical specification, edit a registry around the failed
precondition, or rerun validation.

Given an ambiguous Review follow-up such as "fix it" when several findings or
corrected states are plausible, the agent asks which target and intended state
the researcher authorizes. It does not infer Repair scope from proximity or
conversation history.

Given a parseable record with an intended correction expressible by an owning
`log` action, Repair uses that action rather than editing the registry. Given a
malformed research-owned registry that the action cannot parse, Repair reads
only the applicable mechanical contract, directly corrects the named defect,
and leaves unrelated records unchanged. A Markdown-only defect is corrected in
the affected document without opening registry schemas that are not involved.

Given recognized residue from an interrupted research-owned transaction,
Repair follows its exact diagnostic and owning implementation contract. It
does not treat unknown files as residue. Repair never edits generated
validation state or `pyrun-outputs.json`; the final Validate invocation alone
may replace generated reports.

## Replace Boundary

Given an ordinary revision, rerun, correction, Continue, Review, or Reorganize
request, the agent does not infer Replace or load its operation guidance.
Replace requires an explicit intention to remove superseded experimental work
from the active log.

Given an authorized experimental section, Replace may revise `Background:`,
`Steps:`, `Results:`, and `Observations:`, the section's evidence records and
presentation markers, and its
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
`Observations:`. It enters mechanical validation. A synthesis section contains
`Findings:` without experimental labels; its prose, tables, statistics, and
links receive no evidence records or validation targets. A prose section contains
no block labels and is also skipped.

A section with `Findings:` plus `Steps:` or `Results:`, only one of `Steps:`
and `Results:`, or another unsupported label combination is invalid. Review
reports the structural problem. Validation skips the content, reports the entry
and heading, and retains a failed structural target until a separate Repair
operation corrects it.

## Recorded Command Output

Given a Python command under `Steps:` whose retained output supports a `text`
excerpt under `Results:`, Record captures that output through `pyrun`, puts one
stable `eid` marker immediately before the fence, and adds one `output` record
to entry-level `evidence.json` for the retained command log. Raw shell
redirection or `tee` does not establish the required output support. Review
checks the marker and record shape. Validation verifies the excerpt-to-log
association.

Given only a `text` excerpt under `Results:` with a statement that an agent
copied terminal output, review reports that the recorded command did not retain
the source output.

Given a command option such as `--output-summary-csv data/results.csv` or
`--catalog-input data/catalog.csv`, the leading or trailing role token makes
the exact path relationship discoverable. Record prefers this natural naming
when maintaining the real command interface.

Given a real interface whose natural option is `--results data/results.csv`,
Record preserves the command and invokes `pyrun` with
`--other-outputs results --`. It does not rename only the recorded command.
The runner declaration may also identify positional or whole-directory input
and output roles and infers kind from the registered input or completed output.

Given several non-`pyrun` commands in one fence, an annotation uses `command-N`
to select only the command that needs it. Commands without annotations require
no empty placeholder. Validation does not inspect script internals to infer a
missing relationship.

## Statistics

Given "MSE was `0.184` for `seed=42`", the result expression is presented
evidence only when one adjacent `eid` marker names its entry record; the visibly
named parameter is not. Given "MSE was 0.184", review reports an apparent
unmarked result. Given a derived claim such as `14.3% lower`, Record requires
that the derived value already exist in one retained artifact and adds one
`statistic` record naming that artifact.

Given a numerical result in a Results table that is discussed again under
`Observations:`, the prose occurrence is independently marked and indexed.
The table's evidence record does not exempt or absorb the prose statistic.

## Tables And Artifacts

A Markdown table, image embed, or artifact link under `Results:` is presented
evidence. The same form outside `Results:` is not a validation target; review
reports it only when the content appears intended as evidence.

A presented table may select columns, reorder rows, round values, and reformat
Markdown from one or more retained sources through a supported table recipe.
Record puts one stable `eid` marker immediately before the table and adds one
`table` record that identifies every source, locator, and transformation. A new
derived column without a retained source is not acceptable.

An image embed or artifact link under `Results:` receives no evidence record or
marker. Its target must resolve directly through a recorded command; an
evidence record or input declaration cannot repair an unresolved target.

## Summary

A summary may contain a backticked numerical statistic already supported by
exactly one entry record or exact numerical table cell. Update Summary places an
adjacent hidden `ref` naming the entry and evidence ID, plus one-based row and
column coordinates for a table cell. There is no summary evidence file. Review
reports a summary table, image, generated-output fence, artifact link, newly
calculated statistic, statistic without entry support, or malformed reference.

## Source Locators

Given a CSV record, the locator uses structured `where` conditions and ordered
`select` paths. A filter value such as `1 NGS center, R=17.0` remains one exact
JSON string rather than participating in a delimiter language. Several values
use an `in` condition; several result fields use several `select` paths.

Given JSON or a scientific container, the locator uses path arrays with exact
mapping keys, indexes, slices, or dataset components. Structural evidence uses
only the supported `property` operation with its declared expectations.

Given a retained log excerpt, the locator uses the supported exact text
selection and occurrence. Every evidence source has a bounded locator. Review
reports free-form selector prose, ambiguous or over-broad selection, and
missing identity or cardinality constraints where the relationship can drift.
Validation never deserializes pickle evidence and instead requires a retained
safe producer summary.

## Evidence Record Maintenance

When Record or Replace changes a presented item, it keeps the stable evidence
ID when identity is unchanged and updates its marker and entry record in the
same operation. When it removes presented evidence, it removes both and deletes
an empty `evidence.json`. A heading change alone does not change evidence
identity. A document move updates `document`; a source or presentation change
updates only the affected record fields.

Given an approved Reorganize move, Reorganize preserves stable evidence IDs and
repairs document paths or summary entry references that became stale. It does
not change the statistic, transformation, summary wording, or set of summary
references without separate authority.

Review reports missing, duplicate, extra, malformed, or structurally stale
records, markers, and references. It checks ID uniqueness, kind values, source
cardinality, and syntax but does not decide whether the evidence scientifically
supports the prose. Validation resolves the source, locator, transformation,
presentation, and provenance. Neither Review nor Validate edits an evidence
record; an explicit correction of an identified defect routes to Repair.
Removing superseded experimental work still requires Replace, and revising
current synthesis still requires Update Summary. An ambiguous association is
reported rather than guessed.

## Validation Publication Boundary

Given a newly started log with no completed validation, Record creates the
fixed `Validation: [latest completed report](<log>/validation.md)` link directly
below the title. It does not create a validation-status section or report.

Given a Record request to synchronize an out-of-date Markdown table with an
already regenerated artifact, Record changes the table, its evidence-based
observations, and its `evidence.json` record only when the record's document,
source, locator, or transformation changed. It performs the narrow production
check needed to confirm the presentation matches the retained source. It does
not run validation, change the fixed report link, or edit generated records.

Given a source file whose values changed while the evidence ID, document,
source, locator, and transformation remain correct, Record leaves the
`evidence.json` record unchanged and preserves the fixed report link and all
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

## Mechanical Validation

Given current evidence whose source, locator, transformation, presentation
marker, command relationship, and retained-material graph satisfy the
specification, validation completes through code without agent judgment.

Given a supported presentation that differs from its selected source through an
approved percentage, rounding, notation, unit, interval, tuple, uncertainty, or
table transformation, validation applies the declared deterministic form.
An unsupported or ambiguous declaration fails precisely; validation does not
choose a plausible alternative.

Given a mechanical finding, the validation agent reports the exact code,
subject, observed state, violated rule, and dependency cause from the generated
record. The validation agent does not override the result or edit research
material. A later, separately authorized Repair operation resolves the issue
before Validate is rerun.

Given complete findings, the CLI exits zero and publishes
`validation/mechanical.json`, its disposable cache, and `validation.md`.
Given an unavailable required observation, it returns `incomplete`, exits
nonzero, and leaves the prior completed bundle unchanged. Dry-run always writes
nothing.

Given unchanged successful checks, validation reuses a check only when its
complete dependency projection and active rules version still match. A changed
presentation, evidence declaration, artifact, command relationship, input, or
script identity reopens only dependent checks.

Given a recorded command connected to evidence, visible exact output arguments
and deterministic collections enter the material graph. Script internals are
irrelevant. An ambiguous output directory or unsupported collection
relationship fails until research-owned metadata makes the relationship exact.

Given a locally accessible file outside the validated log and consumed by an
active recorded workflow, validation treats it as an origin of the current log
when `data.json` says `origin: true`. It does not inspect another log's
validation state or use that reference to change Hygiene classification in the
file's owning log.

Given retained material outside the evidence-rooted command closure and not
covered by an explicit retention declaration,
validation reports the exact residual path as an orphan Hygiene finding.
Mechanical validation never asks an agent to classify the orphan semantically.

## Input Registry

A `data.json` item consumed through `<input_data>` by a recorded command is
valid. A generated file receives an item only when a later recorded command
consumes it as input. Review reports an unused item, missing declaration,
unresolved token, fingerprint drift, conflicting boundary, or raw input path
that should use `<name>`.

A valid declared input in a workflow branch that does not reach presented
evidence remains a used declaration; the workflow's retained material
remains eligible for Hygiene evaluation.

A reached generated output passes Provenance only when its output-keyed
`pyrun-outputs.json` record is confirmed and exactly matches the current output
fingerprint, script path and fingerprint, ordered parameters, and direct input
fingerprints. Changing any one without a matching successful run fails that
starting artifact's Provenance.

An output record absent from the complete current graph is an unmatched Hygiene
finding. If its file exists, validation does not also report that file as an
orphan. A graph-declared output whose file is missing is a Provenance failure,
not Hygiene.

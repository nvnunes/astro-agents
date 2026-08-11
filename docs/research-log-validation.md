# Research-Log Validation

This document is the human-facing source of truth for agent-led validation of
project-native research logs. It defines the assurance boundary, validation
modes, result model, and record ownership.

The `research-logging` skill contains the self-contained operational workflow.
Normal use of the skill must not require loading this document.

## Assurance Boundary

Validation establishes that deliberately presented computational evidence in a
research log is structurally intact and traceable. An item enters scope only
when the presentation rules identify it mechanically as one of:

- an explicitly presented retained artifact, including a structured file,
  textual output, figure, or artifact collection;
- a Markdown table;
- an explicitly formatted generated-output block; or
- an explicitly marked numerical statistic, including a derived comparison.

Entry sections have three mutually exclusive forms. An experimental section
contains both `Steps:` and `Results:` and generates new evidence. A synthesis
section contains `Findings:` without experimental labels and records
researcher-validated inspection, comparison, audit, or synthesis of existing
material. A prose section contains no block labels and provides contextual or
connective information. Only experimental sections enter agent-led validation.

Synthesis and prose sections contribute no validation targets or `evidence.csv`
rows and changes confined to them do not make validation stale. A synthesis
may preserve external evidence or selected findings from discarded internal
investigations whose experimental records and artifacts are not preserved.
These remain researcher-validated, non-primary material. If an internal
experiment or its supporting artifacts are preserved, document that experiment
and its evidence in an experimental section; a separate synthesis may refer to
it.

Any other label combination is structurally invalid. Research-log review owns
diagnosis and repair. Validation skips the entire invalid section, identifies
the entry and heading, and records a failed structural target so the skipped
content cannot coexist with an all-clear validation result. It does not infer
the intended section type or partially validate the section.

Entry structure makes these forms recognizable. A local artifact link, image
embed, Markdown table, or `text` generated-output fence is presented evidence
only under `Results:`. Tables elsewhere remain available for non-evidential
information. A linked or embedded artifact must be identifiable either as a
`data.csv` resource resolved by a recorded command or as an output path
resolved from a value in a recorded command or shell capture target.
Validation determines the value's workflow role from command and code context;
it does not require a particular parameter name. Merely naming an artifact in
prose does not present it directly.
Prefer tables, figures, and marked statistics to direct links to output files.
Link an output file only when direct inspection or reuse of the file is itself
important; do not list output files merely to report that a command created
them.

A numerical statistic in an experimental entry section or summary is presented
evidence when its value, units, and any comparison wording form an inline
backticked numerical expression. Do not put a named assignment inside the
marker. Backticked text, identifiers, and parameters remain non-evidential when
they are visibly non-statistical. Numerical content that is not evidence must
avoid backticks when it could be mistaken for a marked result. Unmarked
numerical prose is not a validation target. A Markdown table is one presented
item, not a collection of statistic targets: use plain formatting for
numerical cells and reserve backticks in tables for visibly textual identifiers
or code labels. Formatting inside synthesis and prose sections has no
validation meaning.

Recorded executable commands use `bash` fences under `Steps:` and expose
entry-local outputs through stable relative path values.
A collection may use a command-declared directory or manifest. Command output
quoted as evidence must be saved to a retained log by the command itself,
through a command path value or a shell target such as `tee` or redirection,
and the `text` fence copies a relevant excerpt from that log. Output copied
from an agent's captured context is not evidence.

`data.csv` is an optional command-input index, not an artifact inventory. Each
row is consumed through a `<name>` token in a recorded command. A generated
data output may be indexed when another recorded command consumes it as an
input. Entry-local scripts and images are never indexed. Research-log review
checks this index discipline and presentation-like content outside the
approved forms; validation does not audit those authoring rules.

The validator does not infer evidence from narrative meaning or from the mere
existence of a file, command, script, number, or citation. Synthesis and prose
sections, qualitative observations, interpretations, conclusions, decisions,
and unmarked numerical prose are outside agent-led validation.

Presented items and artifacts have a many-to-many relationship. One artifact
may support several presented items, and one presented item may draw on several
artifacts. A prose table does not need a separate artifact for every cell.
Each marked statistic, Markdown table, and generated-output block has one row
in the entry folder's `evidence.csv`, which records its retained source or
sources. Artifact links, image embeds, and artifact collections do not use this
record because they must resolve directly through their recorded workflow.
Validation discovers presented items mechanically and verifies their declared
associations mechanically when reliable, with bounded semantic fallback. An
association must establish logical equivalence in the relevant context;
finding the same numeral in an unrelated field is not sufficient. Rounding,
reformatting, equivalent numeric notation, and lossless selection or ordering
are permitted. A derived result must itself come from a retained workflow
artifact rather than an ad hoc calculation made while drafting.

Structured-source locators may select a container root, exact records or
aligned arrays, relative fields, and a closed set of dataset properties such as
shape and size. Validation uses safe readers and bounded extraction for these
declarations. It never deserializes pickle evidence; a workflow that would
otherwise expose evidence only through a pickle object must retain a CSV or
JSON summary produced by an explicit command.

Every presented project-generated item must trace to retained output produced
by an identified workflow and through its code, configuration, direct inputs,
and upstream generated inputs until the provenance graph reaches terminal
source inputs. Shared parts of that graph are checked once and reused across
dependent targets.

A maintained summary may present marked statistics but does not originate new
statistics. Each presented summary statistic must trace semantically to
logically equivalent evidence in one experimental entry section. The log-level
`evidence.csv` records this association. Determining whether qualitative summary
points come from entries belongs to research-log review rather than validation.
Summaries do not contain tables, images, generated-output blocks, or artifact
links.

An external source is checked only when it is a required workflow input. Record
its identity and actual use, plus a retained copy or stable source identity
when retention is impractical. Validation does not assess the source's
scientific trustworthiness or general external claims.

Validation does not establish that a scientific method was appropriate, an
interpretation is correct, or a conclusion is scientifically verified. Those
remain part of continuous researcher-led scientific review.

## Validation Method

Validation is mechanical first. The validation agent runs deterministic tools
for discovery, structural inspection, provenance-graph traversal, incremental
reuse, comparison, scope reconciliation, and report generation wherever those
checks can be made reliably. Tools report both their result and the unresolved
context needed for review; this may include compact extracted snippets that
reduce the material an agent must read. Such tools assist semantic judgment
without replacing it, and they do not convert ambiguity into success.

Incremental standard validation begins by comparing the current validation
input with compatible completed state. When all material identities and the
validation rules are unchanged, the prior completed outcome remains current,
including any failures. The agent reports that outcome without preparing
semantic review or repeating completed checks.

Use bounded semantic judgment when a check depends on meaning or a mechanical
result is ambiguous. This includes logical-equivalence checks that cannot be
expressed reliably as a deterministic comparison, custom artifact structures,
and assessment of apparent failures before they are reported. Semantic review
stays within the mechanically discovered scope and does not search narrative
prose for additional evidence or judge scientific interpretation.

Executing recorded research commands is distinct from running validation
tools. Standard validation runs validation tools but never executes research
workflows. Reproduction validation may execute eligible recorded workflows
into temporary outputs, then uses deterministic comparison with bounded
semantic fallback. The validation agent reads `evidence.csv` as a declaration
to verify and never modifies it.

## Validation Modes

Standard and reproduction validation are modes, not fixed commands. Requests
such as "validate this research log" and "check whether this research log is
reproducible" are example ways to select them. The researcher may limit a run
to named entries, targets, evidence classes, or checks. Record the requested
scope, reconcile the complete inventory within it, and leave results outside it
unchanged rather than presenting them as newly checked.

Standard validation inventories presented evidence and research-log-owned
materials, then checks the retained artifacts and provenance dependencies
without executing or importing research scripts. Every run reconciles the
requested scope mechanically. An unchanged complete input identity satisfies
this requirement and permits reuse of the whole completed outcome. When only
part of the input changes, reuse every unaffected completed result and recheck
only affected targets and dependent scopes. This applies equally to prior
successes and failures.

A validation-rules version change disables incremental reuse and requires
complete non-incremental validation of the requested scope. Missing or
incompatible state has the same effect. Reproduction validation never uses the
unchanged fast return because rerunning eligible workflows is the purpose of
that mode.

A validation run covers the complete discoverable material within its
requested scope even when it finds failures. Correctly reporting `FAIL` is a
successful execution of the validation workflow.

Reproduction validation reruns eligible workflows into a temporary location
and compares their outputs with the retained artifacts. Slow or
resource-intensive processes are excluded by default, including large data
processing, simulations, and neural-network training. Include them only when
the researcher explicitly requests them.

Run each distinct eligible invocation once, even when it produces several
artifacts. Compare every expected artifact independently and record a result on
its own row. If the invocation itself fails, every eligible artifact expected
from it reports `FAIL`. Retained evidence is read-only during validation and
must never be overwritten, replaced, or modified. Redirect all regenerated
outputs to a temporary location and stop if this cannot be done safely. Delete
the temporary outputs after comparison; they are working material, not
retained evidence.

Exact logical equality is the reproduction default:

- text and opaque files compare byte for byte;
- figures compare decoded pixels;
- structured formats such as CSV, JSON, FITS, and HDF5 compare logical
  structure and values.

An experimental entry section may use its optional `Validation:` label for
short persistent information directed to the validation agent. A reproduction exception names
the exact artifact and affected part, the comparison rule or tolerance, what
must still match, and the technical reason. An orphan-retention note names the
exact item and explains why it is intentionally retained despite not
contributing to presented evidence or a used provenance chain. The validation
agent may apply but must not create, modify, or relax these instructions. The
research agent may add or modify one only under researcher direction. A
changed instruction invalidates the affected cached decision. An instruction
cannot waive evidence provenance or checks outside its stated scope.

Write each orphan-retention note as an ordinary bullet that identifies one
exact file, directory, or script path, or one exact `<name>` token for an
indexed resource consumed by a recorded command. A directory note covers its
contents. Unused `data.csv` rows are review issues rather than exception
candidates.

Within an entry section, place `Validation:` after `Decisions:` when present,
or after the last evidence, interpretation, or uncertainty label otherwise,
and before `Follow-up:`.

## Validation Targets

Validation is scoped by entry document and stable entry ID. Split entry
documents such as `e002a` and `e002b` remain separate.

Presented evidence items and report targets are distinct. Each identified
supporting artifact receives one row in `validation.md`. Markdown tables,
generated-output blocks, and marked statistics map to their supporting
artifact row or rows through `evidence.csv`, so several presented items may
share a row. One table row in `evidence.csv` may contribute to several artifact
targets. The validator must verify each declared association rather than accept
a merely plausible artifact.

Generating scripts, commands, configurations, direct inputs, and upstream
artifacts are provenance dependencies rather than separate targets. Their
applicable checks are incorporated into the supporting-artifact result. This
validates a script as used in the evidenced workflow, not every code path or
general behavior.

A retained directory or dataset collection may use one row when the entry
presents it as a single output. Members presented separately or with different
results receive separate rows.

When a marked statistic, Markdown table, or generated-output block in an
experimental section has no valid
`evidence.csv` row, or its row has no identifiable retained source, create an
explicit unprovenanced-evidence row for the presented item. Keep one whole
Markdown table or generated-output block as one row rather than creating rows
for individual cells or lines. A declared source that is missing receives its
normal artifact target and applicable Integrity and Provenance failures.
`evidence.csv` itself does not receive a target row. Synthesis and prose
sections have no evidence targets. An invalid section receives one failed
structural target and no targets for its contents. An entry containing no
experimental sections may still have an orphan-inventory failure.

Each entry table includes the exact preceding Markdown heading in a `Section`
column. Use the document title for material before the first second-level
heading, and list all exact preceding headings when one target supports several
sections. Labels such as `Results:` or `Observations:` are not section names.
The orphan inventory row described below uses `-` because it covers an
ownership scope rather than a Markdown section.

The Summary section of `validation.md` is separate from entry targets. It
contains one row per presented summary statistic and checks only its
summary-to-entry provenance. A summary statistic must draw from exactly one
entry and one section and must not originate a new derived value.

Standard validation also inventories research-log-owned artifacts and scripts,
plus resources from `data.csv` that recorded commands consume. An item that is
neither presented nor reachable from the provenance graph of presented
evidence is an orphan candidate. A wholly unused `data.csv` row belongs to
research-log review instead. A valid indexed resource used by a recorded
command remains an orphan candidate when that workflow branch does not reach
presented evidence. The inventory is limited to designated entry-level and
log-level research material, including external targets exposed through
log-owned symlinks. A direct external path may be a provenance dependency but
is not part of orphan inventory, and validation does not sweep its surrounding
directory. Evidence-association and validation records are not orphan
candidates.

Script reachability follows mechanically resolvable dependencies transitively.
Resolution uses explicit source values and execution structure, including local
imports, file-relative script paths, interpreter or process launches, and
language-native source or include forms. Static Python import-path additions
use Python's effective path order, including the reversal produced by
consecutive `sys.path.insert(0, ...)` calls; a shadowed same-named module is not
treated as used. Python wrappers that invoke named MATLAB functions through a
static `addpath(...)` directory are also part of this graph. A script invoked
by a recorded command and its transitive code dependencies are used; artifacts
produced by that command remain subject to ordinary evidence reachability.
Resolution does not infer dependency roles from option names. Ambiguous dynamic
relationships remain bounded semantic orphan decisions whose outcomes are
retained for incremental validation.

Cross-log reachability is reconciled through a repository-level reverse-
dependency index. The index assigns each log-relative or symlink-owned path to
its owning log and records incoming use from active workflows in other logs:
recorded commands, presented-evidence associations, completed validation
dependencies, and their mechanically resolvable script closure. Dormant code
does not create use merely because it contains a possible reference. An
incoming dependency removes the owned item from orphan scope; it does not make
the consuming result valid evidence or replace that log's provenance checks.
Only paths owned by a maintained research log participate in this reverse
mapping.

After semantic producer confirmation, extend the retained provenance chain
through upstream producing commands and transitive local script dependencies.
Match a command to a retained artifact mechanically only when the command names
that artifact exactly. A shared output directory does not establish which
command produced each artifact beneath it; resolve that association
semantically. Once the command is established as part of a used workflow, its
declared sibling outputs and shell capture logs are part of the same retained
workflow. A later command's input connects an upstream generated artifact and
its producer. Directory dependencies connect only their reviewed material
members; an unscoped shared directory does not connect every child.
An unresolved orphan must not also be a dependency of a successful check;
record rendering and linting reject that contradiction.

An unexplained orphan creates one catch-all row per affected ownership scope,
not one row per item. Use `Orphaned artifacts, scripts, and references` as the
target, `-` for Section, `N/A` for Integrity and Reproducibility, `FAIL` for
Provenance, and the unresolved item count in Notes. Itemized findings belong in
`validation-failures.md`. An accepted `Validation:` instruction removes the
item from the failure count and does not create a successful exception row.

When stronger evidence does not establish ownership, entry-local material in a
folder with one entry Markdown file belongs to that entry. Entry-local material
in a folder with several entry Markdown files belongs to an entry-global scope.
Log-level material belongs to a log-level scope.

## Checks And Results

Every entry target uses three peer checks:

| Check | Successful result means |
| --- | --- |
| Integrity | The target exists, is accessible, and passes type-appropriate structural checks. Structured artifacts open read-only, figures decode or render, and declared collections have the expected membership or completeness. |
| Provenance | Every presented item associated with the target is logically equivalent in context, and the target has a complete graph through its recorded workflow, code, configuration, inputs, upstream artifacts, and terminal source inputs. |
| Reproducibility | The eligible target was regenerated and matched the retained artifact under the declared comparison rule. |

An external or compiled implementation may satisfy the provenance inspection
boundary without static inspection of its internals when its exact dependency
and version or build are identified, the recorded invocation is well formed,
and the execution record names that implementation. Disclose that boundary in
Notes. A missing or ambiguous implementation identity fails Provenance.

A temporary access failure for an otherwise well-identified external source is
not itself a validation failure. Note the access boundary. A missing required
input identity, retained copy, or stable source identity fails Provenance.

A successful check records its last-success date. An unsuccessful check reports
`FAIL`. Reproducibility uses `-` when it has no current result and `N/A` when it
does not meaningfully apply. Standard validation does not replace `-` with a
date. Requested reproduction always reruns eligible workflows within its
requested scope.

If dependencies of a prior reproduction result change and reproduction is not
requested, replace its date with `-`; do not report `FAIL` because reproduction
was not attempted. Only `FAIL`, `-`, and `N/A` use inline code formatting in
report and summary cells. Successful dates and results use ordinary text.

Every standard run mechanically reconciles the current entries, headings,
presented artifacts, Markdown tables, generated-output blocks, marked
statistics, applicable `evidence.csv` rows, summary statistics, collections,
and orphan inventory before reusing a prior result. A complete unchanged input
snapshot establishes this without repeating semantic decisions. When the
snapshot changes, add new targets, confirm renames, retain missing targets as
failures, remove obsolete evidence uses, and remove a target row only when no
presented item depends on it.

## Validation Records

### Evidence Associations

`data.csv` indexes command inputs; `evidence.csv` maps presented evidence to
its immediate retained sources.

An entry folder uses `evidence.csv` when an experimental section presents at
least one marked statistic, Markdown table, or generated-output block. Use one
row per presented item and this exact header:

```csv
entry,section,kind,evidence,sources,transformation
```

`entry` is the owning entry document ID and distinguishes documents in a split
entry folder. `section` is the exact preceding Markdown heading. `kind` is
`statistic`, `table`, or `output`. `evidence` is a compact content-derived
selector: use the marked numerical expression for a statistic, the ordered
column headings for a table, or the first distinctive non-empty line for an
output block. Add minimal surrounding wording or an occurrence number only
when otherwise identical items occur in the same section.

`sources` names the immediate retained source or sources. Use paths relative to
the entry root, `<log>/...` for log-level material, or an exact `<name>` token
from the entry's `data.csv`. Do not place absolute paths, URLs, or object-store
URIs directly in this record. Separate several source specifications with
` | ` and separate a source from its optional locator with ` :: `. A statistic
names exactly one retained source artifact, an output block names exactly one
retained command log, and a table names one or more retained source artifacts.

Omit the locator only when the whole artifact narrowly and unambiguously
supports the evidence. Otherwise add a stable, mechanically unambiguous source
locator that identifies the relevant region well enough for a validation tool
to extract bounded source context. Prefer row keys and fields, structured key
paths, datasets, records, or distinctive text regions over line numbers. The
research-logging skill owns the operational locator syntax. Use
`transformation` only when needed to describe selection, ordering, table
assembly, rounding, or equivalent formatting. It may not describe a new
calculation; a derived result must already exist in its retained source.

For example:

```csv
entry,section,kind,evidence,sources,transformation
e004,Model comparison,statistic,14.3% lower,"data/comparison.csv :: row=standardized; field=mse_reduction_percent",
e004,Model comparison,table,"model,mse,relative change","data/comparison.csv :: row=baseline|standardized; fields=model|mse|relative_change","Rows selected and values rounded to three decimals"
```

The log-level `evidence.csv` maps every presented summary statistic to exactly
one supporting entry and section. `statistic` uses the same content-derived
selector rule as an entry statistic. Use this exact header:

```csv
statistic,entry,section,transformation
```

For example:

```csv
statistic,entry,section,transformation
14% lower,e004,Model comparison,Rounded from 14.3% to a whole percentage
```

These records are durable research material, not validation results. A
research agent creates, updates, and removes rows with the presented evidence.
It may delete a header-only file after removing its last required row. A
validation agent reads and verifies the records but never modifies, deletes,
or rebuilds them. A research agent may rebuild a missing or damaged record from
retained evidence and workflows but reports any association it cannot
establish confidently rather than guessing. Missing, malformed, ambiguous,
stale, or incorrect required rows fail Provenance. Validation reports the
issue rather than repairing it.

Research-log review checks that presented evidence and these records follow the
required format. Validation checks that declared sources and locators resolve,
that the evidence is logically equivalent to the source content under any
declared transformation, and that the provenance chain is complete. Duplicate
or extra rows are review issues. Do not retain an empty record.

### Validation Report

`<log>/validation.md` is the source of truth for completed validation results.
The report records the log and requested scope, report-update date, validation
mode, and validation-rules version. No single overall validation result is
reported for the log. Row results and counts show what succeeded and what
needs attention.

Report counts are literal table-row counts: total and failed Summary-statistic
rows, total and failed target rows, number of entry documents, and number of
scopes containing a failed row. A row with several failed dimensions counts
once.

Use this table in the dedicated Summary section:

| Statistic | Entry | Section | Provenance |
| --- | --- | --- | --- |
| `<presented statistic with identifying context>` | <supporting entry ID> | <exact supporting entry heading> | 2026-08-07 |

Use one row per presented summary statistic. Include enough surrounding wording
in Statistic to distinguish its meaning. Entry and Section are singular.
Missing support uses `-` for Entry and Section and `FAIL` for Provenance. The
table has no Notes column.

Use this table under each entry identity, title, and file path:

| Target | Section | Integrity | Provenance | Reproducibility | Notes |
| --- | --- | --- | --- | --- | --- |
| <artifact path or evidence identity> | <exact heading> | 2026-08-07 | 2026-08-07 | `-` | `-` |
| Orphaned artifacts, scripts, and references | `-` | `N/A` | `FAIL` | `N/A` | 4 unresolved items |

Notes remain compact and descriptive. They may identify the statistics,
tables, plots, or output blocks supported by a target; summarize an applied
comparison exception; disclose a static-inspection boundary; or provide a
short locator. Findings, expected-versus-observed conditions, diagnostic
output, and remediation belong in `validation-failures.md`.

### Failure Details

`<log>/validation-failures.md` is an optional persistent remediation document,
not a validation result. The validator creates or rebuilds it when failures
exist and links it from `validation.md`. Its absence does not establish that
validation passed.

Include `## Summary` only when summary-statistic failures exist, followed by
only the entry, entry-global, or log-level sections with unresolved findings,
ordered as in `validation.md`. Use one third-level block per failed target.
Repeat the `Check:` and `Finding:` pair when one target fails several
dimensions:

```md
### outputs/predictions.csv

- Check: Integrity
- Finding: The CSV has inconsistent column counts.

- Check: Provenance
- Finding: The generating script is not identified.
```

Keep only current findings and focused diagnostic excerpts. Do not retain
successful checks, full command logs, or failure history in this file.
Itemize each unresolved path or `data.csv` reference inside an orphan catch-all
block.

The research agent may add context, improve descriptions, record progress, and
remove a pair it believes it has resolved. It removes empty target and scope
headings as work proceeds. When the queue is empty, it leaves:

```md
# Validation Failures

None.
```

This means only that no remediation items remain. It does not replace
revalidation or change the last completed result. The research agent does not
delete the file or its report link. On the next run, the validator recreates
unresolved findings or, when no failures remain, deletes the file and removes
the link.

### Incremental State

`<log>/validation-state.json` is a persistent machine-readable cache containing
the input identities, completed outcomes, dependency relationships, and
disposable resolutions needed for incremental reuse. It stores metadata and
fingerprints, not copies of research files or evidence.

Keep the schema and validation-rules version; requested scope and mode; target
and check identities; and the complete material inventory needed to detect
changed, added, removed, or renamed inputs. The inventory includes presented
items, applicable `evidence.csv` rows, supporting artifacts, dependencies,
collection membership, applicable `Validation:` instructions, and material
considered by orphan discovery. Record normalized provenance nodes and edges
with their minimum relevant path, role, type, size, modification time, or hash.
Keep one shared material inventory while retaining the identity used by each
completed outcome in that outcome's dependency snapshot.

Cache every completed check outcome, whether successful or failed, together
with a per-outcome snapshot of its dependency identities and the discovered
dependency contract. The contract changes when presentation or association
scope changes or when command discovery adds or removes a producer or input.
Compare both the contract and every dependency identity before carrying an
outcome forward. Never refresh an old outcome's dependency snapshot merely
because another outcome caused the state file to be rendered. Also retain the
minimum disposable resolution needed to reuse reviewed Summary mappings,
selected collection members, and orphan dispositions. For a failed outcome,
retain only the focused current finding needed to return or reconstruct that
outcome; detailed diagnostics and remediation remain in
`validation-failures.md`. Do not copy
evidence selectors, source declarations, durable locators, transformations,
report prose, agent reasoning, scientific results, artifact contents, or
history.

When the requested standard scope, complete material inventory, dependency
identities, and validation-rules version are unchanged, reuse the complete
prior outcome and dates. Return its counts and failures without preparing a
semantic-review queue or rerendering intact validation records. Generated
validation records, the summary's `## Validation` projection, and
disclosure-oriented `## AI Use` content are not validation inputs.

When material changes, invalidate only outcomes reached by the changed
identities or whose discovered dependency contract changed, and reuse
unaffected successes and failures. A changed presented item, changed
`evidence.csv` association, or newly discovered producer command therefore
reopens its affected outcomes. Re-read affected durable associations, use
disposable fast locators first when they remain valid, and verify logical
equivalence in context. Reassess collection or orphan decisions only when
their inventory, use graph, or applicable instruction changes. Cache an
accepted orphan instruction by the item and note fingerprints.

A validation-rules version change, missing state, or incompatible state causes
complete non-incremental validation of the requested scope. This does not
itself make the research log fail validation.

The repository root's `.research-log-validation-index.json` is a separate
agent-only cache for cross-log ownership and reverse dependencies. Refresh it
from all maintained logs before orphan reconciliation. Reuse it when its input
metadata and rules version are unchanged; when inputs change, rehash only
changed index inputs. Each log's validation fingerprint includes only the
incoming dependency slice owned by that log, so a change elsewhere does not
invalidate unrelated validation outcomes. A changed incoming slice makes the
affected owner's orphan inventory stale and requires its reconciliation.

Whole-log standard validation covers every entry document, entry-global and
log-level orphan scope, presented summary statistic, and required provenance
dependency. A run limited to one entry document includes its complete
dependency closure and, in a single-document folder, its entry-local orphan
inventory. Include an entry-global orphan scope only when its folder or the
whole log is requested. Include the log-level orphan scope only when it is
named or the whole log is requested. Dependencies outside the requested scope
may be checked when required without updating unrelated target results.

If a scoped run finds that a changed shared dependency invalidates prior
results outside its requested scope, invalidate the reusable state and mark the
affected summary scope rows STALE. Leave their detailed rows unchanged and do
not report a failure until those scopes are validated.

### Summary Projection

Each maintained research-log summary places `## Validation` immediately above
`## AI Use`, or at the bottom of the document when there is no `## AI Use`
section. It links to `validation.md`, records summary-statistic provenance on a
compact line, and then uses a scope table:

```md
Summary statistics: 2026-08-07 — 7 checked; 0 failures

| Scope | Last checked | Integrity & Provenance | Reproducibility |
| --- | --- | --- | --- |
```

Before the first completed validation, omit the report link and use `NOT RUN`
for applicable projection values.

Summary statistics uses a date and checked/failure counts on success, `` `FAIL`
- <failed> of <total> statistics failed``, NOT RUN before validation, `N/A`
when there are no applicable statistics, or STALE when revalidation is
required.

Each entry document has one row. `Last checked` records its most recent
standard check. Integrity & Provenance reports `<count> targets checked; 0
failures`, `` `FAIL` - <failed> of <total> targets failed``, NOT RUN, or `N/A`.
Reproducibility reports `<current> of <eligible> eligible targets reproduced`,
`` `FAIL` - <failed> of <eligible> eligible targets failed``, `-`, or `N/A`.
Add an entry-global or log-level row only while that orphan scope has an
unresolved failure or stale result, and remove it after clean revalidation.
Detailed dates and failures remain in `validation.md`.

## Agent Responsibilities And Freshness

Validation runs in a fresh agent task that did not implement the work and does
not inherit its implementation conversation. The validation agent is read-only
with respect to research entries, scripts, retained evidence, and scientific
summary content, including entry-level and log-level `evidence.csv` records. It
may update only `validation.md`, `validation-state.json`,
`validation-failures.md`, and the summary's `## Validation` section. It records
failures without repairing them and does not claim organizational independence
or independent scientific replication.

The research agent may edit research content, maintain
`evidence.csv` records and `validation-failures.md`, and mark summary
projections stale. Under researcher direction, it may also add or revise an
entry section's `Validation:` note. It never modifies `validation.md` or
`validation-state.json`, assigns a completed validation result, or deletes
`validation-failures.md`.

In the maintained summary's `## Validation` section, staleness follows the
affected scope:

- changing presented evidence, its recorded workflow or dependencies, retained
  artifacts, its applicable `evidence.csv` row, research-log-owned inventory,
  or an applicable `Validation:` instruction marks the affected entry,
  entry-global, or log-level row STALE;
- changing a presented summary statistic or its log-level `evidence.csv` row
  marks Summary statistics STALE;
- changing qualitative entry or summary prose alone does not make agent-led
  validation stale;
- changing synthesis or prose sections does not make agent-led validation
  stale;
- a changed shared dependency may mark several dependent scope rows STALE;
- changes confined to `## Validation` or disclosure-oriented `## AI Use` do not
  make validation stale.

While the research agent is addressing findings in
`validation-failures.md`, it leaves an affected summary projection at `FAIL`.
After it believes every finding for an entry, inventory, or Summary-statistic
scope is resolved and removes the last corresponding working item, it changes
that projection to STALE to request revalidation. `validation.md` continues to
show the last completed result until the validation agent reruns the checks.

Validation covers files as they exist on disk when inspected. Relevant files
must remain stable during each check. Compare their minimum content identities,
such as size, modification time, or hash, before and after the check. If a file
changes, do not update the affected result or clear its stale warning; rerun the
check when the files are stable. Concurrent change is a task blocker, not a
research-log failure. Validation does not inspect or report source-control
status.

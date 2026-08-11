# Validate Operation Instructions

Use this operation for agent-led standard validation or a requested
reproducibility check of a research log.

Run validation in a fresh task that did not implement the research work. Read
`skills/research-logging/references/file-validation-records.md` for record
ownership and `skills/research-logging/references/file-summary-validation.md`
before a canonical run. Use
`skills/research-logging/references/file-presented-evidence.md` only when a
presented-item association or source locator needs interpretation.

## Boundaries

- Treat entries, scripts, artifacts, scientific summary content,
  `evidence.csv`, and entry `Validation:` notes as read-only.
- Write only validation working files, validation records, and the summary's
  `## Validation` section. Never edit `evidence.csv` to resolve a finding.
- Validate files as they exist on disk. Ignore source-control status.
- Standard validation may inspect research code but never imports or executes
  it. Run a recorded research command only during requested reproduction.
- Keep semantic review within the evidence scope discovered by the tool. Do
  not search narrative prose for additional evidence or judge scientific
  methods, interpretations, or conclusions.

## Inputs

Establish the maintained summary, standard or reproduction mode, requested
scope, working directory, canonical record directory, and prior
`validation-state.json`. Standard mode is the default. The researcher may
limit a diagnostic run to named entries, targets, evidence classes, or checks.

Use the research project's required Python interpreter or launcher. Read its
applicable project instructions before invoking validation; do not silently
use system `python3` when the project defines an environment with required safe
artifact readers.

Use complete scope for canonical records. Run a limited diagnostic into an
empty temporary record directory and do not update the maintained summary.

## Mechanical Workflow

Resolve `scripts/research_log_validation.py` relative to this skill package.
Keep scan, review, decisions, and adjudication files outside the research log.

```bash
<project-python> <validation-tool> index \
  --project-root <project-root> \
  --output <project-root>/.research-log-validation-index.json

<project-python> <validation-tool> scan \
  --summary <log-summary> \
  --output <work-dir>/scan.json \
  --state <log>/validation-state.json \
  --repository-index <project-root>/.research-log-validation-index.json \
  --mode <standard-or-reproduction>

<project-python> <validation-tool> prepare \
  --scan <work-dir>/scan.json \
  --output <work-dir>/adjudication.json \
  --date YYYY-MM-DD \
  --mode <standard-or-reproduction>

<project-python> <validation-tool> review \
  --scan <work-dir>/scan.json \
  --adjudication <work-dir>/adjudication.json \
  --output <work-dir>/review.md
```

Omit `--state` when no prior state exists. The separate index step may be run
once before validating several logs; `scan` also refreshes the named index
when its inputs change. The index uses only active cross-log consumers and
their dependency closure. An incoming use removes the owned path from orphan
scope but does not establish evidence provenance in the consuming log.

`scan` discovers approved presented evidence, resolves retained sources and
recorded workflow paths, checks known
structures, fingerprints the complete material inventory and dependencies,
and identifies reusable outcomes.

Inspect the scan metrics before preparing adjudication. When a standard scan
reports `incremental_status: unchanged`, return the cached date, scope, counts,
and failures immediately. Do not run `prepare`, create a review packet, perform
semantic review, render records, or update the summary. A reproduction request
never takes this fast return. A missing or incompatible state, or
`incremental_status: rules-changed`, requires complete non-incremental
validation. Otherwise continue with only the checks and dispositions the scan
did not reuse.

`prepare` fills reliable results and queues only ambiguous or failed checks.
`review` extracts bounded entry context, locator-selected source context,
structural results, candidate commands, and collection candidates. Read the
packet before opening raw files. Filter a large packet with `--entry <scope>`
or `--kind <queue-kind>`.

Command discovery is value-driven. Resolve literal paths, path fragments,
shell targets, and `<name>` values through `data.csv`; do not infer workflow
roles from parameter-name vocabulary. When path direction cannot be established
mechanically, inspect the bounded command and producer-code context and retain
that semantic resolution in validation state.

Follow local script dependencies transitively before identifying orphan
candidates. Use mechanically explicit imports, file-relative script values,
process or interpreter launches, static Python import-path additions,
Python-to-MATLAB calls through a static `addpath(...)` directory, and
language-native source or include forms. Apply Python path mutations in runtime
order: later `sys.path.insert(0, ...)` calls take precedence, and a shadowed
same-named module remains unused. Treat a script invoked by a recorded command
and its transitive code dependencies as used; this does not make its outputs
evidence. Treat an unresolved dynamic relationship as a bounded semantic
orphan decision.

Use the repository reverse-dependency index when reconciling orphans. Include
recorded commands, presented-evidence associations, completed validation
dependencies, and their mechanically resolved transitive scripts. Do not let
a dormant script protect files in another log merely because it contains a
possible reference. Fingerprint only the current log's incoming edge slice as
part of that log's incremental state.

After a semantic producer decision, extend that successful check through
upstream recorded producers and transitive local script dependencies before
finalizing orphan dispositions. Preserve the resulting dependency closure in
state. Match a command to an artifact mechanically only when the command names
that artifact exactly; a shared output directory requires semantic producer
selection. Do not render records while an unresolved orphan is also a
dependency of a successful check.

Once a command is established as part of a used workflow, retain its declared
sibling outputs and shell capture logs in the same dependency closure. Follow
an upstream output when a later used command consumes it. For directory inputs
and outputs, carry only the reviewed member scope into orphan reconciliation;
do not treat an unscoped shared directory as proof that every child belongs to
the workflow.

Limit artifact and script orphan inventory to log-relative paths and targets
reached through log-owned symlinks. Treat direct external paths as provenance
dependencies only; do not inventory their surrounding directories for orphans.

## Review And Decisions

Resolve every queue item before rendering:

- `semantic_fallback`: decide only the stated logical-equivalence, custom
  structure, or provenance question.
- `semantic_provenance`: confirm that the Summary statistic is supported by
  the declared experimental entry section.
- `mechanical_failure`: distinguish a real discrepancy from a locator,
  formatting, unit, runtime, or tool limitation before retaining a failure.
- `collection_scope`: select only the relative files materially consumed from
  each declared directory.
- `orphan_candidates`: retain one catch-all failure only for items that are
  genuinely outside a used evidence workflow and lack an applicable existing
  `Validation:` note. Classify every queued item; do not decide the catch-all
  as an indivisible group.
- `reproduction`: run the recorded invocation safely and compare the named
  target, or record why an eligible target was not run.

Record reviewed outcomes in a compact decisions JSON file. Do not write a
one-off adjudication program or patch the full adjudication JSON.

```json
{
  "schema_version": 1,
  "actions": [
    {
      "match": {"entry": "e003", "identity": "retained/result.csv"},
      "decision": "fail",
      "findings": {"Provenance": "Focused failure explanation."}
    },
    {"match": {"kind": "semantic_fallback"}, "decision": "pass"}
  ]
}
```

Actions apply in order to unresolved items. Put exact exceptions before a
reviewed category-wide action. Match by `kind`, `entry`, `identity`, their
exact combination, or a `targets` list of exact `entry`/`identity` pairs.
When all evidence subchecks pass mechanically but another component remains
unresolved, a `fail` action must name that actual component with
`"failure_basis": "workflow"` or `"failure_basis": "integrity"`. Do not
reuse an evidence-value finding for such a row; the tool rejects a failure
basis that is already mechanically resolved.

For each `orphan_candidates` item, use the `orphan` decision and list only the
candidate identities that remain unresolved. The tool records every other
candidate in that queue item as accepted, retains prior item decisions, and
derives the catch-all count and itemized findings. An empty `unresolved` list
removes the catch-all row when no previously unresolved items remain.

```json
{
  "match": {
    "entry": "Log level",
    "identity": "Orphaned artifacts, scripts, and references"
  },
  "decision": "orphan",
  "unresolved": ["docs/example/scripts/unused.py"]
}
```

Use `support` with a one-based packet `candidate` for Summary support; `pass`
or `fail` for checked entry targets; `scope` with a `members` mapping only when
the prepared row already has complete checked results and collection
membership is its sole unresolved question; `pass` with `members` when the
same row also needs semantic approval; `keep` only when the prepared row
already has complete checked results and findings. Do not use `pass`, `fail`,
`keep`, or `scope` for orphan candidates. For a `reproduction` item, use `reproduced`,
`reproduction-fail`, `not-run`, or `not-applicable`. A member scope may be an
exact relative-path list or
`{"glob": "relative/pattern"}` when the reviewed pattern selects exactly the
material files. Use `add_dependencies`, `remove_dependencies`, or
`copy_dependencies_from` only for paths already resolved by the scan.

```bash
<project-python> <validation-tool> decide \
  --scan <work-dir>/scan.json \
  --adjudication <work-dir>/adjudication.json \
  --decisions <work-dir>/decisions.json \
  --output <work-dir>/decided.json
```

Use the reported remaining count to identify unhandled items. A later
`decide` call may use the latest output as its adjudication input. Never
calculate a missing derived result from other artifact values to make an
association pass.

## Checks

- Integrity verifies existence, accessibility, and type-appropriate
  structural validity.
- Provenance verifies each presented association and follows the retained
  target through its recorded code, configuration, direct inputs, and upstream
  artifacts. Rounding, equivalent notation, lossless selection, ordering, and
  formatting may be logically equivalent. A derived result must already exist
  in retained workflow output.
- Summary validation checks only marked statistics and their declared
  summary-to-entry associations.

Repeat mechanical discovery and scope reconciliation on every run. Reuse any
completed outcome, including `FAIL`, only when state shows that all material
dependencies and applicable inventory are unchanged. Compare the outcome's
stored dependency contract and each of its dependency identities with the
current scan. Reopen the outcome if a presented item, evidence association, or
producer command changed. Do not carry an old result forward while replacing
its dependency snapshot with current identities. Reassess collection and
orphan dispositions only when their inventory, use graph, or instruction
changes. Treat a correct `FAIL` report as a successful execution of the
validation workflow.

## Reproduction

Run reproduction only when requested. Exclude slow or resource-intensive
workflows unless the researcher includes them explicitly. Redirect every
output to a temporary location and stop if retained evidence cannot remain
untouched. Run each distinct eligible invocation once and compare each output
independently. Prefer byte comparison for text and opaque files, decoded pixels
for figures, and logical structure and values for structured files. Apply only
pre-existing, precisely scoped `Validation:` comparison instructions. Delete
temporary regenerated outputs after comparison.

## Render And Complete

```bash
<project-python> <validation-tool> render \
  --scan <work-dir>/scan.json \
  --adjudication <work-dir>/decided.json \
  --output-dir <record-dir>

<project-python> <validation-tool> lint \
  --output-dir <record-dir> \
  --scan <work-dir>/scan.json

<project-python> <validation-tool> update-summary \
  --summary <log-summary> \
  --output-dir <record-dir>
```

Correct decisions and rerender; never hand-edit generated validation records.
Update the maintained summary only after a complete canonical render passes
lint. Report the requested scope, elapsed time, mechanically completed checks,
semantic-review count, reused checks, report rows, failures, and files changed
during inspection. For an unchanged fast return, instead report the scan time
and cached date, scope, counts, and failures, and state that no semantic review
or record write occurred.

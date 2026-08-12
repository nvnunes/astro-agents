# Validate Operation Instructions

Use this operation for independent standard validation or a requested
reproducibility check. Run it in a fresh task that did not implement the
research work; successful execution or inspection during Record is not
validation. Validation may repeat relevant checks independently. Read
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
limit a diagnostic review to a named queue scope, exact target, or review kind.

Use the research project's required Python interpreter or launcher. Read its
applicable project instructions before invoking validation; do not silently
use system `python3` when the project defines an environment with required safe
artifact readers.

Use complete scope for canonical records. Keep a limited diagnostic's working
files in an empty temporary directory; do not render canonical records or
update the maintained summary.

## Mechanical Workflow

Resolve `scripts/research_log_validation.py` relative to this skill package.
Keep scan, review, decisions, and adjudication files outside the research log.

```bash
<project-python> <validation-tool> scan \
  --summary <log-summary> \
  --output <work-dir>/scan.json \
  --state <log>/validation-state.json \
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

Omit `--state` when no prior state exists. Each canonical render writes the
owning log's `validation-index.json`. Run the separate `index` step after
refreshing one or more logs to rebuild the disposable repository aggregate in
`.research-log-validation-index/`. A scan builds its repository view directly
from the current per-log slices; the aggregate is a disposable projection, not
an input authority. It contains only active cross-log consumers and their
dependency closure. An incoming use removes the owned path from orphan scope
but does not establish evidence provenance in the consuming log.

`scan` builds a replacement view from the per-log slices. It excludes the log
being refreshed, whose prior slice may be missing, stale, malformed, or use old
rules, and uses every current compatible slice from the other maintained logs.
The disposable aggregate remains a mechanically rebuilt projection of the
compatible canonical slice set. Missing, invalid, or incompatible other-log
slices make cross-log orphan reconciliation incomplete, so the scan withholds
orphan candidates until coverage is restored; they do not require an atomic
all-log generation.
Use `--repository-index` only when deliberately supplying an explicit
canonical graph view for isolated testing.

### Initialize Or Upgrade All Logs

For first-time initialization or a validation-rules upgrade, validate every
maintained log sequentially without prior state. Temporary mixed rules versions
are allowed. During that interval, scans rebuild per-log facts but withhold
orphan conclusions because the cross-log slice set is incomplete.

After every maintained log has a current compatible slice, rebuild the
disposable aggregate with `index`. Then run one reconciliation pass over every
log against the complete slice set and rebuild the aggregate once more.
Consumer-owned cross-log relationships do not require atomic repository
publication, rollback, or fixed-point generation.

Run `update-summary` after each canonical render. When the complete rollout
finishes, run an immediate state-backed `scan` for every log. Each unchanged
scan must reuse all completed checks, require no semantic review, and write
nothing.

`scan` discovers approved presented evidence, resolves retained sources and
recorded workflow paths, checks known
structures, fingerprints the complete material inventory and dependencies,
and identifies reusable outcomes.

Inspect the scan metrics before preparing adjudication. When a standard scan
reports `incremental_status: unchanged`, return the cached date, scope, counts,
and failures immediately. Run `update-summary` against the unchanged canonical
bundle so the dated snapshot is present and any legacy `STALE` values are
replaced; this writes nothing when the snapshot already matches. Do not run
`prepare`, create a review packet, perform semantic review, or render records.
A reproduction request never takes this fast return. A missing or incompatible state, or
`incremental_status: rules-changed`, requires complete non-incremental
validation. Otherwise continue with only the checks and dispositions the scan
did not reuse.

`prepare` fills reliable results and queues only ambiguous or failed checks.
`review` extracts bounded entry context, locator-selected source context,
structural results, candidate commands, and collection candidates. Read the
packet before opening raw files. Filter a diagnostic packet with
`--entry <scope>`, `--target <identity>`, or `--kind <queue-kind>`. Do not
render or publish a filtered diagnostic as canonical validation.

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

Use the canonical dependency graph when reconciling orphans. The repository
aggregate projects consumer-owned cross-log edges from the per-log slices. Include
recorded commands, presented-evidence associations, completed validation
dependencies, and their mechanically resolved transitive scripts. Do not let
a dormant script protect files in another log merely because it contains a
possible reference. Fingerprint only the current log's incoming edge slice as
part of that log's incremental state.

After a semantic producer decision, extend that successful check through
upstream recorded producers and transitive local script dependencies before
finalizing orphan dispositions. Preserve the resulting dependency closure in
state. If a reached generated input has multiple eligible recorded producers,
resolve the `upstream_producer` review item by binding that material to one
packet-listed invocation. Do not union alternatives. A scoped collection
follows both its selected members and its bound producer. Preserve every
binding in state; changes to the selected invocation or eligible candidate set
must reopen the result. Match a command to an artifact mechanically only when the command names
that artifact exactly; a shared output directory requires semantic producer
selection. Do not render records while an unresolved orphan is also a
dependency of a successful check.

Once a command is established as part of a used workflow, retain its declared
sibling outputs and shell capture logs in the same dependency closure. Follow
an upstream output when a later used command consumes it. Do not reverse this
relationship: a command that consumes a retained input is not its producer,
and its downstream workflow is not provenance for that input. For directory
inputs and outputs, carry only the reviewed member scope into orphan reconciliation;
do not treat an unscoped shared directory as proof that every child belongs to
the workflow. Protect an owned directory container when one reviewed child is
used, but do not extend the child's provenance through unrelated siblings or
other commands that share that container.

Limit artifact and script orphan inventory to log-relative paths and targets
reached through log-owned symlinks. Treat direct external paths as provenance
dependencies only; do not inventory their surrounding directories for orphans.
When a recorded command only consumes a direct external evidence target, treat
that target as a terminal source rather than requiring a recorded producer.
Treat the graph result as authoritative: a reported unresolved orphan must be
unreachable from every applicable root, and render or lint must reject a
contradictory classification.

## Review And Decisions

Resolve every queue item before rendering:

- Treat packet-listed immutable Integrity or Provenance failures as final
  failures. Do not use semantic review to pass them.
- `semantic_fallback`: decide only the stated logical-equivalence, custom
  structure, or provenance question. A locator-selected numeric match is a
  review candidate, not a mechanical Provenance pass; confirm that the selected
  field is logically equivalent in the presented context.
- `semantic_provenance`: confirm that the Summary statistic is supported by
  the declared experimental entry section.
- `mechanical_failure`: distinguish a real discrepancy from a locator,
  formatting, unit, runtime, or tool limitation before retaining a failure.
- `collection_scope`: select only the relative files materially consumed from
  each declared directory.
- `upstream_producer`: bind each listed generated material to one exact
  packet-listed invocation. Choose one producer per material; never merge
  alternative workflows.
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
  "schema_version": 4,
  "actions": [
    {
      "match": {
        "kind": "upstream_producer",
        "entry": "e003",
        "identity": "retained/result.csv"
      },
      "decision": "bind",
      "producer_bindings": [
        {
          "material": "retained/generated-input.h5",
          "invocation": "e003:L42:1:abc123"
        }
      ]
    },
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

For each `orphan_candidates` item, use the `orphan` decision and partition all
candidates among `unresolved`, `connected`, and `retained`. Use `connected`
only after bounded semantic review establishes that the item participates in
presented work but the relationship is not mechanically discoverable. That
decision becomes a fingerprinted semantic graph root and is invalidated when
its reviewed inputs change. A retained item instead names the SHA-256 of one
exact existing `Validation:` note printed in the packet. Do not retain an item
merely because the agent judges it useful. The tool derives the catch-all count
and itemized findings from the unresolved list.

```json
{
  "match": {
    "entry": "Log level",
    "identity": "Orphaned artifacts, scripts, and references"
  },
  "decision": "orphan",
  "unresolved": ["docs/example/scripts/unused.py"],
  "connected": ["docs/example/data/reviewed-dynamic-output.csv"],
  "retained": [
    {
      "identity": "docs/example/data/intentionally-retained.csv",
      "validation_note": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
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
When provenance needs semantic producer confirmation, add `"producer": N` to
select the one-based candidate command printed in the packet. The tool binds
that choice to the exact recorded invocation; a generic reviewed workflow is
not a valid producer.

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
  structural validity. Keep known unsupported or prohibited formats unresolved;
  generic readability is sufficient only for an explicitly opaque format.
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

The render also writes `<log>/validation-index.json`. After all intended logs
are rendered, rebuild the repository aggregate with `index`. The per-log slice
is the independently stageable record; `manifest.json` and `incoming.json` are
small disposable projections and may be rebuilt from the slices.

Rendering first builds and lints the complete generated record bundle in a
temporary directory. One repository advisory lock serializes canonical
validation operations. Under that lock, rendering verifies the complete
scanned input snapshot and atomically replaces each report, state, graph slice,
and optional failure file. The tool rejects noncurrent rules packets.

An interruption may leave a partial generated bundle. The next canonical
operation treats that bundle as unusable, performs fresh validation, and
rebuilds it. It does not roll back through a publication journal. The aggregate
is likewise disposable and rebuilt whenever its files or contributing slices
are inconsistent. Run `update-summary` last so its snapshot follows the
completed report.

## Extending The Tool

Keep graph ownership explicit when adding validation mechanics:

- `validation/graph.py` owns typed facts and invariants;
- `validation/graph_adapter.py` turns bounded scan and review
  results into graph facts;
- `validation/graph_queries.py` owns reachability, provenance,
  and orphan queries;
- `validation/graph_store.py` owns per-log slices and the
  repository aggregate;
- `validation/records.py` owns the repository advisory lock,
  record-bundle identities, and atomic generated-file replacement;
- `research_log_summary.py` owns maintained-summary UTF-8 validation and the
  generated validation snapshot;
- `validation/state.py` owns the persisted validation-state
  contract and decoder;
- `validation/contracts.py` owns typed lifecycle record shapes;
- `validation/cli.py` owns command-line arguments, command
  dispatch, and command-specific orchestration;
- `validation/runtime.py` composes the current schema versions
  and concrete scan, adjudication, render, and incremental policies;
- `validation/scan.py`, `validation/incremental.py`,
  `validation/adjudication.py`, and `validation/render.py` own their lifecycle
  assemblies;
- `validation/commands.py` owns recorded-command and local-script
  dependency discovery;
- `validation/evidence.py` owns bounded artifact inspection,
  locator extraction, and mechanical evidence equivalence;
- `validation/identities.py` owns scope-aware summary, entry, and
  generated-text identities;
- `research_log_validation.py` is only the executable CLI entrypoint.

Add a new dependency relationship once in the adapter, with its invalidating
inputs, then consume it through a graph query. Do not add a parallel closure
or orphan rule to review, rendering, state reuse, or repository indexing.
Add contract and end-to-end regression coverage for every new node, edge,
root, or persistence rule.

Correct decisions and rerender; never hand-edit generated validation records.
Update the maintained summary after a complete canonical render passes lint or
after an unchanged fast return against the current canonical bundle. Report
the requested scope, elapsed time, mechanically completed checks,
semantic-review count, reused checks, report rows, failures, and files changed
during inspection. For an unchanged fast return, instead report the scan time
and cached date, scope, counts, and failures, and state that no semantic review
or record write occurred.

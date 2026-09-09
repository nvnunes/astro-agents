# Repair Operation Instructions

Repair corrects an explicitly requested research-log defect. It does not perform
new research, choose new evidence, or revise scientific meaning. A failed
command, Validate result, or Review finding does not itself authorize Repair.
A valid advanced evidence presentation belongs to Record's definition mode.

## Execute One Target At A Time

Complete this loop for one authorized batch or named defect before investigating
the next. In a campaign, choose one log and begin as soon as its batch listing
identifies an authorized target. Do not first inventory all logs, diagnose all
batches, or build a separate queue from queryable results.

### 1. Select

For a validation finding, use the supplied result ID. If none is supplied:

```text
<skill>/scripts/log results show --path <log> --latest --kind full
```

`<log>` is the log directory, not its summary Markdown file. Pin the returned ID
for subsequent queries. List batches with the request's applicable selectors,
then select one whose findings are in scope:

```text
<skill>/scripts/log results show --path <log> --id <result-id> --view batches \
  [--entry <entry>] [--code <code>]
<skill>/scripts/log results batch --path <log> --id <result-id> --batch <batch-id>
```

Use CLI text views for validation-state inspection. Do not read, parse, or search
`validation.md`, `validation/results.json`, `validation/batches.json`, or the
inspection database, including through recursive filesystem searches. JSON
output is for scripts that consume it; do not copy queryable payloads into files
or parse JSON to rebuild reports.

For an uncached current publication, use `findings list` or `findings show` with
exact selectors. Do not run validation merely to populate the cache. No matching
findings ends the target only when a completed evaluation covers its current
state. Otherwise the target remains unresolved.

### 2. Diagnose

Start at the selected batch's stated blocker or inspection starting point. Read
only enough to establish a correction or a concrete reason it cannot proceed.
For a named defect without a batch, inspect that defect through its owning CLI.

```text
<skill>/scripts/log results finding --path <log> --id <result-id> --finding <check-id>
```

When command relationships are missing, inspect the batch's commands:

```text
<skill>/scripts/log results show --path <log> --id <result-id> \
  --view commands --batch <batch-id>
```

Retrieve missing detail through `results command`, `artifact`, or the printed
collection/value command. Follow a cursor only when more matching items are
needed. Use `--view chains` only for needed provenance membership. Read affected
source records only for needed information these views do not provide. Another
batch's details are relevant only when they help resolve the active target.

For `producer.missing` or `lineage.missing`, establish whether the apparent
producer declares the reported material and is admitted by discovery. For
lineage, also check that it precedes the consumer. Confirmation status alone
does not explain these codes. Diagnose the actual failed condition before
changing registrations or provenance structure.

### 3. Correct Or Set Aside

Apply the established correction through its owning CLI action when that action
can safely express it. Read only the matching case in **Repair Methods** below.
When no action can express the authorized correction, edit only the affected
non-validation source records under that section's contracts.

Ordinary Repair includes moving an unchanged command after its established
producer within the same document to correct an ordering defect without changing
scientific meaning. Changing document or entry boundaries requires explicit
Reorganize authorization. Removing superseded experimental work requires Replace
authorization.

Preserve presented evidence, tolerances, scientific meaning, and all unrelated
records and material. Keep stable evidence IDs unless the requested defect is
the ID and its replacement is explicit. Never hand-edit generated validation.

If retained evidence and the request do not establish the intended correction,
or the correction requires a researcher decision or additional authority, set
this target aside with the concrete reason. Do not invent a replacement source,
ID, transformation, origin boundary, interpretation, or execution confirmation.
In a campaign, continue independent authorized targets after recording the reason.
Ask the researcher when further work depends on their decision.

A failed correction command stops this target. Retry only after a revised,
evidence-backed hypothesis; do not repeat the mutation or bypass its precondition.
Use a printed diagnostic-inspection command for omitted failure details rather
than rerunning the correction. A diagnostic snapshot is not a validation result.
For `operation.lock.conflict`, report the supplied owner metadata once and stop
the target; do not retry, poll, inspect processes, or work around the lock.

### 4. Check The Correction

After a correction to a published batch, check it once using the original
published validation and requested batch IDs:

```text
<skill>/scripts/log validate-batch --path <log> --validation <validation-id> \
  --batch <batch-id>
```

For a defect with no published batch, use the owning bounded decoder or command
postcondition instead. Do not claim `complete_clear` without a batch result.

| Result | Next Action |
|---|---|
| `complete_clear` or `complete_findings` | Assess the reconciled current membership, remaining findings and overlaps. The result covers only that membership. |
| `incomplete` with `coverage_incomplete` | Use an already-authorized full validation to assess the correction. Without that authority, leave it unverified and report the needed validation. Do not infer repair failure or check overlapping batches to resolve the same coverage limit. |
| Other `incomplete` result | Resolve the reported evaluation failure before judging the correction. |

An incomplete check establishes neither success nor failure. Reuse completed
results while their scope and state remain applicable; assess uncovered work
separately. Repeat evaluation only after a correction, relevant state change,
or resolution of the incomplete check's cause, never to display more fields.

Full `log validate --path <log>` requires separate validation authorization.
Outdated generated state needs that full validation to rebuild it. Repair alone
does not authorize Validate, Review, or reproduction. Report unrelated remaining
findings without correcting them.

### 5. Record Before Advancing

Retain the result ID, outcome and next action. In a campaign, append the outcome
and its evidence to history, then update the current note's active target, next
action and blockers before advancing. Keep completed detail in history; do not
copy queryable result payloads into the note or a new queue. Cached detail can
be removed by later evaluation or cache clearing, so record the significant
outcome while it is available.

Use `$plan-execution` for planned continuation. For a standalone campaign, create
`repair-<campaign>-current.md` in the project's temporary-work location at start
and append-only `repair-<campaign>-history.md` with the first outcome. At completion,
save remaining outcomes to history and delete the current note.

If producer stdout was lost, use **Recover A Check Result** below. If no result
was cached, record the operational error rather than inventing an outcome.

### 6. Advance

Select the next independent authorized target using the CLI batch listing and
recorded dispositions. Uninspected work remains pending, including inspection
groups. Mark a group blocked or skipped only when the established reason covers
every member. A campaign is exhausted only when no authorized work remains pending.

Reuse a diagnosis when recorded relationships establish the same cause and safety
conditions. Check each new case against those conditions; investigate material
differences. Inspection groups establish no common cause: correct and assess
their members individually. Keep mutations and checks within the authorized
batch's affected records, including multiple entries when required.

Record repeated cases as target, outcome and exception. Group user-facing updates
at campaign milestones and preserve requested approval stops.

## Repair Methods

Read only the case needed for the selected correction:

- **Owning action:** input registrations and fingerprints use `log data`; evidence
  records and associations use `log evidence`; retention uses `log retention`;
  recorded execution policy uses `log pyrun`. If the action is unknown, read that
  family's `--help`, then the selected action's help. Invoke it through
  `<skill>/scripts/log`.
- **Unresolved material role:** for `material.candidate.unresolved`, inspect the
  argument selectors and read `references/file-entry-commands.md`'s material-role
  guidance. Resource registration does not assign an input/output role.
- **Provenance shape:** when choosing one, read `references/provenance-patterns.md`
  and only the matching card. Otherwise, skip the catalog.
- **Retained generated target:** when registry admission must precede reproduction,
  `log data add-generated --pending-confirmation` requires one structurally valid,
  unambiguous same-log producer. It is not ordinary pre-production declaration
  or a way to bypass a missing or ambiguous producer.
- **Direct source repair:** preserve the original state or backup and derive
  reconstructed fields from retained state. Use the affected decoder, serializer,
  locks and validation contracts when available; directly edit malformed state
  only when those tools cannot load it. For malformed or legacy state that
  prevents the owning CLI action, search
  `../../../docs/research-log-mechanical-validator-spec.md` for the reported
  code, field or contract and read only the applicable section. Load a detailed
  bundled file contract only when that section or the affected material requires
  it. Do not open the complete specification or every registry reference. This
  focused lookup is Repair's sole repository-level instruction dependency; do
  not load the specification during another operation. Keep reconstructed
  execution-support records `confirmed: false`; only successful owning execution
  may confirm them.
- **Transaction residue:** follow the exact diagnostic and owning implementation
  contract. Remove or reconcile only residue mechanically identified as belonging
  to the interrupted research-owned transaction.

## Recover A Check Result

If stdout was lost, list candidate results without reevaluating:

```text
<skill>/scripts/log results list --path <log> --kind batch \
  --validation <validation-id> --batch <batch-id>
```

Match scope and evaluation time to the invocation before using an ID: a failed
run can leave an older result. If the match is uncertain, report the outcome as
unknown. Query the matched ID for additional detail.

# Repair Operation Instructions

Use this operation only when the researcher explicitly asks to correct a named
research-log finding, causal group, finding class, malformed or legacy state,
recognized transaction residue, or another identified research-log defect.
Repair restores intended valid state; it does not perform new research, choose
new evidence, or revise scientific meaning.

A failed Record command, completed Validate operation, or Review finding does
not authorize Repair by itself. Report the condition and wait for an explicit
correction request. A valid advanced evidence presentation belongs to Record's
definition mode, not Repair.

## Resolve The Target

- Begin with the requested log and the narrowest authorized finding, causal
  group, or finding class. Do not expand the task to unrelated findings or
  nearby cleanup.
- For a validation finding, use the supplied result ID. Only when none is
  supplied, obtain one with
  `<skill>/scripts/log results show --path <log> --latest --kind full`.
  Pin that ID for subsequent views:

  ```text
  <skill>/scripts/log results show --path <log> --id <result-id> --view batches \
    [--entry <entry>] [--code <code>]
  <skill>/scripts/log results batch --path <log> --id <result-id> --batch <batch-id>
  <skill>/scripts/log results finding --path <log> --id <result-id> --finding <check-id>
  ```

  Start with the batch's stated blocker or inspection starting point.
  Retrieve only missing detail using `results command`, `artifact`, or the
  collection/value command printed in the view. Follow a cursor when
  more matching items are needed. For an uncached current publication, use
  `findings list` or `findings show` with exact selectors; do not run validation
  merely to populate the cache. End repair for no matching findings only when
  a completed evaluation covers the target and remains applicable to its
  current state. An incomplete observation leaves the target unresolved.
  Treat returned conditions as read-only. Do not read or parse `validation.md`,
  `validation/results.json`, `validation/batches.json`, or the inspection database.
- Read affected source records only for information the CLI does not provide
  that is needed to establish a correction or concrete blocker.
- If the request and retained log do not establish the intended corrected
  state, stop and ask the researcher. Do not choose among plausible IDs,
  sources, transformations, origin boundaries, prose meanings, or structural
  destinations.

## Apply The Correction

For each authorized batch:

1. Inspect the selected batch through its CLI text views. For
   `producer.missing` or `lineage.missing`, inspect discovery findings for the
   apparent producer before changing registrations or provenance structure.
   Establish why the command was excluded, correct that cause within the
   authorized scope, then reassess the original finding.
2. If choosing a provenance shape, read `references/provenance-patterns.md`
   and only the matching card. Otherwise, skip the catalog.
3. Apply the correction through the owning command or permitted edit.
4. Run `validate-batch` and record the outcome.
5. Continue to another independent authorized batch. Pause only when requested
   or when a research-owned decision remains.

- For `material.candidate.unresolved`, inspect the reported argument selectors
  and read the material-role guidance in `references/file-entry-commands.md`.
  Resource registration does not assign an input/output role.
- If a failed correction prints a diagnostic ID, use its printed text-inspection
  command for omitted details. This snapshot is not a validation result; do not
  rerun the correction to display more fields.
- Reuse a diagnosis only when recorded relationships establish a shared cause.
  Explain a representative correction and its safety conditions; reuse that understanding for subsequent cases.
  Check each case against those conditions; investigate and explain material
  differences instead of repeating the full diagnosis. Keep mutations and
  postcondition checks bounded to the authorized batch's affected records,
  including multiple entries when required. Inspection groups establish no
  common cause; correct and assess their members individually.
  Continue independent cases after a skipped or blocked case.
- Record repeated cases as target, outcome, and exception. Group user-facing
  updates at campaign milestones; preserve required approval stops.
- Preserve presented evidence, evidence tolerances, and scientific meaning.
  Skip any case whose correction would require changing presented evidence or
  choosing new scientific content, and report it for researcher direction.
- When an existing retained generated target must enter the registry before
  reproduction can confirm it, `log data add-generated --pending-confirmation`
  is the narrow migration path. Require one structurally valid, unambiguous
  same-log producer; do not use it for ordinary pre-production declaration or
  to bypass a missing or ambiguous producer.

- Choose the command family for the affected material:

  | Affected Material | Command Family |
  |---|---|
  | Input registrations and fingerprints | `log data` |
  | Evidence records and associations | `log evidence` |
  | Retention declarations | `log retention` |
  | Recorded execution policy | `log pyrun` |

  If the action is unknown, read that family's `--help`, then the selected
  action's help. Invoke it through `<skill>/scripts/log` when it can safely
  express the authorized correction.
- When no owning action can safely express an explicitly authorized
  correction, edit only the affected non-validation Markdown or JSON. Use its
  decoder, serializer, locks, and validation contracts when available; edit
  malformed state directly only when those tools cannot load it. Search
  `../../../docs/research-log-mechanical-validator-spec.md` for the reported
  code, field, or contract and read only the applicable section. Load a
  detailed bundled file contract only when that section or the affected
  material requires it; do not open the complete specification or every
  registry reference up front. This focused lookup is Repair's sole
  repository-level instruction dependency; do not load the specification
  during another operation.
- Preserve the original state or backup and derive reconstructed fields from
  retained state. Keep reconstructed execution-support records
  `confirmed: false`; only successful owning execution may confirm them.
  Preserve all fields, records, prose, and material outside the requested
  correction. Keep the stable evidence ID unless the identified defect is the
  ID itself and the intended replacement is explicit.
- Treat recognized transaction residue by its exact diagnostic and owning
  implementation contract. Remove or reconcile only residue mechanically
  identified as belonging to the interrupted research-owned transaction.
- Stop the affected case on a failed correction command. Retry only after a
  revised, evidence-backed hypothesis; do not repeat the same mutation, edit
  around a precondition, or widen the repair.

## Campaign Records

For planned campaigns, use `$plan-execution` for continuation. For standalone
campaigns, keep `repair-<campaign>-current.md` and `repair-<campaign>-history.md`
in the project's temporary-work location. Create the note at campaign start
and append-only history with the first outcome. At completion, save remaining
outcomes to history and delete the current note.

Keep every authorized batch in the pending queue, including inspection groups.
An unknown cause requires inspection. Mark work blocked or skipped only after
inspection establishes a concrete reason; apply that disposition to a whole
group only when the reason covers every member. Leave uninspected members
pending. Report a campaign exhausted only when no authorized work remains pending.

Keep the active batch, next action, and blockers in the current note. Record
completed/skipped batches once in history with outcomes and evidence links.

## Boundaries

- Do not alter conclusions, interpretations, method choices, evidence values,
  or researcher decisions unless the explicit repair request supplies the
  intended replacement.
- Do not fix unrelated validation or review findings.
- Never hand-edit generated validation state; use its owning CLI.
- Do not infer Replace authorization. If the correction would remove
  superseded experimental work, stop and request explicit Replace authority.
- Do not reorganize document or entry boundaries unless the researcher also
  explicitly authorizes Reorganize.

## Complete

After correcting a projected batch, run its lock-free check using the original
published validation and requested batch IDs:

```text
<skill>/scripts/log validate-batch --path <log> --validation <validation-id> \
  --batch <batch-id>
```

If generated state is outdated, report that a separately authorized full
validation is required to rebuild it. Do not rerun validation implicitly.
Use `results show --view chains` only for needed provenance membership.

`complete_clear` or `complete_findings` applies only to the reconciled current
batch membership. Inspect remaining findings and overlaps before advancing.
If the result is `incomplete`, report the precise reason and
do not claim the batch cleared. For malformed state, transaction residue, or
another defect with no published batch, use the owning bounded decoder or
command postcondition instead and do not claim `complete_clear`.

Read the compact text result and retain its ID with the outcome and next action.
If producer stdout is lost, list candidate results without reevaluating:

```text
<skill>/scripts/log results list --path <log> --kind batch \
  --validation <validation-id> --batch <batch-id>
```

Match the result's scope and evaluation time to the invocation before using
its ID; a failed run can leave an older result. If the match is uncertain,
report the outcome as unknown.

Query the matched ID for additional detail. Do not copy queryable payloads into
files, parse JSON to rebuild reports, or rerun validation to display different fields.
JSON output is for scripts that explicitly request it. Record operational errors
when no result was cached. Keep outcomes in history: another check of this batch,
a new full validation, or cache clearing can remove its cached detail.

Repeat validation only after repairs, relevant state changes, or fixing an
incomplete check's cause. Otherwise reuse applicable evidence. For joined or
overlapping batches, check returned membership and coverage before reusing
results; assess uncovered work separately.

Run full `log validate --path <log>` only when the researcher separately asks
for validation. Report unrelated remaining findings without correcting them.

If any command reports `operation.lock.conflict`, report the supplied owner
metadata once and stop the affected case. Do not retry, poll, inspect process
tables, or work around the lock.

For a Review finding or another non-validation defect, run only the bounded
checks appropriate to the correction. Do not start Validate, Review, or
reproduction merely because the repair changed research-owned material.

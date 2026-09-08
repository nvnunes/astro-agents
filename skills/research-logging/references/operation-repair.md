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
- For a published validation finding, locate only the relevant bounded group:

  ```text
  <skill>/scripts/log findings list --path <log> \
    [--entry <entry>]... [--validation-area <area>]... [--code <code>]... \
    [--family <family>]... [--subject <subject>]... [--command <command>]...
  ```

  For an exact finding, retrieve the selected complete check:

  ```text
  <skill>/scripts/log findings show --path <log> --id <check-id>
  ```

  For a connected command chain, retrieve its complete finding batch:

  ```text
  <skill>/scripts/log findings batch --path <log> --projection <projection-id> \
    --entry <entry> --chain <chain-id>
  ```

  Combine repeatable selectors to narrow rather than loading unrelated groups.
  A list result with no matching published findings ends the repair without
  running validation. Treat every returned machine condition as read-only. Do
  not read or parse `validation.md`, `validation/results.json`, or
  `validation/batches.json` directly.
- Inspect the affected files and only enough surrounding log
  state to establish the intended relationship.
- If the request and retained log do not establish the intended corrected
  state, stop and ask the researcher. Do not choose among plausible IDs,
  sources, transformations, origin boundaries, prose meanings, or structural
  destinations.

## Apply The Correction

For each authorized chain:

1. Retrieve it once with `findings batch` and inspect only needed current records.
2. If choosing a provenance shape, read `references/provenance-patterns.md`
   and only the matching card. Otherwise, skip the catalog.
3. Apply the correction through the owning command or permitted edit.
4. Run `validate-batch` and record the outcome.
5. Continue to another independent authorized chain. Pause only when requested
   or when a research-owned decision remains.

- Group batches by likely shared cause. Explain a representative correction
  and its safety conditions; reuse that understanding for subsequent cases.
  Check each case against those conditions; investigate and explain material
  differences instead of repeating the full diagnosis. Keep mutations and
  postcondition checks bounded to one entry or chain at a time.
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

- Use the owning `<skill>/scripts/log` action when it can safely express the
  intended correction. Read only that action's help before invoking it.
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

Keep the active batch, next action, and blockers in the current note. Record
completed/skipped batches once in history with outcomes and evidence links.

## Boundaries

- Do not alter conclusions, interpretations, method choices, evidence values,
  or researcher decisions unless the explicit repair request supplies the
  intended replacement.
- Do not fix unrelated validation or review findings.
- Never edit generated validation files; only Validate may replace them.
- Do not infer Replace authorization. If the correction would remove
  superseded experimental work, stop and request explicit Replace authority.
- Do not reorganize document or entry boundaries unless the researcher also
  explicitly authorizes Reorganize.

## Complete

After correcting one projected command-chain batch, run its lock-free,
write-free check:

```text
<skill>/scripts/log validate-batch --path <log> --projection <projection-id> \
  --entry <entry> --chain <chain-id>
```

`complete_clear` or `complete_findings` applies only to the reconciled current
chain membership. If the result is `incomplete`, report the precise reason and
do not claim the batch cleared. For malformed state, transaction residue, or
another defect that has no projection, use the owning bounded decoder or
command postcondition instead and do not claim `complete_clear`.

Save batch query and validation responses before summarizing. Inspect saved
responses for more detail; never rerun commands just to display different fields.

Repeat validation only after repairs, relevant state changes, or fixing an
incomplete check's cause. Otherwise reuse applicable evidence. For joined or
overlapping chains, check returned membership and coverage before reusing
results; assess uncovered work separately.

Run full `log validate --path <log>` only when the researcher separately asks
for validation. Report unrelated remaining findings without correcting them.

If any command reports `operation.lock.conflict`, report the supplied owner
metadata once and stop the affected case. Do not retry, poll, inspect process
tables, or work around the lock.

For a Review finding or another non-validation defect, run only the bounded
checks appropriate to the correction. Do not start Validate, Review, or
reproduction merely because the repair changed research-owned material.

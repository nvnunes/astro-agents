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

- For each authorized chain: retrieve it once with `findings batch`; inspect
  only its needed current records; when the correction requires choosing a good
  provenance shape, load `references/provenance-patterns.md` and then only the
  matching card; state the proposed correction; apply it through the owning
  command or permitted edit; run `validate-batch`; report the outcome; then
  continue to another independent authorized chain. Pause only when requested
  or when a research-owned decision remains. Unrelated Repair does not load the
  catalog.
- Treat a shared cause inferred from several findings as a tentative campaign
  hypothesis, not as authority to edit every match. Change one entry or command
  chain at a time, confirm the local postcondition, and revise the hypothesis
  when a case does not fit. Continue independent cases after a skipped or
  blocked case.
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
  around a precondition, or widen the repair. Keep a compact current note for a
  multi-case campaign so completed, skipped, and remaining chains stay clear.

## Boundaries

- Do not alter conclusions, interpretations, method choices, evidence values,
  or researcher decisions unless the explicit repair request supplies the
  intended replacement.
- Do not fix unrelated validation or review findings.
- Do not edit generated validation files. Repair may read them before the
  correction; only Validate may replace them afterward.
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

Run full `log validate --path <log>` only when the researcher separately asks
for validation. Report unrelated remaining findings without correcting them.

If any command reports `operation.lock.conflict`, report the supplied owner
metadata once and stop the affected case. Do not retry, poll, inspect process
tables, or work around the lock.

For a Review finding or another non-validation defect, run only the bounded
checks appropriate to the correction. Do not start Validate, Review, or
reproduction merely because the repair changed research-owned material.

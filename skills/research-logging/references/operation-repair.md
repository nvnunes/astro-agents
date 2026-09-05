# Repair Operation Instructions

Use this operation only when the researcher explicitly asks to correct a named
research-owned validation finding, malformed or legacy research-owned state,
recognized transaction residue, or another identified research-log defect.
Repair restores intended valid state; it does not perform new research, choose
new evidence, or revise scientific meaning.

A failed Record command, completed Validate operation, or Review finding does
not authorize Repair by itself. Report the condition and wait for an explicit
correction request. A valid advanced evidence presentation belongs to Record's
definition mode, not Repair.

## Resolve The Target

- Begin with the requested log, finding, and affected files. Do not expand the
  task to other findings or nearby cleanup.
- For a published validation finding, read its exact check in
  `validation/mechanical.json` and use `validation.md` only as the human
  projection. Treat both as read-only generated state.
- Inspect the affected research-owned files and only enough surrounding log
  state to establish the intended relationship.
- If the request and retained log do not establish the intended corrected
  state, stop and ask the researcher. Do not choose among plausible IDs,
  sources, transformations, origin boundaries, prose meanings, or structural
  destinations.

## Apply The Correction

- Use the owning `<skill>/scripts/log` action when the current research-owned
  state is parseable and that action expresses the intended correction. Read
  only that action's help before invoking it.
- When an explicitly identified retained generated target must be registered
  before reproduction can confirm it, use `log data add-generated
  --pending-confirmation`. Require the target and exactly one current same-log
  `pyrun` producer to be unambiguous. Treat the resulting unconfirmed
  Provenance finding as pending reproduction, not as a completed repair. Do
  not use this form to bypass a missing or ambiguous producer or to classify an
  uncertain boundary.
- When malformed or legacy research-owned Markdown or JSON prevents the owning
  action from operating, edit only the affected file with the normal workspace
  tools. Search
  `../../../docs/research-log-mechanical-validator-spec.md` for the reported
  code, field, or contract and read only the applicable section. Load a
  detailed bundled file contract only when that section or the affected
  material requires it; do not open the complete specification or every
  registry reference up front. This focused lookup is Repair's sole
  repository-level instruction dependency; do not load the specification
  during another operation.
- Preserve all fields, records, prose, and material outside the requested
  correction. Keep the stable evidence ID unless the identified defect is the
  ID itself and the intended replacement is explicit.
- Treat recognized transaction residue by its exact diagnostic and owning
  implementation contract. Remove or reconcile only residue mechanically
  identified as belonging to the interrupted research-owned transaction.
- Stop on any failed correction command. Report the precise failure rather
  than editing around a command precondition or widening the repair.

## Boundaries

- Do not alter conclusions, interpretations, method choices, evidence values,
  or researcher decisions unless the explicit repair request supplies the
  intended replacement.
- Do not fix unrelated validation or review findings.
- Do not edit generated validation files. Repair may read them before the
  correction; only Validate may replace them afterward.
- Do not edit `pyrun-outputs.json` or its recognized backups. Those are
  tool-owned execution-support state. Use an existing owning command when one
  can re-establish the required support. Pending generated-input registration
  may precede that reproduction but never changes or substitutes for execution
  support; otherwise report the unresolved tool-state condition.
- Do not infer Replace authorization. If the correction would remove
  superseded experimental work, stop and request explicit Replace authority.
- Do not reorganize document or entry boundaries unless the researcher also
  explicitly authorizes Reorganize.

## Complete

After the requested correction succeeds, invoke mechanical validation as a
separate operation:

```text
<skill>/scripts/log validate --path <log>
```

Confirm whether the named condition cleared. Report any unrelated findings
without correcting them. If validation remains incomplete or the named defect
persists, report the exact result and stop; do not keep broadening the repair.

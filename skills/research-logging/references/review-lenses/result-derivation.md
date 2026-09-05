# Result Derivation

Assess whether a reported result follows correctly from its upstream inputs
through the substantive calculations and transformations in the recorded
research workflow.

## Inspect

Use the reported result as a locator, its retained source artifact, upstream
inputs and intermediates, relevant code and configuration, recorded commands,
method descriptions, and existing diagnostic outputs.

## Review Criteria

- Trace the retained result to the correct upstream inputs and cases.
- Check filtering, normalization, aggregation, unit conversion, arithmetic, and
  other substantive transformations.
- Check whether intermediate quantities and assumptions are used consistently.
- Check whether the result preserves the intended population, condition,
  configuration, and units.
- Identify material transformations missing from the account or unsupported by
  the retained workflow.

Start from the retained result in the target and trace backward through the
substantive computation to its upstream inputs. Stop at the retained result-to-
presentation boundary owned by mechanical validation.

Before reporting a quantitative contradiction, verify feasible arithmetic from
the retained inputs with a read-only or disposable calculation. Do not treat
that diagnostic as new evidence or as reproduction of the recorded workflow.

## Exclusions

- Mechanical validation owns deterministic selection and rendering from the
  retained result into its log presentation.
- Implementation Fidelity compares the implementation as a whole with the
  described method; this lens traces a particular result.
- Statistical, Numerical, and Methodological Validity own the soundness of the
  chosen inferential, numerical, and scientific approaches.
- Reproduction executes the recorded workflow; this lens does not rerun it.

## Finding Guidance

- **Issue:** A result is materially disconnected from its upstream inputs or
  depends on an incorrect, omitted, or inconsistent substantive transformation.
- **Improvement:** The derivation is sound, but a consequential intermediate
  relationship or assumption should be easier to inspect.
- **Unverified:** Missing inputs, intermediates, code, configuration, or lineage
  prevent the derivation from being traced.

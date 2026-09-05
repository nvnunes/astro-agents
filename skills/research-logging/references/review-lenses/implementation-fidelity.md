# Implementation Fidelity

Assess whether research code faithfully implements the method, configuration,
selections, and output construction described by the research account.

## Inspect

Use relevant scripts, modules, configuration, recorded commands, manifests,
method descriptions, retained outputs, and static diagnostics needed to compare
the implementation with the account.

## Review Criteria

- Compare the implemented algorithm, model, comparison, and data flow with the
  recorded method.
- Trace recorded parameters, defaults, units, selections, and configuration to
  implemented behavior.
- Identify consequential implicit defaults or hidden branches inconsistent with
  the account.
- Check whether output construction preserves the described quantities, cases,
  ordering, and metadata.
- Identify changes that have left the prose and code materially out of sync.

Compare the account and implementation in both directions: confirm that
described behavior exists in code and that consequential code behavior appears
in the account. Static analysis and disposable source inspection are allowed;
running the research workflow is not.

## Exclusions

- General maintainability, architecture, style, and production quality belong
  to code-quality review.
- Methodological Validity owns whether the described method is suitable.
- Numerical Validity owns whether its numerical behavior is trustworthy.
- Result Derivation traces one result through its substantive computation.
- Reproduction executes recorded commands. Mechanical validation does not
  inspect code internals.

## Finding Guidance

- **Issue:** Code behavior materially differs from the recorded method,
  configuration, selection, or output construction.
- **Improvement:** Implementation and account agree, but an implicit behavior
  should be easier to understand or verify.
- **Unverified:** Required code, configuration, generated parameters, or
  dependency behavior cannot be inspected.

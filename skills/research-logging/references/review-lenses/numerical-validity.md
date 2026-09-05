# Numerical Validity

Assess whether numerical methods, approximations, convergence, stability,
precision, and units are sufficient to make the computed results trustworthy.

## Inspect

Use algorithms and code, retained convergence or stability diagnostics, solver
settings and tolerances, discretization and precision choices, unit handling,
intermediate and final numerical outputs, and relevant numerical-method
references.

## Review Criteria

- Assess approximation, discretization, solver, and stopping choices against
  the required accuracy.
- Check whether convergence is demonstrated or otherwise justified where it
  matters.
- Assess conditioning, stability, sensitivity, overflow, underflow, and
  precision risks.
- Check units, scales, coordinate conventions, and numerical conversions.
- Compare numerical error with the distinctions or uncertainty on which the
  conclusions depend.
- Check whether material tolerances and numerical assumptions are visible in
  the retained account or implementation.

Use retained diagnostics and static inspection first. Disposable calculations
may probe reasoning already represented in the account. Rerunning the recorded
workflow is reproduction; varying it is follow-on research.

## Exclusions

- Methodological Validity owns whether the scientific method or model is
  suitable.
- Statistical Validity owns statistical inference and uncertainty.
- Implementation Fidelity owns correspondence between code and the described
  numerical method; this lens owns whether the method and behavior are
  trustworthy.
- Result Derivation traces a particular result through the computation.
- Reproduction may show that an artifact can be regenerated without proving
  numerical validity.

## Finding Guidance

- **Issue:** Numerical instability, nonconvergence, inadequate resolution or
  precision, a unit error, or uncontrolled approximation materially threatens a
  result.
- **Improvement:** Existing numerical support is adequate, but an additional
  diagnostic, sensitivity check, or recorded tolerance would improve confidence.
- **Unverified:** Necessary diagnostics, settings, implementation details, or
  numerical expertise are unavailable.

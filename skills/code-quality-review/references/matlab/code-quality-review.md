# MATLAB Code-Quality Review

## Purpose

Review current-state MATLAB source for numerical correctness, explicit contracts, state ownership, reproducibility, and proportional maintainability.

Use the profiles and local-instruction discovery in `skills/code-quality-review/references/code-quality-review.md`. Full current standards are the default; lower profiles require an explicit applicable `AGENTS.md` override. Keep simulator-specific policy in the reviewed project's instructions.

## Review Standard

- Apply comparable rigor to the Python workflow, using MATLAB's own conventions rather than Python's architecture or tooling preferences.
- Require correct behavior, clear consequential contracts, reliable outputs, reproducibility where required, and verification proportional to the code's risk.
- Treat MATLAB idioms as preferences unless their absence causes a concrete correctness, contract, or maintainability problem. Explain that problem in each finding; a style preference alone is not a finding.
- Do not demand more abstraction, validation, documentation, or testing ceremony merely because MATLAB offers it. Apply the selected profile to the scope of improvements, not the correctness standard.

## Scope Identification

- Identify the requested MATLAB functions, scripts, classes, and their directly relevant callers and tests.
- Read applicable review instructions before assigning profiles. Do not infer a lower profile from a directory or filename.
- Distinguish project-owned code from inherited dependencies without silently excluding either from an explicitly requested scope.
- Inspect local documentation only where it defines scientific behavior, input/output contracts, release compatibility, or verification expectations.
- Check Python, shell, and MAT-file interfaces when they materially determine the MATLAB code's contract. Use the corresponding review workflow for another supported language; state uncovered boundaries.
- Identify the supported MATLAB release and required toolboxes from local configuration. Do not recommend syntax or functions unavailable in that environment.

## Correctness And Scientific Contracts

Apply these checks in every profile:

- Trace array dimensions, axis ordering, indexing, and sample ordering through consequential calculations and external interfaces.
- Check matrix versus elementwise operations, row/column orientation, implicit expansion, linear indexing, and singleton dimensions where they affect the result. Do not flag valid array idioms merely because they are concise.
- Check units, wavelength conventions, normalizations, centring, and covariance definitions against the stated scientific contract.
- Distinguish transpose from conjugate transpose where complex arrays are possible.
- Check matrix conditioning, singular cases, and the assumptions behind symmetry or positive-definiteness checks where those properties are required.
- Check explicit matrix inverses for concrete numerical problems. In preservation-focused reviews, recommend replacement with a linear solve only when it addresses a demonstrated numerical problem, not merely a stylistic preference.
- Check consequential configuration and input assumptions, including empty or malformed arrays where supported or plausible at the boundary. Validate at the owning boundary; do not require repeated checks in trusted internal helpers. Require finite values only where the contract requires them; preserve explicitly permitted missing-data values.
- Reject silent fallback, clipping, reshaping, or coercion that changes scientific meaning. Distinguish a documented approximation from an implementation error.
- Keep scientific formulas and configuration under clear ownership. Flag duplicated definitions when they can diverge, not merely repeated syntax.
- Check MAT-file variable names, schema, dimensions, units, and MATLAB/Python ordering at serialization boundaries. Prefer loading into a struct or selecting named variables in new code to avoid workspace collisions.

## State, Resources, And Outputs

Apply these checks in every profile:

- Identify dependencies on caller workspace, globals, persistent state, current directory, and MATLAB path order.
- Distinguish value data from handle objects. Check unintended shared mutation or incorrect assumptions about copying where objects are passed between components.
- Check ownership and restoration of paths, RNG state, warning state, graphics defaults, and figures when a callable component changes them. A top-level script may own its session if that contract is explicit.
- Check file, figure, detector, and other resource cleanup on successful and failed execution. Use `onCleanup` or an equivalent reliable ownership mechanism where appropriate.
- Verify that deterministic seeds and independent draws follow the measurement contract. Parallel execution must not silently duplicate random streams or alter the sampling definition.
- Check path resolution before changing directories or handing paths to another process.
- Check overwrite, partial-output, and failure behavior. A failed run must not leave stale or incomplete artifacts appearing to be a successful current result.
- Respect the project's execution runner and process-ownership rules. A review does not authorize starting expensive or externally disruptive work.

## Structure And Clarity

Apply these expectations under the assigned profile:

- Prefer explicit function inputs and outputs for reusable new code. Top-level scripts are acceptable for clear configuration and orchestration; do not require conversion to functions solely for uniformity.
- Separate responsibilities when their contracts or reuse justify it. A procedural workflow with local functions is acceptable; do not require a framework or a separate file for every calculation.
- Keep configuration separate from measured targets and fitted outputs. Make consequential defaults and retained configuration discoverable.
- Use arrays, structs, cells, tables, or classes according to the data and behavior they represent. Do not require typed objects or classes where arrays or structs provide a clear contract.
- Use local functions or small helpers when they give a calculation a clear owner. Keep file and primary-function names consistent where MATLAB requires it; do not impose Python module organization.
- Use MATLAB help comments for callable entrypoints where correct use requires explanation of inputs, outputs, units, dimensions, or side effects. Keep inline comments focused on approximations and non-obvious numerical steps; do not demand exhaustive help blocks for obvious private helpers.
- Keep compatibility code at an explicit boundary. Do not spread release-specific assumptions into otherwise independent estimation code.
- Prefer preallocation and appropriate array operations where they materially improve runtime or clarity. Clear loops are valid MATLAB; do not demand vectorization or propose optimizations without considering memory use and numerical effects.
- Do not require classes, package hierarchies, elaborate abstractions, or a particular input-validation syntax merely to resemble a Python project.

## Applying Review Profiles

- Full current standards: apply all relevant checks above and expect explicit contracts, deliberate state ownership, readable structure, and suitable regression tests. Recommend proportionate corrections rather than maximizing abstraction.
- Incremental improvement: preserve established interfaces and execution style. Recommend small, local changes that clarify inputs, outputs, state, or error handling; avoid a rewrite to eliminate inherited workspace coupling when a bounded repair suffices.
- Preservation-focused: follow established style and scientific behavior. Report concrete defects, unsafe interfaces, and missing evidence needed to verify repairs. Do not recommend broad conversion of scripts to functions, renaming, formatting, or algorithm replacement merely to modernize inherited code.

## Verification

- Prefer small, deterministic fixtures for formulas, dimensional contracts, error paths, state restoration, and serialization interfaces.
- Use MATLAB-native function-based tests, class-based tests, or focused assertion scripts according to the existing workflow. Do not demand a testing framework migration or Python tooling for MATLAB-only checks.
- For behavior-preserving changes, compare scientific arrays and metadata with an independent retained baseline using justified tolerances. Check exact identity where the contract requires it.
- For intentional scientific changes, require an explicit statement of the changed behavior and evidence appropriate to it; passing smoke tests do not establish scientific validity.
- Use existing applicable evidence before proposing new acquisition. If necessary verification cannot be run, state that limitation without claiming a pass.
- Use MATLAB Code Analyzer when available and relevant, but treat its warnings as prompts for inspection, not automatic findings or proof of correctness.
- Keep the review read-only unless implementation is separately requested.

## Output

Use the shared review output. State the code areas and profiles reviewed, the checks actually run, and any verification limits. Order concrete findings by severity, distinguish correctness defects from contract/lifecycle weaknesses and optional maintainability improvements, and recommend the smallest appropriate repair.

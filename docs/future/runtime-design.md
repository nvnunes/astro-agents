# Runtime Design

## Status

This document is intentionally incomplete.

Do not treat it as implementation-ready until the workstream documents have been advanced and their unresolved cross-workstream dependencies have been synthesized here.

## Purpose

Use this document as the umbrella design frame and eventual integrated design target for the future runtime redesign.

While the work is still audit-heavy and repo-specific, use this document to keep the workstreams aligned, preserve the intended sequencing, and record the integrated decisions that cannot be settled inside one workstream alone.

## Workstreams

### Runtime Governance

Develop a clearer runtime-governance model. This should focus on how routing, instruction loading and applicability, conflict handling among applicable instructions, context boundaries, tool contracts, permission levels, and approval checkpoints behave during execution rather than only in document structure. Routing belongs primarily in this area, because it is the main mechanism by which the system decides which instructions and context are applicable and what the agent is allowed to carry forward into action. The goal is to define the execution model more explicitly before trying to test or harden it in detail.

### Observability And Provenance

Make the governed system more observable. This means developing a stronger account of traces, intermediate state, applicable instructions and context, source linkage, and reconstructable records of why the system behaved as it did. Effective use of context belongs mainly here, because context needs to be treated not only as something to optimize, but also as something to inspect, recover, and audit. The goal is to make behavior visible enough that later validation and failure analysis are based on evidence rather than reconstruction.

### Validation

Extend validation beyond review prompts into behavior-focused testing. The current review structure is useful, but it should be supplemented with routing tests, instruction-loading and applicability checks, representative cases, regression checks, and longer-horizon evaluations where appropriate. Routing also belongs partly here, because once routing rules are defined they need to be tested to confirm that the intended route, handoff, task ownership, and conflict-handling decisions actually occur. The goal is to move from validating the design of the agent surface to validating the behavior that the surface produces.

### Safety

Turn safety into a more explicit design and review axis. This should include prompt injection, unsafe tool use, excessive agency, untrusted context, memory poisoning, and related runtime risks. Both routing and context management matter here, because poor routing can bring the wrong instructions into scope and poor context handling can expose the system to unsafe inputs or unsafe action chains. The goal is to build on the earlier work in governance, observability, and validation so that safety is treated as a concrete operational concern rather than only a general preference for caution.

## Cross-Workstream Dependencies

- `docs/future/runtime-governance.md` defines the intended runtime behavior for routing, instruction loading and applicability, conflict handling among applicable instructions, customization behavior, and context-boundary decisions.
- `docs/future/runtime-observability-and-provenance.md` defines what runtime state must be visible, linked, and reconstructable.
- `docs/future/runtime-validation.md` defines how the intended runtime behavior will be tested, measured, and accepted.
- `docs/future/runtime-safety.md` defines runtime-risk framing and the control points that the design must satisfy.
- This document depends on all four workstreams and should not be treated as implementation-ready until those inputs are mature enough to support integrated decisions.

## Current Program Frame

The workstream documents still contain substantial current-project audit material and provisional recommendations. That is acceptable for now.

Use the workstream documents for current-project findings, open questions, and workstream-specific target models. Use this document for shared framing, cross-workstream dependencies, high-level sequencing, and later synthesis.

## Planned Synthesis Structure

Populate the sections below only after the workstream documents are mature enough to support integrated decisions.

### Runtime Governance Model

Define the intended runtime behavior for routing, handoffs, task ownership after the relevant `Route` or `Handoff`, instruction loading and applicability, conflict handling among applicable instructions, common execution paths, and governance-side compaction handling.

### Observability And Provenance Model

Define what runtime state, route decisions, applicable instructions and context, and source linkage must be visible and reconstructable.

### Validation Model

Define how the intended runtime behavior will be tested, measured, and accepted.

### Safety Model

Define the runtime-risk constraints, control points, and safety review requirements that the integrated design must satisfy.

### Integrated Decisions

Settle cross-workstream dependencies, conflicts, and tradeoffs that cannot be settled inside one workstream alone.

## Sequencing

1. Complete and refine the current-project and external-guidance audits across the four workstreams.
2. Advance Runtime Governance to a synthesis-ready target model while keeping observability, validation, and safety dependencies explicit.
3. Advance Runtime Observability And Provenance, Runtime Validation, and Runtime Safety to synthesis-ready target models, with explicit cross-checks whenever governance decisions depend on evidence surfaces, evaluation methods, or safety controls.
4. Synthesize the workstream outputs here and settle the integrated design decisions, guardrails, and acceptance criteria.

Avoid treating this as a strict waterfall. Governance decisions should remain provisional where they still depend on observability, validation, or safety work that has not yet matured enough to support them.

## Open Integrated Questions

- Which tool, interface, and harness contracts must be explicit in the first integrated runtime design rather than deferred?
- How strong should the first integrated model be on session identity, checkpointing, and handoff artifacts for longer-running or resumed work?
- How should the validation workstream move from a maintained scenario baseline toward a fuller eval program without making ordinary repo work too heavy?
- Which containment layers must the first integrated safety model choose explicitly, even if richer controls remain deferred?

## Completion Conditions

Treat this document as ready to drive implementation only when it:

- resolves the cross-workstream design decisions that remain open in the workstream documents
- defines the intended runtime behavior clearly enough for implementation work to proceed without additional planning decisions
- includes the observability, validation, and safety constraints needed to make the governance model operable
- states the guardrails and acceptance criteria that implementation should satisfy

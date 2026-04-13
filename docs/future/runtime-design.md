# Runtime Design

## Status

This document is intentionally incomplete.

Do not treat it as implementation-ready until the workstream documents have been advanced and their unresolved cross-workstream dependencies have been synthesized here.

## Purpose

Use this document as the umbrella design frame and eventual integrated design target for the runtime design program.

Use it to keep the workstreams aligned, preserve the intended sequencing, and record the integrated decisions that cannot be settled inside one workstream alone.

## Workstreams

### Runtime Governance

Develop a clearer runtime-governance model on top of the current lightweight live surface. This should focus on how routing, instruction loading and applicability, conflict handling among applicable instructions, context boundaries, tool contracts, permission levels, and approval checkpoints behave during execution rather than only in document structure. Routing belongs primarily in this area, because it is the main mechanism by which the system decides which instructions and context are applicable and what the agent is allowed to carry forward into action. The goal is to define the execution model more explicitly before trying to test or harden it in detail.

### Observability And Provenance

Extend the current lightweight observability baseline. Today that baseline is static routing and source-of-truth visibility plus `Route Summary` on shared selector and combined-review outputs. This workstream develops a stronger account of traces, intermediate state, applicable instructions and context, source linkage, and reconstructable records of why the system behaved as it did. Effective use of context belongs mainly here, because context needs to be treated not only as something to optimize, but also as something to inspect, recover, and audit. The goal is to make behavior visible enough that later validation and failure analysis are based on evidence rather than reconstruction.

### Validation

Extend the current review-driven validation system beyond review prompts into behavior-focused testing. The current review structure and maintained validation-path scenario baseline are useful starting points, but they should be supplemented with routing tests, instruction-loading and applicability checks, representative cases, regression checks, and longer-horizon evaluations where appropriate. Routing also belongs partly here, because once routing rules are defined they need to be tested to confirm that the intended route, any handoff or ownership model, and conflict-handling decisions actually occur. The goal is to move from validating the design of the agent surface to validating the behavior that the surface produces.

### Safety

Turn safety into a more explicit design and review axis on top of the current lightweight, review-driven surface. This should include prompt injection, unsafe tool use, excessive agency, untrusted context, memory poisoning, and related runtime risks. Both routing and context management matter here, because poor routing can bring the wrong instructions into scope and poor context handling can expose the system to unsafe inputs or unsafe action chains. The goal is to build on governance, observability, and validation so that safety is treated as a concrete operational concern rather than only a general preference for caution.

## Cross-Workstream Dependencies

- `docs/future/runtime-governance.md` defines the intended runtime behavior for routing, instruction loading and applicability, conflict handling among applicable instructions, customization behavior, and context-boundary decisions.
- `docs/future/runtime-observability-and-provenance.md` defines what runtime state must be visible, linked, and reconstructable.
- `docs/future/runtime-validation.md` defines how the intended runtime behavior will be tested, measured, and accepted.
- `docs/future/runtime-safety.md` defines runtime-risk framing and the control points that the design must satisfy.
- This document depends on all four workstreams and should not be treated as implementation-ready until those inputs are mature enough to support integrated decisions.

## Program Frame

Use the workstream documents for the current project assessment, open questions, and workstream-specific target models. Use this document for shared framing, cross-workstream dependencies, high-level sequencing, and later synthesis.

## Planned Synthesis Structure

Populate the sections below only after the workstream documents are mature enough to support integrated decisions.

### Runtime Governance Model

Define the intended runtime behavior for routing, handoffs, any ownership or governing-path model after the relevant `Route` or `Handoff`, instruction loading and applicability, conflict handling among applicable instructions, common execution paths, and governance-side compaction handling.

### Observability And Provenance Model

Define what runtime state, route decisions, governing-path information, applicable instructions and context, and source linkage must be visible and reconstructable.

### Validation Model

Define how the intended runtime behavior will be tested, measured, and accepted.

### Safety Model

Define the runtime-risk constraints, control points, and safety review requirements that the integrated design must satisfy.

### Integrated Decisions

Settle cross-workstream dependencies, conflicts, and tradeoffs that cannot be settled inside one workstream alone.

## Sequencing

1. Keep the current project assessments and source-backed comparisons aligned across the four workstreams.
2. Advance Runtime Governance from the current lightweight routing baseline to a synthesis-ready target model while keeping observability, validation, and safety dependencies explicit.
3. Advance Runtime Observability And Provenance from the current static visibility plus `Route Summary` baseline, Runtime Validation from the current shared review family plus maintained validation-path scenario baseline, and Runtime Safety from the current lightweight review-driven surface to synthesis-ready target models.
4. Synthesize the workstream outputs here and settle the integrated design decisions, guardrails, and acceptance criteria.

Avoid treating this as a strict waterfall. Governance decisions should remain provisional where they still depend on observability, validation, or safety work that has not yet matured enough to support them.

## Open Integrated Questions

- Which tool, interface, and harness contracts must be explicit in the first integrated runtime design rather than deferred?
- How strong should the first integrated model be on session identity, checkpointing, and handoff artifacts for longer-running or resumed work?
- How should the validation workstream extend the maintained validation-path scenario baseline into a fuller eval program without making ordinary repo work too heavy?
- How should observability extend beyond `Route Summary` and static routing visibility without over-instrumenting ordinary repo work?
- Which containment layers must the first integrated safety model choose explicitly, even if richer controls remain deferred?

## Completion Conditions

Treat this document as ready to drive implementation only when it:

- resolves the cross-workstream design decisions that remain open in the workstream documents
- defines the intended runtime behavior clearly enough for implementation work to proceed without additional planning decisions
- includes the observability, validation, and safety constraints needed to make the governance model operable
- states the guardrails and acceptance criteria that implementation should satisfy

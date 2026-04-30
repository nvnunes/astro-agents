# Runtime Design

## Status

This document is intentionally incomplete.

Do not treat it as implementation-ready until the workstream documents have been advanced and their unresolved cross-workstream dependencies have been synthesized here.

## Purpose

Use this document as the umbrella design frame and eventual integrated design target for the runtime design program.

Use it to keep the workstreams aligned, preserve the intended sequencing, and record the integrated decisions that cannot be settled inside one workstream alone.

Use `docs/future/agent-context-engineering-patterns.md` as an external context engineering input for the runtime design program. That document records current context engineering practice across agent tools and should inform the workstreams without being treated as the integrated design itself.

## Workstreams

### Runtime Governance

Develop a clearer runtime-governance model on top of the current lightweight live surface. This should focus on defining explicit route contracts: which pattern a branch uses, who owns the next user-facing output, which tools or specialists are allowed, what state and context may carry forward, and where approval or guardrail boundaries apply. Routing belongs primarily in this area because it is the first control point that determines ownership, tool access, and carry-forward behavior. The goal is to make the execution contract explicit enough that later observability, validation, and safety work can test and enforce it.

### Observability And Provenance

Extend the current lightweight observability baseline. Today that baseline is static skill/reference visibility plus `Review Path Summary` on combined-review outputs. This workstream should start with event-first evidence: route and handoff events, tool calls, guardrails and approvals, state-class transitions, compaction artifacts, and source provenance. Effective use of context belongs mainly here because context needs to be inspectable, recoverable, and auditable, not just optimized. The goal is to make behavior visible enough that later validation and failure analysis are based on concrete runtime evidence rather than reconstruction.

### Validation

Extend the current review-driven validation system beyond static review skills into behavior-focused testing. The current review structure, static validation harness, Codex discovery smoke test, and activation eval fixture are useful starting points, but the next layer should begin with representative tasks, expected observable outcomes, trace-backed debugging, and repeatable graders before expanding into broader routing, applicability, and longer-horizon checks. Routing also belongs partly here because once route contracts are defined they need to be tested to confirm that the intended branch, tool boundary, handoff, or approval behavior actually occurs. The goal is to move from validating the design of the agent surface to validating the behavior that the surface produces.

### Safety

Turn safety into a more explicit design and review axis on top of the current lightweight, review-driven surface. This should include prompt injection, unsafe tool use, excessive agency, untrusted context, memory poisoning, and related runtime risks. The first useful output should be a concrete control stack: trust classes for context and state, per-tool or per-action permission classes, approval thresholds for side effects, post-tool and output validation, and incident evidence requirements. The goal is to build on governance, observability, and validation so that safety is treated as a concrete operational concern rather than only a general preference for caution.

## Cross-Workstream Dependencies

- `docs/future/runtime-governance.md` defines the intended route contracts, coordination patterns, state classes, and control boundaries for execution.
- `docs/future/runtime-observability-and-provenance.md` defines which events, state transitions, and provenance links must be visible and reconstructable.
- `docs/future/runtime-validation.md` defines how representative tasks, observable outcomes, and repeatable checks will be used to test the intended behavior.
- `docs/future/runtime-safety.md` defines runtime-risk framing, trust classes, and the control points that the design must satisfy.
- `docs/future/agent-context-engineering-patterns.md` provides the external context engineering layers: runtime defaults, user and team defaults, project instructions, scoped instructions, reusable workflows, runtime controls, and task prompt and session context.
- This document depends on all four workstreams and should not be treated as implementation-ready until those inputs are mature enough to support integrated decisions.

## Program Frame

Use the workstream documents for the current project assessment, open questions, and workstream-specific target models. Use `docs/future/agent-context-engineering-patterns.md` as source-backed context engineering input for those workstreams. Use this document for shared framing, cross-workstream dependencies, high-level sequencing, and later synthesis.

## Context Engineering Input

Use `docs/future/agent-context-engineering-patterns.md` for the external context engineering layers. Use `docs/runtime-model.md#current-codex-runtime-mapping` for how those layers map onto the current Codex and `AGENTS.md` operational path.

This runtime design should build on those inputs, but its focus is future governance, observability, validation, and safety decisions.

## Defined Terms

Use the terms below as the shared state taxonomy for the future runtime workstreams.

### Shared State Classes

| State class | Meaning | Default stability | Recomputable | Default trust | Normal carry-forward rule |
| --- | --- | --- | --- | --- | --- |
| `Stable policy` | Slow-changing guidance that defines durable runtime behavior, such as route contracts, tool policies, and safety constraints. | high | yes | high | should remain available across relevant runs until intentionally changed |
| `Task-local state` | Short-lived working state for the current task or branch, such as intermediate decisions, partial outputs, and current-step bookkeeping. | low to medium | sometimes | medium | carry forward only within the active task or branch |
| `Session history` | Prior messages, outputs, tool results, and other thread-local interaction history. | medium | no | mixed | carry forward only while still relevant to the active branch and context window |
| `Compaction summary` | A distilled summary of earlier session history created to preserve continuity after trimming or compaction. | medium | no | medium | carry forward only as an explicitly marked summary artifact, not as equivalent to full history |
| `Rediscovered project state` | Facts reloaded from the current project surface during execution, such as discovered files, current docs, and current routing structure. | medium | yes | medium to high | prefer fresh rediscovery over blind carry-forward when possible |
| `Retrieved context` | Context fetched during a run from search, retrieval, external systems, or other dynamic sources. | low | sometimes | lower-trust by default | carry forward only with provenance and only while still relevant |
| `Longer-lived memory` | State intended to persist beyond one task or session, such as reusable preferences, stored notes, or memory-bank content. | variable | no | variable and risk-sensitive | treat as a privileged subsystem with explicit rules for write, read, trust, and monitoring |

These classes are shared design terms, not yet implementation commitments. Governance should define how they are used, observability should define how they are exposed, validation should define how they are checked, and safety should define their trust and control requirements.

### Exemplar Tool Contract

Use the shape below as a shared example of what a first tool or interface contract should make explicit.

| Field | Purpose |
| --- | --- |
| `Tool name` | Stable identifier used in prompts, traces, and reviews. |
| `Purpose` | Short statement of the bounded capability the tool provides. |
| `Caller pattern` | Whether the tool is called directly by the active agent, through a manager-style specialist call, or through another bounded route contract. |
| `Inputs / schema` | Required arguments, schema constraints, and any input examples needed to make invocation reliable. |
| `Output shape` | Expected return format, including whether the output is free text, structured data, or a side-effect record. |
| `Permission class` | The minimum privilege level required to call the tool. |
| `Approval requirement` | Whether the tool can run automatically, needs conditional approval, or always requires explicit human approval. |
| `Allowed side effects` | What kinds of external changes the tool is allowed to make, if any. |
| `Post-call validation` | What must be checked after the tool runs, such as schema conformance, redaction, or side-effect confirmation. |
| `Required trace fields` | What the runtime must record for debugging, validation, and audit, such as tool name, arguments, result, approval state, and any side-effect outcome. |
| `Carry-forward rule` | What parts of the tool result may enter task-local state, session history, summaries, or longer-lived memory. |

This is an example contract shape, not an implementation format. Governance should define when this level of detail is required, observability should define how it is recorded, validation should define how it is checked, and safety should define which fields are risk-critical.

## Planned Synthesis Structure

Populate the sections below only after the workstream documents are mature enough to support integrated decisions.

### Runtime Governance Model

Define the intended route-contract model for routing, handoffs, manager-style specialist calls, tool permissions, state carry-forward, common execution paths, and governance-side compaction handling.

### Observability And Provenance Model

Define what runtime events, state transitions, route decisions, source linkage, and compaction artifacts must be visible and reconstructable.

### Validation Model

Define how the intended runtime behavior will be tested, measured, and accepted.

### Safety Model

Define the runtime-risk constraints, control points, and safety review requirements that the integrated design must satisfy.

### Integrated Decisions

Settle cross-workstream dependencies, conflicts, and tradeoffs that cannot be settled inside one workstream alone.

## Sequencing

1. Keep the current project assessments and source-backed comparisons aligned across the four workstreams and `docs/future/agent-context-engineering-patterns.md`.
2. Define a shared state taxonomy and a first route-contract bundle for one representative pilot path while keeping observability, validation, and safety dependencies explicit.
3. Advance Runtime Observability And Provenance from the current static visibility plus `Review Path Summary` baseline, Runtime Validation from the current shared review skill surface plus static and activation-eval checks, and Runtime Safety from the current lightweight review-driven surface by building around that pilot route contract and its evidence surfaces.
4. Synthesize the workstream outputs here and settle the integrated design decisions, guardrails, and acceptance criteria.

Avoid treating this as a strict waterfall. Governance decisions should remain provisional where they still depend on observability, validation, or safety work that has not yet matured enough to support them.

### Pilot Selection Criteria

Choose the first integrated pilot path using a shared rubric rather than by defaulting to the deepest existing workflow.

Prefer a path that has:

- a clear route contract or one that can be made clear with limited design work
- a meaningful success signal, acceptance condition, or user-visible outcome
- enough structure to expose state transitions, evidence surfaces, or approval boundaries
- bounded side effects and bounded risk while the design is still immature
- enough complexity to teach the workstreams something real, but not so much ambiguity that failures become hard to interpret

Use this rubric across governance, observability, validation, and safety. Different workstreams may emphasize different pilot paths, but they should explain that choice in terms of the same criteria.

## Open Integrated Questions

- Which tool, interface, and harness contracts must be explicit in the first integrated runtime design rather than deferred?
- Which route pattern should be used for the first integrated pilot: direct route, handoff, or manager-calls-specialist?
- How strong should the first integrated model be on session identity, checkpointing, and handoff artifacts for longer-running or resumed work?
- Which state classes need to be distinguished in the first integrated model: stable policy, task-local state, retrieved context, compaction summaries, and longer-lived memory?
- How should the validation workstream extend the static checks and activation eval fixture into a fuller eval program without making ordinary project work too heavy?
- How should observability extend beyond `Review Path Summary` and static skill/reference visibility without over-instrumenting ordinary project work?
- Which containment boundaries must the first integrated safety model choose explicitly, even if richer controls remain deferred?

## Completion Conditions

Treat this document as ready to drive implementation only when it:

- resolves the cross-workstream design decisions that remain open in the workstream documents
- defines the intended runtime behavior clearly enough for implementation work to proceed without additional planning decisions
- includes the observability, validation, and safety constraints needed to make the governance model operable
- states the guardrails and acceptance criteria that implementation should satisfy

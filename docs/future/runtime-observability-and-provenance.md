# Runtime Observability And Provenance Plan

## Overview

Use this plan to define the observability and provenance model for the runtime system.

Runtime Observability And Provenance is the design of the evidence surfaces that make runtime behavior inspectable, traceable, source-linked, and reconstructable. It covers not only traces and logs, but also visibility into route choice, instruction loading and applicability, session and state evolution, provenance for retrieved or carried-forward context, and what evidence must remain available after failures, compaction, resets, or longer-running work.

This workstream owns:

- traces, spans, and inspectable runtime state
- visibility into instruction loading and applicability, route decisions, and customization behavior
- source linkage and provenance for runtime context, retrieved evidence, and grounded outputs
- reconstructability after longer threads, failures, compaction, or explicit state handoff
- observability constraints and privacy tradeoffs that affect what validation and later review can rely on

Use `docs/future/runtime-design.md` for the umbrella program frame and shared terminology.

Do not use this plan to redefine routing policy, conflict-handling behavior among applicable instructions, customization behavior, or validation methodology. This workstream should make runtime behavior visible and reconstructable, not invent the behavior on its own.

## Dependencies

- Use `docs/future/runtime-governance.md` for intended runtime behavior that observability must expose.
- Feed evidence-surface requirements into `docs/future/runtime-validation.md`.
- Expose provenance and traceability surfaces needed by `docs/future/runtime-safety.md`.
- Contribute the observability and provenance portions of `docs/future/runtime-design.md`.

## Current Project Assessment

This section describes the current runtime observability and provenance state of `astro-agents`.

### Findings

#### Traceability

- the project is reasonably traceable at the static document level: `AGENTS.md`, `docs/architecture.md`, `docs/testing.md`, and `validation/README.md` make the intended routing and review surface legible to a human reviewer
- shared selector and combined-review outputs expose a short `Route Summary`, which makes the chosen validation path more legible than before
- actual runtime traceability is still weak: the project has no trace, span, or run record showing which dispatchers, selectors, prompts, or source-of-truth docs were actually discovered, loaded, or superseded during a concrete task
- this gap is most visible in `validation/`, where reviewers can see the declared review path but still cannot replay discovery or runtime instruction loading from evidence
- current status: partly covered

#### Instruction Applicability Visibility

- `docs/runtime-model.md` grounds instruction authority and conflict behavior in platform guidance, while the live repo docs intentionally avoid a stronger local applicability doctrine
- the project does not expose which prompts supplied active `Instructions` or which supporting docs were loaded as `Context` in any specific run, nor when broader guidance should stop applying after narrower routing
- instruction applicability is therefore grounded conceptually, but not visible as runtime evidence
- current status: weakly covered

#### Route-Decision Visibility

- root dispatchers and family selectors describe intended route-selection rules clearly, and selectors such as `validation/review/documentation-review.md` make some branch logic explicit
- shared selector and combined-review outputs expose the active review path through a `Route Summary`, but they do not record why one route or selector branch won, what competing branches were considered, or whether a route narrowed, broadened, or delegated work internally
- the generic routing wording recommended in `docs/usage.md` improves portability, but it also reduces visibility into the exact shared route a downstream repo expects to choose
- current status: partly covered

#### Local Customization And Instruction Authority Visibility

- `docs/runtime-model.md` and `docs/architecture.md` make the platform authority model and the repo's structural customization surfaces reasonably legible at design time
- local customization remains structural and inferred: the project has no runtime evidence surface showing when a local prompt superseded or supplemented a shared one or which conflicting `Instructions` actually applied
- explicit local customization boundaries are still limited, so most local customization behavior would have to be reconstructed from file layout and instruction authority rules
- current status: partly covered

#### Context Provenance

- provenance for static repo guidance is relatively strong: dispatchers and selectors point to named source-of-truth docs, shared reviews name their internal reusable prompts, and documentation-profile resolution is explicit
- provenance for runtime context is weak: the project does not define how carried-forward context, compacted summaries, rediscovered repo facts, or future retrieved evidence should remain linked to their source
- this means the repo can explain where guidance lives, but not yet where active runtime context came from
- current status: partly covered

#### State Visibility

- the current shared model has almost no explicit runtime state surface beyond review-path visibility and document-level routing
- there is no inspectable notion of active route, active `Instructions`, supporting `Context`, carry-forward state class, or stale-state marker
- the design docs name these needs, but the current project does not yet expose them
- current status: not meaningfully covered

#### Reconstructability

- in a fresh audit, a reviewer can often reconstruct the intended route from the repo structure, source-of-truth docs, validation selectors, and the `Route Summary` emitted by shared selector and combined-review outputs
- reconstructing which `Instructions` were active in a longer or compacted thread would still be inference-heavy because no runtime history preserves route choice, changes in instruction applicability, or state transitions
- the current review-path visibility helps on ordinary validation runs, but longer or divergent threads still lack the runtime history needed for reliable reconstruction
- current status: partly covered

#### Compaction And Reset Evidence

- the runtime program and governance plans already recognize compaction and rediscovery as important design concerns
- the current project does not define what evidence should survive compaction, reset, rerouting, or partial rediscovery, and it does not distinguish preserved summary state from newly rediscovered repo state
- compaction-related diagnosis would therefore rely mainly on retrospective reasoning
- current status: not meaningfully covered

#### Failure Analysis Support

- the shared review surface does produce structured findings for some bounded design failures, such as unsupported documentation profiles, undefined upgrade paths, and route-structure drift
- broader runtime failures such as wrong-route instruction applicability, stale active or carried-forward context, lost authority after narrowing, or compaction-related drift do not leave enough direct evidence for reliable diagnosis
- failure analysis is therefore stronger for document-design problems than for runtime-behavior problems
- current status: partly covered

#### Validation Support

- the current docs and review prompts provide a strong static evidence base for design review, which is why the existing validation family works reasonably well for prompt-surface maintenance
- they do not yet provide the runtime evidence surfaces that `docs/future/runtime-validation.md` needs for route checks, instruction-applicability checks, context-cost measurement, or longer-thread behavior validation
- until observability improves, behavior-facing validation will remain review-heavy and reconstruction-heavy
- current status: partly covered

#### Safety Review Support

- the current structure helps reviewers inspect intended routing, source-of-truth ownership, and some bounded failure cases, which gives safety review a useful static starting point
- it does not yet make unsafe runtime behavior observable enough to audit directly, especially for stale context, wrong-route instruction applicability, inherited customization effects, or future untrusted-context problems
- safety review can currently assess structure and policy more confidently than runtime evidence
- current status: weakly covered

#### Proportionality And Privacy

- the current repo is lightweight by design: it imposes almost no tracing overhead, preserves little extra runtime data, and keeps the prompt-library maintenance path cheap
- that proportionality is useful for ordinary repo work and lower-budget runtime paths, but it also leaves the project under-instrumented for this runtime-design program
- privacy and data-minimization tradeoffs are not yet explicitly designed; the current low-retention model is a consequence of missing observability surfaces rather than a stated policy
- current status: partly covered

### Recommended Observability Actions

**TO BE REVIEWED**

- the current baseline is lightweight observability: static routing and source-of-truth visibility plus `Route Summary` on shared selector and combined-review outputs
- preserve that lightweight baseline, but add explicit runtime evidence surfaces rather than relying on reconstruction from files alone
- define a small runtime event model that makes route choice, instruction loading and applicability, customization behavior, source-of-truth loading, and route changes inspectable
- define what the observable runtime state should include, especially active route, active `Instructions`, supporting `Context`, carry-forward state class, and stale-state markers
- make the active governing path and contributing prompts or docs visible after the relevant `Route` or `Handoff` so later inspection does not have to infer them indirectly
- define how runtime context should retain provenance when it comes from source-of-truth docs, carried-forward thread state, compaction summaries, rediscovery, or later external retrieval
- define what evidence should survive compaction, reset, rerouting, partial recovery, and rediscovery failure
- make customization outcomes and any future conflict-handling or narrowing outcomes visible enough that validation and later debugging do not have to infer them from file structure alone
- define the minimum observability surface needed to support routing and instruction-applicability validation without turning ordinary repo work into a heavy tracing workflow
- define what failure-analysis evidence should exist for wrong-route instruction applicability, stale carried-forward context, lost narrowing, unsupported profile handling, and degraded runtime cases
- define explicit reconstructability requirements for longer-thread and compaction-sensitive paths
- treat observability as a cross-workstream dependency: expose what validation needs to check, what safety needs to audit, and what governance decisions must be externally visible
- make privacy and data-minimization tradeoffs explicit instead of letting low observability act as an accidental policy
- use `validation/` as the first pilot area because it combines the deepest multi-step workflows with the weakest current runtime evidence

### Open Observability Questions

- what is the minimum runtime event set that would materially improve traceability without over-instrumenting ordinary repo work
- what should count as the observable source of truth for a concrete run: discovered files, loaded files, files contributing active `Instructions`, files used as supporting `Context`, or all of the above with different roles
- how should the system distinguish active `Instructions` from supporting `Context` and merely discovered but unused `Context`
- what should be visible when a route narrows, broadens, delegates internally, or is partially recovered after failure, especially how the active governing path should be exposed
- if the future governance model allows multiple active prompts or other multi-source coordination, how should those outcomes be surfaced without collapsing back into file-structure inference
- how should carried-forward thread context, compacted summaries, rediscovered repo facts, and any future retrieved evidence be distinguished for provenance purposes
- what evidence must survive compaction, reset, and rerouting for later reconstruction to remain trustworthy
- what observable state is needed to tell when context has become stale after narrower routing, source-of-truth loading, or failed rediscovery
- which observability requirements are preconditions for the first behavior-facing validation stage
- which runtime blind spots matter most for later safety review if the first observability surface stays intentionally lightweight
- what privacy, retention, and minimization rules should bound the observability model
- how much explicit runtime evidence can be added before common prompt-surface work becomes too expensive or noisy

## Next Design Steps

### Observable Runtime Target Set And Event Inventory

- use the current assessment in this document, the shared program frame in `docs/future/runtime-design.md`, and the current `Route Summary` surface as the basis for observability scope
- define the first explicit target set for what runtime behavior must become externally visible
- identify a first runtime event and state inventory for route choice, instruction loading and applicability, customization behavior, source loading, and route transitions
- separate common-path observability needs from longer-thread, compaction, and degraded-case needs so the first design stays proportional

### Evidence Surfaces

- treat the current `Route Summary` surface as the starting point for lightweight observability rather than as the finished observability model
- define what should be visible about instruction loading and applicability, route decisions, customization behavior, source-of-truth loading, and context additions
- define what observable runtime state should exist for active route, active `Instructions`, supporting `Context`, and stale-state markers
- define what source linkage or provenance is needed for carried-forward, compacted, rediscovered, or later retrieved context
- define what evidence should remain recoverable after compaction, reset, rerouting, or partial recovery

### Reconstruction And Audit Requirements

- define what it should mean to reconstruct a runtime path after a long thread, a summarized history, a reroute, or an unexpected result
- define how inspectable instruction applicability, route transitions, and source linkage should support later validation and failure analysis
- define what evidence should exist for wrong-route instruction applicability, stale carried-forward context, lost narrowing, and degraded recovery cases
- define the observability constraints that the governance design must satisfy

### Cross-Workstream Constraints

- identify which observability requirements are preconditions for behavior-focused validation
- identify which observability gaps create safety review blind spots
- identify where governance decisions still need observability-specific constraints
- identify what proportionality and privacy constraints should remain on the first observability surface

## Deliverables

- an observability workstream plan with stable current-state findings and target questions
- a maintained runtime event and state inventory for observability design
- a definition of the runtime evidence surfaces needed for traceability, reconstruction, provenance, and audit
- reconstruction and failure-analysis requirements for representative runtime paths
- observability and provenance inputs for `docs/future/runtime-design.md`

## Assumptions And Deferred Decisions

- This workstream starts lighter than Runtime Governance and Validation.
- Context observability should be treated as an audit and reconstruction problem, not only as a performance problem.
- The exact trace format, storage model, or tooling surface is deferred until the integrated runtime design is more stable.

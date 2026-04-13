# Runtime Governance Plan

## Overview

Use this plan to define the runtime-governance model for `astro-agents`.

Runtime Governance is the design of the control surfaces that determine what governs behavior at runtime, how work is routed or delegated, what instructions and context are applicable at each stage of execution, how conflicts and customization are handled, how context and state carry forward, and what boundaries exist around tools, permissions, side effects, and degraded operation.

Current working direction, pending later review: the future governance design should make the active governing path explicit after the relevant `Route` or `Handoff` rather than relying on broad co-owning instruction sources by default. Whether that should become a one-task-owner model or a different explicit coordination model remains open.

This workstream owns:

- instruction-source ordering and scope behavior
- routing, orchestration, and local customization behavior
- conflict-handling behavior among applicable instructions
- prompt use and workflow-boundary design
- governance-side context, state, and memory decisions
- governance-side tool, permission, and approval concerns
- governance-side failure, recovery, and bounded-autonomy behavior

Use `docs/future/runtime-design.md` for the umbrella program frame and shared terminology.

Do not use this plan to define validation methodology, observability surfaces, or safety review criteria except where those concerns materially constrain the governance model.

## Dependencies

- Use `docs/future/runtime-design.md` for the shared program frame, terminology alignment, and cross-workstream sequencing.
- Feed intended runtime behavior into `docs/future/runtime-validation.md`.
- Expose governance decisions that `docs/future/runtime-observability-and-provenance.md` must make visible.
- Surface governance control points that `docs/future/runtime-safety.md` must evaluate.
- Contribute the governance portions of `docs/future/runtime-design.md`.

## Current Project Assessment

This section describes the current runtime-governance state of `astro-agents`.

### Governance Surfaces

These findings are provisional and should be refined as the governance review continues.

The audit below uses a broader framing than the repo's day-to-day routing docs: instruction authority, orchestration, tool and interface contracts, context and state management, and human control around the agent loop. It keeps the repo-specific routing problems inside that broader runtime-governance view.

Observability, tracing, and eval design remain first-class concerns in the source material, but they belong primarily to `docs/future/runtime-observability-and-provenance.md` and `docs/future/runtime-validation.md`. This audit only covers them where they materially constrain the governance model.

#### Authority And Instruction Separation

- the repo clearly distinguishes `AGENTS.md` as routing map and deeper docs as source of truth, which helps separate discovery from detailed guidance
- the live surface keeps this boundary lightweight: it separates routing files from deeper supporting docs without trying to define a stronger repo-local `Instructions` versus `Context` doctrine
- root and family routing-and-workflow files therefore still rely mostly on structural routing and document-level authority rather than on an explicit execution-time control model
- the live docs avoid stronger authority-heavy claims, but future runtime-governance questions remain open around untrusted context, carried-forward context, and multi-step review paths
- no current rule says how carried-forward context, narrowed routes, and future lower-trust context should interact once the runtime model becomes more explicit
- current status: partly covered

#### Routing, Orchestration, And Delegation

- the root dispatcher provides a clear top-level split between `authoring` and `validation`, and routes upgrade design work into `authoring` while upgrade review goes through `validation`
- `authoring` and doc-led upgrade design work usually behave like direct routes that land quickly on one prompt family or source-of-truth document
- `validation` behaves more like multi-step `Workflow`s with internal prompts, especially in generic docs review and full agent-surface review
- the current repo is strongest when routing is treated as direct intent selection, and weakest where routing starts to act like implicit orchestration across multiple review passes
- some narrow routes already use explicit review-surface defaults, such as unsupported-profile handling in `validation/review/documentation-review.md` and the review-led upgrade model's explicit default to `private-default` when no profile is declared, but these do not yet amount to a broader runtime orchestration or degradation model
- the current system is still more file-structure-centric than the broader sources, which frame routing as runtime workflow control and delegation rather than only folder selection
- the project documents selectors and composites, but it does not define a general rule for when a route may orchestrate multiple internal passes, when that should instead become a thinner direct route, or when a route should hand work off to a narrower specialist path
- there is no explicit delegation or handoff model; multi-pass behavior is expressed as one prompt invoking others inside the same surface
- current status: partly covered

#### Instruction Applicability, Scope, And Local Customization Boundaries

- the live source-of-truth docs prefer explicit routing, explicit local follow-up prompts, and explicit local exceptions over broad additive-composition claims
- local and shared customization is still mostly structural through `AGENTS.md` files, subtree routing, and explicit routing into `agents/`, not through a richer extension-point model
- the documentation-surface-profile selector remains the clearest explicit local customization surface in the shared library
- the recommended downstream workspace model still optimizes for portability and generic routing wording, so runtime applicability remains more implicit than fully governed
- validation selectors often prefer the narrowest matching review, but the project intentionally leaves broader instruction-applicability semantics to platform behavior and later runtime design work
- current status: partly covered

#### Conflict Resolution And Ownership Transfer

- `docs/runtime-model.md` grounds conflict behavior mainly in the platform's instruction-discovery and instruction-following behavior rather than in a stronger repo-local conflict doctrine
- the live repo surface reduces overclaim by using direct routing, explicit local exceptions, and explicit follow-up prompts where possible
- mixed runtime cases involving parent prompts, local customization, and internal reusable prompts are still mostly unresolved at the future-governance level
- full-agent-surface review defines how overlapping findings are merged, but not how a future runtime model should assign ownership once multiple internal review steps contribute
- there is no clear future-governance rule for when conflicts should be prevented earlier by route choice, scope narrowing, or explicit local customization boundaries
- current status: partly covered

#### Prompt Use And Runtime Boundaries

- the file-type role model is clear at the architecture level: `AGENTS.md` routes, `README.md` explains, and prompt files carry substantive reusable behavior
- `validation/README.md` and `validation/AGENTS.md` clearly distinguish public review entrypoints, the upgrade-specific path, internal documentation workflows, and reusable internal review files
- selectors such as `documentation-review.md` and `full-agent-surface-review.md` still act as directly user-addressable coordinating prompts that invoke deeper internal workflow steps
- the repo does not yet define when a reusable prompt may be directly user-addressable versus internal only, or when a selector should stop shaping behavior after it selects a deeper prompt
- this remains a local repo concern rather than a universal runtime-governance category, but it materially affects the current design
- current status: partly covered

#### Tool And Interface Contracts

- the primary sources treat tool definition, namespacing, schemas, and returned-context quality as central runtime-governance surfaces rather than optional implementation details
- the shared runtime surface in this repo does not currently define tool contracts, tool schemas, tool namespacing, or returned-context standards
- adjacent guidance exists in code-authoring and upgrading documents, but it is not yet part of the shared runtime-governance model for `astro-agents`
- this gap is therefore incomplete rather than immediately blocking, but the first integrated runtime design should explicitly say whether tool and interface governance remains out of scope or needs an initial contract model
- current status: not meaningfully covered yet

#### Context, State, And Memory Governance

- the project strongly prefers progressive disclosure and routing rediscovery from current repo state, which is a good starting point for bounded context use
- compaction resilience matters because long instruction chains are fragile in longer threads
- generic applicability wording in `docs/usage.md` improves portability but increases dependence on a multi-step route being rediscovered correctly at runtime
- the live surface avoids a stronger repo-local carry-forward or applicability model, so state boundaries remain minimal rather than explicit
- the stronger primary-source framing distinguishes stable policy, task-local state, retrieved context, session continuity, and longer-lived memory; the repo does not yet have an equivalent runtime state model
- no current rule says what should persist, what must be rediscovered, or what becomes stale after route changes, scope narrowing, compaction, or longer-thread summarization
- current status: partly covered

#### Human Control, Permissions, And Side-Effect Boundaries

- the shared runtime surface does not define a general permission model, approval model, or side-effect boundary
- `docs/upgrade-design.md` does use an explicitly review-led loop with user-chosen grouping of the work, which shows the project can express human-in-the-loop control even without a separate approval-state machine, but those controls are not yet part of the shared runtime model itself
- the strongest external sources frame human review, approvals, and guardrails as core boundaries for agentic execution rather than optional safety extras
- for `astro-agents`, this area is currently underdeveloped rather than actively wrong: the runtime model is not yet rich enough for these controls to be exercised often, but it also does not define whether they are intentionally out of scope
- current status: not meaningfully covered yet

#### Failure Recovery, Runtime Limits, And Degradation

- some bounded failure behavior already exists: documentation review returns a validation-architecture finding for unsupported profiles, upgrade review defaults to review-first handling instead of blocking on missing profile declarations, and validation routing defaults to narrower matching reviews and the requested target root when scope is unclear
- those rules are useful local fail-stop or narrow-default behaviors, not a general runtime recovery model
- recomputable routing is implicitly a recovery requirement, especially after compaction or route drift
- deeper validation routing is more failure-prone than the direct routes used in `authoring` and doc-led upgrade design work, and the current docs do not define what should happen when generic applicability wording or workspace-root routing fails to rediscover the intended path
- the stronger source framing also emphasizes cost, coordination limits, and long-horizon brittleness; the repo does not yet translate those limits into explicit governance rules for degraded operation
- the current system does not yet define a broader bounded-autonomy model for what should happen when the preferred route is ambiguous, missing, partially recoverable, or too expensive to keep composing
- current status: partly covered

### Current Route Examples

Use these representative routes to compare the current governance model against later target routes.

| Task | Current route | Current shape | Main observation |
| --- | --- | --- | --- |
| Repo-doc authoring | `AGENTS.md` -> `authoring/AGENTS.md` -> `authoring/writing/repo-docs.md` | direct `Route` | relatively shallow and easy to reason about |
| Python code authoring | `AGENTS.md` -> `authoring/AGENTS.md` -> `authoring/code/AGENTS.md` -> `authoring/code/python.md` | direct `Route` with language-specific branch | acceptable baseline for a common authoring path |
| Generic docs review | `AGENTS.md` -> `validation/AGENTS.md` -> `validation/review/documentation-review.md` -> profile-specific internal prompts | multi-step `Workflow` | deeper than common review requests should need and already behaves like implicit orchestration |
| Full agent-surface review | `AGENTS.md` -> `validation/AGENTS.md` -> `validation/review/full-agent-surface-review.md` -> shared review passes -> documentation route -> profile-specific internal prompts -> local validation | multi-step `Workflow` with synthesis | the most expensive common route and the clearest example of mixed routing, coordination depth, and unclear ownership |
| Upgrade review | `AGENTS.md` -> `validation/AGENTS.md` -> `validation/review/upgrade-review.md` -> shared review passes | multi-step `Workflow` | intentionally review-led and no longer modeled as a separate prompt family |

### Recommended Governance Actions

**TO BE REVIEWED**

- the current live surface keeps authority-heavy claims light, clearly distinguishes public versus internal review paths, and keeps the routing model intentionally minimal
- preserve that lighter live surface as the baseline rather than reintroducing a stronger repo-local runtime theory into `AGENTS.md`, `README.md`, `docs/usage.md`, or the shared review entrypoints
- if the integrated runtime design needs it, define a runtime authority model that distinguishes active `Instructions`, supporting `Context`, and untrusted or merely informative context
- if the integrated runtime design needs stronger source-of-truth delegation rules, define when named docs stay supporting `Context` and when higher-authority `Instructions` may explicitly delegate narrower authority to them
- decide whether the integrated runtime design should use one task-owning prompt after the relevant `Route` or `Handoff`, or whether a different coordination model is more appropriate
- treat routing as a workflow-control problem, not only a folder-selection problem; define when a path is a direct route, when it orchestrates internal passes, and when ownership should transfer
- if the integrated runtime design adopts stronger applicability semantics, define when instructions or context become applicable, when narrower scope removes broader applicability, and what limited multi-source coordination is still allowed
- use explicit local customization boundaries for repo-local and workspace-local customization if the future runtime design moves beyond the current lighter structural model
- keep local and workspace customization bounded to named extension points instead of allowing open-ended matching across the active subtree
- use the documentation-surface-profile selector as a model for more explicit local customization behavior where that pattern fits
- define which shared prompts are directly user-addressable and which are internal to a `Workflow`, and keep internal review workflows and reusable prompts off common request paths unless they are intentionally user-addressable
- settle more conflicts early through route choice, scope narrowing, local customization design, and clearer output ownership instead of relying only on late conflict handling among loaded instructions
- require common routing paths to be recomputable from the current request and current repo state
- treat `compaction resilience` as a governance requirement rather than as a secondary runtime concern
- define an explicit runtime state model covering carried-forward context, compacted summaries, rediscovered repo state, and any longer-lived memory assumptions
- define what becomes stale after route changes, narrower routing, compaction, or failed rediscovery
- decide whether tool and interface governance remains out of scope for the first integrated runtime design or needs an initial contract model now
- decide whether human-control, permission, and approval boundaries should be minimally defined now or explicitly deferred
- define explicit failure, degradation, and recovery behavior for ambiguous routes, missing implementations, rediscovery failures, partial recovery, and context loss
- use `validation/` as the first pilot area because it currently combines the deepest common request paths, the weakest prompt-use boundaries, and the highest context cost
- preserve the direct route patterns that already work well in `authoring/` and in source-of-truth-driven design work such as `docs/upgrade-design.md`
- if downstream templates later need a stronger runtime model, revise them from the current direct-routing baseline rather than reintroducing broad additive overlay language
- define concrete governance guardrails, including acceptable route depth for common requests, which prompts may remain active inside one `Workflow`, what may persist across longer threads, and which prompts are never meant to stay on the normal runtime path
- make governance decisions explicit enough that the observability and validation workstreams can trace and test them directly

### Open Governance Questions

- what route-structure model should shape runtime behavior beyond document instruction authority, especially for supporting docs, internal reusable prompts, and carried-forward context
- what should count as active `Instructions` at runtime versus supporting, discoverable, or untrusted context
- in what narrow cases, if any, should higher-authority `Instructions` explicitly delegate narrower authority to a loaded source-of-truth doc beyond its default role as supporting `Context`
- is the current public-entrypoint versus internal-workflow split in `validation/` the right long-term boundary, or should some selectors become thinner or more internal
- when should a task stay on a direct route, when should it orchestrate multiple internal passes, and when would explicit delegation or ownership transfer be justified
- when should instructions or context stop being applicable, especially after route changes, scope narrowing, compaction, or longer-thread summarization
- how should conflicts be resolved between parent prompts, local customization, and internal reusable prompts during execution rather than only at document-structure level
- who owns the synthesized output when a route invokes multiple internal review passes or review workflows
- what mechanism should define a local customization boundary for repo-local and workspace-local customization
- how explicit the downstream routing templates in `docs/usage.md` should become before they give up too much portability
- what maximum route depth is acceptable for common requests in lower-budget subscription tiers
- whether `full-agent-surface-review.md` should remain directly user-addressable, become a thinner coordinating prompt, or be split into a different user-addressable prompt plus internal workflow logic
- what the future runtime model should expose about repo-local follow-on review inclusion after the shared validation path is active
- whether the first integrated runtime design should treat tool and interface governance as intentionally out of scope or define an initial contract model
- whether the first integrated runtime design should define any permission or approval model for consequential actions, or explicitly defer that surface
- how should carried-forward context, compacted summaries, rediscovered repo state, and any longer-lived memory be distinguished in this repo's runtime model
- what becomes stale after route changes, scope narrowing, or context resets
- what explicit recovery and degraded-operation behavior should exist when a preferred route is ambiguous, unavailable, too expensive to keep composing, or cannot be re-established cleanly

## Maturation Path

### Stage 1 — Clarify Current-State Governance

- apply the shared program frame to authority, routing, instruction applicability, customization behavior, state boundaries, and bounded runtime control
- identify which governance questions remain local to this workstream after the cross-workstream audit
- refine the current-state governance model for this repo using the shared terminology and recommendation set
- carry the clarified current-state governance reading into `docs/future/runtime-design.md`

### Stage 2 — Define The Governance Model

- define the runtime authority model and the boundary between active `Instructions`, supporting `Context`, and lower-trust context
- define any ownership or handoff model after the relevant `Route` or `Handoff`, plus prompt roles, route types, conflict handling among applicable instructions, local customization boundaries, and synthesized-output ownership
- define governance-side context, state, carry-forward, stale-context, rediscovery-failure, tool, permission, approval, and failure-recovery expectations
- carry the first governance decision set into `docs/future/runtime-design.md`

### Stage 3 — Define Representative Target Routes

- reduce routing hops for common requests while preserving the direct-route patterns that already work in `authoring/` and source-of-truth-driven design work
- redesign the common validation route as the first pilot area
- define which shared review prompts stay directly user-addressable, which become thinner coordinating prompts, and which become internal-only reusable prompts
- compare current routes against target routes for representative tasks and carry the target-route inputs into `docs/future/runtime-design.md`

### Stage 4 — Define Governance Guardrails

- define acceptable route depth for common requests
- define when direct routing is required and whether any limited multi-source applicability should remain allowed
- define compaction-resilience, stale-context, degraded-operation, and recomputability expectations
- define the governance inputs that observability, validation, and safety must later satisfy

## Deliverables

- a runtime-governance workstream plan with stable current-state findings and target questions
- a target governance model for authority, routing, instruction applicability, local customization behavior, local customization boundaries, prompt-use boundaries, state boundaries, and bounded runtime control
- governance guardrails and target-route inputs for `docs/future/runtime-design.md`

## Assumptions And Deferred Decisions

- Validation remains the first pilot area unless later governance review shows a different path offers larger runtime gains.
- This plan defines intended runtime behavior. It does not define the test harness or regression strategy for proving that behavior.
- The exact implementation-facing format of governance decisions is deferred to `docs/future/runtime-design.md`.

# Runtime Governance Plan

## Overview

Use this plan to define the runtime-governance model for `astro-agents`.

Runtime Governance is the design of the control surfaces that determine what governs behavior at runtime: how work is routed or delegated, which pattern a branch uses, who owns the next user-facing output, which tools or specialists are allowed, how context and state carry forward, and what boundaries exist around permissions, side effects, and degraded operation.

Current working direction, pending later review: the future governance design should make branch-level coordination explicit after the relevant `Route` or `Handoff` rather than relying on broad co-owning instruction sources by default. The main open design choice is not whether one global ownership doctrine exists, but which coordination pattern each route should use and what that pattern allows to carry forward.

This workstream owns:

- instruction-source ordering and scope behavior
- routing, orchestration, and local customization behavior
- route-contract and workflow-boundary design
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

The audit below uses a broader framing than the project's day-to-day skill and instruction docs: instruction authority, orchestration, tool and interface contracts, context and state management, and human control around the agent loop. It keeps project-specific control-flow questions inside that broader runtime-governance view.

Observability, tracing, and eval design remain first-class concerns in the source material, but they belong primarily to `docs/future/runtime-observability-and-provenance.md` and `docs/future/runtime-validation.md`. This audit only covers them where they materially constrain the governance model.

#### Authority And Instruction Separation

- the project clearly distinguishes `AGENTS.md` as project-local working brief and deeper docs as source of truth, which helps separate discovery from detailed guidance
- the live surface keeps this boundary lightweight: it separates instruction files from deeper supporting docs without trying to define a stronger project-local `Instructions` versus `Context` doctrine
- root and subtree instruction files therefore still rely mostly on
  document-level authority and explicit skill invocation rather than on an
  execution-time control model
- the live docs avoid stronger authority-heavy claims, but future runtime-governance questions remain open around untrusted context, carried-forward context, and multi-step review paths
- no current rule says how carried-forward context, narrowed routes, and future lower-trust context should interact once the runtime model becomes more explicit
- current status: partly covered

#### Routing, Orchestration, And Delegation

- runtime skill discovery provides a clear user-facing capability surface, including writing skills, review skills, research logging, and upgrade planning
- writing skills and skill-local upgrade model work usually use direct skill
  invocations that lead quickly to one skill or source-of-truth document
- `agent-surface-review` behaves more like a multi-step `Workflow` with internal references, especially in documentation review and full agent-surface review
- the current project is strongest when routing is treated as direct intent selection, and weakest where routing starts to act like implicit orchestration across multiple review passes
- some narrow routes already use explicit review-surface defaults, such as unsupported-profile handling in `skills/documentation-surface-review/SKILL.md` and the review-led upgrade model's explicit default to `private-default` when no profile is declared, but these do not yet amount to a broader runtime orchestration or degradation model
- the current system is still more file-structure-centric than the broader sources, which frame routing as runtime workflow control and delegation rather than only folder selection
- the project documents selectors and composites, but it does not define a general rule for when a route may orchestrate multiple internal passes, when that should instead become a thinner direct route, or when a route should hand work off to a narrower specialist path
- there is no explicit delegation or handoff model; multi-pass behavior is expressed as one prompt invoking others inside the same surface
- current status: partly covered

#### Trust, Scope, And Carry-Forward Boundaries

- the live source-of-truth docs prefer explicit routing, explicit local follow-up prompts, and explicit local exceptions over broad additive-composition claims
- local and shared customization is still mostly structural through `AGENTS.md` files, subtree routing, and explicit routing into `agents/`, not through a richer extension-point model
- the documentation-surface-profile selector remains the clearest explicit local customization surface in the shared library
- the recommended downstream shared-environment model still optimizes for portability and generic routing wording, so trust, scope, and carry-forward boundaries remain more implicit than fully governed
- validation selectors often prefer the narrowest matching review, but the project intentionally leaves deeper instruction-applicability semantics to platform behavior and later runtime design work
- the larger unresolved issue is not raw applicability theory, but when route narrowing, local customization, or newer source material should change what may still carry forward into the active branch
- current status: partly covered

#### Conflict Resolution And Ownership Transfer

- `docs/runtime-model.md` grounds conflict behavior mainly in the platform's instruction-discovery and instruction-following behavior rather than in a stronger project-local conflict doctrine
- the live project surface reduces overclaim by using direct routing, explicit local exceptions, and explicit follow-up prompts where possible
- mixed runtime cases involving parent prompts, local customization, and internal reusable prompts are still mostly unresolved at the future-governance level
- full-agent-surface review defines how overlapping findings are merged, but not how a future runtime model should assign ownership once multiple internal review steps contribute
- there is no clear future-governance rule for when conflicts should be prevented earlier by route choice, scope narrowing, or explicit local customization boundaries
- current status: partly covered

#### Prompt Use And Runtime Boundaries

- the file-type role model is clear at the architecture level: `AGENTS.md` routes, `SKILL.md` packages carry user-facing capability wrappers, references carry detailed reusable behavior, and docs own source-of-truth context
- `skills/agent-surface-review/SKILL.md`, `skills/documentation-surface-review/SKILL.md`, `skills/code-quality-review/SKILL.md`, and `skills/project-upgrade-planning/SKILL.md` distinguish the user-facing review/planning surface from internal review references
- references such as `full-agent-surface-review.md` still coordinate deeper internal workflow steps once the skill is active
- the project does not yet define when a skill reference should stop shaping behavior after it selects a deeper reference
- this remains a local project concern rather than a universal runtime-governance category, but it materially affects the current design
- current status: partly covered

#### Tool And Interface Contracts

- the primary sources treat tool definition, namespacing, schemas, and returned-context quality as central runtime-governance surfaces rather than optional implementation details
- the shared runtime surface in this project does not currently define tool contracts, tool schemas, tool namespacing, or returned-context standards
- adjacent guidance exists in code-writing and upgrading documents, but it is not yet part of the shared runtime-governance model for `astro-agents`
- this gap is therefore incomplete rather than immediately blocking, but the first integrated runtime design should explicitly say whether tool and interface governance remains out of scope or needs an initial contract model
- current status: not meaningfully covered yet

#### Context, State, And Memory Governance

- the project strongly prefers progressive disclosure and routing rediscovery from current project state, which is a good starting point for bounded context use
- compaction resilience matters because long instruction chains are fragile in longer threads
- generic applicability wording in `docs/usage.md` improves portability but increases dependence on a multi-step route being rediscovered correctly at runtime
- the live surface avoids a stronger project-local carry-forward or applicability model, so state boundaries remain minimal rather than explicit
- the stronger primary-source framing distinguishes stable policy, task-local state, retrieved context, session continuity, and longer-lived memory; the project does not yet have an equivalent runtime state model
- no current rule says what should persist, what must be rediscovered, or what becomes stale after route changes, scope narrowing, compaction, or longer-thread summarization
- current status: partly covered

#### Human Control, Permissions, And Side-Effect Boundaries

- the shared runtime surface does not define a general permission model, approval model, or side-effect boundary
- `skills/project-upgrade-planning/references/upgrade-model.md` does use an explicitly review-led loop with user-chosen grouping of the work, which shows the project can express human-in-the-loop control even without a separate approval-state machine, but those controls are not yet part of the shared runtime model itself
- the strongest external sources frame human review, approvals, and guardrails as core boundaries for agentic execution rather than optional safety extras
- for `astro-agents`, this area is currently underdeveloped rather than actively wrong: the runtime model is not yet rich enough for these controls to be exercised often, but it also does not define whether they are intentionally out of scope
- current status: not meaningfully covered yet

#### Failure Recovery, Runtime Limits, And Degradation

- some bounded failure behavior already exists: documentation review returns a validation-architecture finding for unsupported profiles, upgrade review defaults to review-first handling instead of blocking on missing profile declarations, and validation routing defaults to narrower matching reviews and the requested target root when scope is unclear
- those rules are useful local fail-stop or narrow-default behaviors, not a general runtime recovery model
- recomputable routing is implicitly a recovery requirement, especially after compaction or route drift
- deeper review routing is more failure-prone than the direct routes used in writing skills and skill-local upgrade model work, and the current docs do not define what should happen when generic applicability wording or shared-environment routing fails to rediscover the intended path
- the stronger source framing also emphasizes cost, coordination limits, and long-horizon brittleness; the project does not yet translate those limits into explicit governance rules for degraded operation
- the current system does not yet define a broader bounded-autonomy model for what should happen when the preferred route is ambiguous, missing, partially recoverable, or too expensive to keep composing
- current status: partly covered

### Current Route Examples

Use these representative routes to compare the current governance model against later target routes.

| Task | Current route | Current shape | Main observation |
| --- | --- | --- | --- |
| Project documentation writing | `AGENTS.md` -> `skills/project-docs-writing/SKILL.md` -> `references/project-docs.md` | direct `Route` | relatively shallow and easy to reason about |
| Python code writing | `AGENTS.md` -> `skills/python-code-writing/SKILL.md` -> `references/python.md` | direct `Route` with language-specific capability | acceptable baseline for a common code-writing path |
| Generic docs review | `AGENTS.md` -> `skills/documentation-surface-review/SKILL.md` -> `references/documentation-review.md` -> profile-specific internal references | multi-step `Workflow` | now isolated as its own user-facing review skill, but still deeper than direct writing requests |
| Full agent-surface review | `AGENTS.md` -> `skills/agent-surface-review/SKILL.md` -> internal review references -> `skills/documentation-surface-review/SKILL.md` as documentation branch -> local validation | multi-step `Workflow` with synthesis | the most expensive common route and the clearest example of mixed routing, coordination depth, and unclear ownership |
| Upgrade planning | `AGENTS.md` -> `skills/project-upgrade-planning/SKILL.md` -> `references/upgrade-review.md` -> supporting agent-surface review as needed | multi-step `Workflow` | intentionally review-led and no longer modeled as a separate prompt family |

### Recommended Governance Actions

**TO BE REVIEWED**

- the current live surface keeps authority-heavy claims light, clearly distinguishes public versus internal review paths, and keeps the routing model intentionally minimal
- preserve that lighter live surface as the baseline rather than reintroducing a stronger project-local runtime theory into `AGENTS.md`, `README.md`, `docs/usage.md`, or the shared review skills
- define a route-contract bundle for each representative branch: route pattern, owner of the next user-facing output, allowed specialists or tools, allowed carried-forward state, approval boundaries, and expected result surface
- treat routing as a workflow-control problem, not only a folder-selection problem; define when a path is a direct route, when it orchestrates internal passes, when it uses a manager-calls-specialist pattern, and when a true handoff is justified
- add specialists only when the contract changes materially through different tools, instructions, policy, or approval surfaces
- if the integrated runtime design needs it, define a runtime trust model that distinguishes active instructions, supporting context, and lower-trust or untrusted context
- if the integrated runtime design adopts stronger applicability semantics, define when instructions or context become applicable, when narrower scope removes broader applicability, and what limited multi-source coordination is still allowed
- use explicit local customization boundaries for project-local and workspace-local customization if the future runtime design moves beyond the current lighter structural model
- keep local and workspace customization bounded to named extension points instead of allowing open-ended matching across the active subtree
- use the documentation-surface-profile selector as a model for more explicit local customization behavior where that pattern fits
- define which shared skills are directly user-addressable and which references are internal to a `Workflow`, and keep internal review workflows and reusable references off common request paths unless they are intentionally user-addressable
- settle more conflicts early through route choice, scope narrowing, local customization design, and clearer output ownership instead of relying only on late conflict handling among loaded instructions
- require common routing paths to be recomputable from the current request and current project state
- treat `compaction resilience` as a governance requirement rather than as a secondary runtime concern
- define an explicit runtime state model covering stable policy, task-local state, compaction summaries, rediscovered project state, retrieved context, and any longer-lived memory assumptions
- define what becomes stale after route changes, narrower routing, compaction, or failed rediscovery
- define a first tool and interface contract model covering names, schemas, return-shape expectations, and which tools may be called directly versus through a manager path
- decide whether human-control, permission, and approval boundaries should be minimally defined now or explicitly deferred
- define explicit failure, degradation, and recovery behavior for ambiguous routes, missing implementations, rediscovery failures, partial recovery, and context loss
- choose the first pilot area using the shared pilot-selection rubric in `docs/future/runtime-design.md`; `agent-surface-review` is a strong candidate for routing and visibility work, but not the only option for tool, approval, or safety design
- preserve the direct route patterns that already work well in writing skills and in skill-local model work such as `skills/project-upgrade-planning/references/upgrade-model.md`
- if downstream templates later need a stronger runtime model, revise them from the current direct-routing baseline rather than reintroducing broad additive overlay language
- define concrete governance guardrails, including acceptable route depth for common requests, which prompts may remain active inside one `Workflow`, what may persist across longer threads, and which route contracts require explicit approval or richer evidence
- make governance decisions explicit enough that the observability and validation workstreams can trace and test them directly

### Open Governance Questions

- what route-contract bundle should be defined for each representative branch, especially ownership of the next reply, allowed tools or specialists, and allowed carry-forward
- which routes should stay direct, which should use manager-style specialist calls, and which would justify a true handoff
- what should count as active instructions at runtime versus supporting, discoverable, or untrusted context
- is the current user-facing skill versus internal-reference split the right long-term boundary, or should some selectors become thinner or more internal
- when should a task stay on a direct route, when should it orchestrate multiple internal passes, and when would explicit delegation or ownership transfer be justified
- when should instructions or context stop being applicable, especially after route changes, scope narrowing, compaction, or longer-thread summarization
- how should conflicts be resolved between parent prompts, local customization, and internal reusable prompts during execution rather than only at document-structure level
- who owns the synthesized output when a route invokes multiple internal review passes or review workflows
- what mechanism should define a local customization boundary for project-local and workspace-local customization
- how explicit the downstream routing templates in `docs/usage.md` should become before they give up too much portability
- what maximum route depth is acceptable for common requests in lower-budget subscription tiers
- whether `skills/agent-surface-review/SKILL.md` should remain the single user-addressable review skill or split into thinner user-facing skills plus internal workflow logic
- what the future runtime model should expose about project-local follow-on review inclusion after the shared validation path is active
- whether the first integrated runtime design should treat tool and interface governance as intentionally out of scope or define an initial contract model
- whether the first integrated runtime design should define any permission or approval model for consequential actions, or explicitly defer that surface
- how should stable policy, task-local state, compaction summaries, rediscovered project state, retrieved context, and any longer-lived memory be distinguished in this project's runtime model
- what becomes stale after route changes, scope narrowing, or context resets
- what explicit recovery and degraded-operation behavior should exist when a preferred route is ambiguous, unavailable, too expensive to keep composing, or cannot be re-established cleanly

## Maturation Path

### Stage 1 — Clarify Current-State Governance

- apply the shared program frame to route contracts, trust boundaries, customization behavior, state classes, and bounded runtime control
- identify which governance questions remain local to this workstream after the cross-workstream audit
- refine the current-state governance model for this project using the shared terminology and recommendation set
- carry the clarified current-state governance reading into `docs/future/runtime-design.md`

### Stage 2 — Define The Governance Model

- define the route-contract model for representative branches, including direct routes, manager-style specialist calls, and any true handoffs
- define the runtime trust model and the boundary between active instructions, supporting context, and lower-trust context
- define governance-side context, state classes, carry-forward, stale-context, rediscovery-failure, tool, permission, approval, and failure-recovery expectations
- carry the first governance decision set into `docs/future/runtime-design.md`

### Stage 3 — Define Representative Target Routes

- reduce routing hops for common requests while preserving the direct-route patterns that already work in writing skills and source-of-truth-driven design work
- choose a first pilot area with clear runtime control boundaries and objective feedback; `agent-surface-review` remains one candidate, especially for routing and visibility work
- define which shared review skills stay directly user-addressable, which become thinner coordinating skills, and which references stay internal-only
- compare current routes against target routes for representative tasks and carry the target-route inputs into `docs/future/runtime-design.md`

### Stage 4 — Define Governance Guardrails

- define acceptable route depth for common requests
- define when direct routing is required and whether any limited multi-source applicability should remain allowed
- define compaction-resilience, stale-context, degraded-operation, and recomputability expectations
- define the governance inputs that observability, validation, and safety must later satisfy

## Deliverables

- a runtime-governance workstream plan with stable current-state findings and target questions
- a target governance model for route contracts, routing, instruction applicability, local customization behavior, prompt-use boundaries, state boundaries, tool contracts, and bounded runtime control
- governance guardrails and target-route inputs for `docs/future/runtime-design.md`

## Assumptions And Deferred Decisions

- No pilot area is assumed globally; the first pilot should be chosen for signal quality, control-boundary clarity, and validation value.
- This plan defines intended runtime behavior. It does not define the test harness or regression strategy for proving that behavior.
- The exact implementation-facing format of governance decisions is deferred to `docs/future/runtime-design.md`.

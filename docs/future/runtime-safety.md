# Runtime Safety Plan

## Overview

Use this plan to define the runtime-safety model for the program.

Runtime Safety is the design of the boundaries, control points, and review criteria that reduce harm from runtime behavior. It covers not only prompt injection and other adversarial inputs, but also unsafe instruction following, untrusted context, data exfiltration, excessive agency, tool and action permissions, human approvals, side-effect isolation, memory or state poisoning, and degraded behavior under uncertainty or failure.

Across the reviewed sources, runtime safety is treated as a layered control problem rather than a single guardrail. The recurring control surfaces are structured data flow, least privilege at tool and identity boundaries, explicit approval for consequential actions, isolation of untrusted content, tracing and evaluation for safety-relevant behavior, and human review where residual risk remains meaningful.

This workstream owns:

- runtime threat framing for routing, instruction applicability, context carry-forward, and longer-running state
- safety constraints on untrusted inputs, retrieved content, and stored or carried-forward context
- least-privilege, approval, and bounded-side-effect requirements for tool and action surfaces
- safety review criteria and control points that the governance, observability, and validation workstreams must satisfy
- explicit treatment of residual risk and the cases where human review must remain in the loop

Use `docs/future/runtime-design.md` for the umbrella program frame and shared terminology.

Do not use this plan to redefine routing policy, observability surfaces, or validation methods. This workstream should frame risk and required controls, then rely on the other workstreams for the underlying behavior, evidence, and checks.

## Dependencies

- Use `docs/future/runtime-governance.md` for intended runtime behavior and control-point placement.
- Use `docs/future/runtime-observability-and-provenance.md` for the evidence needed to inspect or investigate runtime-risk cases.
- Use `docs/future/runtime-validation.md` for the checks that should exercise safety-relevant runtime behavior.
- Contribute the safety portions of `docs/future/runtime-design.md`.

## Current Project Assessment

This section describes the current runtime-safety state of `astro-agents`.

### Assessment Criteria

Assess the current project against these criteria:

- `Instruction And Trust Boundaries`
  - can the project distinguish active `Instructions`, supporting `Context`, and untrusted or merely informative context well enough to reduce unsafe instruction following
- `Routing And Instruction-Applicability Safety`
  - does the runtime structure reduce the risk of making the wrong instructions applicable, preserving stale carried-forward context, or widening active context unsafely
- `Untrusted Context And Injection Exposure`
  - does the current design bound risks from prompt injection, indirect prompt injection, and other untrusted content entering the runtime path
- `Carry-Forward, Memory, And State Safety`
  - does the project reduce the risk of stale, poisoned, or contaminated context persisting across turns, summaries, rediscovery, or longer-running work
- `Least Privilege And Permission Boundaries`
  - does the runtime model define meaningful limits on what the system may access or do, especially as tool use or external actions expand
- `Approval And Side-Effect Boundaries`
  - does the project define where consequential actions should require explicit human approval or stronger containment
- `Failure Containment And Degraded Operation`
  - does the design fail safely when routing is ambiguous, context is missing, recovery is partial, or runtime confidence is low
- `Incident Visibility And Forensics`
  - does the current system leave enough evidence to investigate safety-relevant failures rather than relying only on reconstruction
- `Safety Validation Support`
  - does the project have, or clearly support, validation methods for the runtime behaviors that matter most to safety
- `Residual Risk And Human Oversight`
  - does the project make clear where human review must remain in the loop because runtime controls are insufficient or intentionally bounded
- `Proportionality`
  - are safety controls likely to stay practical for ordinary repo work rather than becoming so heavy that they will be skipped or bypassed

For `astro-agents` now, the primary criteria are `Instruction And Trust Boundaries`, `Routing And Instruction-Applicability Safety`, `Carry-Forward, Memory, And State Safety`, `Failure Containment And Degraded Operation`, and `Incident Visibility And Forensics`. `Least Privilege And Permission Boundaries` and `Approval And Side-Effect Boundaries` are still important, but they are more forward-looking until the runtime model becomes more explicitly tool-capable and action-capable.

### Findings

#### Instruction And Trust Boundaries

- the repo does distinguish routing files from deeper source-of-truth docs, which is a useful first trust boundary because it reduces accidental reliance on one large undifferentiated prompt surface
- the live surface keeps that boundary intentionally lightweight instead of defining a stronger local trust model, so it still does not specify how untrusted context, carried-forward context, or stale-state handling should work once multiple prompts and docs have been discovered
- there is also no explicit rule for when carried-forward context should be downgraded to supporting `Context` or discarded after narrower guidance or newly loaded source-of-truth docs appear
- current status: partly covered

#### Routing And Instruction-Applicability Safety

- direct routes in `authoring/` and source-of-truth-driven upgrade design work are relatively bounded and therefore lower-risk than the deeper multi-step workflow paths in `validation/`
- the live surface prefers explicit routing and explicit local follow-on paths over broad additive applicability claims, which reduces one source of ambiguity on the current review surface
- some bounded review-surface defaults already exist, such as unsupported documentation profiles returning a finding and upgrade review defaulting to review-first handling when no profile is declared, but this is not yet a general safety discipline for routing or carry-forward state
- current status: partly covered

#### Untrusted Context And Injection Exposure

- the current repo is still mostly a prompt-library and documentation system, so it is less exposed to live adversarial input than a tool-using or web-connected runtime
- even so, the runtime model does not yet define how untrusted context should be isolated from active `Instructions`, whether retrieved or externally supplied content should be lower-trust by default, or how indirect prompt injection would be contained
- this means the project is safer today mainly because its runtime is narrow, not because the trust model is already explicit
- current status: weakly covered

#### Carry-Forward, Memory, And State Safety

- the repo already recognizes longer-thread fragility, compaction, and rediscovery as important concerns, and it prefers rediscovery from current repo state over blind dependence on long prompt chains
- that is not yet a safety model for state: the project does not distinguish stable policy, task-local state, compacted summaries, rediscovered facts, or stale carry-forward context in a way that would prevent contamination or memory-like poisoning
- no current rule defines what becomes stale after rerouting, narrowing, compaction, or partial recovery
- current status: weakly covered

#### Least Privilege And Permission Boundaries

- the current runtime surface does not yet expose a real tool-permission model, identity model, or least-privilege contract
- that is understandable because `astro-agents` is still primarily a prompt-system repo, but the first integrated runtime design will need to say explicitly whether these boundaries are deferred or minimally defined now
- until then, least privilege is effectively absent rather than intentionally bounded
- current status: not meaningfully covered

#### Approval And Side-Effect Boundaries

- the current project assumes a human-led workflow and uses review completion rules to keep prompt-surface work from being treated as done prematurely
- it does not define runtime approval checkpoints for consequential actions, because the current shared runtime model is not yet action-capable in that way
- as a result, human oversight exists mainly by operating context rather than by an explicit approval design
- current status: not meaningfully covered

#### Failure Containment And Degraded Operation

- the repo already has a few useful narrow-default and fail-stop behaviors: unsupported documentation profiles produce an explicit finding, upgrade routing stops when no defined path applies, and validation defaults to narrower reviews and the requested target root
- these local protections do not add up to a broader degraded-operation model for ambiguous routing, failed rediscovery, lost narrowing, stale carried-forward context, or compaction-related context loss
- the current runtime can therefore fail safely in some bounded selector cases while still remaining underdefined in longer or noisier execution paths
- current status: partly covered

#### Incident Visibility And Forensics

- safety-relevant design issues are reasonably inspectable from the static repo surface because routing, instruction authority, and source-of-truth structure are documented clearly
- shared selector and combined-review outputs expose a short `Route Summary`, which modestly improves review-path visibility on the current validation surface
- runtime forensics are still much weaker: there is no evidence surface showing which `Instructions` were active, what context was carried forward, or when a risky transition occurred
- this means investigation of safety incidents would still depend heavily on reconstruction after the fact
- current status: weakly covered

#### Safety Validation Support

- the current validation library is good at reviewing route structure, prompt-writing quality, documentation structure, and some bounded design failures that matter to safety indirectly
- the repo also has a lightweight validation-path scenario baseline for the current shared review surface
- it still does not validate safety-relevant runtime behavior directly, such as wrong-route instruction applicability, stale carried-forward or active context, compaction-sensitive failures, or unsafe carry-forward
- safety review therefore has a decent structural baseline but not yet a behavior-facing validation stage
- current status: weakly covered

#### Residual Risk And Human Oversight

- human oversight is still the effective backstop throughout the current repo: the project is review-driven, source-of-truth driven, and not designed to let the runtime act with broad autonomy
- that is an important present safety feature, but the repo does not yet state clearly which risks are intentionally left to human judgment and which should later be controlled by runtime mechanisms
- residual risk is therefore managed implicitly rather than as an explicit design surface
- current status: partly covered

#### Proportionality

- the current safety posture is proportionate to the repo as it exists today: lightweight docs, static review prompts, and limited runtime capability keep overhead low
- the tradeoff is that many safety surfaces are presently safe by absence of capability, not by explicit control design
- that is acceptable for the current prompt-library phase, but it will not be enough once the integrated runtime design starts making stronger claims about routing, state, observability, and eventual tool use
- current status: partly covered

### Recommended Safety Actions

**TO BE REVIEWED**

- the live surface stays intentionally lightweight, so the actions below describe future control-model work rather than missing live-surface doctrine
- define an explicit runtime trust model that distinguishes active `Instructions`, supporting `Context`, untrusted context, and lower-trust carried-forward state
- define when carried-forward, compacted, or rediscovered context must be revalidated, downgraded in authority, or discarded
- if the future runtime grows deeper coordination paths, constrain risky routing behavior so the active governing path stays explicit and carry-forward stays bounded
- if the future governance model adopts stronger ownership or handoff semantics, define how that model should limit unsafe carry-forward and ambiguous control after the relevant `Route` or `Handoff`
- define fail-safe behavior for ambiguous routes, unsupported implementations, failed rediscovery, stale carried-forward context, and partial recovery after context loss
- define how untrusted or externally supplied content should be isolated from active `Instructions` even before the runtime grows more tool-capable
- decide the minimum least-privilege and approval posture required in the first integrated runtime design, even if full tool governance is deferred
- define which side-effect boundaries and human approval checkpoints must exist before the runtime is allowed to take more consequential actions
- require observability surfaces that can expose wrong-route instruction applicability, stale carried-forward context, and unsafe carry-forward for later investigation
- require validation coverage for the highest-risk runtime behaviors rather than relying only on structural review
- define where human oversight remains intentionally mandatory because runtime controls are incomplete, expensive, or inappropriate to automate
- preserve proportionality by keeping the first safety stage lightweight enough for ordinary repo work while still making the main risks explicit
- use `validation/` as the first pilot area because it combines the deepest routing, the weakest current runtime evidence, and the clearest risk of unsafe carried-forward guidance

### Open Safety Questions

- what trust classes should the runtime use for active `Instructions`, supporting docs, carried-forward state, rediscovered repo facts, and future retrieved content
- which context sources should be treated as lower-trust by default, and what would raise or lower that trust
- when should carried-forward context be dropped, downgraded, or revalidated after narrowing, rerouting, compaction, or partial recovery
- what minimum fail-safe behavior should exist when the preferred route is ambiguous, unavailable, or too weakly recovered to trust
- if the future governance design adopts stronger ownership or handoff semantics, what safety guarantees should that model provide
- what minimum least-privilege model is needed in the first integrated runtime design even if the repo remains mostly prompt-centric
- whether approval checkpoints should be defined now as a general control surface or explicitly deferred until tool and action surfaces are more concrete
- what safety-relevant runtime evidence must be visible before the project can claim that routing and state risks are reviewable rather than inferred
- which runtime behaviors most urgently need safety-focused validation coverage
- how explicit the project should be about residual risk that remains under human judgment rather than under runtime controls
- what proportional safety stage would materially improve the design without making common prompt-surface work too heavy

## Next Design Steps

### Runtime Threat Model And Trust Boundaries

- use the current assessment in this document together with the shared program frame in `docs/future/runtime-design.md` as the basis for safety scope
- define the first explicit runtime threat inventory for routing, instruction applicability, trust boundaries, carry-forward state, and degraded operation
- define the first trust classes for active `Instructions`, supporting `Context`, lower-trust context, and untrusted context
- separate current risks from forward-looking risks that only become material when the runtime grows more tool-capable or more stateful

### Safety Control Points

- define where runtime design should bound untrusted context, unsafe carry-forward, and future tool or action escalation
- define which governance decisions need explicit safety constraints
- define which observability surfaces are necessary for later safety review
- define the minimum least-privilege, approval, and side-effect boundaries needed in the first integrated runtime design

### Safety Review Criteria And Validation Hooks

- define which safety-relevant runtime behaviors must have explicit validation coverage
- define what evidence should be available when a safety-sensitive runtime path is reviewed
- define how residual risk and human-oversight requirements should shape acceptance criteria for the integrated runtime design
- define the first safety-sensitive degraded and edge-case scenarios that validation must cover

### Program-Level Safety Constraints

- identify unresolved safety questions that must be settled before `docs/future/runtime-design.md` can be implementation-ready
- identify where the runtime program needs stronger safety framing than the current workstream material provides
- identify what proportionality limits should remain on the first safety-control stage

## Deliverables

- a safety workstream plan with stable current-state findings and target questions
- a maintained runtime threat inventory for routing, trust-boundary, context, and carry-forward concerns
- a first model of the safety controls required by the runtime design
- safety review criteria, validation hooks, and evidence expectations for the integrated runtime model
- safety inputs for approval boundaries, residual risk, and human-oversight requirements in `docs/future/runtime-design.md`

## Assumptions And Deferred Decisions

- This workstream starts lighter than Runtime Governance and Validation.
- The current runtime model is mostly a prompt-system problem, but the plan should leave room for broader runtime safety concerns if the system grows more stateful or more tool-capable.
- The exact approval, permission, or enforcement mechanisms are deferred until the integrated runtime design is more stable.

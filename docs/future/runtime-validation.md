# Runtime Validation Plan

## Overview

Use this plan to define how the runtime model will be tested, measured, and accepted.

Runtime Validation is the design of the evaluation methods, scenario sets, graders, measurements, evidence requirements, and acceptance thresholds used to determine whether the runtime model behaves as intended. It covers not only final outputs, but also route choice, instruction-loading and applicability behavior, tool and workflow trajectories, failure modes, runtime cost signals, and longer-thread behavior where those matter to the intended design.

This workstream owns:

- evaluation objectives and success criteria for runtime behavior
- representative runtime scenarios and reusable validation datasets
- instruction-loading and applicability checks, routing tests, and trajectory-oriented evaluation methods
- grading methods, metrics, and evidence requirements for runtime behavior
- runtime-context and runtime-cost measurement where those materially affect design
- acceptance criteria and regression priorities for behavior produced by the runtime model

Use `docs/future/runtime-design.md` for the umbrella program frame and shared terminology.

Do not use this plan to define the intended runtime policy. Use `docs/future/runtime-governance.md` for what the runtime model should do, then use this plan to define how that behavior will be checked.

## Dependencies

- Use `docs/future/runtime-governance.md` for the target runtime behaviors that validation should test.
- Depend on `docs/future/runtime-observability-and-provenance.md` for the evidence surfaces that make validation inspectable and repeatable.
- Feed behavior evidence and acceptance criteria into `docs/future/runtime-safety.md`.
- Contribute the validation portions of `docs/future/runtime-design.md`.

## Current Project Audit

This section records the Runtime Validation output for Phase 1 in `docs/future/runtime-design.md`. Keep it current as the project audit evolves.

### Findings

#### Validation Objective Coverage

- the project defines a clear validation contract for the current agent surface: prompt-writing quality, routing and authority behavior, documentation review, and combined review are all explicit in `docs/testing.md` and `validation/README.md`
- profile-specific documentation validation objectives are also explicit: the shared family distinguishes `private-default` and `public-python` documentation surfaces and defines separate writing and architecture checks for each
- runtime behaviors that matter most to the next design phase are now named in `docs/future/runtime-governance.md`, especially routing, instruction applicability, conflict handling among applicable instructions, customization behavior, prompt-use boundaries, and degraded routing cases
- those runtime behaviors are still defined mainly as design concerns to validate later, not as a current test objective set with explicit success criteria, evaluator types, or thresholds

#### Current Review-Surface Adequacy

- for the current prompt-library repo, the shared review family is strong: narrow review prompts are separated cleanly, profile-specific documentation review workflows are explicit, the full agent-surface review gives a combined pass, and repo-local consistency reviews catch drift in the most important downstream templates
- this review surface is adequate for maintaining prompt-writing quality, route-structure discipline, documentation structure, and validation-contract consistency in the repo as it exists today
- it is not adequate on its own for proving runtime behavior once the project starts making stronger claims about instruction applicability, route choice, carried-forward context, or degraded execution paths

#### Behavior-Facing Coverage

- the current validation model is still primarily review-driven: the prompts inspect files, compare them against source-of-truth docs and guides, and synthesize findings from static evidence
- even the strongest behavior-oriented prompt, `validation/review/routing-and-authority-review.md`, evaluates whether routing and authority behavior work as designed by reading the route structure and its source-of-truth docs rather than by checking live runtime instruction loading and applicability
- there is no stable method yet for checking that the intended task-owning prompt actually provides the active instructions in a live runtime path
- instruction-loading and applicability checks, routing tests, and longer-thread behavior checks are still planning items rather than established validation methods

#### Scenario Representativeness

- the repo has useful scenario seeds in practice: validation starter requests, the review matrix in `docs/testing.md`, the profile-scoped documentation branches, repo-local consistency checks, and the representative route examples in `docs/future/runtime-governance.md`
- those seeds are not yet turned into a maintained scenario set with named baseline cases for virgin threads, repeated regressions, or cross-profile comparisons
- the current project does not yet define representative runtime edge-case scenarios for unsupported profiles, rediscovery failures, longer threads, compaction-sensitive routes, or ambiguous multi-level routing

#### Evidence And Inspectability

- current validation depends heavily on static inspection and reviewer reconstruction from prompt files, `AGENTS.md`, and source-of-truth docs
- the review prompts do ask for dynamic file discovery within scope, which is better than hardcoded assumptions, but that is still runtime discovery of repo artifacts rather than observable evidence of instruction loading and applicability or route choice
- `docs/future/runtime-observability-and-provenance.md` correctly identifies that instruction applicability, route choice, and carried-forward context are not yet inspectable enough for behavior-focused validation
- until observable runtime evidence exists, validation of routing and instruction-applicability behavior will remain inference-heavy

#### Metrics And Measurement

- the project has almost no mature runtime metrics today
- review prompts explicitly avoid deterministic pass/fail scoring, and the current repo-local validation model is built around findings rather than measurable behavior outputs
- runtime-context size, routing reliability, and runtime-cost concerns are recognized in `docs/future/runtime-design.md` and this workstream plan, but no standard measurement method exists yet
- the current validation surface therefore produces findings and review judgments, not measurable runtime performance signals

#### Acceptance Criteria And Completion Bar

- `docs/testing.md` defines a clear completion bar for agent-surface review work: do not treat work as complete while direct validation findings remain unresolved
- that completion bar works for the current review-driven model, and it is reinforced by explicit required-review categories and a concrete regression-priority list
- it does not yet define what counts as acceptable runtime behavior for routing correctness, instruction-applicability correctness, compaction resilience, trace evidence quality, or context-cost limits

#### Regression Discipline

- the repo already has a useful regression structure for prompt-surface maintenance: required review categories are explicit, regression priorities are named, starter requests make the main reviews reusable, and repo-local consistency reviews add a small but meaningful stage of stable repeatability for the root dispatcher and shared-validation template
- public-profile branching and narrow-review independence also reduce accidental broadening, which helps preserve regression meaning inside the current review family
- regression discipline is still weak for runtime behavior itself because there is no stable scenario set, dataset, or harness to rerun against the same behavior questions after changes

#### Failure And Edge-Case Coverage

- the current validation surface does cover some important bounded cases indirectly through selectors and review design: unsupported documentation profiles, undefined upgrade paths, narrow-review defaults, scope defaults, and public-doc reachability boundaries are already treated as meaningful design behavior
- broader degraded cases are still mostly uncovered as validation scenarios: instruction-rediscovery failure, ambiguous multi-level routing, context loss after compaction, stale carried-forward context, and route failure when broader and local prompts do not compose as expected
- this means the current project recognizes several failure modes in design, but does not yet validate them systematically as runtime cases

#### Method Proportionality

- the current validation approach is well matched to the repo’s present maturity: shared review prompts plus a small number of repo-local checks are lightweight enough for ordinary prompt and doc work
- that proportionality is a real strength, especially for lower-budget runtime paths
- it also means the current system is under-instrumented for behavior validation, so the next phase needs to add representative tests and measurements without losing the lightweight review workflow and narrow-review discipline that already work

### Recommended Validation Actions

**TO BE REVIEWED**

- preserve the current shared review family as the lightweight baseline for prompt-surface maintenance rather than replacing it with a heavier runtime harness
- define an explicit validation target set for the runtime behaviors named in `docs/future/runtime-governance.md`, especially routing, instruction applicability, conflict handling among applicable instructions, customization behavior, prompt-use boundaries, and degraded routing cases
- turn the current scenario seeds into a maintained baseline scenario set, starting with common virgin-thread routes and the highest-risk validation routes
- define behavior-facing checks for routing and instruction applicability, rather than relying only on design review and static file inspection
- define checks that confirm one task-owning prompt provides the active instructions after the relevant `Route` or `Handoff` on normal paths
- define a small set of degraded and edge-case scenarios, including unsupported profiles, undefined upgrade paths, rediscovery failure, compaction-sensitive routes, and stale-context cases
- define what observable runtime evidence validation will require from the observability workstream before behavior checks can be trusted
- define a practical measurement method for runtime-context size and other runtime-cost signals that materially affect route design
- decide which validation questions can use deterministic checks, which require rubric-based grading, and which still need human review
- define runtime acceptance criteria that go beyond unresolved review findings and state what counts as acceptable routing and instruction-applicability behavior
- build regression discipline around repeatable scenarios and evidence expectations rather than around one-off review prompts alone
- keep the first behavior-facing validation stage lightweight enough for ordinary repo use and lower-budget runtime paths
- use `validation/` as the first pilot area because it contains the deepest routes, the clearest orchestration behavior, and the most direct current validation responsibility

### Open Validation Questions

- which runtime behaviors should be in the first explicit validation target set, and which can remain design-only until later
- what should the initial baseline scenario set include for common routes, narrow reviews, combined reviews, and degraded cases
- how should virgin-thread routing checks be structured so they are stable and reusable without overfitting to one prompt wording
- what evidence must be visible to validate route choice, task ownership after `Route` or `Handoff`, instruction applicability, conflict handling among applicable instructions, and carried-forward context with confidence
- which behaviors can be validated through static inspection plus traces, and which need direct runtime replay or scenario execution
- what should count as success or failure for routing correctness, instruction-applicability correctness, and degraded-route recovery
- how should runtime-context size and runtime-cost concerns affect validation thresholds and representative scenarios
- how much longer-thread and compaction coverage is needed in the first validation stage before the observability workstream matures further
- which current review prompts should remain purely qualitative and which should later feed into more structured evaluators or scoring
- how should repo-local review prompts under `agents/validation/` be incorporated into future behavior-facing validation without duplicating the shared methods
- what minimum regression suite would materially improve confidence without making ordinary prompt-surface changes too expensive to validate
- where should the line be drawn between validation work, observability requirements, and governance decisions when one is blocked on the others

## Maturation Path

### Stage 1 — Define Target Set And Baseline Scenarios

- use the current-project audit output in this document and the shared runtime audit in `docs/future/runtime-design.md` as the basis for scenario and method selection
- define the first explicit validation target set for runtime behavior
- turn the current route examples, starter requests, and known degraded cases into a small maintained baseline scenario set
- separate common-path scenarios from degraded or edge-case scenarios so later validation can stay proportional

### Stage 2 — Define Behavior Checks And Evidence Requirements

- define how to check routing choices on representative scenarios
- define how to check instruction applicability, conflict handling among applicable instructions, and customization behavior on those scenarios
- define what observable runtime evidence is required to validate route choice, instruction applicability, and carried-forward context
- define what compaction-related evidence is required for longer-thread cases

### Stage 3 — Define Measurement, Acceptance, And Regression

- define a standard approach to measuring the size of runtime context assembled by representative prompt paths
- define how routing reliability and runtime-cost concerns should be measured on the first scenario set
- define what counts as acceptable routing, instruction applicability, conflict handling among applicable instructions, and degraded-case behavior
- define compaction-resilience checks for routes that must still work after longer-thread summarization
- define how lower-budget runtime cost concerns should affect validation thresholds and representative scenarios
- define the first repeatable regression suite for runtime behavior

### Stage 4 — Integrate With The Other Workstreams

- identify which validation methods depend on unresolved governance decisions
- identify where observability gaps block robust validation
- identify which safety-sensitive behaviors need explicit validation coverage
- identify which parts of the current lightweight review family should remain unchanged even after behavior-facing validation is added

## Deliverables

- a validation workstream plan with stable current-state findings and target questions
- a maintained baseline scenario set for common and degraded runtime behavior
- validation methods for routing, instruction applicability, conflict handling among applicable instructions, and degraded-case checks
- a runtime-context and runtime-cost measurement approach for representative runtime paths
- runtime behavior acceptance criteria, evidence requirements, and regression priorities

## Assumptions And Deferred Decisions

- This workstream defines how runtime behavior will be checked, not what the behavior should be.
- Virgin-thread scenarios should remain a primary validation surface even after additional longer-thread checks are introduced.
- The exact harness, tooling, or automation surface for these checks is deferred until the integrated runtime design is more stable.

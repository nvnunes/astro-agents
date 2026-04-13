# Runtime-Aligned Cleanup Plan for `astro-agents`

This document is the cleanup and execution plan for reducing composition-and-authority overreach in the live surface, simplifying the validation surface, and improving review-driven observability while staying aligned with documented platform behavior.
It records the current problems, the target design direction, and the phased work needed to carry those decisions into the live agent surface without prescribing a stronger repo-local runtime theory than the repo intends to maintain.

The `docs/future/` folder is the forward-looking redesign space for deeper runtime work that goes beyond this cleanup. Within this cleanup, treat `docs/future/` as in scope only where this plan explicitly names a future document or phase; everything else in that folder remains out of scope.

## Background

### Findings

1. Simplify the common validation route, stop exposing internal branches as public entry points, and treat internal review pieces as internal.
   - Right now [validation/AGENTS.md:14](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/AGENTS.md#L14), [validation/README.md:19](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md#L19), [validation/README.md:28](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md#L28), [validation/README.md:83](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md#L83), and [docs/testing.md:33](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/testing.md#L33) all expose profile review workflows and internal review steps too directly.
   - Make only four validation starting documents public: `documentation-review`, `prompt-writing-review`, `routing-and-scope-review`, and `full-agent-surface-review`.
   - Mark everything else as internal prompts used within a `Workflow`.
2. Use plain-language validation-file roles and retire `validation prompt(s)` as a canonical category.
   - At planning time, the live prompt surface did not clearly distinguish directly user-addressable review entrypoints from internal workflow files and still used `validation prompt(s)` as an umbrella category in [README.md:11](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/README.md#L11), [README.md:15](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/README.md#L15), [README.md:31](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/README.md#L31), [docs/usage.md:245](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L245), [docs/usage.md:279](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L279), and [validation/review/prompt-writing-review.md:9](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/prompt-writing-review.md#L9).
   - Use plain-language role descriptions in `validation/README.md` and the affected shared review files rather than metadata headers.
   - Use `validation` as the umbrella for the broader checking surface, and use `review` for the prompt type users invoke on the current surface.
   - Replace the umbrella category with role-based language: directly user-addressable review entrypoints, internal workflow files, internal reusable review files, and repo-local review files.
3. Remove broad additive simultaneous-applicability wording from the shared templates and replace it with simpler routing and local-boundary guidance.
   - The root dispatcher and usage templates still normalize “keep applicable prompts active together” in [AGENTS.md:12](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/AGENTS.md#L12), [docs/usage.md:101](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L101), and the repo `AGENTS.md` template in [docs/usage.md:113](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L113).
   - The runtime governance and safety plans point toward narrower handoffs, explicit local customization boundaries, and stale-context boundaries in [docs/future/runtime-governance.md:149](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-governance.md#L149) and [docs/future/runtime-safety.md:146](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-safety.md#L146), but this cleanup should not turn that broader redesign into a stronger live doctrine than the repo wants to maintain.
   - Prefer explicit routing, named local entry points, and reviewable local-boundary statements over additive overlay wording.
4. Remove overclaimed `Instructions` versus `Context` doctrine from the live surface and handle that ambiguity in review guidance instead.
   - The repo previously experimented with a stronger boundary statement in [AGENTS.md:22](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/AGENTS.md#L22) and [docs/architecture.md:21](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md#L21), but the shared templates and downstream guidance did not carry it consistently in [docs/usage.md:123](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L123) and [docs/usage.md:170](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L170).
   - Instead of installing a stronger repo-local doctrine, remove wording that makes deeper source-of-truth docs sound like implicitly active instructions and move that failure-mode handling into authoring and review guidance.
5. Add a lightweight route trace to combined review outputs.
   - The current outputs in [validation/review/documentation-review.md:52](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/documentation-review.md#L52), [validation/review/full-agent-surface-review.md:57](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/full-agent-surface-review.md#L57), and [validation/review/private-default/documentation-review.md:42](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/private-default/documentation-review.md#L42) report findings but not the actual prompt path used.
   - Add a short `Route Summary` block with selected starting document, resolved profile, internal review steps invoked, repo-local prompts included, and source-of-truth docs consulted.
   - That gives immediate observability aligned with [docs/future/runtime-observability-and-provenance.md:122](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-observability-and-provenance.md#L122).
6. Create a small maintained scenario set for routing and validation-path coverage.
   - The runtime validation plan already calls for a baseline scenario set in [docs/future/runtime-validation.md:103](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-validation.md#L103).
   - The governance plan already has representative routes in [docs/future/runtime-governance.md:135](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-governance.md#L135).
   - A short scenario file under `agents/validation/` with 6-8 cases would be enough to start.
   - Include: common docs review, full agent-surface review, `public-python` review, unsupported profile, ambiguous routing, repo-local validation inclusion, and missing upgrade-review implementation.

### Broad Design Improvements

The points below include both direct implications of the findings and broader design guardrails from the runtime-model review.

If this repo makes a major routing change, it should move toward behavior that Codex and current models are actually built to support rather than leaning on ambitious local intent. Strong claims should be reserved for platform behavior that is real and documented; the rest should be treated as prompt-system conventions that must be made explicit if we want the model to follow them reliably.

In practice, that means:

1. Prefer direct references, explicit entrypoints, and explicit local customization boundaries over broad implicit composition.
2. Expose only a small set of directly user-addressable review prompts and keep profile-specific and reusable prompts internal to workflows.
3. Label prompts by runtime role rather than by broad category names such as `validation prompt(s)`, and keep `validation` as the umbrella term for the broader checking surface rather than the prompt type.
4. Reduce authority-heavy and additive wording on normal paths so the repo does not overclaim deterministic multi-layer behavior.
5. Replace open-ended shared-plus-local simultaneous applicability and generic subtree overlay discovery with explicit local entrypoints or explicit local customization boundaries.
6. Keep routing and task language operational without prescribing a stronger repo-local ownership model than the runtime docs actually support.
7. Keep source-of-truth guidance discoverable without turning deeper docs into a separate repo-local doctrine about active instructions.
8. Treat `precedence` primarily as a Codex/runtime mechanism, not as a general repo-local conflict resolver.
9. Treat repo-local conflict handling as prompt design and review guidance, not as deterministic runtime behavior.
10. Make route selection, internal review steps, repo-local inclusions, and consulted source-of-truth docs visible in combined review outputs.
11. Prefer review-driven handling of ambiguous routing, unclear local boundaries, and overclaimed prompt surfaces over adding unsupported fail-safe doctrine.
12. Maintain a small scenario baseline for routing, validation-path coverage, and related review-surface regressions.
13. Scale back claims that narrower prompts will reliably control behavior unless the route, local customization, and ownership boundaries are concretely operationalized.

This is a design correction from abstract hierarchy theory toward model-compatible control flow.

### Current Composition + Authority Framing That Drove The Cleanup

At planning time, the downstream docs frequently described the surface as if it were strongly additive and authority-resolved. That framing is what this cleanup is trying to reduce.

The important problem was not only that the wording was stale. It was that the docs often implied a stronger repo-local runtime-control model than the repo could reliably justify from documented platform behavior.

Phase 1 showed that the better response was usually to remove or narrow those claims, simplify the affected templates and review surfaces, and rely more on prompt-authoring and review guidance rather than replacing the old framing with a stronger repo-local runtime doctrine.

The examples that originally motivated this cleanup were concentrated in:

- bootstrap and template wording that normalized additive routing or generic overlay behavior
- source-of-truth guidance that risked sounding like a stronger repo-local runtime doctrine than intended
- review-surface naming and validation wiring that kept advertising authority-heavy framing after the repo had stopped wanting to rely on it

The remaining phases should be read in that same spirit: simplify and clarify the live surface, make review behavior more observable, and add lightweight scenario coverage without quietly reintroducing the stronger runtime theory that Phase 1 intentionally avoided.

### Current Validation Surface to be Simplified

Current validation wiring, compressed:

- [validation/AGENTS.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/AGENTS.md) dispatches into shared review files under `validation/review/`.
- [validation/README.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md) presents those shared reviews and starter requests.
- The shared surface mixes several roles:
  - direct reviews like [prompt-writing-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/prompt-writing-review.md) and [routing-and-scope-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/routing-and-scope-review.md)
  - combined reviews like [full-agent-surface-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/full-agent-surface-review.md)
  - selectors like [documentation-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/documentation-review.md)
  - profile-specific workflows under `validation/review/private-default/` and `validation/review/public-python/`
  - internal reusable review files like [core-document-writing-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/core-document-writing-review.md)
- [docs/testing.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/testing.md) defines those shared reviews as the baseline checks and adds repo-local review files from [agents/validation/](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/agents/validation) when they apply.

So the validation surface is currently a layered mix of shared dispatch, shared reviews, internal workflow pieces, profile-specific workflows, and repo-local additions, without a clean enough separation between public entrypoints and internal workflow nodes.

### Transitional Vocabulary Reference

Use this table during the cleanup phases below when older repo runtime language appears in the live surface. It is a working replacement aid, not a long-term glossary or a second runtime source of truth.

Each row names a live repo term that still appears somewhere in the current surface and should be replaced as the owning files are revised. Use the middle column to choose the term that names the actual mechanism in context, not just the nearest synonym.

In the middle column, defined ontology terms and other supporting terms from `docs/runtime-model.md` appear in `backticks`, plain-language replacements appear in plain text, and CODEX-specific mechanisms appear in ALL CAPS.

| Live repo term | Potential replacement term(s) | Mapping note |
| --- | --- | --- |
| `Precedence` | `authority`; higher-priority `Instructions`; ordering of `Instructions`; CODEX instruction discovery and merge behavior | Still used in this repo, but not treated as a top-level ontology bucket. Choose the replacement that names the actual mechanism rather than using `precedence` as a general runtime term. |
| `Router` | `Route`; `Agent`; `Deterministic controller`; `dispatcher`; `selector`; `orchestrator` | Still used in this repo as a local label, but not treated as a separate ontology class. Use the narrower role term only when that narrower role is actually intended. |
| `Resolve` | determine; identify; `Route`; determine the applicable `Prompt`; determine the applicable profile | Useful as an operation name, but not as a top-level runtime concept. Use it for a determination step, not for a runtime entity or ontology bucket. |
| `Override` | higher-priority `Instructions`; superseding file; replace the default; supersede broader `Instructions`; CODEX override file | Reserve the product-specific sense for CODEX's per-directory override file. Otherwise explain the exact mechanism instead of using `override` as a broad local runtime term. |
| `Activate` | `Route`; load; make applicable; `Handoff` / `Transfer`; select | Still used in the repo, but too broad. Pick the term that names the concrete mechanism actually happening. |
| `Activation` | loading `Instructions`; route choice; selection; `Handoff` / `Transfer`; `guidance` becoming applicable; scope change | Still used in the repo, but it conflates several different runtime ideas. Prefer wording that names the concrete mechanism or state change. |
| `Govern` | own the task; apply `Instructions`; determine the active `Instructions`; `Orchestration` | Used in the repo, but usually better replaced with a more concrete statement about ownership or applicable instructions. |
| `Attach` | extend; add local guidance; apply at a named extension point; supplement the current scope | Mostly appears in transitional planning language. Keep it only if the rewrite introduces explicit extension points; otherwise prefer more concrete wording. |
| `Composition` | simultaneous applicability; overlapping `Instructions`; multi-step `Workflow`; reusable prompt combination | Too broad as a standalone runtime term. Explain whether you mean overlapping applicable instructions, workflow structure, or authoring-time prompt reuse. |
| `Shared Activation` | shared `Route`; shared selection; shared `Instructions` becoming applicable | A local runtime label, not a common ontology term. Prefer wording that names the concrete shared-library step or state change. |
| `Bootstrap Routing` | initial `Route`; initial dispatch; first-step route choice | A repo-local control-flow phase, not a general ontology bucket. Use plain language for the initial route into the relevant branch. |
| `Bootstrap Prompt` | initial `Prompt`; bootstrap request; initial user request | Better treated as plain language for the first prompt or request that triggers the intended route. |
| `Authoring Prompt` | `Prompt`; writing-focused `Prompt`; authoring `Task` guidance | A useful functional description, but not a separate runtime ontology term. Use it only when the writing-focused role matters. |
| `Review Prompt` | `Prompt`; review `Task`; review `Workflow` | Better treated as a prompt role or workflow role than as a top-level runtime concept. |
| `Validation Prompt` | `Prompt`; validation `Task`; validation `Workflow` | Better treated as a prompt role or workflow role than as a top-level runtime concept. |
| `Routing Prompt` | `Prompt`; `Route`; `dispatcher`; `selector`; `orchestrator` | Too local as a canonical category. Use the narrower routing or coordination term that matches the actual behavior. |
| `Layer` | scope; source; place where `Instructions` or `Context` are introduced | Better treated as plain architectural language than as a common runtime ontology term. Name the specific scope or source when possible. |
| `Inheritance` | reuse; refinement; narrower `Instructions`; derived prompt | An authoring-time relation, not a core runtime term. Prefer concrete language about reuse or refinement. |
| `Review Lens` | review criterion; evaluation dimension; review angle | A review-structure term, not a runtime ontology term. Prefer plain language unless a stable local review rubric really needs the label. |
| `Select` | bounded choice; choose one option; `selector` | Better treated as an action or supporting role label than as a top-level ontology term. |
| `Hierarchy` | scope ordering; source ordering; `authority`; `Route` structure; `Workflow` structure | Too broad as a single runtime term. Explain whether you mean document/source ordering, authority among `Instructions`, or control-flow structure. |
| `Control Flow` | `Workflow`; `Route`; `Handoff` / `Transfer`; `Orchestration` | Better replaced by the more specific runtime mechanism actually being described. |
| `Entrypoint` | initial `Route`; directly user-addressable `Prompt`; entry document; starting path | Useful as plain language, but not a separate runtime ontology bucket. Use the concrete entry mechanism that actually applies. |
| `Bundle` | grouped prompts; internal `Workflow`; reusable review set | A local packaging term, not a common runtime concept. Prefer wording that says whether the grouping is a workflow, a reusable set, or an internal review path. |
| `Component` | internal prompt; reusable prompt; internal workflow step | Too broad on its own. Name the concrete prompt or workflow role instead. |
| `Composite` | multi-step `Workflow`; coordinating `Prompt`; synthesized output | A local shorthand, not a core runtime term. Prefer wording that says whether the item coordinates a workflow, combines outputs, or both. |
| `Shared Validation Family` | shared validation prompts; shared review library; validation `Workflow`s | A repo-local organizational label, not a general runtime term. Use it only for the shared validation collection itself, not as a runtime category. |

## Execution Plan

### Overview

This is a staged cleanup of the repo’s live routing and validation surface, route visibility, and scenario baseline.

Execute the phases in order. For every pass:

- decide the rule or boundary
- get user approval for that decision before implementing it
- persist it in the owning long-lived file
- rewrite the live surface that still encodes the old model
- run the phase check before moving on

### Phase Dependencies

- Phase 1 comes first because the validation surface should not be rewired until the additive and authority-heavy framing in the live routing surface has been reduced.
- Phase 2 depends on Phase 1 because validation entrypoints, internal workflow wiring, and repo-local validation inclusion all depend on the simplified routing and review-surface framing established there.
- Phase 3 depends on the first two phases because the maintained scenario baseline should test the simplified routing and validation surface that the repo actually uses, not a stronger runtime theory that the cleanup does not intend to preserve.
- Phase 4 depends on the first three phases because the future docs should be refreshed only after the live runtime, validation, route-summary, and scenario-baseline surfaces they describe have actually changed.
- When a later phase uncovers a contradiction in an earlier phase decision, fix the owning source-of-truth document first rather than patching around the contradiction locally.

### Phases

#### Phase 1 — Reduce Composition + Authority Overreach

Goal: align the live surface with normal agent conventions, remove authority-heavy and additive wording that overstates repo-local runtime control, and shift ambiguity handling into prompt-authoring and review guidance where appropriate.

Primary outputs: `docs/architecture.md`, `docs/usage.md`, affected framing and examples in `docs/runtime-model.md`, and any directly affected authoring, review, validation, testing, or repo-entry files needed to keep the changed surface coherent.

##### Pass 1A — Define Bootstrap Semantics

- Decide what the initial workspace or repo `AGENTS.md` is allowed to do.
- Update `docs/runtime-model.md` only where example framing or path guidance needs to match the new bootstrap style; do not add a new bootstrap-semantics doctrine unless the runtime docs actually need it.
- Rewrite the bootstrap guidance in `docs/architecture.md` and `docs/usage.md`.
- Replace the old “thin initial route into the chain” wording in the repo and workspace `AGENTS.md` templates.
- Allow adjacent cleanup in `authoring/agents/` when the prompt-writing guidance needs to match the revised bootstrap style.

##### Pass 1B — Redefine Repo `/agents`

- Decide what belongs in a repo’s `agents/` folder under the new model.
- Persist the repo-local scope and ownership rule in `docs/architecture.md`.
- Rewrite the repo-level guidance and templates in `docs/usage.md`.
- Remove old matching-subtree and additive overlay guidance wherever it no longer survives.
- Update the live repo root `AGENTS.md` when it still talks generically about `agents/` instead of naming the specific local prompt that actually matters.

##### Pass 1C — Redefine Workspace `/agents`

- Decide what belongs in global or workspace-level prompt storage under the new model without overcommitting to a workspace-specific runtime chain.
- Persist the workspace scope and ownership rule in `docs/architecture.md`.
- Rewrite the workspace or global templates in `docs/usage.md` when they are still needed.
- Remove wording that keeps workspace-global and shared prompts active together or otherwise implies a stronger deterministic merge model than the repo intends to claim.

##### Pass 1D — Move Ownership Concerns Into Prompt-Authoring Guidance

- Do not define repo-local handoff or task-ownership semantics in the source-of-truth docs unless the runtime docs genuinely need them.
- Rewrite prompt-authoring guidance so prompts prefer explicit routing, named next prompts, and explicit local exceptions.
- Remove live wording that implies stronger ownership semantics than the runtime docs actually support.
- Allow adjacent cleanup in `authoring/agents/agents-md.md` and related authoring guides when needed to keep that guidance aligned with the new explicit-routing style.

##### Pass 1E — Move Deeper-Doc Ambiguity Handling Into Review

- Do not preserve a formal repo-local `Instructions` versus `Context` doctrine in the source-of-truth docs unless the runtime docs genuinely require it.
- Remove wording that makes deeper docs sound like implicit active instructions.
- Move failure-mode handling for deeper-doc overreach into authoring and review guidance where that is enough to protect repo intent.
- Allow simplification of live `AGENTS.md` files when removing an explicit `Context` / `Instructions` line leaves a cleaner and still sufficient surface.

##### Pass 1F — Remove Authority Framing From The Review Surface

- Treat this pass as subtraction rather than as a replacement runtime theory.
- Rewrite `docs/architecture.md` and `docs/usage.md` so downstream guidance no longer presents authority framing as the main repo-runtime model.
- Rename or rewrite review-surface files and references where that is needed to remove the old authority framing cleanly.
- Keep `authority` only where it still names real platform or model behavior rather than a repo-local routing theory.

##### Pass 1G — Simplify Testing And Validation Baselines

- Allow this pass to simplify oversized validation-template guidance when it no longer fits the narrowed model.
- Introduce a shared baseline file when that removes repeated doctrine from `docs/testing.md` and `docs/usage.md`.
- Rewrite repo and downstream testing guidance around that shared baseline.
- Remove repo-local consistency-review prompts that existed only to police the older template model once they no longer add value.

##### Pass 1H — Strengthen Review Checks Instead Of Defining Fail-Safe Doctrine

- Do not add repo-specific fail-safe runtime semantics to the source-of-truth docs unless later phases genuinely require them.
- Strengthen prompt-writing and routing/scope review criteria so they check explicit routing and explicit local boundaries instead.
- Remove lingering higher-authority or higher-authority-local wording from the validation surface when explicit local implementation, routing, or scope wording is clearer.
- Allow adjacent cleanup in validation docs and review prompts when needed to keep the changed review surface coherent.

#### Phase 2 — Simplify Validation Model

Goal: simplify the public validation surface, separate public review entrypoints from internal workflow pieces, and make the validation surface easier to understand, including visible route summaries in combined review outputs, without introducing a stronger runtime theory than the repo intends to maintain.

Primary outputs: `validation/AGENTS.md`, `validation/README.md`, shared review files under `validation/review/`, and the validation guidance in `README.md`, `docs/testing.md`, and `docs/usage.md`.

##### Pass 2A — Public Validation Surface

- Decide the small set of directly user-addressable shared review prompts.
- Persist that public surface in `validation/README.md` and `validation/AGENTS.md`.
- Rewrite `README.md`, `docs/testing.md`, and `docs/usage.md` so starter requests and validation guidance point to the same public set.
- Remove public-facing references that expose profile-specific workflows and internal reusable review files as normal starting documents.
- Keep `validation` as the umbrella for the broader checking surface and `review` as the prompt type users invoke on the current shared surface.

##### Pass 2B — Internal Validation Wiring

- Decide the plain-language internal categories for validation files.
- Persist those categories in `validation/README.md` and the affected shared review files.
- Rewrite `validation/AGENTS.md`, `validation/review/documentation-review.md`, `validation/review/full-agent-surface-review.md`, the profile-specific workflows, and the internal reusable review files so their normal path into use is clear.
- Prefer simple wording such as `normally reached via`, `internal workflow step`, or `internal reusable review file` over stronger ownership or runtime-semantics language.
- Keep independently invokable advanced paths explicit only where they genuinely need to stay visible, but stop documenting them as the normal public entry surface.

##### Pass 2C — Repo-Local Validation Integration

- Decide when repo-local review files under `agents/validation/` are included after a shared review path.
- Persist that inclusion path in `docs/testing.md` and `docs/usage.md`.
- Rewrite repo-local validation guidance and shared review outputs so repo-local inclusion is explicit and visible when it occurs.
- Keep repo-local inclusion route-based and reviewable rather than implied by broad layering or additive composition.

##### Pass 2D — Make Validation Routes Visible

- Decide which review-path details should be visible in user-facing combined validation outputs.
- Persist that contract in `docs/testing.md`, `validation/README.md`, and the affected combined shared review files.
- Decide the `Route Summary` shape for the selected public review entrypoint, the resolved documentation profile when relevant, the internal review steps invoked, the repo-local review files included, and the source-of-truth docs consulted when they materially shaped the result.
- Rewrite output guidance so those details are visible without turning internal prompts into public entrypoints or implying stronger runtime tracing than the cleanup intends to maintain.

#### Phase 3 — Establish Validation-Path Scenario Baseline

Goal: add a small maintained scenario baseline for routing, review-path selection, profile resolution, repo-local validation inclusion, and related validation-surface regressions without turning this cleanup into a full eval-harness project.

Primary outputs: `docs/testing.md`, a maintained scenario file under `agents/validation/`, and the linked future validation docs.

##### Pass 3A — Define Scenario Baseline Scope

- Decide which routing and validation-surface behaviors the baseline must cover.
- Persist that scope in `docs/testing.md` and a new maintained scenario file under `agents/validation/`.
- Define the minimum scenario schema for the expected public review entrypoint, resolved documentation profile when relevant, internal review steps, repo-local validation inclusion, and route-summary shape in that scenario file.
- Separate the initial baseline from future work so the first version stays small enough to maintain.

##### Pass 3B — Seed The Initial Scenario Set

- Create the first maintained scenario set under `agents/validation/`.
- Add the initial cases for common review paths, profile-specific paths, repo-local validation inclusion, ambiguous routing, unsupported profiles, and other validation-path edge cases that materially affect the shared review surface.
- Record the expected public review entrypoint, internal review path, repo-local validation inclusion, and route summary for each scenario.
- Cross-link the new file from `docs/testing.md`.

##### Pass 3C — Define How The Baseline Is Used

- Decide how the scenario set fits into the validation contract without requiring a full harness.
- Persist that rule in `docs/testing.md`.
- Rewrite `docs/usage.md` and the repo-local validation guidance so routing, validation-surface, and source-of-truth changes explicitly re-check the maintained scenarios.
- Record in the future runtime-validation docs how later scenario and regression assets should extend rather than replace the baseline.

#### Phase 4 — Refresh Future Runtime Docs

Goal: update `docs/future/` so it accurately reflects the post-cleanup live surface, starts from the narrower live model this cleanup intentionally preserves, and focuses on the deeper redesign work that remains after this cleanup.

Primary outputs: `docs/future/runtime-design.md`, `docs/future/runtime-governance.md`, `docs/future/runtime-observability-and-provenance.md`, `docs/future/runtime-validation.md`, and `docs/future/runtime-safety.md`.

##### Pass 4A — Remove Resolved Findings

- Review the current-project audit sections in the future docs against the completed Phase 1 through Phase 3 changes.
- Remove findings that are no longer true after the cleanup, and narrow findings that are now only partly true.
- Remove future-doc findings that still assume the live surface should define stronger ownership doctrine, `Instructions` versus `Context` doctrine, or repo-specific fail-safe runtime behavior when this cleanup intentionally left those as future-design questions.
- Keep the remaining current-project audit material focused on the deeper redesign problems that the cleanup intentionally does not solve.

##### Pass 4B — Reframe Remaining Gap Statements

- Rewrite the remaining gap descriptions so they clearly distinguish between:
  - problems already solved by the cleanup
  - problems intentionally left minimal in the live surface
  - deeper redesign work that still belongs in `docs/future/`
- Update recommended actions and open questions where the cleanup changes the starting point for the future redesign.

##### Pass 4C — Align Cross-References And Sequencing

- Update `docs/future/runtime-design.md` and the workstream docs so their dependencies, sequencing notes, and integrated questions reflect the cleaned-up live surface.
- Remove references that still assume the pre-cleanup runtime or validation structure.
- Rewrite `docs/future/runtime-observability-and-provenance.md` so it starts from the Phase 2 route-summary surface and treats deeper tracing or provenance work as future work.
- Rewrite `docs/future/runtime-validation.md` so it starts from the Phase 3 validation-path scenario baseline and treats richer eval or regression infrastructure as future work.
- Keep stronger runtime-governance or safety ideas in the future docs as forward-looking redesign material rather than as missing live requirements from this cleanup.
- Keep the future docs explicitly forward-looking rather than letting them become a second source of truth for the live runtime model.

##### Pass 4D — Revalidate Against Primary Sources And The Current Surface

- Return to the primary sources after the cleanup changes land.
- Re-check the future-doc findings for both completeness and accuracy against:
  - the primary sources
  - the then-current `astro-agents` surface
- Validate the rest of the future-doc content against those same source-backed findings so the overviews, recommended actions, open questions, and sequencing notes still follow from the evidence.
- Remove, narrow, or add findings where the post-cleanup repo state or the primary-source review requires it.

### Persistence Targets

Use these as the main persistence targets when carrying decisions into the live surface:

- `docs/runtime-model.md`
  - stable runtime terminology, platform-grounded runtime behavior, and route-visibility expectations where the live surface actually depends on them
- `docs/architecture.md`
  - route structure, scope ownership, bootstrap model, repo/workspace `agents/` role, and downstream structural guidance
- `docs/usage.md`
  - repo and workspace templates, starter patterns, downstream practical guidance, and validation-contract starter language
- `validation/AGENTS.md`
  - live shared validation dispatch behavior
- `validation/README.md`
  - the public validation surface, role descriptions, and starter requests
- `validation/review/*.md`
  - role descriptions, coordination rules, output requirements, and route-summary behavior in the shared review files
- `docs/testing.md`
  - the repo’s validation contract, canonical checks, and any maintained scenario-baseline requirement for routing and validation-surface regressions
- `agents/validation/`
  - repo-local validation additions and the maintained scenario baseline created in Phase 3
- `docs/future/runtime-observability-and-provenance.md`
  - future observability work that stays out of scope for this cleanup
- `docs/future/runtime-validation.md`
  - future validation work that extends the baseline created here without assuming a stronger live runtime doctrine than the cleanup adopts
- `docs/future/runtime-design.md`, `docs/future/runtime-governance.md`, `docs/future/runtime-safety.md`
  - future integrated design framing and workstream-specific redesign material that must be refreshed after the live cleanup changes land

When a pass changes the live model, do not leave the decision only in this plan. Persist it in the owning file above during the same pass.

### Exit Checks

Use these as the minimum end-of-phase checks:

- After Phase 1:
  - confirm the live docs no longer normalize additive prompt composition or authority-heavy overlay behavior
  - confirm bootstrap and customization guidance is minimal and aligned with normal agent conventions
  - confirm authoring and review guidance now catch unclear routing, unclear local boundaries, and deeper-doc overreach
  - confirm the validation surface no longer advertises the old authority framing or obsolete template-model checks
  - run the applicable checks from `docs/testing.md` for the touched docs, prompt-guidance files, and review-surface files
- After Phase 2:
  - run the shared review checks that cover prompt writing, routing and scope behavior, documentation review, and full-surface coherence
  - confirm the public validation surface names only the intended user-facing review entrypoints
  - confirm profile-specific and reusable internal prompts are no longer documented as the normal public starting documents
  - confirm repo docs, validation docs, and dispatcher docs point to the same public review set
  - confirm repo-local validation inclusion is explicit in docs and visible where the shared review path includes it
  - confirm the route-summary contract appears consistently in the combined shared review outputs and the human-facing validation docs
- After Phase 3:
  - confirm the maintained scenario file exists, is linked from `docs/testing.md`, and is specific enough to check routing, profile resolution, internal review selection, repo-local validation inclusion, and route-summary expectations
  - confirm the validation contract names when the maintained scenarios must be re-checked
- After Phase 4:
  - confirm the `docs/future/` audits no longer describe resolved cleanup findings as current problems
  - confirm the future docs now start from the cleaned-up live surface and focus on the deeper redesign work that remains
  - confirm the future docs do not reintroduce the older authority/composition framing or treat intentionally removed live doctrine as a current defect
  - confirm the future docs do not reintroduce stale terminology or act as a second live source of truth
  - confirm the future-doc findings and the surrounding content have been revalidated against both the primary sources and the then-current agent surface

At the end of the overall cleanup:

- `docs/runtime-model.md`, `docs/architecture.md`, `docs/usage.md`, `validation/AGENTS.md`, `validation/README.md`, `docs/testing.md`, and the relevant shared review files should describe compatible runtime, routing, validation, and review framing without overclaiming repo-local runtime control
- the public validation surface should expose only the intended user-addressable review prompts
- the repo should have a maintained scenario baseline for routing and validation-surface regressions
- the future runtime docs should describe the remaining redesign space rather than the live problems already solved by this cleanup

### Execution Rules

- Do not treat this document as the final owner for any runtime or validation rule. Every settled rule should be moved into its long-lived owner during the same phase.
- Treat every `decide` step in this plan as an approval gate: propose the decision explicitly, wait for user approval, and only then implement and validate it.
- Prefer replacing old model language over layering new language on top of it.
- When a pass touches both source-of-truth docs and prompt files, update the source-of-truth docs first, then align the prompt files to them.
- Keep `validation` as the umbrella term for the broader checking surface and `review` as the prompt type on the current shared surface unless a phase explicitly changes that rule.
- Do not reintroduce open-ended composition, implicit authority inheritance, or public exposure of internal workflow nodes while implementing later phases.
- Keep future-facing material in `docs/future/` aligned with the live cleanup decisions, but do not let future docs become the only place where a live rule is stated.

### Assumptions And Scope Limits

- This plan currently assumes the cleanup will be carried out in place rather than through a parallel redesign branch inside the docs.
- File paths are expected to stay stable unless a phase explicitly decides a rename is worth the churn.
- The validation surface remains the main prompt-facing pilot area for this cleanup.
- Broader runtime tooling, trace storage, or a full eval harness remain out of scope for this plan.
- If a pass reveals a new major design area that does not fit the current four phases, add it only after recording why the existing phase structure is no longer enough.

# Runtime-Aligned Cleanup Plan for `astro-agents`

This document is the cleanup and execution plan for replacing the repo's older composition-and-authority runtime model with a narrower, more explicit runtime and validation model.
It records the current problems, the target design direction, and the phased work needed to carry those decisions into the live agent surface.

The `docs/future/` folder is the forward-looking redesign space for deeper runtime work that goes beyond this cleanup. Within this cleanup, treat `docs/future/` as in scope only where this plan explicitly names a future document or phase; everything else in that folder remains out of scope.

## Background

### Findings

1. Simplify the common validation route, stop exposing internal branches as public entry points, and treat internal review pieces as internal.
   - Right now [validation/AGENTS.md:14](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/AGENTS.md#L14), [validation/README.md:19](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md#L19), [validation/README.md:28](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md#L28), [validation/README.md:83](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md#L83), and [docs/testing.md:33](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/testing.md#L33) all expose profile review workflows and internal review steps too directly.
   - Make only four validation starting documents public: `documentation-review`, `prompt-writing-review`, `routing-and-authority-review`, and `full-agent-surface-review`.
   - Mark everything else as internal prompts used within a `Workflow`.
2. Add explicit prompt-use markers and retire `validation prompt(s)` as a canonical category.
   - The runtime plans now distinguish directly user-addressable prompts from internal workflow prompts, but the live prompt surface does not label files that way and still uses `validation prompt(s)` as an umbrella category in [README.md:11](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/README.md#L11), [README.md:15](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/README.md#L15), [README.md:31](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/README.md#L31), [docs/usage.md:245](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L245), [docs/usage.md:279](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L279), and [validation/review/prompt-writing-review.md:9](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/prompt-writing-review.md#L9).
   - Add a one-line header in each shared review file saying `Prompt Use`, `Workflow Position`, and `Task Ownership`.
   - Use `validation` as the umbrella for the broader checking surface, and use `review` for the prompt type users invoke on the current surface.
   - Replace the umbrella category with role-based language: directly user-addressable review prompts, internal workflow prompts, reusable internal prompts, and repo-local review prompts.
3. Replace broad additive simultaneous applicability with explicit handoff rules in the shared templates.
   - The root dispatcher and usage templates still normalize “keep applicable prompts active together” in [AGENTS.md:12](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/AGENTS.md#L12), [docs/usage.md:101](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L101), and the repo `AGENTS.md` template in [docs/usage.md:113](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L113).
   - The runtime governance and safety plans now point toward explicit local customization boundaries, narrower handoffs, and stale-context boundaries in [docs/future/runtime-governance.md:149](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-governance.md#L149) and [docs/future/runtime-safety.md:146](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-safety.md#L146).
   - The minimal version is: once a narrower route is selected, parent dispatchers and selectors stop supplying active `Instructions` except for output framing.
   - Local customization should occur only at named extension points.
4. Make the `Instructions` versus `Context` boundary explicit for deeper source-of-truth docs.
   - The root docs now state the intended rule in [AGENTS.md:22](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/AGENTS.md#L22) and [docs/architecture.md:21](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md#L21), but the shared templates and downstream guidance still under-specify the same boundary in [docs/usage.md:123](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L123) and [docs/usage.md:170](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md#L170).
   - Make the shared guidance say that deeper source-of-truth docs are supporting `Context` by default.
   - Treat deeper source-of-truth docs as active `Instructions` only when higher-authority instructions explicitly delegate narrower authority to them.
5. Add a lightweight route trace to combined review outputs.
   - The current outputs in [validation/review/documentation-review.md:52](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/documentation-review.md#L52), [validation/review/full-agent-surface-review.md:57](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/full-agent-surface-review.md#L57), and [validation/review/private-default/documentation-review.md:42](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/private-default/documentation-review.md#L42) report findings but not the actual prompt path used.
   - Add a short `Route Summary` block with selected starting document, resolved profile, internal review steps invoked, repo-local prompts included, and source-of-truth docs consulted.
   - That gives immediate observability aligned with [docs/future/runtime-observability-and-provenance.md:122](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-observability-and-provenance.md#L122).
6. Create a small maintained scenario set for routing and instruction applicability.
   - The runtime validation plan already calls for a baseline scenario set in [docs/future/runtime-validation.md:103](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-validation.md#L103).
   - The governance plan already has representative routes in [docs/future/runtime-governance.md:135](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/future/runtime-governance.md#L135).
   - A short scenario file under `agents/validation/` with 6-8 cases would be enough to start.
   - Include: common docs review, full agent-surface review, `public-python` review, unsupported profile, stale context after narrowing, ambiguous routing, and missing upgrade-review implementation.

### Broad Design Improvements

The points below include both direct implications of the findings and broader design guardrails from the runtime-model review.

If this repo makes a major routing change, it should move toward behavior that Codex and current models are actually built to support rather than leaning on ambitious local intent. Strong claims should be reserved for platform behavior that is real and documented; the rest should be treated as prompt-system conventions that must be made explicit if we want the model to follow them reliably.

In practice, that means:

1. Prefer direct references, explicit entrypoints, and explicit local customization boundaries over broad implicit composition.
2. Expose only a small set of directly user-addressable review prompts and keep profile-specific and reusable prompts internal to workflows.
3. Label prompts by runtime role rather than by broad category names such as `validation prompt(s)`, and keep `validation` as the umbrella term for the broader checking surface rather than the prompt type.
4. Reduce the number of simultaneously active instruction sources on normal paths so the model has fewer overlapping directives to reconcile.
5. Replace open-ended shared-plus-local simultaneous applicability and generic subtree overlay discovery with explicit local implementations or explicit local customization boundaries.
6. Use explicit selection and explicit handoff language so one prompt normally owns the `Task` after the relevant routing step.
7. Treat deeper source-of-truth docs as supporting `Context` by default and require explicit delegated authority before they become active `Instructions`.
8. Treat `precedence` primarily as a Codex/runtime mechanism, not as a general repo-local conflict resolver.
9. Treat repo-local conflict handling as prompt design, not as deterministic runtime behavior.
10. Make route selection, internal review steps, repo-local inclusions, and consulted source-of-truth docs visible in combined review outputs.
11. Prefer fail-safe narrowing over continued composition when route selection, profile resolution, or delegated authority is ambiguous.
12. Maintain a small scenario baseline for routing, instruction applicability, and fail-safe behavior.
13. Scale back claims that narrower prompts will reliably control behavior unless the route, local customization, and ownership boundaries are concretely operationalized.

This is a design correction from abstract hierarchy theory toward model-compatible control flow.

### Current Composition + Authority Model to be Replaced

As currently documented, the downstream model is still **additive**. The core assumption is:

- applicable prompts compose by default
- conflicts are settled by instruction authority
- broader prompt layers usually remain active unless a narrower file explicitly says otherwise

That is stated most directly in [docs/architecture.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md:98) and [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:99).

1. **Bootstrapping**
- Bootstrapping is currently envisaged as a **thin initial route** into the prompt chain, not as a formal handoff model. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:172)
- At workspace scope, `Projects/AGENTS.md` should be a thin starter that routes into `Projects/agents/AGENTS.md` and `astro-agents/AGENTS.md`, while keeping applicable layers active together. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:174)
- At repo scope, the repo root `AGENTS.md` should follow higher-level instructions, route into applicable shared prompts, then check matching local prompts under the repo’s `agents/` subtree. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:105)
- Starter requests are part of this same model: short initial requests are expected to trigger the intended shared route with minimal extra prompting. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:204)
- Separate source-of-truth docs do not automatically become active instructions. They are supporting `Context` by default unless narrower authority is explicitly delegated to them. [docs/architecture.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md:21)

2. **How `/agents` Works At The Repo Level**
- A repo’s `agents/` folder is for prompts that are **too local for the shared library** but still reusable within that repo. [docs/architecture.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md:135)
- When higher-level instructions route work into a shared subtree, the repo should check the corresponding subtree under its own `agents/` folder for matching local prompts. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:111)
- If both shared and repo-local prompts apply, the current model says to keep compatible guidance from both active together. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:113)
- If they conflict, higher-authority instructions win. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:117)
- In the formal authority chain, repo/subtree prompts outrank repo `agents/`, which outrank workspace `Projects/agents`, which outrank shared prompts in `astro-agents/`. [docs/architecture.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md:110)
- For validation specifically, repo-local validation prompts belong under `agents/validation/`. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:245)

3. **How `/agents` Works At The Workspace Level**
- `Projects/agents/` is for **workspace-global reusable prompts, user preferences, and defaults** that should apply across multiple repos. [docs/architecture.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md:127)
- The workspace root `AGENTS.md` is supposed to be thinner than repo `AGENTS.md`; it routes into `Projects/agents/AGENTS.md` and then into `astro-agents/AGENTS.md`. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:174)
- If both workspace-global and shared prompts apply, the current model again says to keep them active together. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:180)
- The workspace-global `Projects/agents/AGENTS.md` then behaves much like a repo-local `agents/AGENTS.md`: it checks for matching local prompts corresponding to shared `astro-agents` subtrees, keeps compatible guidance from both, and uses authority to settle conflicts. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:188)
- Workspace-global prompts are lower-authority than repo-local prompts, but higher than shared `astro-agents` prompts. [docs/architecture.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/architecture.md:112)
- The docs also warn public repos not to depend too heavily on workspace-global prompting or hardcoded private paths. [docs/usage.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/usage.md:327)

So in current source-of-truth terms:
- **bootstrapping** = thin initial route into the chain
- **repo `/agents`** = repo-local reusable overlays/extensions
- **workspace `/agents`** = workspace-global reusable overlays/defaults

And all three currently sit inside the older **composition + authority** model, not the newer handoff-centered model described in [docs/runtime-cleanup-plan.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/docs/runtime-cleanup-plan.md).

### Current Validation Surface to be Simplified

Current validation wiring, compressed:

- [validation/AGENTS.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/AGENTS.md) dispatches into shared review files under `validation/review/`.
- [validation/README.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/README.md) presents those shared reviews and starter requests.
- The shared surface mixes several roles:
  - direct reviews like [prompt-writing-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/prompt-writing-review.md) and [routing-and-authority-review.md](/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/astro-agents/validation/review/routing-and-authority-review.md)
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

This is a staged rewrite of the repo’s live runtime model, validation model, route visibility, and behavior-validation baseline.

Execute the phases in order. For every pass:

- decide the rule or boundary
- persist it in the owning long-lived file
- rewrite the live surface that still encodes the old model
- run the phase check before moving on

### Phase Dependencies

- Phase 1 comes first because the validation surface should not be rewired until the repo’s downstream runtime model is explicit.
- Phase 2 depends on Phase 1 because validation entrypoints, internal workflow wiring, and repo-local validation inclusion all depend on the new routing, handoff, and local-customization model.
- Phase 3 depends on Phase 1 and Phase 2 because route summaries and visibility rules should reflect the settled runtime and validation structure rather than the old one.
- Phase 4 depends on the first three phases because the maintained scenario baseline should test the model that the live surface actually uses, not the model being replaced.
- Phase 5 depends on the first four phases because the future docs should be refreshed only after the live runtime, validation, visibility, and scenario-baseline surfaces they describe have actually changed.
- When a later phase uncovers a contradiction in an earlier phase decision, fix the owning source-of-truth document first rather than patching around the contradiction locally.

### Phases

#### Phase 1 — Replace Composition + Authority Model

Goal: replace additive downstream composition with explicit routing, ownership, local-customization, conflict, and fail-safe rules.

Primary outputs: `docs/runtime-model.md`, `docs/architecture.md`, `docs/usage.md`, and the repo/workspace templates they govern.

##### Pass 1A — Define Bootstrap Semantics

- Decide what the initial workspace or repo `AGENTS.md` is allowed to do.
- Persist the canonical bootstrap rule in `docs/runtime-model.md`.
- Rewrite the bootstrap guidance in `docs/architecture.md` and `docs/usage.md`.
- Replace the old “thin initial route into the chain” wording in the repo and workspace `AGENTS.md` templates.

##### Pass 1B — Redefine Repo `/agents`

- Decide what belongs in a repo’s `agents/` folder under the new model.
- Persist the repo-local scope and ownership rule in `docs/architecture.md`.
- Rewrite the repo-level guidance and templates in `docs/usage.md`.
- Remove old matching-subtree and additive overlay guidance wherever it no longer survives.

##### Pass 1C — Redefine Workspace `/agents`

- Decide what belongs in `Projects/agents/` under the new model.
- Persist the workspace scope and ownership rule in `docs/architecture.md`.
- Rewrite the workspace root and workspace-global templates in `docs/usage.md`.
- Remove wording that keeps workspace-global and shared prompts active together after handoff if that behavior no longer survives.

##### Pass 1D — Define Handoff And Task Ownership

- Decide when one prompt becomes the owner of the `Task`.
- Persist the handoff and ownership rule in `docs/runtime-model.md`.
- Rewrite the routing guidance, templates, and examples in `docs/architecture.md` and `docs/usage.md` to name the task-owning prompt explicitly.
- Remove live wording that leaves dispatcher or selector ownership indefinite after a narrower route is chosen.

##### Pass 1E — Define `Instructions` Versus `Context`

- Decide the exact `Instructions` versus `Context` rule for deeper source-of-truth docs.
- Persist the canonical statement in `docs/runtime-model.md`.
- Rewrite `docs/architecture.md`, `docs/usage.md`, and the repo/workspace templates so deeper docs are supporting `Context` by default.
- Replace wording that implies deeper docs become active `Instructions` implicitly, and fold quoted text and tool outputs into the same rule where those cases are documented.

##### Pass 1F — Replace The Old Conflict Model

- Decide the replacement conflict model.
- Persist it in `docs/runtime-model.md`.
- Rewrite `docs/architecture.md` and `docs/usage.md` so downstream guidance no longer treats `precedence` as the general repo-runtime mechanism.
- Replace conflict language with route choice, handoff, delegated authority, and explicit local customization boundaries, while keeping `authority` only where it is still genuinely needed.

##### Pass 1G — Define Explicit Local Customization Boundaries

- Decide which repo-local and workspace-local inclusions are allowed.
- Persist the boundaries in `docs/runtime-model.md` and `docs/architecture.md`.
- Rewrite `docs/usage.md` so the repo and workspace templates show how local customization enters under the new model.
- Remove open-ended inclusion language that would turn those boundaries back into broad composition.

##### Pass 1H — Define Fail-Safe Behavior

- Decide the fail-safe defaults.
- Persist them in `docs/runtime-model.md`.
- Rewrite `docs/usage.md` and the downstream templates so ambiguous routing, unclear profile resolution, and missing delegated authority narrow or fail safe explicitly.
- Carry the same defaults into `docs/testing.md` where runtime behavior is later checked.

#### Phase 2 — Simplify Validation Model

Goal: rewire the validation surface around the Phase 1 runtime model by separating public review entrypoints from internal workflow nodes and making prompt roles explicit.

Primary outputs: `validation/AGENTS.md`, `validation/README.md`, shared review files under `validation/review/`, and the validation guidance in `README.md`, `docs/testing.md`, and `docs/usage.md`.

##### Pass 2A — Public Validation Surface

- Decide the small set of directly user-addressable shared review prompts.
- Persist that public surface in `validation/README.md` and `validation/AGENTS.md`.
- Rewrite `README.md`, `docs/testing.md`, and `docs/usage.md` so starter requests and validation guidance point to the same public set.
- Remove public-facing references that expose profile-specific workflows and internal reusable review files as normal starting documents.
- Keep `validation` as the umbrella for the broader checking surface and `review` as the prompt type users invoke on the current shared surface.

##### Pass 2B — Internal Validation Wiring

- Decide the internal role classification for validation files.
- Persist it in `validation/README.md` and the affected shared review files.
- Rewrite `validation/AGENTS.md`, `validation/review/documentation-review.md`, `validation/review/full-agent-surface-review.md`, the profile-specific workflows, and the internal reusable review files to follow the new wiring.
- Remove review coordination language that still behaves like unbounded composition.
- Keep independently invokable advanced paths explicit, but stop documenting them as the normal public entry surface.

##### Pass 2C — Repo-Local Validation Integration

- Decide when repo-local review prompts under `agents/validation/` are included after the shared route.
- Persist that rule in `docs/testing.md` and `docs/usage.md`.
- Add the standard prompt-use metadata and any additional ownership or local-customization fields to the shared review files that need them.
- Rewrite repo-local validation guidance and shared review outputs so repo-local inclusion appears only through the approved path and is visible in route summaries and source-of-truth guidance.

#### Phase 3 — Improve Runtime Visibility And Observability

Goal: make route choice, ownership, local inclusions, consulted source-of-truth docs, and fail-safe outcomes visible across the live surface, with validation outputs as the first concrete target.

Primary outputs: `docs/runtime-model.md`, `docs/architecture.md`, `docs/testing.md`, `validation/README.md`, the combined shared review files, and the future observability plan.

##### Pass 3A — Define Runtime Route Visibility

- Decide which route decisions, ownership changes, local inclusions, and consulted source-of-truth docs should be visible in user-facing outputs.
- Persist that rule in `docs/runtime-model.md`.
- Rewrite `docs/architecture.md` and `docs/testing.md` wherever the new visibility expectations affect review and source-of-truth usage.
- Define what belongs in prompt-level output summaries versus future tracing or other instrumentation, and record the out-of-scope remainder in `docs/future/runtime-observability-and-provenance.md`.

##### Pass 3B — Define Validation Route Summaries

- Decide the `Route Summary` shape for user-visible combined validation outputs.
- Persist the contract in `docs/testing.md` and `validation/README.md`.
- Rewrite the user-visible combined review files so they emit the new summary shape.
- Rewrite output guidance so profile selection and internal workflow steps are visible without turning internal prompts into public entrypoints.

##### Pass 3C — Define Broader Runtime Observability Follow-Ons

- Decide which observability needs stay out of scope for this cleanup.
- Persist that future-work boundary in `docs/future/runtime-observability-and-provenance.md`.
- Link the cleanup decisions to the future observability docs so the live surface and future work do not drift apart.
- Define how non-review validation assets such as scenario sets, regression specs, or other behavior-validation artifacts fit into the broader observability model, and record that in the future runtime docs.

#### Phase 4 — Establish Behavior Validation Baseline

Goal: add a small maintained scenario baseline for routing, instruction applicability, ownership, local customization, and fail-safe behavior without turning this cleanup into a full eval-harness project.

Primary outputs: `docs/testing.md`, a maintained scenario file under `agents/validation/`, and the linked future validation docs.

##### Pass 4A — Define Scenario Baseline Scope

- Decide which runtime behaviors the baseline must cover.
- Persist that scope in `docs/testing.md` and a new maintained scenario file under `agents/validation/`.
- Define the minimum scenario schema for routing, ownership, instruction applicability, local customization, and fail-safe outcomes in that scenario file.
- Separate the initial baseline from future work so the first version stays small enough to maintain.

##### Pass 4B — Seed The Initial Scenario Set

- Create the first maintained scenario set under `agents/validation/`.
- Add the initial cases for common review paths, profile-specific paths, repo-local validation inclusion, ambiguous routing, unsupported profiles, and delegated-authority edge cases.
- Record the expected directly user-addressable prompt, task-owning prompt, route summary, and fail-safe behavior for each scenario.
- Cross-link the new file from `docs/testing.md`.

##### Pass 4C — Define How The Baseline Is Used

- Decide how the scenario set fits into the validation contract without requiring a full harness.
- Persist that rule in `docs/testing.md`.
- Rewrite `docs/usage.md` and the repo-local validation guidance so routing, prompt-role, and source-of-truth changes explicitly re-check the maintained scenarios.
- Record in the future runtime-validation docs how later behavior-facing validation assets should extend rather than replace the baseline.

#### Phase 5 — Refresh Future Runtime Docs

Goal: update `docs/future/` so it accurately reflects the post-cleanup live surface and focuses on the deeper redesign work that remains after this cleanup.

Primary outputs: `docs/future/runtime-design.md`, `docs/future/runtime-governance.md`, `docs/future/runtime-observability-and-provenance.md`, `docs/future/runtime-validation.md`, and `docs/future/runtime-safety.md`.

##### Pass 5A — Remove Resolved Findings

- Review the current-project audit sections in the future docs against the completed Phase 1 through Phase 4 changes.
- Remove findings that are no longer true after the cleanup, and narrow findings that are now only partly true.
- Keep the remaining current-project audit material focused on the deeper redesign problems that the cleanup intentionally does not solve.

##### Pass 5B — Reframe Remaining Gap Statements

- Rewrite the remaining gap descriptions so they clearly distinguish between:
  - problems already solved by the cleanup
  - problems partly narrowed by the cleanup
  - deeper redesign work that still belongs in `docs/future/`
- Update recommended actions and open questions where the cleanup changes the starting point for the future redesign.

##### Pass 5C — Align Cross-References And Sequencing

- Update `docs/future/runtime-design.md` and the workstream docs so their dependencies, sequencing notes, and integrated questions reflect the cleaned-up live surface.
- Remove references that still assume the pre-cleanup runtime or validation structure.
- Keep the future docs explicitly forward-looking rather than letting them become a second source of truth for the live runtime model.

##### Pass 5D — Revalidate Against Primary Sources And The Current Surface

- Return to the primary sources after the cleanup changes land.
- Re-check the future-doc findings for both completeness and accuracy against:
  - the primary sources
  - the then-current `astro-agents` surface
- Validate the rest of the future-doc content against those same source-backed findings so the overviews, recommended actions, open questions, and sequencing notes still follow from the evidence.
- Remove, narrow, or add findings where the post-cleanup repo state or the primary-source review requires it.

### Persistence Targets

Use these as the main persistence targets when carrying decisions into the live surface:

- `docs/runtime-model.md`
  - stable runtime terminology, runtime rules, handoff/ownership language, `Instructions` versus `Context`, fail-safe defaults, and route-visibility expectations
- `docs/architecture.md`
  - route structure, scope ownership, bootstrap model, repo/workspace `agents/` role, and downstream structural guidance
- `docs/usage.md`
  - repo and workspace templates, starter patterns, downstream practical guidance, and validation-contract starter language
- `validation/AGENTS.md`
  - live shared validation dispatch behavior
- `validation/README.md`
  - the public validation surface, role descriptions, and starter requests
- `validation/review/*.md`
  - prompt-use metadata, ownership behavior, coordination rules, output requirements, and route-summary behavior in the shared review files
- `docs/testing.md`
  - the repo’s validation contract, canonical checks, and any maintained scenario-baseline requirement
- `agents/validation/`
  - repo-local validation additions and the maintained scenario baseline created in Phase 4
- `docs/future/runtime-observability-and-provenance.md`
  - future observability work that stays out of scope for this cleanup
- `docs/future/runtime-validation.md`
  - future behavior-facing validation work that extends the baseline created here
- `docs/future/runtime-design.md`, `docs/future/runtime-governance.md`, `docs/future/runtime-safety.md`
  - future integrated design framing and workstream-specific redesign material that must be refreshed after the live cleanup changes land

When a pass changes the live model, do not leave the decision only in this plan. Persist it in the owning file above during the same pass.

### Exit Checks

Use these as the minimum end-of-phase checks:

- After Phase 1:
  - review the updated runtime and downstream-usage docs against `docs/runtime-model.md`
  - run the applicable checks from `docs/testing.md` for `AGENTS.md`, `docs/architecture.md`, and `docs/usage.md`
- After Phase 2:
  - run the shared review checks that cover prompt writing, routing and authority behavior, documentation review, and full-surface coherence
  - confirm the public validation surface and the internal validation wiring no longer contradict each other
- After Phase 3:
  - confirm the new route-visibility expectations appear consistently in the shared review outputs and the human-facing validation docs
  - confirm future observability docs now reflect only the remaining out-of-scope work
- After Phase 4:
  - confirm the maintained scenario file exists, is linked from `docs/testing.md`, and is specific enough to check routing, ownership, applicability, and fail-safe behavior
  - confirm the validation contract names when the maintained scenarios must be re-checked
- After Phase 5:
  - confirm the `docs/future/` audits no longer describe resolved cleanup findings as current problems
  - confirm the future docs now start from the cleaned-up live surface and focus on the deeper redesign work that remains
  - confirm the future docs do not reintroduce stale terminology or act as a second live source of truth
  - confirm the future-doc findings and the surrounding content have been revalidated against both the primary sources and the then-current agent surface

At the end of the overall cleanup:

- `docs/runtime-model.md`, `docs/architecture.md`, `docs/usage.md`, `validation/AGENTS.md`, `validation/README.md`, `docs/testing.md`, and the relevant shared review files should describe the same runtime and validation model
- the public validation surface should expose only the intended user-addressable review prompts
- the repo should have a maintained scenario baseline for behavior-facing validation
- the future runtime docs should describe the remaining redesign space rather than the live problems already solved by this cleanup

### Execution Rules

- Do not treat this document as the final owner for any runtime or validation rule. Every settled rule should be moved into its long-lived owner during the same phase.
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
- If a pass reveals a new major design area that does not fit the current five phases, add it only after recording why the existing phase structure is no longer enough.

# Usage

This document is the human-facing source of truth for how repos and workspaces should apply the shared prompt library and structure their supporting documents.

Use `docs/glossary.md` when usage guidance depends on shared prompt-system terms that need one stable meaning across repos.

## Quick Navigation

Use this document in two passes:

- read the early sections for decision rules about minimum support, supporting documents, document splitting, and cross-linking
- use the later sections when you need concrete templates, examples, or suggested patterns to adapt

Key sections:

- `Minimum Document Set`
  - baseline document set for nontrivial repos
- `Document Naming And Cross-Linking`
  - stable naming and source-of-truth visibility rules
- `Routing Architecture`
  - repo, workspace root, and workspace-global `AGENTS.md` patterns
- `Bootstrap Prompts`
  - short prompts for invoking shared validation reviews in fresh threads
- `Recommended docs/testing.md Pattern`
  - validation contract structure and trigger design

## Minimum Document Set

Every nontrivial repo should provide a small minimum document set that helps both humans and agents work effectively.

Recommended minimum:

- `AGENTS.md`
  - the operational working brief for agents
- `README.md`
  - the human-facing overview, setup entrypoint, and orientation document
- `docs/architecture.md` or an equivalent design document
  - the source of truth for system structure, boundaries, and ownership
- `docs/testing.md` or an equivalent verification document
  - the source of truth for canonical test commands and verification expectations

If a repo is still too small to justify separate documents, keep the minimum necessary guidance in `README.md` and `AGENTS.md`. Split it out once the content becomes reusable, stable, or operationally important.

Larger repos may also add repo-local prompts under `agents/` and long-lived supporting docs under `docs/` when those materials are stable enough to justify their own source-of-truth location.

## When A Separate Source-Of-Truth Document Is Warranted

Prefer a separate supporting document when:

- the guidance is substantial enough to need its own stable source-of-truth location
- the guidance is explanatory rather than operational
- the information needs to stay stable across many tasks
- the same instruction would otherwise be repeated across multiple files
- the repo has recurring local terms, term boundaries, or term ownership that materially affect how it should be understood and warrant a dedicated glossary such as `docs/glossary.md`
- the repo has enough complexity that agents need a persistent source of truth

`AGENTS.md` should stay limited to short operational guidance: routing, immediate working constraints, and the source-of-truth docs an agent should follow.

## Document Naming And Cross-Linking

Prefer stable, predictable document names when they fit the repo:

- `README.md`
- `AGENTS.md`
- `docs/architecture.md`
- `docs/glossary.md`
- `docs/testing.md`
- `docs/<topic>-plan.md`
- `docs/<topic>-design.md`

These names are not required, but predictable names make documents easier for both humans and agents to find.

When these documents exist:

- make long-lived source-of-truth docs discoverable from `AGENTS.md`, `README.md`, or another clear entrypoint
- make the role of each doc explicit near the top, especially whether it is operational guidance or supporting explanation
- keep cross-references direct and current when doc names, paths, or ownership change
- avoid scattering the same instruction across multiple files without a clear owner

## Routing Architecture

Use this section as the practical companion to `docs/architecture.md`. For the formal routing model, precedence, and layer ownership, use `docs/architecture.md`. In that model, applicable prompts compose by default, and precedence resolves conflicts between applicable instructions. The templates below show how to establish that routing architecture at the repo and workspace levels.

### Repo AGENTS Template

The workspace `Projects/AGENTS.md` file is a bootstrap layer that helps route into the broader precedence chain, not the main reusable prompt layer inside it. Repo-level `AGENTS.md` files should stay focused on repo-local routing, source-of-truth activation, and routing into applicable shared prompts that may exist outside the repo.

```md
# <Repo> Agent Brief

## Prompt Routing
- Follow any higher-level workspace prompt-routing instructions when present.
- When higher-level routing selects a higher-level prompt subtree, check the corresponding subtree under `agents/` for matching local prompts.
- Keep applicable higher-level and matching local prompts active together.
- When applicable instructions conflict, use the applicable precedence rules to decide which instruction governs.
- Use other prompts under `agents/` when they directly match the request and do not correspond to a higher-level counterpart.

## Precedence
- More specific subtree-level `AGENTS.md` files take precedence within their scope.
- Otherwise instructions in this file apply by default within this repository.
- When matching local prompts under `agents/` and higher-level prompts both apply, keep compatible guidance from both.
- When their instructions conflict, the higher-precedence instruction governs.

## Source Of Truth
- Use `README.md` for the repo overview and major entrypoints.
- Use `docs/architecture.md` for structure, ownership, and interfaces when present.
- Use `docs/testing.md` for validation requirements and canonical checks when present.
- Use any other named local source-of-truth docs directly.
```

Add sections like these only when inline local guidance materially improves runtime use:

```md
## Scope
- This repo owns `<repo role>`.
- Prefer `<main change discipline>` for changes in this repo.

## Architecture
- Use `docs/architecture.md` for boundaries, ownership, and interfaces when present.
- Treat this section as a short local summary, not a replacement for `docs/architecture.md`.
- Treat `<public API boundary>` as the intended public interface.
- Keep `<behavior or asset>` in `<layer, module, or path>`.

## Contracts
- Preserve `<important contract>`.
- Preserve `<validation or compatibility expectation>`.

## Workflow
- Use `<repo-specific command or environment rule>`.
- Respect `<deployment or operational constraint>`.

## Testing Expectations
- Use `docs/testing.md` for validation requirements and canonical checks when present.
- Treat this section as a short local summary, not a replacement for `docs/testing.md`.
- Run `<canonical repo-specific command>` for meaningful changes.

## Review Lens
- Prioritize `<repo-specific review concern>`.
- Watch for `<repo-specific risk>`.
```

For repo files that may later become public, prefer this kind of generic activation wording over hardcoded workspace paths. The repo file can name the kind of shared guide that should govern locally without assuming a specific private prompt-library location.

When deeper source-of-truth docs exist, repo `AGENTS.md` should point to them explicitly instead of assuming the agent will discover them on its own.

### Workspace Root AGENTS Template

At the workspace level, for example under `Projects/`, prefer a much thinner bootstrap router whose job is to bootstrap the shared router, not to compete with the prompt library or repo-local files. Keep workspace-global reusable preferences and defaults in `Projects/agents/` instead:

```md
# Workspace Root Agent Brief

## Prompt Routing
- When available, use `agents/AGENTS.md` to activate any applicable workspace-global prompts.
- Use `astro-agents/AGENTS.md` to route into the shared prompt library.
- Keep applicable workspace-global and shared prompts active together.
- When applicable instructions conflict, use the applicable precedence rules to decide which instruction governs.
```

### Workspace Global AGENTS Template

When reusable prompts or user preferences should apply across multiple repos in one workspace without belonging in the shared library, prefer a separate `Projects/agents/AGENTS.md` layer:

```md
# Workspace Global Agent Brief

## Prompt Routing
- When higher-level routing selects a subtree under `astro-agents/`, check the corresponding subtree here for matching local prompts.
- Keep applicable shared and matching local prompts active together.
- When applicable instructions conflict, use the applicable precedence rules to decide which instruction governs.
- Use other prompts here when they directly match the request and do not correspond to a shared counterpart under `astro-agents/`.

## Precedence
- When matching local prompts here and shared prompts under `astro-agents/` both apply, keep compatible guidance from both.
- When their instructions conflict, the higher-precedence instruction governs.
```

## Bootstrap Prompts

Use bootstrap prompts when you want a fresh thread to invoke a shared path with minimal manual prompting. They should be short but lead to the intended activation within the routing system.

Common examples:

- `Do a full agent surface review`
- `Review the repo docs using the shared document writing review prompt`
- `Revise manuscript.tex using the shared science writing guide`

For additional examples, see:
- `validation/README.md` for validation- and review-related bootstrap prompts

## Pattern For Repos Using Shared Validation

When a repo uses shared validation from this prompt library, its `docs/testing.md` should define when validation is required, which shared reviews apply, and what completion bar the repo uses.

The example below is a recommended starter template for downstream repos importing shared validation from `astro-agents`. It is not the concrete validation contract for `astro-agents` itself; for this repo, use `docs/testing.md`.

For example, in `docs/testing.md` include a structure like:

```md
# Testing

This document is the human-facing source of truth for validation requirements in this repo.

## Purpose

Use this document to decide what validation is required when changing:

- `AGENTS.md`
- `README.md`
- files under `docs/`
- other files that change how agents should navigate, interpret, or apply the repo

## Canonical Checks

The canonical shared checks for this repo are the shared review prompts it relies on.

## Repo-Local Validation

When a repo needs validation prompts that are specific to its own structure, examples, or exceptions, keep them under `agents/validation/` rather than in the shared validation library, and point to them from `docs/testing.md` or repo `AGENTS.md` as appropriate.

## Agent Surface Validation

Use agent surface validation when changes affect the repo's agent surface, including `AGENTS.md`, `README.md`, relevant files under `docs/`, or other agent-facing prompt files.

### Required Reviews

- Changes to `AGENTS.md` files:
  - run prompt-writing review
  - run hierarchy-behavior review

- Changes to `README.md` or files under `docs/`:
  - run document-writing review
  - run documentation-architecture review

- Changes to other agent-facing prompt files:
  - run prompt-writing review
  - run hierarchy-behavior review

- Changes that substantially alter prompt or instruction structure:
  - run a full agent surface review

### Completion Standard

- Do not treat agent surface work as complete while direct validation findings remain unresolved.

## Regression Priorities

- prioritize preventing regressions in hierarchy clarity, source-of-truth visibility, and consistency across the repo's agent surface
```

Once adapted in a target repo, that repo's own `docs/testing.md` becomes the source of truth for its actual validation contract.

When a repo can safely depend on this shared library in its workspace context, the repo's `docs/testing.md` may name the specific shared validation prompts directly. Keep repo-local agent-facing validation prompts under `agents/validation`. Likewise, at the workspace level, keep validation prompts in `Projects/agents/validation` where practical.

## Repo AGENTS.md Guidance

Keep in repo `AGENTS.md`:

- repo purpose and boundaries
- important interface boundaries, architecture and ownership rules, and any important data or format assumptions the repo depends on
- repo-specific environment or deployment constraints, validation commands, completion expectations, and review priorities

Keep out of repo `AGENTS.md`:

- private absolute paths to the workspace prompt library
- assumptions that another repo's `AGENTS.md` will always be available
- generic authoring rules already covered by `astro-agents/authoring/*`
- generic coding-style defaults already covered by `astro-agents/authoring/code/*`
- subtree-specific rules that belong in a deeper `AGENTS.md`
- long background documentation that repo docs should own instead

## Agent Surface Considerations for Public Projects

For a public project, keep its agent surface from depending too heavily on workspace-global prompting, particularly when other contributors are expected.

- keep project-specific guidance visible inside the project's own agent surface
- do not hardcode absolute paths to the private workspace prompt library
- use a generic bootstrap line such as `Follow any higher-level workspace prompt-routing instructions when present.`

This allows a private workspace to supply higher-level routing and shared prompts without baking private path assumptions into public repositories.

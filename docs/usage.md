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
- `Routing And Workflow Architecture`
  - repo, workspace root, and workspace-global `AGENTS.md` patterns
- `Starter Requests`
  - short prompts for invoking shared validation reviews in fresh threads
- `Recommended docs/testing.md Pattern`
  - validation contract structure and trigger design

## Minimum Document Set

Every nontrivial repo should provide a small minimum document set that helps both humans and agents work effectively.

Recommended minimum:

- `AGENTS.md`
  - the operational working brief for agents
- `README.md`
  - the human-facing overview, setup starting document, and orientation document
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

`AGENTS.md` should stay limited to short operational guidance: routing and workflow, immediate working constraints, and the source-of-truth docs an agent should follow.

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

- make long-lived source-of-truth docs discoverable from `AGENTS.md`, `README.md`, or another clear starting document
- make the role of each doc explicit near the top, especially whether it is operational guidance or supporting explanation
- keep cross-references direct and current when doc names, paths, or ownership change
- avoid scattering the same instruction across multiple files without a clear owner

When a repo uses `docs/data-sources.md`, treat that document as the source of truth for durable data artifacts the repo consumes, produces, ships, or expects users to work with.

Use `docs/data-sources.md` for questions such as:

- which data artifacts matter in this repo
- whether an artifact is committed, generated, external, downloaded, cached, or reproducible
- where those artifacts usually live
- which parts of the repo or workflow produce or consume them
- which data examples are real sample data artifacts that users or agents are expected to inspect or run against

Do not use `docs/data-sources.md` as the owner for:

- CLI or API input grammar
- normalization rules
- persisted schema contracts
- field-level interface semantics

When a repo needs a stable source of truth for data contracts or persistence rules, use a more explicit owner such as `docs/architecture.md`, `docs/api.md`, or a narrower document whose name makes that contract role clear.

## Routing And Workflow Architecture

Use this section as the practical companion to `docs/architecture.md`. For the formal routing and workflow model, instruction authority, and scope ownership, use `docs/architecture.md`. In that model, applicable prompts compose by default, and higher-authority instructions settle conflicts between applicable instructions. The templates below show how to establish that routing and workflow model at the repo and workspace levels.

### Repo AGENTS Template

The workspace `Projects/AGENTS.md` file is a thin starting file that helps dispatch into the broader instruction-authority chain, not the main reusable prompt set inside it. Repo-level `AGENTS.md` files should stay focused on repo-local routing-and-workflow guidance, source-of-truth references, and broad routing into applicable shared prompts that may exist outside the repo.

```md
# <Repo> Agent Brief

## Prompt Routing And Workflow
- Follow any higher-level workspace routing-and-workflow instructions when present.
- When higher-level instructions route work into a higher-level prompt subtree, check the corresponding subtree under `agents/` for matching local prompts.
- Keep applicable higher-level and matching local prompts active together.
- When applicable instructions conflict, use the applicable instruction-authority rules to decide which instruction applies.
- Use other prompts under `agents/` when they directly match the request and do not correspond to a higher-level counterpart.

## Instruction Authority And Conflict Handling
- More specific subtree-level `AGENTS.md` files have higher instruction authority within their scope.
- Otherwise instructions in this file apply by default within this repository.
- When matching local prompts under `agents/` and higher-level prompts both apply, keep compatible guidance from both.
- When their instructions conflict, follow the higher-authority instruction.

## Source Of Truth
- Use `README.md` for the repo overview and major starting documents.
- Use `docs/architecture.md` for structure, ownership, and interfaces when present.
- Use `docs/testing.md` for validation requirements and canonical checks when present.
- Use any other named local source-of-truth docs directly.
```

Add sections like these only when inline local guidance materially improves runtime use:

```md
## Scope
- This repo owns `<repo role>`.
- Prefer `<main change discipline>` for changes in this repo.
- Documentation surface profile: `<profile-name>`.

## Architecture
- Use `docs/architecture.md` for boundaries, ownership, and interfaces when present.
- Treat this section as a short local summary, not a replacement for `docs/architecture.md`.
- Treat `<public API boundary>` as the intended public interface.
- Keep `<behavior or asset>` in `<scope, module, or path>`.

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

## Review Criteria
- Prioritize `<repo-specific review concern>`.
- Watch for `<repo-specific risk>`.
```

Use this document as the source of truth for how a downstream repo declares `Documentation surface profile` in its root `AGENTS.md`.

When a repo declares `Documentation surface profile`, that value should be implemented either by the shared validation library or by higher-authority local prompt files.

When present, keep that `Documentation surface profile` declaration in a short `## Scope` section near the top of the root `AGENTS.md` so shared upgrade review and later repo editing work can read it reliably.

For repo files that may later become public, prefer this kind of generic routing wording over hardcoded workspace paths. The repo file can name the kind of shared guide that should apply locally without assuming a specific private prompt-library location.

When deeper source-of-truth docs exist, repo `AGENTS.md` should point to them explicitly instead of assuming the agent will inspect them on its own.

### Workspace Root AGENTS Template

At the workspace level, for example under `Projects/`, prefer a much thinner initial dispatcher whose job is to route into the shared dispatcher, not to compete with the prompt library or repo-local files. Keep workspace-global reusable preferences and defaults in `Projects/agents/` instead:

```md
# Workspace Root Agent Brief

## Prompt Routing And Workflow
- When available, use `agents/AGENTS.md` to apply any applicable workspace-global prompts.
- Use `astro-agents/AGENTS.md` to dispatch into the shared prompt library.
- Keep applicable workspace-global and shared prompts active together.
- When applicable instructions conflict, use the applicable instruction-authority rules to decide which instruction applies.
```

### Workspace Global AGENTS Template

When reusable prompts or user preferences should apply across multiple repos in one workspace without belonging in the shared library, prefer a separate `Projects/agents/AGENTS.md` scope:

```md
# Workspace Global Agent Brief

## Prompt Routing And Workflow
- When higher-level instructions route work into a subtree under `astro-agents/`, check the corresponding subtree here for matching local prompts.
- Keep applicable shared and matching local prompts active together.
- When applicable instructions conflict, use the applicable instruction-authority rules to decide which instruction applies.
- Use other prompts here when they directly match the request and do not correspond to a shared counterpart under `astro-agents/`.

## Instruction Authority And Conflict Handling
- When matching local prompts here and shared prompts under `astro-agents/` both apply, keep compatible guidance from both.
- When their instructions conflict, follow the higher-authority instruction.
```

## Starter Requests

Use starter requests when you want a fresh thread to invoke a shared path with minimal manual prompting. They should be short but lead to the intended route within the shared routing and workflow system.

Common examples:

- `Do a full agent surface review`
- `Review the repo docs using the shared documentation review`
- `Revise manuscript.tex using the shared science writing guide`

For additional examples, see:
- `validation/README.md` for validation- and review-related starter requests

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
  - run routing-and-authority review (`validation/review/routing-and-authority-review.md`)

- Changes to `README.md` or files under `docs/`:
  - run the shared documentation review selector
  - let it determine the repo's declared documentation surface profile, or `private-default` when none is declared

- Changes to other agent-facing prompt files:
  - run prompt-writing review
  - run routing-and-authority review (`validation/review/routing-and-authority-review.md`)

- Changes that substantially alter prompt or instruction structure:
  - run a full agent surface review

### Completion Standard

- Do not treat agent surface work as complete while direct validation findings remain unresolved.

## Regression Priorities

- prioritize preventing regressions in route-structure clarity, source-of-truth visibility, and consistency across the repo's agent surface
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

## Documentation Surface Considerations for Public Python Projects

For a public Python project, treat the public documentation surface as more than `README.md` plus repo-operational docs.

By default, treat these as part of the public documentation surface:

- `README.md`
- public package metadata in `pyproject.toml` that affects package presentation or documentation discovery
- `docs/` source pages and docs-site configuration when the project publishes docs

Treat these as part of the public documentation surface when the public entry surface exposes or depends on them:

- generated API-doc inputs such as docstrings and docs-generation config
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- `LICENSE`
- examples, notebooks, or other tutorial assets
- docs-related tests or scripts that verify public examples, README snippets, or docs drift

For files under `docs/`, review the publicly reachable graph by default rather than the full tree.

- start from public starting points such as `README.md`, docs navigation/config, and public package metadata
- include docs pages that those starting documents link to or publish
- ignore unlinked planning or draft material unless it is explicitly published or requested

Treat docstrings, docs-generation config, examples, and docs-related tests as documentation-review inputs only when they materially define or verify reachable public docs.

Use `docs/public-python-docs-design.md` for the deeper design rationale and source-backed definition of this public-doc surface model.

## Agent Surface Considerations for Public Projects

For a public project, keep its agent surface from depending too heavily on workspace-global prompting, particularly when other contributors are expected.

- keep project-specific guidance visible inside the project's own agent surface
- do not hardcode absolute paths to the private workspace prompt library
- use a generic bootstrap line such as `Follow any higher-level workspace routing-and-workflow instructions when present.`

This allows a private workspace to supply higher-level routing-and-workflow guidance and shared prompts without baking private path assumptions into public repositories.

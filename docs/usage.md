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
- `Bootstrap Model`
  - minimal bootstrap patterns for repo-local and global use
- `Starter Requests`
  - short prompts for invoking shared validation reviews in fresh threads
- `Shared Validation from astro-agents`
  - minimal downstream reference pattern for shared validation

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

## Bootstrap Model

Use this section when deciding how little bootstrap a repo or user setup needs in order to use `astro-agents`. The goal is to keep bootstrap minimal and keep reusable routing, terminology, and workflow context inside `astro-agents` itself.

In both cases below, the suggested bootstrap prompt should do only three things:

- route into `astro-agents`
- make it clear that `astro-agents` is the shared prompt library in use
- provide only the minimum local context needed to make that routing intelligible

Bootstrap prompts should stay limited to routing, library identification, and the minimum local context needed for that route.

### Case 1: Single-Repo Use

When only one repo should use `astro-agents`, put the bootstrap in that repo's root `AGENTS.md`.

Use a minimal repo-level bootstrap such as:

```md
## Astro-Agents Bootstrap
- Use `astro-agents` for reusable authoring, review, and routing guidance in this repo.
```

This keeps the repo-specific bootstrap local to the repo without requiring any global Codex setup.

### Case 2: Global Use

When `astro-agents` should be the user's shared default across repos, put the bootstrap in `$CODEX_HOME/AGENTS.md`, commonly `~/.codex/AGENTS.md`.

Use a minimal global bootstrap such as:

```md
## Astro-Agents Bootstrap
- Use `astro-agents` by default for reusable authoring, review, and routing guidance across repos.
```

In this mode, repo root `AGENTS.md` files should add only repo-local guidance, source-of-truth docs, or narrower routing that the repo itself needs. If one repo should not use the global default, keep the global bootstrap in `$CODEX_HOME/AGENTS.md` and use the repo root `AGENTS.md` to opt out or redirect for that repo with a minimal repo-level exception such as:

```md
## Astro-Agents Bootstrap
- Do not use the shared `astro-agents` prompt library in this repo.
- Follow this repo's local guidance and source-of-truth documents instead.
```

## Local Customization Entry Points

Use this section when a repo or user setup needs local customization beyond the shared defaults in `astro-agents`.

Recommended entry points are:

- `$CODEX_HOME/AGENTS.md` for global bootstrap
- explicitly routed prompts under `<global>/agents`
- the repo root `AGENTS.md`
- narrower subtree `AGENTS.md` files
- explicitly routed prompts under `agents/`

To use one of these entry points, route to it explicitly from the nearest enclosing `AGENTS.md` that governs the work.

For example:

```md
## Prompt Routing And Workflow
- Use agents/review/astrophysics-notebook-review.md for astrophysics Jupyter notebook review in this repo.
```

## Documentation Surface Profile

When a downstream repo uses `Documentation surface profile`, declare it in a short `## Scope` section near the top of the root `AGENTS.md`, for example:

```md
## Scope
- Documentation surface profile: public-python.
```

## Starter Requests

Use starter requests when you want a fresh thread to invoke a shared path with minimal manual prompting. They should be short but lead to the intended route within the shared routing and workflow system.

Common examples:

- `Do a full agent surface review`
- `Review the repo docs using the shared documentation review`
- `Revise manuscript.tex using the shared science writing guide`

For additional examples, see:
- `validation/README.md` for the public shared review entrypoints and upgrade-specific starter requests

## Shared Validation from astro-agents

When a downstream repo wants to rely on shared validation from `astro-agents`, its `docs/testing.md` can be as small as:

```md
# Testing

## Shared Validation from astro-agents
Use the shared base testing guidance in `astro-agents/validation/base-testing.md`.

## Repo-Local Validation
Run repo-local review files under `agents/validation/` only when the changed scope makes them applicable after the shared review path is active.
```

## Repo AGENTS.md Guidance

Prefer repo `AGENTS.md` content such as:

- repo purpose and boundaries
- important interface boundaries, architecture and ownership rules, and any important data or format assumptions the repo depends on
- repo-specific environment or deployment constraints, validation commands, completion expectations, and review priorities
- generic routing wording that keeps shared guides portable across setups
- subtree-local rules in deeper `AGENTS.md` files when the guidance belongs to a narrower scope
- long background explanation in repo docs when that material needs a stable source-of-truth location

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

For a public project, keep its agent surface from depending too heavily on user-specific global prompting, particularly when other contributors are expected.

- keep project-specific guidance visible inside the project's own agent surface
- do not hardcode absolute paths to the private workspace prompt library
- use a generic bootstrap line such as `Use the shared astro-agents prompt library for reusable authoring, review, and routing guidance.`

This allows a user to keep a reusable shared prompt library without baking private path assumptions or user-specific bootstrap structure into public repositories.

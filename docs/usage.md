# Usage

This document is the human-facing source of truth for how repos and workspaces
should apply the shared prompt library.

Use it when adopting `astro-agents`, choosing bootstrap location, declaring a
documentation surface profile, and wiring shared validation.

Use `docs/glossary.md` when usage guidance depends on shared prompt-system
terms that need one stable meaning across repos.

## Bootstrapping Astro-Agents

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

## Documentation Surface Profile

When a downstream repo uses `Documentation surface profile`, declare it in a short `## Scope` section near the top of the root `AGENTS.md`, for example:

```md
## Scope
- Documentation surface profile: public-python.
```

## Shared Validation

When a downstream repo wants to rely on shared validation from `astro-agents`, its `docs/testing.md` can be as small as:

```md
# Testing

## Shared Validation
Use the shared base testing guidance in `astro-agents/validation/base-testing.md`.

## Repo-Local Verification
Add repo-local verification commands and completion expectations below as needed.
```

## Shared Guidance

For shared recommendation docs that downstream repos may reference directly, use:

- `guidance/agent-surface.md`
  - shared agent-surface starter, placement, and local/shared structure guidance for downstream repos
- `guidance/public-python-projects.md`
  - shared public Python repo-shape, source-of-truth, and public-surface guidance
- `guidance/python-development.md`
  - shared Python architecture, coding-policy, and development-workflow guidance

These are shared recommendation docs that downstream repos may reference directly.

When a downstream repo adopts one of these docs:

- reference it directly from root `AGENTS.md` when the repo wants that shared recommendation visible in the operational working surface
- reference it from local source-of-truth docs such as `docs/architecture.md` or `docs/development.md` when the recommendation should be part of the repo's durable local guidance
- keep exact commands, package boundaries, persisted contracts, lifecycle rules, and repo-specific exceptions in the repo's own docs

For example, a repo root `AGENTS.md` may include:

```md
## Shared Guidance
- Use `astro-agents/guidance/agent-surface.md` for shared agent-surface guidance.
- Use `astro-agents/guidance/public-python-projects.md` for shared public Python repo guidance.
- Use `astro-agents/guidance/python-development.md` for shared Python development guidance.
```

And a local source-of-truth doc such as `docs/architecture.md` may include:

```md
## Shared Guidance

This repo adopts the shared guidance in:
- `astro-agents/guidance/agent-surface.md`
- `astro-agents/guidance/public-python-projects.md`
- `astro-agents/guidance/python-development.md`

Repo-local commands, package boundaries, contracts, lifecycle rules, and exceptions in this repo's own docs remain the source of truth.
```

## Authoring Requirements

If a downstream repo wants to impose strong requirements on writing or coding style, add an explicit `## Authoring Requirements` section to the root `AGENTS.md` and point directly to the shared guides it wants to require.

This is stronger than a generic bootstrap line. Use it when the repo wants agents to follow specific shared authoring guides for recurring work.

For example:

```md
## Authoring Requirements
- For Python code, follow `astro-agents/authoring/code/python.md`.
- For repo docs such as `docs/architecture.md`, `docs/testing.md`, `docs/development.md`, and similar long-lived repo documents, follow `astro-agents/authoring/writing/repo-docs.md`.
- For `README.md`, follow `astro-agents/authoring/writing/readme-md.md` in addition to `astro-agents/authoring/writing/repo-docs.md`.
- For plan documents or phased execution docs when they are created or revised, follow `astro-agents/authoring/writing/plan.md`.
```

## Starter Requests

Use starter requests when you want a fresh thread to invoke a shared path with minimal manual prompting. They should be short but lead to the intended route within the shared routing and workflow system.

Common examples:

- `Do a full agent surface review using astro-agents`
- `Review the repo docs using the shared documentation review using astro-agents`
- `Revise manuscript.tex using the shared science writing guide using astro-agents`

For additional examples, see:
- `validation/README.md` for the public shared review entrypoints and upgrade-specific starter requests

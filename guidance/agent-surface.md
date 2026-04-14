# Agent Surface

This document is a shared recommendation for downstream repos, workspaces, and
users that use `astro-agents`.

Use it when deciding what a repo's initial agent surface should include, where
local agent-surface guidance should live, and how shared and local guidance
should connect without collapsing into one large prompt surface.

Use `docs/usage.md` for how to include this guidance in a downstream repo. Use
this document for starter-surface, customization placement, and local/shared
structure guidance.

Keep repo commands, package boundaries, persisted contracts, lifecycle rules,
and repo-specific exceptions in repo-local docs.

## Greenfield Starter Surface

- For a new repo, establish enough local agent surface early that a brand new
  thread can follow core repo expectations before review findings surface.
- Use `guidance/public-python-projects.md` for the broader repo document set.
  For the local operational starter surface, start with:
- root `AGENTS.md`: bootstrap, source-of-truth references, shared-guidance
  references, authoring requirements when needed, and `Working Rules`
- `docs/architecture.md`: intended package shape, contracts, lifecycle, and
  other early design decisions
- `docs/testing.md`: shared validation plus the current repo-local
  verification commands or expectations, even if still minimal
- `docs/development.md`: local bootstrap, environment, hooks, and daily
  commands once those choices exist

## Where Customization Belongs

- `$CODEX_HOME/AGENTS.md`
  - global bootstrap when one user wants `astro-agents` available
    across repos
- `<global>/agents`
  - optional global reusable prompts or guides that should be used only through
    explicit routing rather than through another dispatcher layer
- `<repo>/AGENTS.md`
  - repo-specific architecture, workflow, testing expectations, review
    priorities, and references to any shared guidance the repo has chosen to
    adopt
- `<repo>/agents`
  - repo-local reusable prompts that are too specific for the shared library
    and should be invoked through explicit repo-local routing
- `<repo>/<subtree>/AGENTS.md`
  - narrow local routing or bounded-choice behavior, or subtree-specific
    constraints tied to document type, notation, data, tooling, or workflow
- repo-local source-of-truth docs
  - explanatory, architectural, contractual, or operational reference material
    that should stay in docs rather than prompts

## Customization Guidance

- Keep shared guidance shared, repo-specific rules local to the repo, and
  subtree-specific constraints local to the subtree.
- When an important local behavior should affect a brand new thread, surface it
  in the repo root `AGENTS.md` with a small shared core such as:

```md
## Working Rules
- For package structure, public API boundaries, persisted contracts, and lifecycle-sensitive changes, consult `docs/architecture.md` before editing.
- Before concluding substantial work, satisfy the verification expectations in `docs/testing.md`.
```

- Add only repo-specific bullets beyond that shared core when a repo has local
  docs or triggers that should affect first-turn behavior.
- Keep durable detail in repo-local source-of-truth docs. Use reviews to
  enforce or refine those expectations rather than to introduce them for the
  first time.
- Prefer explicit routing to local prompts from the nearest enclosing
  `AGENTS.md` that governs the work.
- Keep bootstrap files brief. They should identify the shared library in use,
  establish immediate scope, and point to the next source of truth or prompt.
- Avoid duplicating shared guidance in repo-local prompts unless the repo is
  intentionally making a local exception.
- Keep repo-specific facts visible inside the repo's own agent surface rather
  than assuming a user-global setup will supply them.
- Avoid hardcoding private absolute workspace paths in public repos.
- Prefer a narrower local prompt over expanding a broad repo bootstrap file
  when the guidance matters only to one subtree or one recurring local task.

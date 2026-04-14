# Guidance

This folder holds `astro-agents`-aware shared recommendation docs that
downstream repos may reference directly from `AGENTS.md` or local
source-of-truth docs.

These docs are for both humans and agents. They are not routed prompts and not
source-of-truth docs for `astro-agents` itself.

## Boundary

- This repo's own `AGENTS.md` files should not route to or reference
  `guidance/`.
- Downstream repos may reference `guidance/` directly from `AGENTS.md`,
  `docs/architecture.md`, `docs/development.md`, or similar local
  source-of-truth docs when they adopt a recommendation.
- Downstream repos should still keep exact commands, package boundaries,
  persisted contracts, lifecycle rules, and repo-specific exceptions in their
  own local docs.

## Current Documents

- `agent-surface.md`
  - shared agent-surface starter, placement, and local/shared structure guidance for
    downstream repos
- `public-python-projects.md`
  - shared repo-shape, source-of-truth, and public-surface guidance for
    downstream public Python repos
- `python-development.md`
  - shared Python architecture, coding-policy, and development-workflow
    guidance for downstream repos

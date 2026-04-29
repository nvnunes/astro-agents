# Guidance

This folder holds `astro-agents`-aware shared recommendation docs that
downstream projects may reference directly from `AGENTS.md` or local
source-of-truth docs.

These docs are for both humans and agents. They are not routed prompts and not
source-of-truth docs for `astro-agents` itself.

## Boundary

- This project's own `AGENTS.md` files should not route to or reference
  `guidance/`.
- Downstream projects may reference `guidance/` directly from `AGENTS.md`,
  `docs/architecture.md`, `docs/development.md`, or similar local
  source-of-truth docs when they adopt a recommendation.
- Downstream projects should still keep exact commands, package boundaries,
  persisted contracts, lifecycle rules, and project-specific exceptions in their
  own local docs.

## Current Documents

- `agent-surface.md`
  - shared agent-surface starter, placement, and local/shared structure guidance for
    downstream projects
- `public-python-projects.md`
  - shared project-structure, source-of-truth, and public-surface guidance for
    downstream public Python projects
- `python-development.md`
  - shared Python architecture, coding-policy, and development-workflow
    guidance for downstream projects

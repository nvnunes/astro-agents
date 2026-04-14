# Astro Agents

Astro Agents is a reusable library for authoring, routing, reviewing, and
supporting AI-agent guidance in repositories.
It also includes source-of-truth design docs for repo-upgrade guidance,
downstream shared recommendation docs, and related agent-surface patterns.

## Overview

It currently includes:

- authoring prompts and guides in `authoring/`
- shared downstream recommendation docs in `guidance/`
- shared validation review entrypoints and workflows in `validation/`
- upgrade design guidance in `docs/upgrade-design.md`
- top-level and folder-level `AGENTS.md` guidance

This repo also includes repo-local validation review files for `astro-agents` itself under `agents/`.

## Start Here

- To understand the library's route structure and design, start with `docs/architecture.md`.
- To use the library in another repo, start with `docs/usage.md`.
- To use the shared downstream recommendation docs, start with `guidance/README.md`.
- To author or revise prompts and guides, start with `authoring/README.md`.
- To review prompts, documentation, or route structure, start with `validation/README.md`.
- To assess a repo or propose how to group the upgrade work, start with the upgrade path in `validation/README.md`.
- To revise the shared upgrade model, start with `docs/upgrade-design.md`.

## Repository Layout

- `authoring/`
  - shared authoring prompts and guides
- `guidance/`
  - shared recommendation docs for downstream repos
- `validation/`
  - shared validation review entrypoints, workflows, and upgrade assessment paths
- `docs/`
  - architecture, usage, testing, glossary, and upgrade-design documentation
- `agents/`
  - repo-local validation review files for `astro-agents`

## Key Documents

For deeper architecture, usage, downstream guidance, and validation material,
see:

- `docs/architecture.md`
  - route structure, rationale, scope ownership, validation, maintenance, and starting documents
- `docs/usage.md`
  - bootstrap, documentation surface profile declaration, starter requests, and shared validation wiring
- `guidance/README.md`
  - boundary and entrypoint for the shared downstream recommendation-doc family
- `guidance/agent-surface.md`
  - shared agent-surface starter, placement, and local/shared structure guidance
- `guidance/public-python-projects.md`
  - shared public Python repo-shape, source-of-truth, and public-surface guidance
- `guidance/python-development.md`
  - shared Python architecture, coding-policy, and development-workflow guidance
- `docs/glossary.md`
  - shared terminology, term boundaries, and rules for when a term stays local versus becoming repo-wide
- `docs/runtime-model.md`
  - runtime terminology, control-flow concepts, instruction authority, and terminology-reframing guidance for this repo
- `docs/testing.md`
  - the repo-local validation contract for this repo, including when repo-local review files apply
- `validation/base-testing.md`
  - the shared validation baseline: review mapping, completion expectations, and regression priorities for agent-surface work
- `docs/upgrade-design.md`
  - the shared review-led upgrade model and guidance for grouping the work
- `authoring/README.md`
  - authoring-guide design, including the special note on `authoring/agents/agents-md.md`
- `validation/README.md`
  - folder-level guidance for the public shared review entrypoints, the upgrade-specific path, and the internal workflow map

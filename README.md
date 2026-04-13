# Astro Agents

Astro Agents is a reusable prompt library for authoring, routing, and reviewing AI-agent guidance in repositories.
It also includes source-of-truth design docs for repo-upgrade guidance and related agent-surface patterns.

## Overview

It currently includes:

- authoring prompts and guides in `authoring/`
- shared validation review entrypoints and workflows in `validation/`
- upgrade design guidance in `docs/upgrade-design.md`
- top-level and folder-level `AGENTS.md` guidance

This repo also includes repo-local validation review files for `astro-agents` itself under `agents/`.

## Start Here

- To understand the library's route structure and design, start with `docs/architecture.md`.
- To use the library in another repo, start with `docs/usage.md`.
- To author or revise prompts and guides, start with `authoring/README.md`.
- To review prompts, documentation, or route structure, start with `validation/README.md`.
- To assess a repo or propose how to group the upgrade work, start with the upgrade path in `validation/README.md`.
- To revise the shared upgrade model, start with `docs/upgrade-design.md`.

## Repository Layout

- `authoring/`
  - shared authoring prompts and guides
- `validation/`
  - shared validation review entrypoints, workflows, and upgrade assessment paths
- `docs/`
  - architecture, usage, testing, glossary, and upgrade-design documentation
- `agents/`
  - repo-local validation review files for `astro-agents`

## Key Documents

For deeper architecture, usage, and validation guidance, see:

- `docs/architecture.md`
  - route structure, rationale, scope ownership, validation, maintenance, and starting documents
- `docs/usage.md`
  - minimum supporting docs, routing and workflow patterns, starter requests, and patterns for repos that may later become public
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

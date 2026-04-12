# Astro Agents

Astro Agents is a reusable prompt library for authoring, routing, and reviewing AI-agent guidance in repositories.
It also includes source-of-truth design docs for repo-upgrade guidance and related agent-surface patterns.

## Overview

It currently includes:

- authoring prompts and guides in `authoring/`
- validation prompts in `validation/`
- upgrade design guidance in `docs/upgrade-design.md`
- top-level and folder-level `AGENTS.md` guidance

This repo also includes repo-local validation prompts for `astro-agents` itself under `agents/`.

## Start Here

- To understand the library's route structure and design, start with `docs/architecture.md`.
- To use the library in another repo, start with `docs/usage.md`.
- To author or revise prompts and guides, start with `authoring/README.md`.
- To review prompts or documentation, start with `validation/README.md`.
- To assess a repo or propose how to group the upgrade work, start with `validation/README.md`.
- To revise the shared upgrade model, start with `docs/upgrade-design.md`.

## Repository Layout

- `authoring/`
  - shared authoring prompts and guides
- `validation/`
  - shared validation prompts
- `docs/`
  - architecture, usage, testing, glossary, and upgrade-design documentation
- `agents/`
  - repo-local validation prompts for `astro-agents`

## Key Documents

For deeper architecture, usage, and validation guidance, see:

- `docs/architecture.md`
  - route structure, rationale, instruction authority, scope ownership, validation, maintenance, and starting documents
- `docs/usage.md`
  - minimum supporting docs, routing and workflow patterns, starter requests, and patterns for repos that may later become public
- `docs/glossary.md`
  - shared terminology, term boundaries, and rules for when a term stays local versus becoming repo-wide
- `docs/runtime-model.md`
  - runtime terminology, control-flow concepts, and terminology-reframing guidance for this repo
- `docs/testing.md`
  - the validation contract for this repo: requirements, canonical checks, and completion expectations
- `docs/upgrade-design.md`
  - the shared review-led upgrade model and guidance for grouping the work
- `authoring/README.md`
  - authoring-guide design, including the special note on `authoring/agents/agents-md.md`
- `validation/README.md`
  - folder-level guidance for the shared validation prompts, including reusable starter requests

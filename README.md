# Astro Agents

Astro Agents is a reusable prompt library for authoring, routing, reviewing, and upgrading AI-agent guidance in repositories.
It was developed for astrophysics research workflows and will grow over time to become more specific to that domain.

## Overview

It currently includes:

- authoring prompts and guides in `authoring/`
- validation prompts in `validation/`
- upgrade prompts in `upgrade/`
- top-level and folder-level routing rules in `AGENTS.md` files

This repo also includes repo-local validation prompts for `astro-agents` itself under `agents/`.

## Start Here

- To understand the library's hierarchy and design, start with `docs/architecture.md`.
- To use the library in another repo, start with `docs/usage.md`.
- To author or revise prompts and guides, start with `authoring/README.md`.
- To review prompts or documentation, start with `validation/README.md`.
- To plan a repo upgrade, start with `docs/upgrade-design.md` and `upgrade/README.md`.

## Repository Layout

- `authoring/`
  - shared authoring prompts and guides
- `validation/`
  - shared validation prompts
- `upgrade/`
  - shared upgrade prompts
- `docs/`
  - architecture, usage, testing, glossary, and planning documentation
- `agents/`
  - repo-local validation prompts for `astro-agents`

## Key Documents

For deeper architecture, usage, and validation guidance, see:

- `docs/architecture.md`
  - hierarchy model, rationale, precedence, layer ownership, validation, maintenance, and entrypoints
- `docs/usage.md`
  - minimum supporting docs, routing architecture, bootstrap prompts, and patterns for repos that may later become public
- `docs/glossary.md`
  - shared terminology, term boundaries, and rules for when a term stays local versus becoming repo-wide
- `docs/testing.md`
  - the validation contract for this repo: requirements, canonical checks, and completion expectations
- `docs/upgrade-design.md`
  - the upgrade process design and next steps
- `authoring/README.md`
  - authoring-guide design, including the special note on `authoring/agents/agents-md.md`
- `validation/README.md`
  - folder-level guidance for the shared validation prompt families, including reusable bootstrap prompts
- `upgrade/README.md`
  - folder-level guidance for the shared upgrade prompts

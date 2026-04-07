# Astro Agents

This repository is the shared prompt library for the workspace.

This `README.md` is the human-facing entrypoint for the library. Its peer file, `AGENTS.md`, is the operational router for agents using it. The pair is intentional: `AGENTS.md` stays short, scoped, and action-oriented, while `README.md` points to the deeper design and usage guidance that would otherwise overload the runtime file.

## Overview

This library holds reusable prompt assets that should not be duplicated across repositories. It currently includes:

- authoring guides in `authoring/`
- validation prompts in `validation/`
- top-level and subgroup routing rules in `AGENTS.md` files

This repo also includes repo-local validation overlays for `astro-agents` itself under `agents/`.

## Hierarchy At A Glance

The library sits within a broader instruction hierarchy. The following are conceptual hierarchy locations, not local repo paths:

1. `Projects/AGENTS.md`
   - workspace bootstrap and workspace-only preferences
2. `Projects/astro-agents`
   - shared prompt assets and prompt-group routing rules
3. `Projects/<repo>/AGENTS.md`
   - repo-specific architecture, workflow, testing, and review guidance
4. `Projects/<repo>/<subtree>/AGENTS.md`
   - narrower local overrides for document type, notation, data, or workflow

## Structure

- `AGENTS.md`
  - top-level intent router for this library
- `docs/`
  - deeper hierarchy and repo-authoring guidance
- `authoring/`
  - shared authoring guides, organized into `authoring/agents/` for AI-facing prompt-writing guides, `authoring/writing/` for human-facing writing guides, and `authoring/code/` as a routed subgroup for source-code authoring guides
- `validation/`
  - shared validation prompts plus subgroup routing and authoring docs
- `agents/`
  - repo-local validation prompts layered on top of the shared validation library

## Core Principles

- Keep `AGENTS.md` files short, operational, and scoped to the directories where they apply.
- Let more specific nested `AGENTS.md` files override broader ones.
- Treat `AGENTS.md` as a map or working brief, not the full knowledge base.
- Route by intent first, then descend only as needed.
- Let repo or subtree `AGENTS.md` files activate shared prompt assets locally instead of restating them.
- Keep repo files public-safe when they may later become public.

## Detailed Guidance

Use the deeper docs for the parts of the system that should not live in the entrypoint README:

- `docs/architecture.md`
  - hierarchy model, rationale, precedence, layer ownership, validation, maintenance, and references
- `docs/usage.md`
  - minimum supporting docs, section defaults, repo `AGENTS.md` template, and public-safe patterns
- `docs/glossary.md`
  - shared terminology for prompt-system concepts such as surfaces, routers, prompt assets, scope drift, and smells
- `docs/testing.md`
  - the validation contract for this repo: requirements, canonical checks, and completion expectations
- `docs/validation.md`
  - reusable bootstrap prompts for invoking that validation contract in fresh threads
- `authoring/README.md`
  - authoring-guide design, including the special note on `authoring/agents/agents-md.md`
- `validation/README.md`
  - explanatory subgroup guidance for the shared validation prompt family

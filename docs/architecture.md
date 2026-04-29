# Architecture

This document is the human-facing source of truth for the `astro-agents` route
structure and scope model. Use it when designing or revising the library's own
structure, or when reasoning about how the library fits into a broader shared
working environment.

Use `docs/glossary.md` for shared local vocabulary such as `agent surface`, `documentation surface`, `documentation surface profile`, and `source of truth`. Use `docs/runtime-model.md` for runtime and control-flow terms such as `route`, `handoff`, `dispatcher`, `selector`, `orchestrator`, `prompt`, `instructions`, and `context`.

Use `docs/usage.md` when applying this library in another project or workspace.

## Architecture Model

The prompt library holds reusable prompts, guides, routing and workflow
conventions, source-of-truth design docs, research-log guidance, and shared
downstream recommendation docs that should not be duplicated across
projects. These include authoring guides, review files, upgrade design
guidance, research-log prompt surfaces, prompt-family routing conventions, and
`guidance/` reference docs that downstream projects may cite directly.

The routed prompt surface is built around three recurring roles:

- `AGENTS.md` files direct routing and workflow by routing into prompt families, choosing narrower prompts when needed, and pointing to the next prompt or source-of-truth document
- `README.md` files explain folder purpose, supporting guidance, and rationale
- prompt files carry the substantive reusable behavior

That `AGENTS.md`/`README.md` split repeats at narrower scopes in folders such
as `authoring/`, `authoring/code/`, `validation/`, and `research-log/`; in this
project, it is a local way of applying the broader recommendation to keep routing
and bounded choice brief and explanation in supporting docs.

Not every reusable document in this project belongs to that routed prompt
surface. The `guidance/` family holds shared recommendation docs for downstream
projects. Those docs are intentionally non-routed and are cited directly by
downstream projects when useful.

## AGENTS.md As Map, Docs As Source Of Truth

`AGENTS.md` should be a quick operational map to the right constraints, routes, and source-of-truth docs.

Prefer progressive disclosure: start with short operational guidance and point to deeper source-of-truth documents only when more detail is needed.

In practice:

- keep `AGENTS.md` focused on boundaries, commands, constraints, priorities, and routing-and-workflow instructions
- keep project overview and starting document guidance in `README.md`
- keep explanatory architecture material in architecture docs
- keep stable verification procedures in testing docs
- keep reusable validation starter requests in validation docs
- keep long plans, migration notes, and design rationale in dedicated docs
- point from `AGENTS.md` to those deeper documents when recurring detail matters

The goal is to make the right information easy to find and hard to misapply.

## Guidance As Downstream Reference

The `guidance/` family is intentionally outside the routed prompt surface.

It holds shared recommendation docs that downstream projects may reference
directly from local `AGENTS.md` files or local source-of-truth docs when they
choose to adopt a shared recommendation.

Within this project:

- `guidance/` docs are `astro-agents`-aware rather than generic detached
  templates
- this project's own `AGENTS.md` files should not route into `guidance/`
- project-local facts for a downstream project should remain in that downstream
  project's own source-of-truth docs

## Library Structure

At the project root, the architecture separates source-of-truth docs in `docs/`,
shared downstream recommendation docs in `guidance/`, shared prompt families
in `authoring/`, `validation/`, and `research-log/`, and project-local prompts in
`agents/`, with `README.md` and `AGENTS.md` providing the starting documents
and top-level routing and workflow guidance.

Within that split:

- `docs/` holds the stable source-of-truth documents that explain how the library is structured and used
- `guidance/` holds shared human-and-agent recommendation docs for downstream projects and is intentionally outside the routed prompt surface
- `authoring/`, `validation/`, and `research-log/` hold the reusable shared prompt families that the library is organized around
- `research-log/` holds reusable research-log and theme-document guidance for source-plus-summary research records
- `docs/upgrade-design.md` holds the shared human-facing design for review-led project upgrades
- folder-level `AGENTS.md` and `README.md` files in those areas repeat the same routing-and-guidance pattern at a narrower scope
- `agents/` holds project-local prompts that apply specifically to `astro-agents`

## Starting Documents

- `README.md`
  - the main human starting document for library overview and navigation
- `AGENTS.md`
  - the main agent starting document for prompt-family dispatch and bounded choice
- `docs/usage.md`
  - the main companion document for applying this library in other projects and workspaces
- `guidance/README.md`
  - the main human-and-agent entrypoint for shared downstream recommendation docs
- `validation/README.md`
  - the human-facing guide to the shared validation library, including reusable starter requests
- `research-log/README.md`
  - the human-facing guide to reusable research-log and theme-document guidance
- `docs/upgrade-design.md`
  - the human-facing source of truth for the shared upgrade model
- `authoring/`, `validation/`, and `research-log/`
  - the main shared prompt families in the library

## Bootstrap Model

At user-global or project entry, a root `AGENTS.md` file should act as a bootstrap file: it establishes the immediate scope, points to the next starting document or prompt family, and surfaces any local constraints that must be known before the next route is chosen.

Within that model:

- `$CODEX_HOME/AGENTS.md` is the canonical global bootstrap file when a user wants `astro-agents` available by default across projects
- a project root `AGENTS.md` is the bootstrap file for project-specific adoption or for project-specific exceptions to a global default
- bootstrap files should stay brief and focus on immediate scope, the next route, and any local constraints needed for that route

## Workspace Context

When the library is used beyond a single project, the practical bootstrap model is intentionally simple:

- global bootstrap belongs in `$CODEX_HOME/AGENTS.md`
- project-specific bootstrap belongs in the project root `AGENTS.md`
- project-local prompts under `agents/` provide the project-local prompt structure inside a project

This model depends on the participating bootstrap files actually performing the intended routing and bounded choice. Use `docs/usage.md` for the recommended minimum bootstrap prompts that make that model hold together.

These user-global and project-local bootstrap layers are integration context around `astro-agents`, not part of the project's own top-level structure. In that broader model, the prompt system stays discoverable while still allowing a bootstrap file to direct the agent straight into a specific shared prompt when that is the stable local default.

## Path Convention

In project-facing docs and prompt files in this project, prefer project-root-relative paths for internal file references.

Within this project:

- use forms such as `docs/architecture.md`, `authoring/agents/agents-md.md`, and `validation/review/full-agent-surface-review.md`

In the agent-facing files of other projects, when referring to reusable material
from this library:

- prefer generic routing wording over hardcoded workspace paths, such as `For docs review, use the shared documentation review.`, which is intended to route into `astro-agents/validation/review/documentation-review.md` when the recommended shared prompts are present
- use explicit `astro-agents/...` references when the local setup intentionally depends on this project as a named shared prompt library, including direct references to non-routed docs under `astro-agents/guidance/`

## Prompt-Writing Guidance For Shared Context

The prompt library is shared and reusable. It does not replace project-level or subtree-level `AGENTS.md` files.

This section explains how to write prompt files in this library so shared guidance stays legible when multiple prompt files are present and local routing remains clear.

When writing shared prompt files:

- prefer direct routing and specific prompt references
- make local exceptions and local follow-up prompts explicit
- name the specific local prompt or follow-up prompt directly when local guidance is needed
- state any real local exception directly when it matters

## Scope Ownership

This section is about placement in the broader prompt system: it answers where a rule should live.

Use each scope in the broader prompt system for a distinct kind of instruction:

- `$CODEX_HOME/AGENTS.md`
  - global bootstrap that can direct agents into shared guidance across projects
- `<global>/agents`
  - optional global prompt assets that should be used only through explicit routing rather than through an `agents/AGENTS.md` routing layer
- `<astro-agents-path>`
  - the shared prompt library project, typically used as reusable infrastructure by other projects rather than modified during ordinary project work
- `<astro-agents-path>/guidance`
  - shared human-and-agent recommendation docs for downstream projects; non-routed, `astro-agents`-aware, and directly referenceable from downstream project surfaces
- `<project>/AGENTS.md`
  - project-specific architecture, contract boundaries, workflow commands, testing expectations, deployment or environment rules, and review priorities
- `<project>/agents`
  - project-local reusable prompts that are too specific for the shared library and should be invoked through explicit project-local routing
- `<project>/<subtree>/AGENTS.md`
  - narrow local routing or bounded-choice behavior, or local constraints tied to a subtree's document type, notation, data, tooling, or workflow

When deciding where a rule belongs:

- if it is truly reusable across users, projects, and tasks without depending on one project's internal design, it is a good candidate for inclusion in the `astro-agents` prompt library
- if it is global bootstrap guidance that should direct work across projects, keep it in `$CODEX_HOME/AGENTS.md`
- if it is a global reusable prompt or user preference that should be used across projects through explicit routing, keep it in `<global>/agents`
- if it is reusable downstream guidance that projects may cite directly but it should not be part of the routed prompt surface or this project's own source-of-truth docs, keep it in `guidance/`
- if it is reusable within one project but too local for the shared library, keep it in that project's `agents/` and route to it explicitly from project-local guidance when needed
- if it depends on a project's architecture, API, testing strategy, deployment path, or domain contracts, keep it in that project's source-of-truth docs or root `AGENTS.md`
- if it matters only inside one subtree, keep it in that subtree's `AGENTS.md` or source-of-truth docs

## Validation

- Use review files as the primary way to review the route structure, prompt-library design, and project/subtree `AGENTS.md` files.
- Prefer review files that check scope ownership, routing and workflow clarity, duplication, portability when a project may later become public, and source-of-truth usage across the route structure.
- Use `validation/README.md` for the human-facing validation model and reusable starter requests such as `Do a full agent surface review.`, and `validation/review/full-agent-surface-review.md` for the combined review.

## Maintenance Expectations

Treat shared prompts, routing-and-workflow files, project `AGENTS.md` files, and supporting docs as maintained operational infrastructure. Keep them current as the project evolves. When their meaning changes, update the related routing-and-workflow and source-of-truth docs together.

- update `AGENTS.md` when working constraints, priorities, or routing-and-workflow assumptions change
- update architecture docs when boundaries, ownership, or extension points change
- update testing docs when canonical commands or verification expectations change
- remove or revise stale instructions instead of layering new guidance on top of them

Once agents begin to rely on these documents, stale guidance is often worse than missing guidance.

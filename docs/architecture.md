# Architecture

This document is the human-facing source of truth for the `astro-agents` hierarchy model. Use it when designing or revising the library's own structure, or when reasoning about how the library fits into a broader workspace routing model.

Use `docs/glossary.md` for the shared vocabulary behind recurring prompt-system terms such as `project surface`, `agent surface`, `documentation surface`, `router`, `prompt`, `activation`, `composition`, and `precedence`.

Use `docs/usage.md` when applying this library in another repo or workspace.

## Architecture Model

The prompt library holds reusable prompts, guides, and routing rules that should not be duplicated across repositories. These include authoring prompts, validation prompts, upgrade prompts, and routing rules for prompt folders.

The hierarchy is built around three recurring roles:

- `AGENTS.md` files route, scope, and activate applicable instructions
- `README.md` files explain folder purpose, supporting guidance, and rationale
- prompt files carry the substantive reusable behavior

That `AGENTS.md`/`README.md` split repeats at narrower scopes in folders such as `authoring/`, `authoring/code/`, `validation/`, and `upgrade/`; in this repo, it is a local way of applying the broader recommendation to keep routing brief and explanation in supporting docs.

## AGENTS.md As Map, Docs As Source Of Truth

`AGENTS.md` should help an agent find the right constraints quickly. It should not try to become the full knowledge base for the repo.

Prefer progressive disclosure: start with short operational guidance and point to deeper source-of-truth documents only when more detail is needed.

In practice:

- keep `AGENTS.md` focused on boundaries, commands, constraints, priorities, and routing
- keep repo overview and entrypoint guidance in `README.md`
- keep explanatory architecture material in architecture docs
- keep stable verification procedures in testing docs
- keep reusable validation bootstrap prompts in validation docs
- keep long plans, migration notes, and design rationale in dedicated docs
- point from `AGENTS.md` to those deeper documents when recurring detail matters

The goal is not to put everything in `AGENTS.md`. The goal is to make the right information easy to find and hard to misapply.

## Library Structure

At the repo root, the architecture separates source-of-truth docs in `docs/`, shared prompt families in `authoring/`, `validation/`, and `upgrade/`, and repo-local prompts in `agents/`, with `README.md` and `AGENTS.md` providing the entrypoint and top-level routing.

Within that split:

- `docs/` holds the stable source-of-truth documents that explain how the library is structured and used
- `authoring/`, `validation/`, and `upgrade/` hold the reusable shared prompt families that the library is organized around
- folder-level `AGENTS.md` and `README.md` files in those areas repeat the same router-plus-guidance pattern at a narrower scope
- `agents/` holds repo-local prompts that apply specifically to `astro-agents`

## Entrypoints

- `README.md`
  - the main human entrypoint for library overview and navigation
- `AGENTS.md`
  - the main agent routing entrypoint
- `docs/usage.md`
  - the main companion doc for applying this library in other repos and workspaces
- `validation/README.md`
  - the human-facing guide to the shared validation prompt families, including reusable bootstrap prompts
- `authoring/`, `validation/`, and `upgrade/`
  - the three main shared prompt families in the library

## Workspace Context

When the library is used within a broader workspace, the routing model commonly spans four levels of selection:

- workspace bootstrap in `Projects/AGENTS.md`
- workspace-wide reusable prompts, preferences, or defaults in `Projects/agents/AGENTS.md` when present
- top-level intent routing in `Projects/astro-agents/AGENTS.md`
- narrower local prompts in repo or subtree `AGENTS.md` files, where broader and local prompts may both remain active and precedence resolves any conflicting instructions

In these examples, `Projects/` is only an illustrative workspace root. The same model can apply under a different top-level workspace path.

This model depends on the participating `AGENTS.md` files actually performing the intended routing. Use `docs/usage.md` for the recommended minimum `AGENTS.md` prompts that make that routing model hold together.

These workspace layers are integration context around `astro-agents`, not part of the repo's own top-level structure. In that broader model, the prompt system stays discoverable from the top level while still allowing local `AGENTS.md` files to direct the agent straight into a specific shared prompt when that is the stable local default.

## Path Convention

In repo-facing docs and prompt files in this repo, prefer repo-root-relative paths for internal file references.

Within this repo:

- use forms such as `docs/architecture.md`, `authoring/agents/agents-md.md`, and `validation/review/full-agent-surface-review.md`
- do not use file-location-relative forms such as `./...`, `../...`, or `../../...` for internal references
- keep conceptual hierarchy examples such as `Projects/<repo>/AGENTS.md` as conceptual examples rather than local file references

In the agent-facing files of other repos, when referring to prompts from this library:

- prefer generic activation wording over hardcoded workspace paths, such as `For docs review, use the shared documentation review prompt.`, which is intended to activate `astro-agents/validation/review/documentation-review.md` when the recommended routing prompts are present
- use explicit `astro-agents/...` references only when the local setup intentionally depends on this repo as a named shared prompt library rather than on a portable activation pattern

## Precedence

The prompt library is shared and reusable. It does not replace repo-level or subtree-level `AGENTS.md` files.

This section is about which applicable prompt wins, not where a rule should live.

Applicable prompts compose by default. When more than one prompt applies in the same context, keep compatible guidance from all of them active together.

Use precedence when applicable instructions conflict in the same context: precedence determines which instruction governs.

Precedence resolves conflicts between active instructions. It is not automatic whole-file replacement.

When all applicable points in the routing chain are present, read precedence from highest to lowest as:

1. applicable repo or subtree prompts in the target repo
2. matching prompts under the repo's `agents/` folder when present
3. matching prompts under the workspace `Projects/agents/` folder when present
4. matching shared prompts in `astro-agents/`

This chain determines which conflicting instruction governs when prompts at different levels both apply. If two applicable prompts exist at the same subtree-level, the narrower conflicting guidance should win.

## Layer Ownership

Here, a layer means one position in the broader prompt hierarchy where prompts or instructions can be introduced and take effect. This section is about placement: it answers where a rule should live, not which applicable prompt wins.

As above, the examples below use `Projects/` as an illustrative workspace root. The same ownership model can apply under a different top-level workspace path.

Use each layer in the broader prompt system for a distinct kind of instruction:

- `Projects/AGENTS.md`
  - workspace bootstrap routing that directs agents into workspace-wide prompts or the shared prompt library
- `Projects/agents`
  - workspace-global reusable prompts and user preferences or defaults that should apply across multiple repos in one workspace
- `Projects/astro-agents`
  - the shared prompt library repo, typically used as reusable infrastructure by other repos rather than modified during ordinary repo work
- `Projects/<repo>/AGENTS.md`
  - repo-specific architecture, contract boundaries, workflow commands, testing expectations, deployment or environment rules, and review priorities
- `Projects/<repo>/agents`
  - repo-local prompts that are too specific for the shared library but reusable across multiple tasks in one repo, including prompts that add compatible local guidance and prompts whose conflicting instructions should take precedence locally
- `Projects/<repo>/<subtree>/AGENTS.md`
  - narrow local routing or local constraints tied to a subtree's document type, notation, data, tooling, or workflow

When deciding where a rule belongs:

- if it is truly reusable across users, repos, and tasks without depending on one repo's internal design, it is a good candidate for inclusion in the `astro-agents` prompt library
- if it is bootstrap routing that should direct work across the workspace, keep it in `Projects/AGENTS.md`
- if it is a workspace-wide user preference, reusable default, or reusable prompt that should apply across repos in one workspace, keep it in `Projects/agents`
- if it is reusable within one repo but too local for the shared library, keep it in that repo's `agents/`
- if it depends on a repo's architecture, API, testing strategy, deployment path, or domain contracts, keep it in that repo's source-of-truth docs or root `AGENTS.md`
- if it matters only inside one subtree, keep it in that subtree's `AGENTS.md` or source-of-truth docs

## Validation

- Use validation prompts as the primary way to review the prompt hierarchy, prompt-library design, and repo/subtree `AGENTS.md` files.
- Prefer validation prompts that review layer ownership, routing clarity, duplication, portability when a repo may later become public, and source-of-truth usage across the hierarchy.
- Use `validation/README.md` for the human-facing validation model and reusable bootstrap prompts such as `Do a full agent surface review.`, and `validation/review/full-agent-surface-review.md` for the combined review prompt.

## Maintenance Expectations

Treat shared prompts, routing files, repo `AGENTS.md` files, and supporting docs as maintained operational infrastructure. Keep them current as the repo evolves. When their meaning changes, update the related routing and source-of-truth docs together.

- update `AGENTS.md` when working constraints, priorities, or routing assumptions change
- update architecture docs when boundaries, ownership, or extension points change
- update testing docs when canonical commands or verification expectations change
- remove or revise stale instructions instead of layering new guidance on top of them

Once agents begin to rely on these documents, stale guidance is often worse than missing guidance.

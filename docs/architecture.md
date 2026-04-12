# Architecture

This document is the human-facing source of truth for the `astro-agents` route structure and scope model. Use it when designing or revising the library's own structure, or when reasoning about how the library fits into a broader workspace routing and workflow model.

Use `docs/glossary.md` for shared local vocabulary such as `agent surface`, `documentation surface`, `documentation surface profile`, and `source of truth`. Use `docs/runtime-model.md` for runtime and control-flow terms such as `route`, `handoff`, `dispatcher`, `selector`, `orchestrator`, `prompt`, `instructions`, `context`, and `authority`.

Use `docs/usage.md` when applying this library in another repo or workspace.

## Architecture Model

The prompt library holds reusable prompts, guides, routing and workflow conventions, and source-of-truth design docs that should not be duplicated across repositories. These include authoring guides, review files, upgrade design guidance, and prompt-family routing conventions.

The route structure is built around three recurring roles:

- `AGENTS.md` files direct routing and workflow by routing into prompt families, choosing narrower prompts when needed, and identifying applicable instructions
- `README.md` files explain folder purpose, supporting guidance, and rationale
- prompt files carry the substantive reusable behavior

That `AGENTS.md`/`README.md` split repeats at narrower scopes in folders such as `authoring/`, `authoring/code/`, and `validation/`; in this repo, it is a local way of applying the broader recommendation to keep routing and bounded choice brief and explanation in supporting docs.

## AGENTS.md As Map, Docs As Source Of Truth

`AGENTS.md` should help an agent find the right constraints quickly. It should not try to become the full knowledge base for the repo.

Prefer progressive disclosure: start with short operational guidance and point to deeper source-of-truth documents only when more detail is needed.

When `AGENTS.md` points to a deeper source-of-truth document, treat that document as supporting `Context` by default. Treat it as active `Instructions` only when higher-authority instructions explicitly delegate narrower authority to it.

In practice:

- keep `AGENTS.md` focused on boundaries, commands, constraints, priorities, and routing-and-workflow instructions
- keep repo overview and starting document guidance in `README.md`
- keep explanatory architecture material in architecture docs
- keep stable verification procedures in testing docs
- keep reusable validation starter requests in validation docs
- keep long plans, migration notes, and design rationale in dedicated docs
- point from `AGENTS.md` to those deeper documents when recurring detail matters

The goal is not to put everything in `AGENTS.md`. The goal is to make the right information easy to find and hard to misapply.

## Library Structure

At the repo root, the architecture separates source-of-truth docs in `docs/`, shared prompt families in `authoring/` and `validation/`, and repo-local prompts in `agents/`, with `README.md` and `AGENTS.md` providing the starting documents and top-level routing and workflow guidance.

Within that split:

- `docs/` holds the stable source-of-truth documents that explain how the library is structured and used
- `authoring/` and `validation/` hold the reusable shared prompt families that the library is organized around
- `docs/upgrade-design.md` holds the shared human-facing design for review-led repo upgrades
- folder-level `AGENTS.md` and `README.md` files in those areas repeat the same routing-and-guidance pattern at a narrower scope
- `agents/` holds repo-local prompts that apply specifically to `astro-agents`

## Starting Documents

- `README.md`
  - the main human starting document for library overview and navigation
- `AGENTS.md`
  - the main agent starting document for prompt-family dispatch and bounded choice
- `docs/usage.md`
  - the main companion document for applying this library in other repos and workspaces
- `validation/README.md`
  - the human-facing guide to the shared validation library, including reusable starter requests
- `docs/upgrade-design.md`
  - the human-facing source of truth for the shared upgrade model
- `authoring/` and `validation/`
  - the two main shared prompt families in the library

## Workspace Context

When the library is used within a broader workspace, the routing and workflow model commonly spans four levels of dispatch and bounded choice:

- workspace root routing in `Projects/AGENTS.md`
- workspace-wide reusable prompts, preferences, or defaults in `Projects/agents/AGENTS.md` when present
- top-level intent dispatch in `Projects/astro-agents/AGENTS.md`
- narrower local prompts in repo or subtree `AGENTS.md` files, where broader and local prompts may both remain applicable and higher-authority instructions settle any conflicting instructions

In these examples, `Projects/` is only an illustrative workspace root. The same model can apply under a different top-level workspace path.

This model depends on the participating `AGENTS.md` files actually performing the intended routing and bounded choice. Use `docs/usage.md` for the recommended minimum `AGENTS.md` prompts that make that model hold together.

These workspace layers are integration context around `astro-agents`, not part of the repo's own top-level structure. In that broader model, the prompt system stays discoverable from the top level while still allowing local `AGENTS.md` files to direct the agent straight into a specific shared prompt when that is the stable local default.

## Path Convention

In repo-facing docs and prompt files in this repo, prefer repo-root-relative paths for internal file references.

Within this repo:

- use forms such as `docs/architecture.md`, `authoring/agents/agents-md.md`, and `validation/review/full-agent-surface-review.md`
- do not use file-location-relative forms such as `./...`, `../...`, or `../../...` for internal references
- keep conceptual route-structure examples such as `Projects/<repo>/AGENTS.md` as conceptual examples rather than local file references

In the agent-facing files of other repos, when referring to prompts from this library:

- prefer generic routing wording over hardcoded workspace paths, such as `For docs review, use the shared documentation review.`, which is intended to route into `astro-agents/validation/review/documentation-review.md` when the recommended shared prompts are present
- use explicit `astro-agents/...` references only when the local setup intentionally depends on this repo as a named shared prompt library rather than on a portable routing pattern

## Instruction Authority And Conflict Handling

The prompt library is shared and reusable. It does not replace repo-level or subtree-level `AGENTS.md` files.

This section is about which applicable instruction has higher authority, not where a rule should live.

Applicable prompts compose by default. When more than one prompt applies in the same context, keep compatible guidance from all of them active together.

Use instruction authority when applicable instructions conflict in the same context: authority determines which instruction applies.

Instruction authority resolves conflicts between active instructions. It is not automatic whole-file replacement.

When all applicable points in the prompt chain are present, read instruction authority from highest to lowest as:

1. applicable repo or subtree prompts in the target repo
2. matching prompts under the repo's `agents/` folder when present
3. matching prompts under the workspace `Projects/agents/` folder when present
4. matching shared prompts in `astro-agents/`

This chain determines which conflicting instruction applies when prompts at different levels both apply. If two applicable prompts exist at the same subtree level, the narrower conflicting guidance should win.

## Scope Ownership

This section is about placement in the broader prompt system: it answers where a rule should live, not which applicable instruction has higher authority.

As above, the examples below use `Projects/` as an illustrative workspace root. The same ownership model can apply under a different top-level workspace path.

Use each scope in the broader prompt system for a distinct kind of instruction:

- `Projects/AGENTS.md`
  - workspace root routing that directs agents into workspace-wide prompts or the shared prompt library
- `Projects/agents`
  - workspace-global reusable prompts and user preferences or defaults that should apply across multiple repos in one workspace
- `Projects/astro-agents`
  - the shared prompt library repo, typically used as reusable infrastructure by other repos rather than modified during ordinary repo work
- `Projects/<repo>/AGENTS.md`
  - repo-specific architecture, contract boundaries, workflow commands, testing expectations, deployment or environment rules, and review priorities
- `Projects/<repo>/agents`
  - repo-local prompts that are too specific for the shared library but reusable across multiple tasks in one repo, including prompts that add compatible local guidance and prompts whose conflicting instructions should apply locally
- `Projects/<repo>/<subtree>/AGENTS.md`
  - narrow local routing or bounded-choice behavior, or local constraints tied to a subtree's document type, notation, data, tooling, or workflow

When deciding where a rule belongs:

- if it is truly reusable across users, repos, and tasks without depending on one repo's internal design, it is a good candidate for inclusion in the `astro-agents` prompt library
- if it is workspace-root routing that should direct work across the workspace, keep it in `Projects/AGENTS.md`
- if it is a workspace-wide user preference, reusable default, or reusable prompt that should apply across repos in one workspace, keep it in `Projects/agents`
- if it is reusable within one repo but too local for the shared library, keep it in that repo's `agents/`
- if it depends on a repo's architecture, API, testing strategy, deployment path, or domain contracts, keep it in that repo's source-of-truth docs or root `AGENTS.md`
- if it matters only inside one subtree, keep it in that subtree's `AGENTS.md` or source-of-truth docs

## Validation

- Use review files as the primary way to review the route structure, prompt-library design, and repo/subtree `AGENTS.md` files.
- Prefer review files that check scope ownership, routing and workflow clarity, duplication, portability when a repo may later become public, and source-of-truth usage across the route structure.
- Use `validation/README.md` for the human-facing validation model and reusable starter requests such as `Do a full agent surface review.`, and `validation/review/full-agent-surface-review.md` for the combined review.

## Maintenance Expectations

Treat shared prompts, routing-and-workflow files, repo `AGENTS.md` files, and supporting docs as maintained operational infrastructure. Keep them current as the repo evolves. When their meaning changes, update the related routing-and-workflow and source-of-truth docs together.

- update `AGENTS.md` when working constraints, priorities, or routing-and-workflow assumptions change
- update architecture docs when boundaries, ownership, or extension points change
- update testing docs when canonical commands or verification expectations change
- remove or revise stale instructions instead of layering new guidance on top of them

Once agents begin to rely on these documents, stale guidance is often worse than missing guidance.

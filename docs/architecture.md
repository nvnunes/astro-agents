# Architecture

This document is the human-facing source of truth for the prompt hierarchy used by `astro-agents`. Use it when designing or revising the hierarchy itself.

Use `docs/glossary.md` for the shared vocabulary behind recurring prompt-system terms such as `surface`, `router`, `prompt asset`, `scope drift`, and `smell`.

## Architecture Model

The prompt library holds reusable guides that should not be duplicated across repositories. These include authoring guides, validation prompts, and prompt-group routing rules.

The hierarchy is built around paired roles:

- `AGENTS.md` files route, scope, and activate applicable instructions
- `README.md` files explain architecture, authoring rules, and rationale
- prompt assets carry substantive reusable behavior

Subgroup pairs such as `authoring/AGENTS.md` plus `authoring/README.md`, `authoring/code/AGENTS.md` plus `authoring/code/README.md`, and `validation/AGENTS.md` plus `validation/README.md` repeat the same pattern at a narrower scope. This pairing is a local design choice meant to operationalize the external recommendations rather than compete with them. [2][3]

## AGENTS.md As Map, Docs As Source Of Truth

`AGENTS.md` should help an agent find the right constraints quickly. It should not try to become the full knowledge base for the repo. [2][3]

Prefer progressive disclosure: start with short operational guidance and point to deeper source-of-truth documents only when more detail is needed. [3]

In practice:

- keep `AGENTS.md` focused on boundaries, commands, constraints, priorities, and routing
- keep explanatory architecture material in architecture docs
- keep stable verification procedures in testing docs
- keep reusable validation bootstrap prompts in validation docs
- keep long plans, migration notes, and design rationale in dedicated docs
- point from `AGENTS.md` to those deeper documents when recurring detail matters

The goal is not to put everything in `AGENTS.md`. The goal is to make the right information easy to find and hard to misapply.

## Structure

- `AGENTS.md`
  - top-level routing rules for prompt groups in this directory
- `authoring/`
  - authoring guides plus agent-facing routing in `authoring/AGENTS.md`, human-facing authoring guidance in `authoring/README.md`, and three internal guide families:
    - `authoring/agents/` for AI-facing prompt-writing guides
    - `authoring/prose/` for human-facing prose guides
    - `authoring/code/` as a routed subgroup for source-code authoring guides, with its own `AGENTS.md` and `README.md`
- `validation/`
  - validation prompts plus agent-facing routing in `validation/AGENTS.md` and human-facing authoring guidance in `validation/README.md`

This structure deliberately separates:

- operational routing in `AGENTS.md` files
- human-facing explanation and authoring guidance in `README.md` files
- reusable prompt assets in the prompt-group directories

It also separates three levels of selection:

- workspace bootstrap in `Projects/AGENTS.md`
- top-level intent routing in `astro-agents/AGENTS.md`
- narrower local activation in repo or subtree `AGENTS.md` files when a specific shared prompt asset should govern consistently in that context

This keeps the prompt system discoverable in an ad hoc way from the top level while still allowing local `AGENTS.md` files to direct the agent straight into a specific shared prompt asset when that is the stable local default.

## Path Convention

Inside this repo, prefer repo-root-relative paths for internal file references.

In practice:

- use forms such as `docs/architecture.md`, `authoring/agents/agents-md.md`, and `validation/review/full-agent-surface-review.md`
- do not use file-location-relative forms such as `./...`, `../...`, or `../../...` for internal references
- keep conceptual hierarchy examples such as `Projects/<repo>/AGENTS.md` as conceptual examples rather than local file references

## Precedence

The prompt library is shared and reusable. It does not replace repo-level or subtree-level `AGENTS.md` files.

In practice:

- use the prompt library for shared defaults and reusable prompt assets
- use `Projects/AGENTS.md` for workspace-specific instructions
- use repo `AGENTS.md` files for project-specific rules
- use deeper subtree `AGENTS.md` files for narrower local overrides

More specific instructions should override broader ones.

## Layer Ownership

Use each layer for a distinct kind of instruction:

- `Projects/AGENTS.md`
  - workspace-specific preferences, private environment details, and the bootstrap rule that points to this prompt library when available
- `Projects/astro-agents`
  - reusable prompt assets, routing rules, and shared defaults worth applying across multiple repos
- `Projects/<repo>/AGENTS.md`
  - repo-specific architecture, contract boundaries, workflow commands, testing expectations, deployment or environment rules, and review priorities
- `Projects/<repo>/<subtree>/AGENTS.md`
  - narrow local overrides tied to a subtree's document type, notation, data, tooling, or workflow

When deciding where a rule belongs:

- if it depends on the private workspace layout or local environment, keep it at the workspace layer
- if it is reusable across multiple repos without knowing the repo's internal design, keep it in the prompt library
- if it depends on a repo's architecture, API, testing strategy, deployment path, or domain contracts, keep it in that repo
- if it matters only inside one subtree, keep it in that subtree

In practice, this means:

- `Projects/AGENTS.md` should bootstrap into the shared prompt router and keep only workspace-specific preferences
- the shared prompt library should define prompt areas and reusable prompt assets
- the top-level prompt `AGENTS.md` should route by intent, not by full hierarchy traversal
- repo or subtree `AGENTS.md` files may explicitly activate a specific shared prompt asset when that asset should govern consistently in the local context
- local files should point downward to the applicable shared asset, not restate that asset's substantive instructions

## Prompt Assets As Operational Artifacts

Treat shared prompt assets, routing files, and repo `AGENTS.md` files as operational infrastructure rather than passive notes. [3]

- review them when working patterns, repo boundaries, verification commands, or prompt-routing assumptions change
- revise them deliberately when repeated ambiguity, failure, or duplication appears
- prefer small explicit updates over silent drift
- update related routing or supporting docs in the same change when a prompt asset changes meaning substantially

## Validation

- Use agentic validation as the primary mechanism for reviewing the prompt hierarchy, prompt-library design, and repo/subtree `AGENTS.md` files.
- Prefer validation prompts that review layer ownership, routing clarity, duplication, public-safe portability, and source-of-truth usage across the hierarchy.
- Use `validation/README.md` for the human-facing validation model and `validation/review/full-agent-surface-review.md` for the composite reusable validation prompt.
- Use narrower deterministic checks only if a recurring structural failure later justifies them.

## Maintenance Expectations

Prompt assets and support documents are operational infrastructure. Keep them current as the repo evolves.

- update `AGENTS.md` when working constraints, priorities, or routing assumptions change
- update architecture docs when boundaries, ownership, or extension points change
- update testing docs when canonical commands or verification expectations change
- remove or revise stale instructions instead of layering new guidance on top of them

Once agents begin to rely on these documents, stale guidance is often worse than missing guidance.

## References

1. [OpenAI, Introducing Codex](https://openai.com/index/introducing-codex/)
2. [agents.md, How to use AGENTS.md](https://agents.md/)
3. [OpenAI, Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
4. [GitLab Docs, Documentation AGENTS.md](https://docs.gitlab.com/development/documentation/agents_md/)

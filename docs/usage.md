# Usage

This document is the human-facing source of truth for how projects and workspaces
should apply `astro-agents`.

Use it when adopting `astro-agents`, choosing bootstrap location, declaring a
documentation surface profile, and wiring shared validation.

Use `docs/glossary.md` when usage guidance depends on shared prompt-system
terms that need one stable meaning across projects.

`astro-agents` is a checked-out prompt library, not a package install. The
concrete documented adoption path in this project is Codex plus `AGENTS.md`.
Some project-structure guidance may still transfer to other runtimes, but those
setups may require local adaptation.

## Before You Start

Before adopting `astro-agents` in another project:

- make the `astro-agents` project available at a stable path in the workspace
  where you use your agent tooling
- decide whether the shared bootstrap should be project-local or user-global
- keep project-specific commands, package boundaries, persisted contracts,
  lifecycle rules, and local exceptions in the downstream project's own docs

In the example path references below, use `<astro-agents-path>` as a placeholder
for the stable path to your `astro-agents` checkout.

## Bootstrapping Astro-Agents

Use this section when deciding how little bootstrap a project or user setup needs in order to use `astro-agents`. The goal is to keep bootstrap minimal and keep reusable routing, terminology, and workflow context inside `astro-agents` itself.

In both cases below, the suggested bootstrap prompt should do only three things:

- route into `astro-agents`
- make it clear that `astro-agents` is the shared prompt library in use
- provide only the minimum local context needed to make that routing intelligible

Bootstrap prompts should stay limited to routing, library identification, and the minimum local context needed for that route.

### Case 1: Single-Project Use

When only one project should use `astro-agents`, put the bootstrap in that project's root `AGENTS.md`.

Use a minimal project-level bootstrap such as:

```md
## Astro-Agents Bootstrap
- Use `astro-agents` for reusable authoring, review, and routing guidance in this project.
```

This keeps the project-specific bootstrap local to the project without requiring any global Codex setup.

### Case 2: Global Use

When `astro-agents` should be the user's shared default across projects, put the bootstrap in `$CODEX_HOME/AGENTS.md`, commonly `~/.codex/AGENTS.md`.

Use a minimal global bootstrap such as:

```md
## Astro-Agents Bootstrap
- Use `astro-agents` by default for reusable authoring, review, and routing guidance across projects.
- Resolve `astro-agents/...` references to `<astro-agents-path>/...`.
```

Use an absolute path for `<astro-agents-path>` in the actual global bootstrap.

In this mode, project root `AGENTS.md` files should add only project-local guidance, source-of-truth docs, or narrower routing that the project itself needs. If one project should not use the global default, keep the global bootstrap in `$CODEX_HOME/AGENTS.md` and use the project root `AGENTS.md` to opt out or redirect for that project with a minimal project-level exception such as:

```md
## Astro-Agents Bootstrap
- Do not use the shared `astro-agents` prompt library in this project.
- Follow this project's local guidance and source-of-truth documents instead.
```

## Minimal Adoption Path

For a small initial adoption in a downstream project:

1. Add one of the bootstrap snippets above.
2. Keep the downstream project's own `README.md`, `AGENTS.md`, and source-of-truth
   docs responsible for project-specific facts.
3. Add shared guidance references only when you want those recommendations
   visible in the downstream working surface.
4. Add shared validation only when the downstream project wants the shared review
   library to be part of its normal validation path.

## Documentation Surface Profile

When a downstream project uses `Documentation surface profile`, declare it in a short `## Scope` section near the top of the root `AGENTS.md`, for example:

```md
## Scope
- Documentation surface profile: public-python.
```

## Shared Validation

When a downstream project wants to rely on shared validation from `astro-agents`, its `docs/testing.md` can be as small as:

```md
# Testing

## Shared Validation
Use the shared base testing guidance in `<astro-agents-path>/validation/base-testing.md`.

## Project-Local Verification
Add project-local verification commands and completion expectations below as needed.
```

Use `validation/README.md` for the public shared review entrypoints and starter
requests. Keep project-specific commands and completion expectations in the
downstream project's own `docs/testing.md`.

## Research Log Routing

A downstream project may add a short `## Research Logs` section to root
`AGENTS.md` when it wants natural research-log requests to resolve to known
local theme files and folders.

Keep theme names, aliases, and paths in the downstream project because they are
project-specific facts. Use `astro-agents` for the reusable research-log behavior.

For example:

```md
## Research Logs
- For capture, summary update, summary check, concept maintenance, or source-document upgrade work in `adaptive optics` (`docs/research/adaptive-optics.md`, `docs/research/adaptive-optics/`) or `telemetry` (`docs/research/telemetry.md`, `docs/research/telemetry/`), use `<astro-agents-path>/research-log/AGENTS.md`.
```

This enables natural prompts such as:

- `Let's work on adaptive optics.`
- `Capture this in telemetry.`
- `Update the adaptive optics summary.`
- `Rebuild telemetry.`

## Shared Guidance

For shared recommendation docs that downstream projects may reference directly, use:

- `guidance/agent-surface.md`
  - shared agent-surface starter, placement, and local/shared structure guidance for downstream projects
- `guidance/public-python-projects.md`
  - shared public Python project-structure, source-of-truth, and public-surface guidance
- `guidance/python-development.md`
  - shared Python architecture, coding-policy, and development-workflow guidance

These are shared recommendation docs that downstream projects may reference directly.

When a downstream project adopts one of these docs:

- reference it directly from root `AGENTS.md` when the project wants that shared recommendation visible in the operational working surface
- reference it from local source-of-truth docs such as `docs/architecture.md` or `docs/development.md` when the recommendation should be part of the project's durable local guidance
- keep exact commands, package boundaries, persisted contracts, lifecycle rules, and project-specific exceptions in the project's own docs

For example, a project root `AGENTS.md` may include:

```md
## Shared Guidance
- Use `<astro-agents-path>/guidance/agent-surface.md` for shared agent-surface guidance.
- Use `<astro-agents-path>/guidance/public-python-projects.md` for shared public Python project guidance.
- Use `<astro-agents-path>/guidance/python-development.md` for shared Python development guidance.
```

And a local source-of-truth doc such as `docs/architecture.md` may include:

```md
## Shared Guidance

This project adopts the shared guidance in:
- `<astro-agents-path>/guidance/agent-surface.md`
- `<astro-agents-path>/guidance/public-python-projects.md`
- `<astro-agents-path>/guidance/python-development.md`

Project-local commands, package boundaries, contracts, lifecycle rules, and exceptions in this project's own docs remain the source of truth.
```

## Authoring Requirements

If a downstream project wants to impose strong requirements on writing or coding style, add an explicit `## Authoring Requirements` section to the root `AGENTS.md` and point directly to the shared guides it wants to require.

This is stronger than a generic bootstrap line. Use it when the project wants agents to follow specific shared authoring guides for recurring work.

For example:

```md
## Authoring Requirements
- For Python code, follow `<astro-agents-path>/authoring/code/python.md`.
- For project documentation such as `docs/architecture.md`, `docs/testing.md`, `docs/development.md`, and similar long-lived project documents, follow `<astro-agents-path>/authoring/writing/project-docs.md`.
- For `README.md`, follow `<astro-agents-path>/authoring/writing/readme-md.md` in addition to `<astro-agents-path>/authoring/writing/project-docs.md`.
- For plan documents or phased execution docs when they are created or revised, follow `<astro-agents-path>/authoring/writing/plan.md`.
```

## Starter Requests

Use starter requests when you want a fresh thread to invoke a shared path with minimal manual prompting. They should be short but lead to the intended route within the shared routing and workflow system.

Common examples:

- `Do a full agent surface review using astro-agents`
- `Do a code quality review using astro-agents`
- `Review the project documentation using the shared documentation review using astro-agents`
- `Revise manuscript.tex using the shared science writing guide using astro-agents`

For additional examples, see:
- `validation/README.md` for the public shared review entrypoints and upgrade-specific starter requests
- `research-log/README.md` for research-log activation and operation starter requests

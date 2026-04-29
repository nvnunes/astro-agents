# astro-agents

`astro-agents` is a shared prompt and documentation library for project-level agent guidance. It collects reusable authoring prompts, validation routes, research-log guidance, downstream guidance docs, and design references for people who want a more structured agent surface across projects.

> [!NOTE]
> `astro-agents` is public and pre-1.0. The project is usable today, but its file-level contracts and structure may still change as the public surface settles.

## What It Is

- shared authoring prompts and guides in `authoring/`
- shared validation review entrypoints and workflows in `validation/`
- reusable research-log guidance in `research-log/`
- shared downstream recommendation docs in `guidance/`
- source-of-truth design docs for the library itself in `docs/`
- project-local validation files for `astro-agents` itself in `agents/`

## Who It Is For

- project owners using `AGENTS.md`-style project guidance
- people standardizing prompt, documentation, and review structure across projects
- teams experimenting with reusable project-level agent workflows

## What It Is Not

- not a package API or SDK
- not a hosted service or docs site
- not a replacement for project-local architecture, testing, or workflow docs
- not a claim that every agent runtime named in this project has equal operational support today

## Current Support

The most complete documented path today is Codex plus `AGENTS.md`. The project also tracks broader agent-runtime vocabulary and design ideas so the library can stay portable, but those broader references do not currently imply equal adoption or validation support across runtimes.

## Quickstart

1. Make a checkout of `astro-agents` available in the workspace where you want to use it.
2. Use `docs/usage.md` to choose a bootstrap path and adopt the parts of `astro-agents` that fit your project.

`docs/usage.md` owns the exact project-local and global bootstrap snippets, the minimal adoption path, and the optional shared-validation and shared-guidance layers.

## Project Layout

- `authoring/`
  - shared authoring prompts and guides
- `guidance/`
  - shared recommendation docs for downstream projects
- `validation/`
  - shared validation review entrypoints, workflows, and upgrade assessment paths
- `research-log/`
  - reusable research-log and theme-document guidance
- `docs/`
  - architecture, usage, testing, glossary, runtime, and upgrade-design docs
- `agents/`
  - project-local validation review files for `astro-agents`

`docs/future/` holds roadmap and design material for later runtime work. Keep it out of the normal onboarding path unless you are working on that future design directly.

## Start Here

- `docs/usage.md`
  - concrete adoption path for downstream projects and user-global bootstrap
- `docs/architecture.md`
  - route structure, scope ownership, validation model, and maintenance expectations for this project
- `authoring/README.md`
  - entrypoint for the shared authoring prompts and guides
- `validation/README.md`
  - shared validation library, review entrypoints, and upgrade path
- `research-log/README.md`
  - reusable research-log guidance and theme-document maintenance entrypoint
- `guidance/README.md`
  - entrypoint for the shared downstream recommendation docs
- `docs/runtime-model.md`
  - runtime vocabulary, current support boundary, and concrete Codex behavior
- `docs/testing.md`
  - validation requirements for changes inside `astro-agents`

## Project Status

- Use `CHANGELOG.md` for the public change history.
- This project is public and pre-1.0.
- The public surface is still settling.

# astro-agents

`astro-agents` is a shared library of reusable agent skills, examples, and review/planning workflows. It is for people who want a more structured agent surface across projects.

> [!NOTE]
> `astro-agents` is public and pre-1.0. The project is usable today, but its file-level contracts and structure may still change as the public surface settles.

## What It Is

- user-facing reusable capabilities in `skills/`
- downstream adoption examples in `examples/`
- source-of-truth design docs for the library itself in `docs/`

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

The most complete documented path today is Codex skill discovery plus minimal `AGENTS.md` bootstrap or project context. The project also tracks broader agent-runtime vocabulary and design ideas so the library can stay portable, but those broader references do not currently imply equal adoption or validation support across runtimes.

The current library shape is skills-first. Runtime skill discovery activates skill packages, and skill packages use `references/` and `scripts/` for progressive disclosure. `AGENTS.md` supplies project-local working context for this repository.

## Quickstart

1. Make a checkout of `astro-agents` available in the workspace where you want to use it.
2. Use `docs/usage.md` to choose a bootstrap path and adopt the parts of `astro-agents` that fit your project.

`docs/usage.md` owns the exact project-local and global bootstrap snippets, the recommended project surface, and the optional shared-validation path.

## Project Layout

- `skills/`
  - user-facing reusable capabilities packaged as `SKILL.md` plus references and scripts
- `examples/`
  - example downstream project documents
- `docs/`
  - architecture, usage, testing, glossary, runtime, and future-design docs

`docs/future/` holds roadmap and design material for later runtime work. Keep it out of the normal onboarding path unless you are working on that future design directly.

## Start Here

- `docs/usage.md`
  - concrete adoption path for downstream projects and user-global bootstrap
- `docs/architecture.md`
  - skills-first structure, scope ownership, validation model, and maintenance expectations for this project
- `skills/`
  - reusable skill packages such as `agent-surface-review`, `documentation-surface-review`, `code-quality-review`, `project-upgrade-planning`, `technical-writing`, narrower writing skills, Python code writing, and research logging
- `examples/downstream-testing.md`
  - example downstream `docs/testing.md`
- `docs/runtime-model.md`
  - runtime vocabulary, current support boundary, and concrete Codex behavior
- `docs/testing.md`
  - validation requirements for changes inside `astro-agents`

## Project Status

- Use `CHANGELOG.md` for the public change history.
- This project is public and pre-1.0.
- The public surface is still settling.

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
2. Use the [usage guide](docs/usage.md) to choose a bootstrap path and adopt the parts of `astro-agents` that fit your project.

[`docs/usage.md`](docs/usage.md) owns the exact project-local and global bootstrap snippets, the recommended project surface, and the optional shared-validation path.

## Project Layout

- `skills/`
  - user-facing reusable capabilities packaged as `SKILL.md` plus references and scripts
- `examples/`
  - example downstream project documents
- `docs/`
  - architecture, usage, testing, glossary, runtime, and future-design docs

`docs/future/` holds roadmap and design material for later runtime work. Keep it out of the normal onboarding path unless you are working on that future design directly.

## Available Skills

The current reusable skill packages are listed below. Each skill's `SKILL.md`
frontmatter is the source of truth for exact activation wording.

- `$agent-surface-review`: review agent surfaces, instruction scope, workflow behavior, documentation integration, and validation expectations.
- `$agents-md-writing`: write, revise, or review `AGENTS.md` project and subtree instruction files.
- `$code-quality-review`: review source-code quality, architecture, contracts, lifecycle clarity, tests, and maintainability.
- `$concept-writing`: write, revise, or review concept documents, foundation notes, rationale docs, and early-stage explanatory docs.
- `$documentation-surface-review`: review documentation surfaces, documentation profiles, source-of-truth docs, README scope, and documentation architecture.
- `$plan-writing`: write, revise, or review plans, implementation plans, roadmaps, migration plans, and sequencing notes.
- `$project-docs-writing`: write, revise, or review durable project documentation and source-of-truth docs.
- `$project-upgrade-planning`: assess project upgrade readiness and plan upgrade grouping, sequencing, validation, and next steps.
- `$prompt-writing`: write, revise, or review reusable agent-facing prompts and workflow instructions.
- `$pubify-authoring`: work on `pubify-pubs` and `pubify-ppt` publication and presentation workflows.
- `$python-code-writing`: write, edit, or refactor Python source code and related tests or package structure.
- `$readme-writing`: write, revise, or review `README.md` and folder-level README files.
- `$research-logging`: perform and record investigations, replace superseded
  work, update summaries, review or validate logs, and manage supporting
  evidence and references.
- `$science-writing`: write, revise, or review scientific prose, claims, evidence, methods, results, and interpretation.
- `$skill-md-writing`: write, revise, or review `SKILL.md` files and skill packages.
- `$technical-writing`: write, revise, or review general technical prose and shared writing discipline.

## Start Here

### Research logging

Research logging has three distinct surfaces with separate authority:

- [`skills/research-logging/`](skills/research-logging/) is the self-contained runtime surface containing
  the operational and authoring instructions used by agents. It is
  self-documenting and owns agent behavior.
- [`docs/research-log-mechanical-validator-spec.md`](docs/research-log-mechanical-validator-spec.md) is the normative
  implementation contract that the mechanical-validation CLI and its
  supporting tools must follow.
- [`docs/research-logging.md`](docs/research-logging.md) is human-facing researcher documentation. It
  explains how researchers use the skill, what they should expect from it, and
  which research decisions remain theirs.

Keep these roles separate. The human guide is not a specification or
completeness checklist for either the agent surface or the validation tools.
The three surfaces must remain conceptually compatible, but they are not
mirrors and should not be expected to repeat the same detail.

### Other starting points

- [`docs/usage.md`](docs/usage.md)
  - concrete adoption path for downstream projects and user-global bootstrap
- [`docs/architecture.md`](docs/architecture.md)
  - skills-first structure, scope ownership, validation model, and maintenance expectations for this project
- [`skills/`](skills/)
  - reusable skill packages listed above
- [`examples/downstream-testing.md`](examples/downstream-testing.md)
  - example downstream `docs/testing.md`
- [`docs/runtime-model.md`](docs/runtime-model.md)
  - runtime vocabulary, current support boundary, and concrete Codex behavior
- [`docs/testing.md`](docs/testing.md)
  - validation requirements for changes inside `astro-agents`

## Project Status

- Use [`CHANGELOG.md`](CHANGELOG.md) for the public change history.
- This project is public and pre-1.0.
- The public surface is still settling.

# Usage

This document is the human-facing source of truth for how projects and workspaces
should apply `astro-agents`.

Use it when adopting `astro-agents`, setting up Codex skill discovery, choosing
minimal `AGENTS.md` context, declaring a documentation surface profile, and
wiring shared validation.

Use `docs/glossary.md` when usage guidance depends on shared terms that need
one stable meaning across projects.

`astro-agents` is a checked-out shared library, not a package install. The
concrete documented adoption path in this project is Codex skill discovery plus
minimal `AGENTS.md` bootstrap or project context. Some project-structure
guidance may still transfer to other runtimes, but those setups may require
local adaptation.

## Before You Start

Before adopting `astro-agents`, choose a stable local checkout path.

In the examples below, use `<astro-agents-path>` as a placeholder for that
checkout path.

## Codex Skill Discovery

Make `astro-agents` skills discoverable before relying on them in project work.

For user-global use, symlink the shared skills into `$HOME/.agents/skills`:

```bash
mkdir -p "$HOME/.agents/skills"
ln -s "<astro-agents-path>/skills" "$HOME/.agents/skills/astro-agents"
```

For project-local use, symlink the shared skills into the target project's `.agents/skills` directory:

```bash
mkdir -p "<project-path>/.agents/skills"
ln -s "<astro-agents-path>/skills" "<project-path>/.agents/skills/astro-agents"
```

Use absolute paths for `<astro-agents-path>` and `<project-path>`.

From the `astro-agents` checkout, verify user-global discovery with:

```bash
python3 scripts/validate_agent_surface.py --codex-discovery
```

This verifies that Codex includes the shared skills in the model-visible skill list. It does not prove that every natural-language prompt will activate the intended skill.

## Starter Prompts

Use starter prompts when you want to explicitly invoke an `astro-agents` skill in
a fresh thread.

Start with the skill name when you know the intended skill:

- `$agent-surface-review Review this project agent surface.`
- `$code-quality-review Review the current source-code quality.`
- `$documentation-surface-review Review this project's documentation surface.`
- `$project-upgrade-planning Plan this project's astro-agents upgrade.`
- `$research-logging Add an entry to the telemetry research log.`
- `$science-writing Revise manuscript.tex for scientific clarity and claim discipline.`

Natural-language prompts can also activate skills, but explicit `$skill-name`
prompts are clearer when the task could match more than one skill.

## Project Setup

For a downstream project, start with a small local setup that gives agents the
project facts they cannot get from shared skills alone:

- root `AGENTS.md` for project scope, source-of-truth pointers, documentation surface profile, research-log recognition, and any project-local requirements
- `docs/architecture.md` for package shape, contracts, lifecycle, and durable design decisions
- `docs/testing.md`, adapted from `<astro-agents-path>/examples/downstream-testing.md`
- `docs/development.md` when local setup, environment, hooks, or daily commands need a stable owner
- `docs/glossary.md` when recurring project terms need one stable meaning

Keep reusable behavior in `astro-agents` skills and project-specific facts in
the downstream project's own docs, prompts, and `AGENTS.md` files.

## Skill Requirements

If a downstream project wants to impose strong requirements on writing or coding style, add an explicit `## Skill Requirements` or `## Authoring Requirements` section to the root `AGENTS.md` and point directly to the shared skills it wants to require.

This is stronger than a generic bootstrap line. Use it when the project wants agents to follow specific shared skills for recurring work.

For example:

```md
## Skill Requirements
- For Python code, use `$python-code-writing`.
- For general technical prose, use `$technical-writing`.
- For project documentation such as `docs/architecture.md`, `docs/testing.md`, `docs/development.md`, and similar long-lived project documents, use `$project-docs-writing`.
- For `README.md`, use `$readme-writing`.
- For plan documents or phased execution docs when they are created or revised, use `$plan-writing`.
```

## Shared Validation

Shared validation gives a downstream project a starting shape for validation
without moving project-specific commands into the shared library.

Start from `<astro-agents-path>/examples/downstream-testing.md` when creating a
downstream project's `docs/testing.md`, then replace the project-local
verification section with that project's real commands and completion
expectations.

Common shared validation paths include:

- `$agent-surface-review` for full agent-surface review
- `$documentation-surface-review` for documentation-surface review
- `$code-quality-review` for source-code quality review
- `$project-upgrade-planning` for upgrade planning and readiness review

### Documentation Surface Profile

A `documentation surface profile` tells `astro-agents` which documentation
expectations to use when reviewing a downstream project.

Use a profile when a project wants shared documentation review to distinguish
between different documentation shapes, such as a private/default project and a
public Python package. The profile is evidence for review skills; it is not a
runtime setting or enforcement mechanism.

Declare the profile in a short `## Scope` section near the top of the project
root `AGENTS.md`:

```md
## Scope
- Documentation surface profile: public-python.
```

If no profile is declared, shared documentation review should treat the project
as `private-default`.

Use `$documentation-surface-review` when a project wants shared validation for
documentation surface profile behavior, project documentation architecture,
README scope, private/default docs, or public Python documentation.

### Project Glossaries

When a downstream project provides `docs/glossary.md`, `astro-agents` review skills may use it as part of the target project's own agent surface.

Use a project glossary when recurring project terms need one stable meaning across local docs, prompts, skills, or validation rules. Keep the glossary project-local unless the terms are truly shared across projects.

## Research Log Recognition

A downstream project may add a short `## Research Logs` section to root
`AGENTS.md` so agents can recognize which local files are research logs.

List each research log file, its companion folder, and any short name or alias
users are likely to mention. This helps activate `$research-logging` when the
user refers to the file or topic without saying `research log`.

For example:

```md
## Research Logs
- `docs/research/adaptive-optics.md` is the adaptive optics research log. Its companion folder is `docs/research/adaptive-optics/`. Common aliases: `adaptive optics`, `AO`, `adaptive-optics.md`.
- `docs/research/telemetry.md` is the telemetry research log. Its companion folder is `docs/research/telemetry/`. Common aliases: `telemetry`, `telemetry.md`.
```

This supports prompts such as:

- `Add an entry to adaptive-optics.md.`
- `Capture this in telemetry.`

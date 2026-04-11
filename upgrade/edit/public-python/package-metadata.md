# Edit: Public Package Metadata

## Purpose
Use this prompt for the single `public-python` edit task `public package metadata`.

Read `astro-agents/upgrade/edit/base.md` first. Then read `astro-agents/upgrade/edit/public-python/base.md` and apply the task-specific guidance below.

## Task Definition

- task: public package metadata
- prompt used: `astro-agents/upgrade/edit/public-python/package-metadata.md`
- saved artifact: `docs/upgrade/edit-public-package-metadata.md`
- inspect `pyproject.toml` and related public package metadata that affects package presentation or docs discovery
- keep the task scoped to the public metadata surface rather than general packaging internals

## Exclusions

- do not drift into general build-system cleanup
- do not treat internal packaging details as part of the public metadata surface

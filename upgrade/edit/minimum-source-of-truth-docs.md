# Edit: Minimum Source-Of-Truth Docs

## Purpose
Use this prompt for the single core edit task `minimum source-of-truth docs`.

Read `astro-agents/upgrade/edit/base.md` first, then apply the task-specific guidance below.

## Task Definition

- task: minimum source-of-truth docs
- prompt used: `astro-agents/upgrade/edit/minimum-source-of-truth-docs.md`
- saved artifact: `docs/upgrade/edit-minimum-source-of-truth-docs.md`
- use `astro-agents/docs/usage.md` and `astro-agents/docs/upgrade-design.md` to decide which source-of-truth docs are needed
- include `docs/data-sources.md` only when the repo has meaningful durable data artifacts that need one stable inventory-and-ownership doc
- treat data examples as data artifacts when they are real sample data users or agents are expected to inspect or run against
- do not use `docs/data-sources.md` as the owner for data interfaces, normalization behavior, or persisted contracts

## Exclusions

- do not let this task become a general README rewrite
- do not create source-of-truth docs that the current repo surface does not justify

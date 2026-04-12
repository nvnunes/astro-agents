# AGENTS.md

## Purpose
Use this folder when the task is to choose and apply a shared authoring guide or prompt-writing guide.

## Choosing An Authoring Guide

When choosing among shared authoring guides in this folder:

- identify the document's primary role from its file path, title, section headings, and surrounding context
- for `AGENTS.md` files, prefer `authoring/agents/agents-md.md`
- for prompts that define writing or revision behavior under `authoring/agents/` or repo-local `agents/authoring/writing/`, prefer `authoring/agents/writing-prompt.md`
- for prompts under `authoring/writing/`, prefer `authoring/agents/writing-prompt.md` when revising the prompt itself rather than the human-facing text it governs
- for prompts that define coding or code-review behavior under `authoring/code/` or repo-local `agents/authoring/code/`, prefer `authoring/agents/coding-prompt.md`
- for review prompts and other prompts under `validation/` or `agents/validation/`, prefer `authoring/agents/review-prompt.md`
- for prompts under repo-local `agents/` that do not match a more specific local prompt type, prefer `authoring/agents/base.md`
- for source-code authoring work such as writing, editing, reviewing, or refactoring code, route into `authoring/code/AGENTS.md`
- for `README.md` files, prefer `authoring/writing/readme-md.md`
- for other repo-facing documentation such as `docs/architecture.md`, `docs/testing.md`, `docs/api.md`, or `CONTRIBUTING.md`, prefer `authoring/writing/repo-docs.md`
- for glossary or terminology-reference docs such as `docs/glossary.md`, prefer `authoring/writing/repo-docs.md`
- for scientific manuscripts, theses, proceedings, or proposals, prefer `authoring/writing/science.md`
- for framework overviews, concept notes, design charters, or guiding documents, prefer `authoring/writing/foundation.md`
- for working plans, phased roadmaps, or implementation plans, prefer `authoring/writing/plan.md`
- for ad hoc planning requests such as `plan this work`, `next steps`, `execution plan`, sequencing, roadmap drafting, or review planning, prefer `authoring/writing/plan.md` even when no plan file exists yet
- compare candidate prompts or guides by their stated role and revision behavior
- prefer the most specific applicable prompt or guide over a more general one

When the right prompt or guide is still not clear after this comparison:

- ask the user directly before making substantial revisions

## Use Of Shared Authoring Prompts And Guides

- Use shared authoring prompts and guides for reusable writing, prompt-authoring, and code-authoring defaults, not for repo-specific manuscript constraints or subtree-local notation rules.
- Follow subtree-local `AGENTS.md` files when they define local notation, citation, LaTeX, or document-workflow expectations.

## Practical Rule

Use this folder to answer:

- which shared authoring guide applies

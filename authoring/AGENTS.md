# AGENTS.md

## Purpose
Use this folder when the task is to select and apply a shared authoring guide.

## Authoring Guide Selection

When choosing among shared authoring guides in this folder:

- identify the document's primary role from its file path, title, section headings, and surrounding context
- for `AGENTS.md` files, prefer `authoring/agents/agents-md.md`
- for prompts that define writing or revision behavior under `authoring/agents/` or repo-local `agents/authoring/writing/`, prefer `authoring/agents/writing-prompt.md`
- for prompt assets under `authoring/writing/`, prefer `authoring/agents/writing-prompt.md` when revising the prompt asset itself rather than the human-facing text it governs
- for prompts that define coding or code-review behavior under `authoring/code/` or repo-local `agents/authoring/code/`, prefer `authoring/agents/coding-prompt.md`
- for validation prompts such as review prompts under `validation/` or `agents/validation/`, prefer `authoring/agents/validation-prompt.md`
- for prompt assets under repo-local `agents/` that do not match a more specific local prompt type, prefer `authoring/agents/base.md`
- for source-code authoring work such as writing, editing, reviewing, or refactoring code, route into `authoring/code/AGENTS.md`
- for repo-facing documentation such as `README.md`, `docs/architecture.md`, `docs/testing.md`, `docs/api.md`, or `CONTRIBUTING.md`, prefer `authoring/writing/repo-docs.md`
- for glossary or terminology-reference docs such as `docs/glossary.md`, prefer `authoring/writing/repo-docs.md`
- for scientific manuscripts, theses, proceedings, or proposals, prefer `authoring/writing/science.md`
- for framework overviews, concept notes, design charters, or guiding documents, prefer `authoring/writing/foundation.md`
- for working plans, phased roadmaps, or implementation plans, prefer `authoring/writing/plan.md`
- compare candidate guides by their stated role and revision behavior
- prefer the most specific applicable guide over a more general one

When the right guide is still not clear after this comparison:

- ask the user directly before making substantial revisions

## Use Of Shared Authoring Guides

- Use shared authoring guides for reusable writing, prompt-authoring, and code-authoring defaults, not for repo-specific manuscript constraints or subtree-local notation rules.
- More specific repo or subtree `AGENTS.md` files override this folder's shared defaults within their scope.
- Follow subtree-local `AGENTS.md` files when they define local notation, citation, LaTeX, or document-workflow expectations.

## Practical Rule

Use this folder to answer:

- which shared authoring guide applies

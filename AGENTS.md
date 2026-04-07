# AGENTS.md

## Purpose
Use this file as the top-level intent router for the shared prompt library and as a lightweight repo-level working brief for `astro-agents` itself.

## Precedence
- More specific repo-level or subtree-level `AGENTS.md` files override these shared defaults within their scope.
- Use this directory for shared routing and reusable prompt assets, not as a replacement for repo-local instructions.
- Apply shared prompt assets only when no narrower applicable local rule takes precedence.

## Intent Routing
- Decide the prompt area from the user's immediate request first.
- Enter only the selected prompt area unless the request clearly spans more than one area.
- When this repo provides local prompts under `agents/`, use them for repo-specific agent behavior before falling back to shared prompts.
- Do not navigate deeper into the prompt hierarchy unless the routing decision or a more specific local `AGENTS.md` file tells you to.
- When a repo or subtree `AGENTS.md` explicitly activates a specific shared prompt asset for its local context, drill into that asset directly instead of re-walking the whole prompt tree.

## Repo Sources
- Use `docs/architecture.md` as the source of truth for hierarchy design, layer ownership, precedence, validation model, and maintenance expectations in this repo.
- Use `docs/usage.md` as the source of truth for repo-integration guidance, supporting-doc expectations, and recommended repo-level `AGENTS.md` structure.
- Use `docs/testing.md` as the source of truth for validation requirements and canonical review checks in this repo.
- When a task changes agent-surface files in this repo, consult `docs/testing.md` and run the required validation before treating the work as complete.

## Style Prompts
- When a request says `edit for style` or uses similar wording such as `revise the writing`, `polish the prose`, `improve the wording`, or `tighten the language`, route into `authoring/AGENTS.md`.
- When the task is to revise `AGENTS.md` files or repo-facing documentation such as `README.md`, `docs/architecture.md`, `docs/testing.md`, `docs/api.md`, or `CONTRIBUTING.md`, route into `authoring/AGENTS.md`.

## Coding Prompts
- For source-code authoring work such as writing, editing, reviewing, or refactoring code, route into `authoring/code/AGENTS.md`.

## Validation Prompts
- When a request asks to run or select a shared validation review, route into `validation/AGENTS.md`.
- When a request asks for document-writing review, prompt-writing review, hierarchy-behavior review, documentation-architecture review, or full agent-surface review, route into `validation/AGENTS.md`.
- When a request asks to review a prompt hierarchy, review `AGENTS.md` files, validate prompt-library structure, evaluate layer ownership, or assess prompt-routing design, route into `validation/AGENTS.md`.
- When the task requires repo-specific validation for `astro-agents` itself, use prompts under `agents/validation/` as additive overlays on top of the applicable shared validation reviews.

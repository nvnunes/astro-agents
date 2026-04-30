# Glossary

This document is the human-facing source of truth for shared terminology in `astro-agents`.

Use it when a design term, review term, or context-engineering term needs a stable meaning that both humans and agents can reuse consistently.

For runtime and control-flow terminology, use `docs/runtime-model.md`.

## Inclusion And Style

- Use these rules when deciding whether to add, keep, revise, or remove a glossary entry.
- Include a term here only when it materially improves clarity in this project and is actually used in the project.
- Prefer widely recognized terms from software, AI, prompt-engineering, or context-engineering practice when they fit the concept that needs a stable label.
- Keep adopted terms aligned with common usage. Do not redefine a familiar term in a way that would surprise an informed reader.
- Add a new local term only when it names a recurring concept that plain language cannot express as clearly, briefly, or consistently; otherwise prefer plain language.
- Keep a term local to its source-of-truth document when it reads plainly in context and the surrounding text points clearly to that document. Promote it to the main glossary only when it needs a stable meaning across the project or stops reading plainly outside that source document.
- Prefer linking to the main glossary over redefining the same term in multiple docs unless a narrower local replacement is necessary.
- Let the glossary reflect relevant developer norms only when they help explain concepts the project actually uses.
- Write definitions for a mixed audience, including researchers with different levels of development background. Prefer direct, concrete definitions and short examples over insider shorthand or implied developer context.
- When adding or revising an entry, prefer one short paragraph. Add more structure only when a distinction or example is genuinely necessary.
- Treat the glossary as a reader aid, not a vocabulary-building exercise.

## Terms

#### Agent Surface

The part of a project's surface that agents are expected to rely on directly when navigating, interpreting, and applying the project. In this project, the agent surface includes `AGENTS.md`, `SKILL.md` packages, skill references, prompt files when present, and the relevant documentation and source-of-truth docs that agents are expected to use directly.

#### API

An application programming interface: a defined interface that software exposes for other software to call or use. Use this term when discussing stable programmatic entry points such as functions, methods, endpoints, or other externally intended interfaces, rather than human-facing documentation or command-line usage.

#### CLI

A command-line interface: a text-based interface for running commands and interacting with a tool or program. Use this term when discussing commands, flags, arguments, or other shell-level entry points intended for human or agent use.

#### Context Engineering

The practice of deciding what information and runtime support should be available to a model during execution. In this project, context engineering includes prompt and instruction design, project and scoped guidance, reusable workflows, retrieved or carried-forward context, tool outputs, memory assumptions, and runtime controls. Prompt engineering is one part of context engineering.

#### Documentation Surface

The part of a project's surface whose primary audience is human readers, typically through `README.md`, `docs/`, and other human-facing documentation. In this project, the documentation surface is also part of the agent surface because agents are expected to use those docs directly.

#### Documentation Surface Profile

A named documentation surface pattern that identifies the expected structure and emphasis of a project's human-facing documentation. It can map to corresponding review workflows implemented by shared review skills or by higher-authority workspace- or project-local prompt files.

#### Prompt Family

A logical collection of related prompt files, typically organized within a folder tree. In current `astro-agents` architecture, user-facing reusable capabilities should normally be packaged as skills instead; use `prompt family` mainly when describing legacy structure, downstream prompt folders, or non-skill prompt collections.

#### Review Criterion

A named criterion or angle of evaluation used to structure a review. Use this term when a review explicitly organizes its judgments by stable criteria.

#### Skill

A reusable agent capability packaged in a folder with a `SKILL.md` file and optional `references/`, `scripts/`, or `assets/`. In `astro-agents`, skills are the user-facing runtime capability surface.

#### Source Of Truth

An authoritative document for a particular kind of information within a given scope, often called the single source of truth for that topic. Use this term when clarifying which file owns that information and when avoiding duplication or drift across multiple files.

#### Workspace

The user's shared working environment across projects. In `astro-agents`, workspace-level guidance means above-project setup or defaults, such as a shared `astro-agents` checkout, `$CODEX_HOME/AGENTS.md`, shared routing defaults, or user/team conventions that apply across multiple projects.

## Terms To Avoid

- `activate`, `activation`, `shared activation`: Avoid these as catch-all runtime terms. Prefer `Route`, loading `Instructions`, applicability, selection, `Handoff`, or scope change, depending on the actual mechanism.
- `bundle`, `component`, `composite`: Avoid these as formal context-engineering categories. Prefer grouped prompts, internal prompt, internal workflow step, coordinating `Prompt`, or synthesized output.
- `composition`: Avoid this as a standalone runtime term. Prefer overlapping `Instructions`, simultaneous applicability, reusable prompt combination, or multi-step `Workflow`.
- `control flow`: Avoid this as a broad project-runtime label when `Route`, `Workflow`, `Handoff`, or `Orchestration` would be more exact.
- `govern`: Avoid this as a broad runtime verb. Prefer own the task, apply `Instructions`, determine the active `Instructions`, or `Orchestration`.
- `hierarchy`: Avoid this as a catch-all system term. Prefer source ordering, scope ordering, `authority`, `Route` structure, or `Workflow` structure.
- `layering`, `layered guidance`: Avoid these as primary runtime terms. Prefer `instruction chain`, discovery order, or scope ordering when describing how guidance is arranged across scopes. Prefer `Route`, active branch, `authority`, or carry-forward language when describing runtime behavior. If `layering` is used at all, qualify it as static document arrangement rather than additive runtime composition.
- `override`: Avoid this as a broad project-runtime term. Prefer higher-priority `Instructions`, superseding file, or plain language such as `replace` or `supersede`. Reserve `AGENTS.override.md` for the Codex filename.
- `precedence`: Avoid this as a general runtime term. Prefer `authority`, higher-priority `Instructions`, ordering of `Instructions`, or CODEX instruction discovery and merge behavior when referring specifically to Codex.
- `prompt-group`, `prompt group`, `prompt subgroup`, `subgroup`: Avoid these when `prompt family` or `subfolder` would be clearer. Prefer `prompt family` for the logical grouping and `folder`, `prompt folder`, or `subfolder` for the on-disk location.
- `router`: Avoid this as a generic runtime class. Prefer `Route`, `dispatcher`, `selector`, `orchestrator`, `Agent`, or `Deterministic controller`, whichever names the actual role.
- `surface` by itself: Avoid this when `agent surface` or `documentation surface` would be clearer. Use `surface` by itself only when the intended meaning is already obvious from context.

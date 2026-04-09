# Glossary

This document is the human-facing source of truth for shared terminology in `astro-agents`.

Use it when a design term, review term, or prompt-system term needs a stable meaning that both humans and agents can reuse consistently.

## Inclusion And Style

- Use these rules when deciding whether to add, keep, revise, or remove a glossary entry.
- Include a term here only when it materially improves clarity in this repo and is actually used in the repo.
- Prefer widely recognized terms from software, AI, or prompt-engineering practice when they fit the concept that needs a stable label.
- Keep adopted terms aligned with common usage. Do not redefine a familiar term in a way that would surprise an informed reader.
- Add a new local term only when it names a recurring concept that plain language cannot express as clearly, briefly, or consistently; otherwise prefer plain language.
- Keep a term local to its source-of-truth document when it reads plainly in context and the surrounding text points clearly to that document. Promote it to the main glossary only when it needs a stable meaning across the repo or stops reading plainly outside that source document.
- Prefer linking to the main glossary over redefining the same term in multiple docs unless a narrower local override is necessary.
- Let the glossary reflect relevant developer norms only when they help explain concepts the repo actually uses.
- Write definitions for a mixed audience, including researchers with different levels of development background. Prefer direct, concrete definitions and short examples over insider shorthand or implied developer context.
- When adding or revising an entry, prefer one short paragraph. Add more structure only when a distinction or example is genuinely necessary.
- Treat the glossary as a reader aid, not a vocabulary-building exercise.

## Categories

- `AI`
  - AI and agent-system terms
- `DEV`
  - development terms
- `AA`
  - `astro-agents`-specific terms

## Terms

#### Activation `AI`

The act of making a prompt, source-of-truth document, or other instruction applicable in the current context.

#### Agent Surface `AI`

The part of a project's surface that agents are expected to rely on directly when navigating, interpreting, and applying the repo. In this repo, the agent surface includes `AGENTS.md`, prompt files, and the relevant documentation and source-of-truth docs that agents are expected to use directly.

#### Agentic System `AI`

A software system in which AI agents pursue delegated goals by reasoning about context, making decisions, and taking actions, often through tools, workflows, or coordination with other systems. This kind of system is often discussed under the broader label `agentic AI`.

#### API `DEV`

An application programming interface: a defined interface that software exposes for other software to call or use. Use this term when discussing stable programmatic entry points such as functions, methods, endpoints, or other externally intended interfaces, rather than human-facing documentation or command-line usage.

#### Authoring Prompt `AI`

A prompt whose primary role is to guide the writing, revision, or transformation of content. Some authoring prompts, especially in writing contexts, may also function as style guides.

#### Autonomy Level `AI`

A level that defines how independently an agent may make changes, including when it may act autonomously, when it must wait for approval, and when it requires additional guidance before proceeding.

#### Bootstrap Routing `AA`

For prompts, the initial routing step that gets an agent from a generic starting state into the applicable prompt hierarchy. Bootstrap routing may happen because an agent discovers an `AGENTS.md` file or because a user prompt explicitly directs the initial routing path.

#### Bootstrap Prompt `AA`

A short user prompt intended to trigger the correct shared router, prompt, or review path without manually restating the fuller routing or scope instructions.

#### CLI `DEV`

A command-line interface: a text-based interface for running commands and interacting with a tool or program. Use this term when discussing commands, flags, arguments, or other shell-level entry points intended for human or agent use.

#### Codebase `DEV`

The maintained set of source code and closely related authored files that make up a software project, including documentation, prompts, configuration, and tests. Use this term when referring to the project's authored files as a whole rather than generated artifacts or runtime data.

#### Contract `DEV`

A defined behavior, interface, format, or compatibility condition that other parts of a system rely on and that should not be changed casually.

#### Composition `AA`

For prompts, the default activation-time relationship in which all applicable prompt instructions remain active together. Composition is resolved at the instruction level: compatible guidance stays in force together, while precedence resolves any conflicts between applicable instructions.

#### Documentation Surface `AI`

The part of a project's surface whose primary audience is human readers, typically through `README.md`, `docs/`, and other human-facing documentation. In this repo, the documentation surface is also part of the agent surface because agents are expected to use those docs directly.

#### Inheritance `AA`

For prompts, an authoring-time relationship between prompt files in which a more specific prompt reuses and refines the rules of a base prompt.

#### Layer `AA`

In the prompt hierarchy, one position where prompts or instructions can be introduced and take effect. A layer may be a workspace bootstrap file, a shared prompt library, a repo-local prompt library, or a subtree-local routing layer.

#### Precedence `DEV`

The rule that determines which applicable instruction governs when active prompt instructions conflict in the same context. Precedence resolves conflicts between applicable instructions; it is not automatic whole-file replacement.

#### Project Surface `AI`

The part of a codebase that a user, agent, or other tool directly works with. It is where the codebase presents what it is, how it is organized, what its important entry points and boundaries are, and how it should be used or developed. Common components of a surface include documentation, prompts, code comments, and exposed APIs, CLIs, or other declared entry points. Code can sometimes be used to infer these details, but it is usually a poor substitute for clearer outward-facing sources, so internal implementation is generally not treated as part of the main surface.

#### Prompt `AI`

A prompt is an agent-facing instruction or set of instructions intended to guide the agent’s behavior.

#### Prompt File `AI`

A file whose main contents are prompts. In this repo, `AGENTS.md` files are common examples.

#### Prompt Family `AA`

A logical collection of related prompt files, typically organized within a folder tree.

#### Review Lens `AA`

A named criterion or angle of evaluation used to structure a review, often referred to more briefly as a `lens`. A review may apply lenses defined directly in a review prompt or drawn from another prompt or source-of-truth document.

#### Review Prompt `AA`

A validation prompt whose primary role is to review a target and return findings, judgments, or corrective guidance.

#### Router `AI`

A file whose main prompt behavior is routing. In this repo, `AGENTS.md` files commonly act as routers even when they also include short local operational constraints.

#### Routing Prompt `AI`

A prompt that performs routing by directing the agent to the right narrower prompt area, guide, source-of-truth document, or next instruction path.

#### Shared Activation `AA`

An activation performed within a shared prompt library, typically by a shared router, to make a narrower shared prompt or source-of-truth document govern.

#### Source Of Truth `DEV`

An authoritative document for a particular kind of information within a given scope, often called the single source of truth for that topic. Use this term when clarifying which file owns that information and when avoiding duplication or drift across multiple files.

#### Structural Concern `AA`

A sign that structure, ownership, wording, or layering may be misaligned even if there is not yet a confirmed defect; in developer conversation this may sometimes be called a `smell`. Use this term when the issue is structural and worth attention, but has not yet been established as a concrete bug or direct violation.

#### Surface `AI`

A context-dependent shorthand for `Project Surface`, `Agent Surface`, or `Documentation Surface`. Use `surface` by itself only when the intended meaning is already obvious from context; otherwise prefer the more specific term.

#### Validation Prompt `AA`

A prompt whose primary role is to assess or classify an existing target and determine what should happen next, rather than directly author the target.

#### Workflow `DEV`

A defined sequence of steps, decisions, or checks used to carry out a task. Use this term when discussing how work should be performed, including required commands, ordering, gates, or operational constraints.

#### Workspace `DEV`

A broader working environment that contains one or more projects and any shared resources that apply across them. For example, a collection of repos within a parent folder such as `Projects/`.

## Terms To Avoid

- `prompt asset`: Avoid this when you just mean a prompt or a file containing prompts. Prefer `prompt` or `prompt file`.
- bare `surface`: Avoid this when `project surface`, `agent surface`, or `documentation surface` would be clearer. Use `surface` by itself only when the intended meaning is already obvious from context.
- `surfacing`, `resurface`, `resurfacing`: Avoid these as formal terms. They suggest a broader relationship to `Surface` than we usually mean. Prefer plain language such as `link from a clearer source-of-truth location`, `point to explicitly`, or `retain the document while changing how it is linked or owned`.
- `prompt-group`, `prompt group`, `prompt subgroup`, `subgroup`: Avoid these when `prompt family` or `subfolder` would be clearer. Prefer `prompt family` for the logical grouping and `folder`, `prompt folder`, or `subfolder` for the on-disk location.

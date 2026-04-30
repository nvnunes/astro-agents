# Runtime Terminology Review

## Purpose

Use this reference when reviewing runtime, routing, control-flow, instruction, context, or state terminology in a target project's agent surface or documentation surface.

This is a review lens, not a full runtime architecture document. It helps the reviewer identify unclear, overclaimed, or misleading runtime language.

## Scope

Apply this reference when target docs or agent-facing files discuss:

- agents, tools, specialists, or deterministic controllers
- tasks, workflows, routes, delegation, handoffs, or orchestration
- prompts, instructions, examples, context, constraints, or output schemas
- sessions, state, memory, retrieved context, summaries, or carry-forward behavior
- runtime controls such as permissions, approvals, sandboxing, tool boundaries, observability, or validation

When the target project has its own runtime, glossary, terminology, or architecture docs, inspect those docs first. Use this reference to check clarity and consistency, not to replace the target project's own source of truth.

## Preferred Vocabulary

Use these terms when they precisely describe the mechanism.

- `Agent`
  - A model-driven runtime actor that can interpret instructions, use tools, and carry out work.
- `Tool`
  - An external capability an agent can invoke, such as search, code execution, file access, or an API.
- `Deterministic controller`
  - Non-model logic that shapes execution through fixed rules, state machines, gates, or workflow code.
- `Task`
  - The work the system is trying to complete.
- `Workflow`
  - The sequence or structure of steps used to complete a task.
- `Route`
  - Directing work to the appropriate downstream path, specialist, or workflow.
- `Delegation`
  - Assigning a bounded subtask to another agent or component.
- `Handoff`
  - A change in which another agent or workflow branch takes over active ownership of a branch of work.
- `Orchestration`
  - Coordination of multiple agents, tools, and workflow steps across a task.
- `Prompt`
  - The input given to a model for one interaction step or task, often including instructions and supporting information.
- `Instructions`
  - Rules or guidance about how the model should behave.
- `Context`
  - Additional information available to the model, such as retrieved content, history, or supporting documents.
- `Session`
  - The current conversation or runtime thread.
- `State`
  - Data associated with the current session or execution path.
- `Memory`
  - Information stored outside the immediate session and available for later retrieval.

## Terms To Check Carefully

Do not treat these terms as forbidden. Treat them as review triggers when they hide the actual mechanism.

- `guidance`
  - Check whether the text actually means `Instructions`, `Prompt`, `Context`, or a human-facing recommendation.
- `authority`
  - Use only for instruction priority or trust relationships, not as a vague synonym for importance.
- `dispatcher`
  - Prefer this as a role label for an `Agent` or deterministic controller performing `Route`, not as a separate runtime kind.
- `selector`
  - Prefer this as a role label for choosing from a bounded set.
- `orchestrator`
  - Prefer this as a role label for an `Agent` or controller performing `Orchestration`.
- `activation`
  - Prefer explicit language such as skill selection, instruction loading, route choice, applicability, or handoff.
- `override`
  - Prefer higher-priority instructions, superseding file, replace, or supersede. Reserve `AGENTS.override.md` for the Codex filename.
- `precedence`
  - Prefer authority, instruction ordering, or the named runtime's discovery behavior.
- `layering`
  - Use only for static document arrangement unless the runtime actually composes the layers at execution time.
- `govern`
  - Prefer apply instructions, route, determine, constrain, or validate.
- `router`
  - Prefer `Route`, dispatcher, selector, orchestrator, agent, or deterministic controller depending on the actual role.

## Review Checks

When runtime terminology matters, check whether the target surface:

- names the actual mechanism instead of relying on vague hierarchy or layering language
- distinguishes runtime behavior from project convention
- distinguishes active `Instructions` from supporting `Context`
- distinguishes project-local source-of-truth docs from shared skills or example references
- states when a route, workflow, handoff, or delegation changes ownership of the next output
- avoids implying strict formal semantics where the runtime only provides heuristic model behavior
- avoids implying that project-local docs enforce runtime controls that actually belong to the runtime, tool layer, or human approval flow
- explains tool, permission, approval, or side-effect boundaries when the project gives agents action capability
- treats session history, compaction summaries, retrieved context, and memory as different kinds of context when that distinction matters
- uses target-project glossary or runtime docs consistently when they exist

## Finding Guidance

Raise a finding when unclear runtime terminology could cause one of these practical failures:

- the agent follows the wrong route or combines incompatible workflows
- a project-local convention is mistaken for a runtime-enforced rule
- a document appears to grant tool or approval authority that the runtime does not actually provide
- broad context or stale session history is treated as active instruction
- a review, handoff, or delegation path leaves ownership of the next output unclear
- users cannot tell which file owns a recurring runtime term or instruction boundary

Do not raise findings only because a project uses different vocabulary from this reference. The issue is whether the language is clear, mechanism-aware, and consistent within the target project's own source-of-truth surface.

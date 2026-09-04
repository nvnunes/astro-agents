# Agent Context Engineering Patterns

This document records external pattern observations for organizing agentic AI instructions, context, workflows, and runtime controls. Use it as research input for future runtime design work, not as a formal standard or as the architecture document for any one prompt library.

Across current agent tooling, successful systems usually avoid one large prompt. They distribute guidance across durable instruction files, scoped project context, reusable workflows, runtime controls, and the immediate task prompt. The exact discovery and priority rules differ by tool, but the underlying design goal is consistent: make agent behavior easier to reuse, inspect, and adapt without forcing every request to carry the whole operating manual.

Most instruction and workflow layers become model context: text or structured content made available to the language model. The runtime still matters because it decides what is loaded, in what order, with what tools, permissions, state, and side effects.

In this sense, prompt engineering is part of context engineering. Prompt engineering focuses on writing effective instructions, examples, formats, and task phrasing. Context engineering covers the broader problem of deciding which prompts, instruction files, retrieved context, chat history, tool outputs, memory, workflow guidance, and runtime controls are available to the model during execution.

Relevant current references include:

- [OpenAI Codex custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [OpenAI Agents SDK guide](https://developers.openai.com/api/docs/guides/agents)
- [GitHub Copilot repository custom instructions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions)
- [AGENTS.md community format](https://agents.md/)
- [Cursor Rules](https://cursor.com/docs/rules)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

## Overview

Agentic systems commonly distribute instructions, context, workflows, and runtime controls across several context engineering layers:

```text
Runtime defaults
User and team defaults
Project instructions
Scoped instructions
Reusable workflows
Runtime controls
Task prompt and session context
```

These layers should be read as a context engineering pattern rather than a universal execution stack. Some tools concatenate instruction files. Some select rules by path, relevance, or manual invocation. Some use subagents, skills, or workflow agents with separate context windows. Some expose state, memory, guardrails, tracing, and approvals as first-class runtime features. The practical goal is to put each kind of guidance where it can be applied reliably with the least duplication and the least unnecessary context.

## Context Engineering Layers

### 1. Runtime Defaults

Runtime defaults are the behavior provided by the model provider, agent app, SDK, IDE, sandbox, permission system, or hosting environment before project-specific guidance is loaded.

Typical responsibilities:

- model behavior, tool-calling semantics, and platform safety defaults
- sandboxing, command execution, file access, and approval mechanics
- available tools, MCP or plugin configuration, and tool-discovery behavior
- conversation-state, memory, tracing, telemetry, or compaction behavior
- instruction-discovery behavior, such as how a tool finds and injects project files

Design intent:

Establish the baseline capabilities and limits that application, project, and task guidance must work within. Runtime defaults describe the platform's starting behavior; runtime controls describe configured enforcement and evidence during execution.

The runtime owns the mechanism. Project files supply content that the runtime may discover and load.

### 2. User And Team Defaults

User and team defaults define durable preferences or policies that apply above any one project.

Typical responsibilities:

- communication style, review expectations, and working agreements
- organization-wide security, compliance, or approval requirements
- shared coding, documentation, or operational conventions
- reusable defaults that should follow a user or team across projects

Constraints:

- Keep this layer stable and broadly applicable.
- Avoid project-specific paths, commands, or contracts.
- Do not rely on this layer to supply facts that a project must own locally.

Design intent:

Provide shared defaults without making each project repeat the same general working rules.

### 3. Project Instructions

Project instructions provide the durable context needed to work inside a specific codebase, research project, documentation set, or prompt library.

Typical responsibilities:

- build, test, validation, and development commands
- project structure, source-of-truth documents, and navigation
- local coding standards, documentation expectations, and workflow rules
- project-specific boundaries, such as files or systems the agent should not change without approval

Design intent:

Make a new agent session effective inside the project without requiring the user to restate the project's operating context in every task prompt.

Reference pattern:

`AGENTS.md`, `.github/copilot-instructions.md`, root-level Cursor project rules, `CLAUDE.md`, and similar files all serve this broad purpose in different ecosystems.

### 4. Scoped Instructions

Scoped instructions narrow or specialize behavior for a directory, file type, package, service, domain, or workflow branch.

Typical responsibilities:

- path-specific build, test, or validation commands
- framework-specific conventions
- local domain constraints, data rules, notation, or documentation style
- safer behavior for sensitive areas such as production configuration, migrations, credentials, or regulated data

Constraints:

- Add scoped instructions only when local behavior materially differs from the broader project default.
- Avoid duplicating parent instructions.
- State true local exceptions explicitly instead of relying on vague precedence language.
- Keep the scope clear enough that agents can tell when the instruction applies.

Design intent:

Support local specialization without turning the whole instruction system into one undifferentiated prompt.

### 5. Reusable Workflows

Reusable workflow units capture repeated work that is too detailed, task-specific, or operational to belong in general project instructions.

Current forms include:

- skills or playbooks for repeatable tasks
- subagents with specialized prompts, tool access, or context windows
- workflow agents that run predefined sequences or parallel branches
- routing, handoff, evaluator, review, or repair procedures
- prompt-library files for authoring, review, research logging, data analysis, or release work

Typical responsibilities:

- define the task-specific process
- list required inputs, outputs, and validation expectations
- provide examples, templates, or checklists when they improve reliability
- define when a specialist, skill, or workflow should be used
- keep complex or infrequent procedures out of always-loaded context

Constraints:

- Keep each workflow focused on one recurring job.
- Prefer progressive disclosure: load detailed references only when needed.
- Add specialists only when separate ownership, tools, policies, models, or trace visibility materially improve the workflow.
- Avoid creating multiple agents or skills when a single clear prompt or direct tool call is sufficient.

Design intent:

Make repeated work reliable and shareable while preserving context budget and keeping ordinary requests simple.

### 6. Runtime Controls

Runtime controls are the execution-time mechanisms that shape what an agent can do, what context it can use, when it must pause, and what evidence it leaves behind. Modern agentic systems increasingly treat these controls as part of agent design rather than as incidental implementation detail.

Typical responsibilities:

- tool names, descriptions, input schemas, output shapes, and side effects
- least-privilege tool access for agents, skills, or workflow branches
- approval thresholds for risky changes or external actions
- session state, memory, artifacts, retrieved context, and compaction summaries
- traces, logs, citations, evals, and other evidence needed to inspect behavior

Design intent:

Make agent behavior controllable and auditable, especially when the agent can call tools, persist state, act across systems, or run for many steps.

Important distinction:

Instructions tell the model what to do. Runtime controls shape what the system can actually do, what evidence it leaves behind, and when human review must interrupt execution.

### 7. Task Prompt And Session Context

The task prompt is the immediate user intent. Session context is the relevant conversation, prior tool results, current plan, active assumptions, and recently discovered facts that should carry forward for the current work.

Typical responsibilities:

- define the current objective
- provide task-specific inputs, constraints, and success criteria
- identify any immediate priority or scope change
- carry only the recent context needed for the current branch of work

Constraints:

- Do not restate durable project instructions unless they need emphasis or clarification.
- Do not bury reusable workflows in one-off prompts.
- Treat stale session context carefully when the user changes direction, the route narrows, or new source material supersedes earlier assumptions.

Design intent:

Keep immediate intent separate from durable policy and reusable workflow knowledge.

## Runtime Reality

The layered pattern is not a universal runtime specification.

Important differences across tools include:

- discovery: which files or rules are found automatically
- availability and selection: whether guidance is always loaded, path-scoped,
  selected by relevance, explicitly invoked, or delegated to a subagent
- priority: whether user, team, project, scoped, or agent-specific instructions have stronger influence
- conflict behavior: whether later or nearer instructions replace earlier ones, are combined with them, or merely have more practical effect
- state handling: whether history, memory, artifacts, compaction, and retrieved context are managed by the platform, SDK, application code, or the user
- visibility: whether the user can inspect loaded instructions, selected skills, tool calls, traces, approvals, and validation results

Because of these differences, durable guidance should avoid relying on implied hierarchy alone. Well-designed agent surfaces state scope, boundaries, and exceptions directly.

## Design Principles

### Separation Of Responsibilities

Put each kind of guidance where it belongs:

- shared defaults in user and team defaults
- project facts in project instructions
- local exceptions in scoped instructions
- repeated procedures in skills, agents, prompts, or workflow files
- tool access, permissions, state, and observability in runtime controls
- immediate intent in the task prompt

### Explicit Scope

Every instruction source should make its scope clear. Agents should be able to tell whether guidance applies globally, to one project, to one path, to one workflow, to one tool, or only to the current request.

### Progressive Disclosure

Keep always-loaded context short. Put long references, examples, templates, and specialized procedures in files or systems that can be loaded only when relevant.

### Concrete Operational Detail

Use explicit commands, paths, examples, schemas, acceptance criteria, and boundaries. Vague instructions such as "write good code" or "follow best practices" are weaker than examples and specific checks.

### Minimal Duplication

Keep recurring guidance in one owner and link or route to it. Duplicated instructions drift, increase context cost, and make conflict handling harder.

### Bounded Specialization

Use scoped rules, skills, subagents, and workflow agents when they create real clarity. Avoid splitting too early, because every extra specialist adds instruction surface area, routing complexity, tool-access decisions, and trace complexity.

### Tool And Permission Clarity

Treat tool descriptions, input schemas, side effects, and permission boundaries as part of the prompt design. A tool should be easy for the agent to select, call correctly, and avoid when it is unsafe or irrelevant.

### State And Memory Discipline

Distinguish durable policy, task-local state, conversation history, retrieved context, artifacts, compaction summaries, and longer-lived memory. Prefer fresh source discovery over stale carry-forward when correctness matters.

### Guardrails And Human Review

Define when the agent may proceed automatically, when it must ask, and when it must stop. Risky side effects, credentials, production systems, database writes, dependency changes, and policy-sensitive actions usually need explicit boundaries.

### Observability And Evaluation

Reliable agent systems need evidence. Use traces, logs, citations, test output, review summaries, or evals to make route choices, tool use, approvals, and final claims inspectable.

## Summary

The emerging best practice is not one universal file format or one strict context stack. It is a context engineering discipline:

- stable guidance belongs in durable instruction surfaces
- local context belongs close to the files, domains, or workflows it affects
- repeated procedures belong in reusable skills, agents, or workflow prompts
- tools, permissions, state, and observability need explicit runtime controls
- the task prompt should carry immediate intent, not the whole operating manual

This pattern improves maintainability and agent reliability because it reduces vague prompting, limits unnecessary context, makes specialization explicit, and leaves better evidence for human review.

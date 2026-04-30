# Runtime Model

This document is the source of truth for runtime-related terminology, current-support boundaries, and control-flow concepts in this project, grounded in current agentic AI guidance.

Use this document to understand the runtime mechanics current agent systems actually provide, the vocabulary `astro-agents` uses to describe them, and which parts of that model the project currently supports directly.

For review-time checks of runtime, skill activation, instruction, context, routing, and control-flow terminology, keep `skills/documentation-surface-review/references/runtime-terminology-review.md` and `skills/agent-surface-review/references/runtime-terminology-guard.md` aligned with this document.

## Scope And Current Support

This document uses OpenAI, Anthropic, and Google material to derive shared runtime vocabulary and compare common agent-system patterns.

Within `astro-agents`:

- that broader comparison supports vocabulary, design work, and future planning
- it does not mean `astro-agents` currently provides equally detailed operational guidance for every runtime discussed here
- the concrete runtime path documented for direct use today is Codex skill discovery, explicit `$skill-name` invocation, and project-local `AGENTS.md` context
- references to other runtimes in this document should be read as design input unless another `astro-agents` document says otherwise

## Common Agent Runtime Ontology

This section names a small set of concepts that recur across current agent documentation and research. It is not a complete formal ontology. It is a practical vocabulary for describing common agent runtimes without relying on vendor-specific product terms.

Across OpenAI, Anthropic, and Google documentation, the recurring shared core is a small set of runtime actors, coordination patterns, prompt-and-context inputs, and session/state constructs, even though each vendor packages them somewhat differently. [\[4\]](#ref-4)[\[6\]](#ref-6)[\[17\]](#ref-17)[\[18\]](#ref-18)[\[19\]](#ref-19)[\[20\]](#ref-20)[\[21\]](#ref-21)

The main categories are:

1. runtime actors and capabilities
2. work and coordination
3. prompt and context
4. runtime state

### Runtime Actors And Capabilities

These are the components that can perform work or directly affect execution. OpenAI describes agents, tools, and handoffs as core orchestration elements; Anthropic distinguishes augmented LLMs, workflows, and agents; and Google distinguishes LLM agents from deterministic workflow agents. [\[4\]](#ref-4)[\[6\]](#ref-6)[\[17\]](#ref-17)[\[19\]](#ref-19)

- `Agent`
  - A model-driven runtime actor that can interpret instructions, use tools, and carry out work.
- `Specialist agent`
  - An agent with a narrower domain, capability, or task focus within a larger system.
- `Worker agent`
  - An agent that is assigned work by a coordinating agent or controller, often as part of an orchestrator-worker pattern.
- `Tool`
  - An external capability an agent can invoke, such as search, code execution, file access, or an API.
- `Deterministic controller`
  - Non-model logic that shapes execution through fixed rules, state machines, gates, or predefined workflow code.

### Work And Coordination

These concepts describe the work being done and how it is coordinated. OpenAI emphasizes orchestration patterns and handoffs, Anthropic emphasizes workflows versus agents plus routing and orchestrator-worker patterns, and Google ADK exposes workflow agents as explicit control-flow structures. [\[4\]](#ref-4)[\[5\]](#ref-5)[\[6\]](#ref-6)[\[17\]](#ref-17)[\[19\]](#ref-19)

- `Task`
  - The work the system is trying to complete.
- `Subtask`
  - A bounded part of a larger task.
- `Workflow`
  - The sequence or structure of steps used to complete a task.
- `Route`
  - Directing work to the appropriate downstream path, specialist, or workflow.
- `Delegation`
  - Assigning a subtask to another agent or component.
- `Handoff` / `Transfer`
  - A change in which another agent takes over the active branch of work.
- `Orchestration`
  - Coordination of multiple agents, tools, and workflow steps across a task.

### Prompt And Context

These concepts describe the information that shapes model behavior. OpenAI distinguishes instructions, message roles, and conversation state; Anthropic frames context engineering as managing the broader context state around prompts; and Google’s agent docs treat session data and runtime context as structured inputs to ongoing execution. [\[12\]](#ref-12)[\[14\]](#ref-14)[\[18\]](#ref-18)[\[20\]](#ref-20)[\[21\]](#ref-21)

- `Prompt`
  - The input given to a model for one interaction step or task, often including instructions and supporting information.
- `Instructions`
  - Rules or guidance about how the model should behave.
- `Examples`
  - Demonstrations of desired behavior or output patterns.
- `Context`
  - Additional information available to the model, such as retrieved content, history, or supporting documents.
- `Constraints`
  - Limits on what the model may do or how it should respond.
- `Output schema` / `Output format`
  - Requirements on the structure or form of the result.

In practice, prompts often package some or all of the above together. A `prompt file` is a file that stores a prompt or prompt components for later use by a person, program, or agent runtime.

### Runtime State

These concepts describe information that persists during or across execution. Google’s ADK explicitly separates `Session`, `State`, and `Memory`; OpenAI documents conversation state as a first-class concern; and Anthropic’s context-engineering guidance treats evolving context state as a central runtime design problem. [\[18\]](#ref-18)[\[20\]](#ref-20)[\[21\]](#ref-21)

- `Session`
  - The current conversation or runtime thread.
- `State`
  - Data associated with the current session or execution path.
- `Memory`
  - Information stored outside the immediate session and available for later retrieval.

## Other Common Terms

The terms below are also common in agent and context-engineering discussions, but they are better treated as supporting terms, role labels, or plain-language descriptions rather than as top-level ontology buckets. This is also the pattern in the vendor docs: role labels and runtime mechanisms recur, but they are not always promoted to the same level as the shared ontology above. [\[4\]](#ref-4)[\[17\]](#ref-17)[\[19\]](#ref-19)

- `guidance`
  - A plain-language umbrella term that may refer to `Instructions`, `Prompt`, or `Context`, depending on what is actually meant. [\[18\]](#ref-18)
- `authority`
  - A supporting conflict concept about relative priority among `Instructions`, especially when instructions conflict. [\[12\]](#ref-12)[\[13\]](#ref-13)
- `dispatcher`
  - A role label for an `Agent` or `Deterministic controller` performing `Route`. [\[17\]](#ref-17)[\[19\]](#ref-19)
- `selector`
  - A role label for an `Agent` or `Deterministic controller` that chooses one option from a bounded set. [\[17\]](#ref-17)[\[19\]](#ref-19)
- `orchestrator`
  - An `Agent` performing `Orchestration`. [\[4\]](#ref-4)[\[17\]](#ref-17)[\[19\]](#ref-19)

## Current Concrete Runtime Support: Codex

Codex has built-in instruction discovery behavior. It can load guidance from your Codex home directory and from project-local instruction files. In project scope, it starts at the project root, walks down to the current working directory, checks `AGENTS.override.md` first, then `AGENTS.md`, then any configured fallback filenames, includes at most one file per directory, and skips empty files. Files closer to the current working directory override earlier guidance because they appear later in the merged instruction chain. Codex stops searching once it reaches the current directory. [\[1\]](#ref-1)[\[2\]](#ref-2)[\[3\]](#ref-3)

Codex also injects each discovered instruction file near the top of the conversation history as its own user-role message, in root-to-leaf order. The global file comes first, then the project root, then deeper directories. [\[2\]](#ref-2)

For overall guidance, the standard Codex location is still Codex home, where Codex checks `AGENTS.override.md` before `AGENTS.md`. If you want that guidance to live somewhere else, the documented Codex mechanism is to set `CODEX_HOME` before launch so Codex uses a different home directory. [\[1\]](#ref-1)[\[2\]](#ref-2) If changing `CODEX_HOME` is inconvenient, a practical local workaround is to keep Codex on the default `~/.codex` path and symlink `~/.codex/AGENTS.md` to the real file you want to maintain elsewhere. That symlink approach is a filesystem convenience, not a Codex-specific feature.

In practical `astro-agents` usage, that means the canonical global bootstrap location is `$CODEX_HOME/AGENTS.md` (commonly `~/.codex/AGENTS.md`). Project-specific adoption, or project-specific exceptions to a global default, belong in the project root `AGENTS.md`. Runtime-discovered skills, not `AGENTS.md` routing tables, are the documented activation path for reusable `astro-agents` capabilities.

Example hierarchy:

```text
~/.codex/AGENTS.md
<project>/AGENTS.md
<project>/services/AGENTS.md
<project>/services/payments/AGENTS.override.md
<project>/services/payments/AGENTS.md
<project>/services/payments/src/
```

If Codex is started from `<project>/services/payments/src/`, the loaded instruction order is:

1. `~/.codex/AGENTS.md`
2. `<project>/AGENTS.md`
3. `<project>/services/AGENTS.md`
4. `<project>/services/payments/AGENTS.override.md`

In that case, `<project>/services/payments/AGENTS.md` is ignored because the override file in the same directory takes priority. [\[1\]](#ref-1)

These behaviors are Codex features. They are not defined by `astro-agents`. [\[1\]](#ref-1)[\[2\]](#ref-2)[\[3\]](#ref-3)

### Current Codex Runtime Mapping

The external context engineering layers in `docs/future/agent-context-engineering-patterns.md` map onto the current Codex path through concrete instruction, skill, and runtime surfaces.

| Context engineering layer | Owner | Current operational surface |
| --- | --- | --- |
| Runtime Defaults | Runtime | Codex, the app, the model, available tools, and sandbox behavior. |
| User And Team Defaults | User/team | `$CODEX_HOME/AGENTS.md`, commonly `~/.codex/AGENTS.md`, or other user-level guidance supplied before the project is loaded. |
| Project Instructions | Project | The project root `AGENTS.md`. |
| Scoped Instructions | Project | Subtree `AGENTS.md` or `AGENTS.override.md` files loaded by Codex according to the current working directory. |
| Reusable Workflows | astro-agents | Runtime-discovered `astro-agents` skills and their references. |
| Runtime Controls | Runtime | Codex, the app, tool integrations, sandbox behavior, approvals, and available runtime evidence. |
| Task Prompt And Session Context | Session | The active chat, loaded instruction messages, tool outputs, and any compaction or handoff summaries. User-facing guidance such as `docs/usage.md` helps users provide task prompts that activate the intended project instructions and reusable workflows. |

`astro-agents` can document desired runtime-control behavior, but enforcement belongs to the runtime or to future implementation work.

## How Models Handle Conflicting Instructions

As a practical summary of the model behavior described in the sources below, modern models do not behave like a strict symbolic conflict resolver. [\[11\]](#ref-11)[\[12\]](#ref-12)[\[13\]](#ref-13)[\[14\]](#ref-14)[\[15\]](#ref-15)[\[16\]](#ref-16)

1. Models assign different levels of authority to instructions. Higher-authority instructions override lower-authority ones. [\[12\]](#ref-12)[\[13\]](#ref-13)
2. Subject to that authority ordering, they try to follow all compatible instructions at once. Overlapping instructions usually get merged into one behavior rather than one clean winner being selected. [\[13\]](#ref-13)
3. Within the same authority level, later instructions can supersede earlier ones when they contradict, override, or make them irrelevant. Outside those cleaner cases, ambiguous or poorly structured prompts can still produce heuristic behavior, so clarity, structure, and example design still matter. [\[13\]](#ref-13)[\[14\]](#ref-14)
4. Quoted text, tool outputs, and other untrusted content do not have instruction authority by default unless higher-authority instructions explicitly delegate authority to them. [\[13\]](#ref-13)
5. Contradictions usually degrade performance instead of producing a clean winner. OpenAI's GPT-5 prompting guide explicitly says contradictory or vague prompts can hurt performance because the model may spend effort trying to reconcile them rather than simply picking one. [\[14\]](#ref-14)
6. Other model vendors describe the same failure mode in prompt-design terms. Anthropic advises that if a prompt would confuse a minimally informed colleague, it will likely confuse the model, and Google's prompt-design guidance explicitly calls out conflicting instructions, conflicting examples, and conflicting internal references as prompt problems. [\[15\]](#ref-15)[\[16\]](#ref-16)

The practical implication for this project is an inference from those sources: Codex instruction discovery and merge behavior is a real runtime mechanism, while project-local precedence is mostly design intent unless the instruction surface makes the choice explicit through direct skill invocation, explicit follow-up skill or prompt references, explicit local customization boundaries, explicit conflict language, or clarifying-question behavior. [\[1\]](#ref-1)[\[13\]](#ref-13)[\[14\]](#ref-14)

## Project Control-Flow Example

For a full review requested in a downstream project, the Codex part is instruction discovery, skill discovery, and merge behavior: Codex loads the applicable `AGENTS.md` files from the project root down to the current directory according to its normal search rules, and it exposes discovered skills for model-mediated activation. From there, the remaining behavior comes from how the downstream project and shared skills are written: the task prompt may activate `$agent-surface-review`, the project may declare a documentation surface profile, and local validation expectations may be named in `docs/testing.md`. Those later steps are project conventions expressed through skills and instruction files, not built-in Codex behavior. [\[1\]](#ref-1)[\[2\]](#ref-2)[\[3\]](#ref-3)

## References

<a id="ref-1"></a>
1. OpenAI Developers, "Custom instructions with AGENTS.md – Codex." [https://developers.openai.com/codex/guides/agents-md](https://developers.openai.com/codex/guides/agents-md)
<a id="ref-2"></a>
2. OpenAI Cookbook, "Codex Prompting Guide," section "Using agents.md." [https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#using-agentsmd](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#using-agentsmd)
<a id="ref-3"></a>
3. OpenAI Developers, "Customization," section "When to update AGENTS.md." [https://developers.openai.com/codex/concepts/customization#when-to-update-agentsmd](https://developers.openai.com/codex/concepts/customization#when-to-update-agentsmd)
<a id="ref-4"></a>
4. OpenAI Developers, "Orchestration and handoffs," section "Choose the orchestration pattern." [https://developers.openai.com/api/docs/guides/agents/orchestration#choose-the-orchestration-pattern](https://developers.openai.com/api/docs/guides/agents/orchestration#choose-the-orchestration-pattern)
<a id="ref-5"></a>
5. OpenAI Developers, "Orchestration and handoffs," section "Use handoffs for delegated ownership." [https://developers.openai.com/api/docs/guides/agents/orchestration#use-handoffs-for-delegated-ownership](https://developers.openai.com/api/docs/guides/agents/orchestration#use-handoffs-for-delegated-ownership)
<a id="ref-6"></a>
6. OpenAI Developers, "Building agents," sections "Orchestration" and "Foundations of the Agents SDK." [https://developers.openai.com/tracks/building-agents#orchestration](https://developers.openai.com/tracks/building-agents#orchestration)
<a id="ref-7"></a>
7. Anthropic, "Create custom subagents." [https://docs.anthropic.com/en/docs/claude-code/sub-agents](https://docs.anthropic.com/en/docs/claude-code/sub-agents)
<a id="ref-8"></a>
8. Anthropic, "Ticket routing." [https://docs.anthropic.com/en/docs/about-claude/use-case-guides/ticket-routing](https://docs.anthropic.com/en/docs/about-claude/use-case-guides/ticket-routing)
<a id="ref-9"></a>
9. Google, "Agent Development Kit (ADK) Technical Overview." [https://google.github.io/adk-docs/get-started/about/](https://google.github.io/adk-docs/get-started/about/)
<a id="ref-10"></a>
10. Google Cloud, "Agentic AI use case: Orchestrate access to disparate enterprise systems." [https://cloud.google.com/architecture/agenticai-orchestrate-access-disparate-systems](https://cloud.google.com/architecture/agenticai-orchestrate-access-disparate-systems)
<a id="ref-11"></a>
11. OpenAI Cookbook, "OpenAI Harmony Response Format," section "Roles." [https://developers.openai.com/cookbook/articles/openai-harmony#roles](https://developers.openai.com/cookbook/articles/openai-harmony#roles)
<a id="ref-12"></a>
12. OpenAI Developers, "Text generation," section "Message roles and instruction following." [https://developers.openai.com/api/docs/guides/text#message-roles-and-instruction-following](https://developers.openai.com/api/docs/guides/text#message-roles-and-instruction-following)
<a id="ref-13"></a>
13. OpenAI, "Model Spec (2025/09/12)," sections "Instructions and levels of authority" and "Follow all applicable instructions." [https://model-spec.openai.com/2025-09-12.html](https://model-spec.openai.com/2025-09-12.html)
<a id="ref-14"></a>
14. OpenAI Cookbook, "GPT-5 prompting guide," section "Instruction following." [https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide#instruction-following](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide#instruction-following)
<a id="ref-15"></a>
15. Anthropic, "Be clear, direct, and detailed." [https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/be-clear-and-direct](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/be-clear-and-direct)
<a id="ref-16"></a>
16. Google Cloud, "Overview of prompting strategies," subsection "Issues with instructions and examples." [https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/prompts/prompt-design-strategies](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/prompts/prompt-design-strategies)
<a id="ref-17"></a>
17. Anthropic, "Building effective agents." [https://www.anthropic.com/engineering/building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents)
<a id="ref-18"></a>
18. Anthropic, "Effective context engineering for AI agents." [https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
<a id="ref-19"></a>
19. Google, "Workflow Agents - Agent Development Kit." [https://adk.dev/agents/workflow-agents/](https://adk.dev/agents/workflow-agents/)
<a id="ref-20"></a>
20. Google, "Introduction to Conversational Context: Session, State, and Memory - Agent Development Kit." [https://adk.dev/sessions/](https://adk.dev/sessions/)
<a id="ref-21"></a>
21. OpenAI Developers, "Conversation state." [https://developers.openai.com/api/docs/guides/conversation-state](https://developers.openai.com/api/docs/guides/conversation-state)

# Runtime Model

This document is evolving into a working source of truth for runtime-related terminology and control-flow concepts in this repo, grounded in current agentic AI guidance, and identifies where terminology in `astro-agents` should be updated to match.

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

The terms below are also common in agent and prompt-system discussions, but they are better treated as supporting terms, role labels, or plain-language descriptions rather than as top-level ontology buckets. This is also the pattern in the vendor docs: role labels and runtime mechanisms recur, but they are not always promoted to the same level as the shared ontology above. [\[4\]](#ref-4)[\[17\]](#ref-17)[\[19\]](#ref-19)

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

## Repo Terms To Reframe

Most repo-specific runtime vocabulary should be retired in favor of the generalized ontology above. The table below keeps only the live repo terms that still need explicit reframing during the rewrite.

These entries are transitional. Keep them only where the current repo still uses them, and prefer the generalized terms above in new or revised guidance. In the middle column, defined ontology terms and other defined supporting terms appear in `backticks`, plain-language replacements appear in plain text, and CODEX-specific mechanisms appear in ALL CAPS.

| Live repo term | Potential replacement term(s) | Mapping note |
| --- | --- | --- |
| `Precedence` | `authority`; higher-priority `Instructions`; ordering of `Instructions`; CODEX instruction discovery and merge behavior | Still used in this repo, but not treated as a top-level ontology bucket. Choose the replacement that names the actual mechanism rather than using `precedence` as a general runtime term. |
| `Router` | `Route`; `Agent`; `Deterministic controller`; `dispatcher`; `selector`; `orchestrator` | Still used in this repo as a local label, but not treated as a separate ontology class. Use the narrower role term only when that narrower role is actually intended. |
| `Resolve` | determine; identify; `Route`; determine the applicable `Prompt`; determine the applicable profile | Useful as an operation name, but not as a top-level runtime concept. Use it for a determination step, not for a runtime entity or ontology bucket. |
| `Override` | higher-priority `Instructions`; superseding file; replace the default; supersede broader `Instructions`; CODEX override file | Reserve the product-specific sense for CODEX's per-directory override file. Otherwise explain the exact mechanism instead of using `override` as a broad local runtime term. |
| `Activate` | `Route`; load; make applicable; `Handoff` / `Transfer`; select | Still used in the repo, but too broad. Pick the term that names the concrete mechanism actually happening. |
| `Activation` | loading `Instructions`; route choice; selection; `Handoff` / `Transfer`; `guidance` becoming applicable; scope change | Still used in the repo, but it conflates several different runtime ideas. Prefer wording that names the concrete mechanism or state change. |
| `Govern` | own the task; apply `Instructions`; determine the active `Instructions`; `Orchestration` | Used in the repo, but usually better replaced with a more concrete statement about ownership or applicable instructions. |
| `Attach` | extend; add local guidance; apply at a named extension point; supplement the current scope | Mostly appears in transitional planning language. Keep it only if the rewrite introduces explicit extension points; otherwise prefer more concrete wording. |
| `Composition` | simultaneous applicability; overlapping `Instructions`; multi-step `Workflow`; reusable prompt combination | Too broad as a standalone runtime term. Explain whether you mean overlapping applicable instructions, workflow structure, or authoring-time prompt reuse. |
| `Shared Activation` | shared `Route`; shared selection; shared `Instructions` becoming applicable | A local runtime label, not a common ontology term. Prefer wording that names the concrete shared-library step or state change. |
| `Bootstrap Routing` | initial `Route`; initial dispatch; first-step route choice | A repo-local control-flow phase, not a general ontology bucket. Use plain language for the initial route into the relevant branch. |
| `Bootstrap Prompt` | initial `Prompt`; bootstrap request; initial user request | Better treated as plain language for the first prompt or request that triggers the intended route. |
| `Authoring Prompt` | `Prompt`; writing-focused `Prompt`; authoring `Task` guidance | A useful functional description, but not a separate runtime ontology term. Use it only when the writing-focused role matters. |
| `Review Prompt` | `Prompt`; review `Task`; review `Workflow` | Better treated as a prompt role or workflow role than as a top-level runtime concept. |
| `Validation Prompt` | `Prompt`; validation `Task`; validation `Workflow` | Better treated as a prompt role or workflow role than as a top-level runtime concept. |
| `Routing Prompt` | `Prompt`; `Route`; `dispatcher`; `selector`; `orchestrator` | Too local as a canonical category. Use the narrower routing or coordination term that matches the actual behavior. |
| `Layer` | scope; source; place where `Instructions` or `Context` are introduced | Better treated as plain architectural language than as a common runtime ontology term. Name the specific scope or source when possible. |
| `Inheritance` | reuse; refinement; narrower `Instructions`; derived prompt | An authoring-time relation, not a core runtime term. Prefer concrete language about reuse or refinement. |
| `Review Lens` | review criterion; evaluation dimension; review angle | A review-structure term, not a runtime ontology term. Prefer plain language unless a stable local review rubric really needs the label. |
| `Select` | bounded choice; choose one option; `selector` | Better treated as an action or supporting role label than as a top-level ontology term. |
| `Hierarchy` | scope ordering; source ordering; `authority`; `Route` structure; `Workflow` structure | Too broad as a single runtime term. Explain whether you mean document/source ordering, authority among `Instructions`, or control-flow structure. |
| `Control Flow` | `Workflow`; `Route`; `Handoff` / `Transfer`; `Orchestration` | Better replaced by the more specific runtime mechanism actually being described. |
| `Entrypoint` | initial `Route`; directly user-addressable `Prompt`; entry document; starting path | Useful as plain language, but not a separate runtime ontology bucket. Use the concrete entry mechanism that actually applies. |
| `Bundle` | grouped prompts; internal `Workflow`; reusable review set | A local packaging term, not a common runtime concept. Prefer wording that says whether the grouping is a workflow, a reusable set, or an internal review path. |
| `Component` | internal prompt; reusable prompt; internal workflow step | Too broad on its own. Name the concrete prompt or workflow role instead. |
| `Composite` | multi-step `Workflow`; coordinating `Prompt`; synthesized output | A local shorthand, not a core runtime term. Prefer wording that says whether the item coordinates a workflow, combines outputs, or both. |
| `Shared Validation Family` | shared validation prompts; shared review library; validation `Workflow`s | A repo-local organizational label, not a general runtime term. Use it only for the shared validation collection itself, not as a runtime category. |

## What Codex Does

Codex has built-in instruction discovery behavior. It can load guidance from your Codex home directory and from repo-local instruction files. In project scope, it starts at the project root, walks down to the current working directory, checks `AGENTS.override.md` first, then `AGENTS.md`, then any configured fallback filenames, includes at most one file per directory, and skips empty files. Files closer to the current working directory override earlier guidance because they appear later in the merged instruction chain. Codex stops searching once it reaches the current directory. [\[1\]](#ref-1)[\[2\]](#ref-2)[\[3\]](#ref-3)

Codex also injects each discovered instruction file near the top of the conversation history as its own user-role message, in root-to-leaf order. The global file comes first, then the repo root, then deeper directories. [\[2\]](#ref-2)

For overall guidance, the standard Codex location is still Codex home, where Codex checks `AGENTS.override.md` before `AGENTS.md`. If you want that guidance to live somewhere else, the documented Codex mechanism is to set `CODEX_HOME` before launch so Codex uses a different home directory. [\[1\]](#ref-1)[\[2\]](#ref-2) If changing `CODEX_HOME` is inconvenient, a practical local workaround is to keep Codex on the default `~/.codex` path and symlink `~/.codex/AGENTS.md` to the real file you want to maintain elsewhere. That symlink approach is a filesystem convenience, not a Codex-specific feature.

Example hierarchy:

```text
~/.codex/AGENTS.md
<repo>/AGENTS.md
<repo>/services/AGENTS.md
<repo>/services/payments/AGENTS.override.md
<repo>/services/payments/AGENTS.md
<repo>/services/payments/src/
```

If Codex is started from `<repo>/services/payments/src/`, the loaded instruction order is:

1. `~/.codex/AGENTS.md`
2. `<repo>/AGENTS.md`
3. `<repo>/services/AGENTS.md`
4. `<repo>/services/payments/AGENTS.override.md`

In that case, `<repo>/services/payments/AGENTS.md` is ignored because the override file in the same directory takes priority. [\[1\]](#ref-1)

These behaviors are Codex features. They are not defined by `astro-agents`. [\[1\]](#ref-1)[\[2\]](#ref-2)[\[3\]](#ref-3)

## How Models Handle Conflicting Instructions

As a practical summary of the model behavior described in the sources below, modern models do not behave like a strict symbolic conflict resolver. [\[11\]](#ref-11)[\[12\]](#ref-12)[\[13\]](#ref-13)[\[14\]](#ref-14)[\[15\]](#ref-15)[\[16\]](#ref-16)

1. Models assign different levels of authority to instructions. Higher-authority instructions override lower-authority ones. [\[12\]](#ref-12)[\[13\]](#ref-13)
2. Subject to that authority ordering, they try to follow all compatible instructions at once. Overlapping instructions usually get merged into one behavior rather than one clean winner being selected. [\[13\]](#ref-13)
3. Within the same authority level, later instructions can supersede earlier ones when they contradict, override, or make them irrelevant. Outside those cleaner cases, ambiguous or poorly structured prompts can still produce heuristic behavior, so clarity, structure, and example design still matter. [\[13\]](#ref-13)[\[14\]](#ref-14)
4. Quoted text, tool outputs, and other untrusted content do not have instruction authority by default unless higher-authority instructions explicitly delegate authority to them. [\[13\]](#ref-13)
5. Contradictions usually degrade performance instead of producing a clean winner. OpenAI's GPT-5 prompting guide explicitly says contradictory or vague prompts can hurt performance because the model may spend effort trying to reconcile them rather than simply picking one. [\[14\]](#ref-14)
6. Other model vendors describe the same failure mode in prompt-design terms. Anthropic advises that if a prompt would confuse a minimally informed colleague, it will likely confuse the model, and Google's prompt-design guidance explicitly calls out conflicting instructions, conflicting examples, and conflicting internal references as prompt problems. [\[15\]](#ref-15)[\[16\]](#ref-16)

The practical implication for this repo is an inference from those sources: Codex instruction discovery and merge behavior is a real runtime mechanism, while repo-local precedence is mostly design intent unless the prompt surface makes the choice explicit through direct routing, explicit handoff, explicit local customization boundaries, explicit conflict language, or clarifying-question behavior. [\[1\]](#ref-1)[\[13\]](#ref-13)[\[14\]](#ref-14)

## Repo Control-Flow Example

For a full review requested in a repo like `girmos-aosims`, the Codex part is only instruction discovery and merge behavior: Codex loads the applicable `AGENTS.md` files from the repo root down to the current directory according to its normal search rules. If the repo's own `AGENTS.md` then points the agent to `astro-agents/AGENTS.md`, that cross-repo step is no longer Codex discovery. It is a repo-local `Route` into the shared prompt library. From there, the remaining behavior is repo control flow layered on top of Codex: the system may `Select` the appropriate review `Workflow`, perform a `Handoff` to the prompt that should own the `Task`, determine any applicable documentation surface profile, and apply any explicitly named local validation step. Those later steps are repo conventions, not built-in Codex behavior. [\[1\]](#ref-1)[\[2\]](#ref-2)[\[3\]](#ref-3)

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

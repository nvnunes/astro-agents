# Sharding And Delegated Execution

Sharding moves a unit into a separate plan file. Delegation assigns that file
to a fresh Task. Sharding is required for delegation but does not authorize
it.

Shard only when a substantial, coherent unit will be easier to plan or execute
separately. Headings and file length are not reasons by themselves. Obtain
approval before adding each sharding level, and record the reason in the parent
plan.

Use this structure only as deeply as justified:

```text
my-plan.md
my-plan/
  phase-01.md
  phase-01/
    part-a.md
    part-a/
      task-01.md
```

For each child plan:

- Write directly to its executor. Use imperative instructions rather than
  referring to the executing Task in the third person.
- Include only its assigned work and the dependencies needed to execute it. Do
  not identify, summarize, sequence, or exclude other phases, parts, or plan tasks.
  State necessary boundaries directly, and identify dependencies by their
  required artifact or outcome rather than by main-plan topology.
- Identify it and its execution order in the parent plan.
- Make it executable without the parent plan. Include its work, decisions,
  dependencies, checks, agentic reviews, selected commit policy, and stopping
  conditions.
- Preserve identifiers and repair affected links when extracting it.

Do not create a level below plan tasks.

Consider delegation only when the work presents a meaningful risk of context
compaction. With approval, identify each delegated child plan in the main agent plan,
and instruct the main agent to assign it to a fresh Task. A link alone is
not a delegation instruction.

Plan depth does not define agent depth. The main agent starts every delegated
Task directly, including at deeper approved sharding levels. Each Task starts
its own independent reviewer subagents.

For each delegated unit, the main agent plan states:

- its purpose and expected outcome;
- its order and dependencies;
- shared decisions needed to orchestrate it; and
- the condition for completing the main agent plan's scope.

Treat each child plan as one execution unit. State only what the main agent
needs to dispatch it, recognize its handoff, and continue. Keep the child's
internal actions, checks, reviews, references, and implementation decisions
exclusively in the child plan. A self-contained child plan does not justify duplicating its
contents in the main agent plan: the main agent plan owns relationships between
units, and each child plan owns execution within its unit.

Give delegated Tasks their own execution records in their child plans. In the
main agent plan, track each delegated unit's aggregate state using the same
phase, part, and plan task identifiers, without duplicating its internal progress.

State an exact continuation route and stopping point only when work may continue
after a Task blocks or fails. The main agent plan must support orchestration
without opening the child plan.

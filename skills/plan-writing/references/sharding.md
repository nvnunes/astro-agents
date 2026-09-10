# Sharding And Delegated Execution

Sharding moves a unit into a separate plan file. Delegation assigns that file
to a fresh sub-agent. Sharding is required for delegation but does not authorize
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

- Identify it and its execution order in the parent plan.
- Make it executable without the parent plan. Include its work, decisions,
  dependencies, checks, required reviews, selected commit policy, and stopping
  conditions.
- Keep its progress record in that file.
- Preserve identifiers and repair affected links when extracting it.

Do not create a level below Tasks.

Consider delegation only when the work presents a meaningful risk of context
compaction. With approval, instruct a fresh sub-agent to execute the named child
plan and briefly explain why a fresh context is useful. In the child plan,
state that its executing agent has a parent agent. A link alone is not a
delegation instruction.

For each delegated unit, the parent plan states:

- its purpose and expected outcome;
- its order and dependencies;
- shared decisions needed to orchestrate it; and
- the condition for completing the parent scope.

State an exact continuation route and stopping point only when work may continue
after a child blocks or fails. The parent plan must support orchestration
without opening the child plan.

Direct and delegated work may share one serial execution order. Nested
delegation requires an explicit assignment at each level.

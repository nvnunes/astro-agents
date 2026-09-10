# Sharding And Delegated Execution

Sharding moves a unit into a separate plan file. Delegation assigns that file
to a fresh subagent. Sharding is required for delegation but does not authorize
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
- Preserve identifiers and repair affected links when extracting it.

Do not create a level below Tasks.

Consider delegation only when the work presents a meaningful risk of context
compaction. With approval, identify each subagent plan in the main agent plan,
and instruct the main agent to assign it to a fresh subagent. A link alone is
not a delegation instruction.

Plan depth does not define agent depth. The main agent starts every subagent
directly, including when a subagent plan occurs at a deeper approved sharding
level.

For each delegated unit, the main agent plan states:

- its purpose and expected outcome;
- its order and dependencies;
- shared decisions needed to orchestrate it; and
- the condition for completing the main agent plan's scope.

Do not add separate execution records to child plans. In the main agent plan,
initialize corresponding execution-record entries with the same Phase, Part,
and Task identifiers used for the child units.

State an exact continuation route and stopping point only when work may continue
after a subagent blocks or fails. The main agent plan must support orchestration
without opening the subagent plan.

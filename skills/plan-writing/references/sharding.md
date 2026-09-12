# Sharding And Delegated Execution

Sharding moves a unit into a separate plan file. Delegation assigns a phase
plan to a fresh Task. One Task can execute a sharded plan; sharding does not
authorize delegation.

## Organize Plan Files

Shard when a substantial, coherent unit is easier to plan or execute in a
separate file. Headings and document length alone do not justify sharding.
Obtain approval for the proposed file structure and record the reason in the
parent plan.

For example:

```text
my-plan.md
my-plan/
  phase-01.md
  phase-02.md
```

For each child plan:

- Write directly to its executor using imperative instructions.
- Include its work, decisions, dependencies, checks, reviews, selected commit
  policy, and stopping conditions. State necessary boundaries directly.
- Identify it and its execution order in the parent plan.
- Keep detailed execution steps in the child plan and relationships between
  units in the parent plan. Reference shared requirements rather than copying
  them unless a delegated Task needs a self-contained instruction.
- Preserve identifiers and repair affected links when extracting it.

For single-Task execution, make transitions between files explicit. The same
Task reads and executes each child plan in the stated order and maintains its
execution record. Track aggregate progress in the parent without duplicating
child-plan detail.

## Delegate Phases

Execute ordinary plans in one Task. For large plans with well-separated phases,
use an explicitly authorized coordinating Task and one executing Task per phase.
Choose phase boundaries that provide stable contracts and verifiable handoffs;
context pressure or file boundaries alone do not justify delegation.

The coordinating Task starts each authorized phase Task directly. Each phase
Task owns design, implementation-plan refinement, implementation, checks, and
corrections. It uses subagents only for independent review. Parts and numbered
plan tasks remain within that phase Task, regardless of file organization.

Make each delegated phase plan executable without the parent plan. Include only
its assigned work and required inputs; describe dependencies by their artifact
or outcome rather than by parent-plan topology.

For each delegated phase, the coordinating plan states:

- its purpose and expected outcome;
- its order and dependencies;
- shared decisions needed to orchestrate it; and
- the condition for completing the coordinating plan's scope.

Treat each delegated phase as one execution unit. State only what the
coordinating Task needs to dispatch it, recognize its handoff, and continue.
Keep the phase's internal actions and decisions in its own plan.

Give each phase Task its own execution record in its plan. In the coordinating
plan, track each phase's aggregate state without duplicating internal progress.

State an exact continuation route and stopping point only when work may continue
after a Task blocks or fails. The coordinating plan must support orchestration
without opening the delegated phase plan.

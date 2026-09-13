# Plan

## Purpose
Write executable plans, using progressive planning where design is still needed.
Execution and routine progress updates belong to `$plan-execution`.
Inherit prose discipline from `skills/technical-writing/references/base.md`.

## Scope
- Include a plan task, constraint, or note only when it serves the requested outcome, a necessary dependency, an explicit requirement, or a concrete risk supported by inspected evidence.
- Make the scoped work executable; completeness does not require cataloguing related possibilities.
- Prefer existing interfaces, tools, and validation workflows. Reuse does not imply redesign, generalization, or migration of existing consumers.
- Limit supporting infrastructure changes to what the requested work requires.
- State what completes the requested work. Treat downstream consequences as decisions, not automatic extensions; retain deferred work only when requested or needed to resume the scoped work.

## Structure
- Use the shallowest hierarchy that makes execution clear. Use plan tasks as
  the default top-level work units and number them: `Task 1`, `Task 2`.
  Distinguish a numbered plan task from a Codex Task, which is a separate
  user-facing conversation.
- Group related plan tasks into lettered Parts when the grouping improves
  ownership, sequencing, or tracking: `Part A > Task A.1`. Do not add Parts to
  a short sequence that is already clear.
- Introduce numbered Phases only for meaningful stages or cleanly separable
  work with a distinct outcome and a dependency boundary, gate, or handoff.
  Plan size alone does not justify Phases. State each Phase's outcome, required
  inputs, and conditions for proceeding where relevant. A plan with Phases may
  optionally move a complete Phase into its own plan file; plans without Phases
  remain in one file.
- A Phase may contain plan tasks directly or use lettered Parts for additional
  grouping: `Phase 2 > Task 2.1` or `Phase 2 > Part 2.C > Task 2.C.1`. Plan
  tasks use a number as their final identifier component and may be sequential
  or independent.
- Use Title Case for Phase, Part, and Task headings.
- Use bullets for discrete requirements, outputs, and checks, numbered lists for ordered actions, and paragraphs for explanation.
- Use only sections with useful content. A small plan may be a short sequence of actions; uncertainty about plan type does not justify a heavier template.
- State each requirement once. After orientation, present work in execution
  order. Make each directly executed unit's owner, prerequisites, result,
  gates, and next transition clear without imposing a fixed template. In a
  main agent plan, treat a delegated phase plan as one execution unit whose
  visible result is its handoff.
- When a plan uses several agents, prefer one ownership table showing scope,
  role, model and reasoning effort where specified, and continuity
  responsibilities. Refer to those roles in local steps rather than repeating
  assignments. For simple arrangements, a short list is sufficient.
- Treat dispatch, evidence collection, documentation, checks, reviews,
  approvals, commits, stopping conditions, and continuation decisions as
  ordered actions. Place them at the steps they govern.
- Represent substantive completion work as an ordered work unit; state
  completion only as the resulting condition.
- Put each consequential decision in every plan file whose agent needs it, or
  link directly to its durable source. Do not build a separate dependency
  registry.
- Mark unresolved choices beside the work they affect. Distinguish choices that
  block progress from details that can be resolved during implementation.
- Give every independently executed plan one `## Execution Record`. Its position does not
  affect maintenance; usually place it after brief orientation and before the
  detailed work.
- Format the record as an expandable list, not a table. Identify each entry by
  its plan identifier and title, and initialize it with only an indented
  `State:` point set to `not started`. Organize top-level entries by the
  highest-level work units the plan actually uses: plan tasks for a simple
  plan, Parts for grouped work, or Phases for staged work. Nest lower-level
  units only when separately executed or needing distinct state. Do not add
  empty placeholders for possible later information.

## Execution Detail
- Use concrete action verbs and identify the owner and affected artifacts or
  behavior. In a main agent plan, an unqualified imperative assigns work to the
  main agent; name the delegated Task and linked plan whenever work is
  delegated.
- When the main agent is intended to orchestrate delegated work, limit its
  actions to coordination and any review or commit the plan assigns to it
  directly.
- In plan prose, prefer `review and approval`. Use `user` when the actor must be
  distinguished from an agentic reviewer; do not use `human` as a role label.
- Preserve the inputs, dependencies, and acceptance criteria needed to implement
  and verify the change. Where interpretation matters, define concrete examples
  before implementation with valid inputs, expected outcomes, and preservation
  requirements that distinguish correct behavior from incomplete implementations.
- When design precedes implementation, have the executing Task define acceptance
  gates during design, build and run the checks during implementation, and
  confirm that they demonstrate the agreed behavior.
- For migrations, inspect representative existing source data read-only before
  settling the design. Check the assumptions that determine matching and
  preservation, and identify ambiguous or unsupported cases before broad
  implementation. Use those cases to resolve the migration contract.
- Distinguish agreed work, provisional choices, and decisions still needed. Do not invent missing decisions.
- Define the deliverable needed to continue: resolved implementation decisions
  from design; changes, verification evidence, and remaining gaps from
  implementation; questions, comparisons, evidence, and decision points from
  investigations or reviews. Reference existing artifacts in handoffs.
- Preserve background needed to understand or resume the work when it cannot be readily recovered from existing code, artifacts, or linked documentation. Examples include non-obvious rationale, rejected approaches, scientific assumptions, and recorded decisions.
- Place that background beside the work it informs. If it is too substantial
  for the plan, put it in the owning concept or source-of-truth document and
  link to it. Background does not itself add implementation scope.

## Progressive Planning

Use progressive planning when the outcome and boundaries are understood but the
design is not developed enough to define implementation work reliably.

Write the initial plan around clearly distinct areas of work. State the concept,
required outcomes, known constraints, preservation guarantees, and dependencies
as context. Keep implementation structure provisional.

Include the following work in execution order:

1. **Establish the design.** Assign any audits or investigation needed to resolve
   scope, behavior, interfaces, and consequential design choices. Record the
   design and apply any required user-review checkpoint.
2. **Develop the implementation plan.** Have the executing Task use
   plan-writing to incorporate the completed design into the plan. Define
   cohesive, independently verifiable work units, their dependencies, acceptance
   gates, and integration responsibilities. Add or revise implementation work
   units at the shallowest useful hierarchy within the approved scope. Apply
   any required user-review checkpoint.
3. **Execute the refined plan.** Start dependent implementation only when its
   work units are executable, ownership and gates are specified, and blocking
   decisions are resolved.

Authorize implementation-plan refinement explicitly in the initial plan. This
permits changing the provisional work breakdown within the agreed scope; it does
not authorize new behavior, weaker guarantees, or additional delegation beyond
the stated authority.

Choose implementation units and acceptance boundaries from the completed design
within the executing Task. For each unit, establish what is already settled and
what must be understood and changed together. For each substantial unit, specify
the smallest working result to establish and verify before broadening
implementation, including its prerequisites. Make this an execution milestone,
not merely an example of eventual acceptance. Reconsider units that require understanding
most of the subsystem before producing any accepted result; component names or
lists of remaining callers do not establish useful work boundaries.

The refinement step must update the affected implementation tasks themselves. A
design handoff or execution-record update alone is insufficient.

Use only the stages the work needs. A settled design may need implementation
planning without further design work; a sufficiently detailed implementation
plan may proceed directly to execution.

## Conditional Guidance

- Read `references/sharding.md` only when creating, changing, or evaluating
  plan sharding or delegated execution.
- Read `references/execution-policy.md` when reviewing or finalizing an
  implementation plan, or when choosing or changing agent assignments, checks,
  reviews, or commit policy.

## Revision
- Preserve explicit decisions, necessary implementation detail, and unresolved questions that affect execution; revise wording, length, and organization as needed.
- Preserve stable work-unit names, identifiers, and numbering unless the
  requested change warrants adjustment.
- Consolidate repeated requirements under one owner and remove speculative additions.
- Keep provisional or deferred work distinct from committed deliverables.

## Final Scope Check
Before delivering:

- Remove plan tasks that fail the scope test above.
- Remove hypothetical safeguards, unnecessary generalization, and generic
  execution mechanics already supplied by selected skills. Retain only
  task-specific choices and exceptions.
- Read the plan from the first authorized action to its final stopping point.
  For every imperative, confirm its owner; for every gate, confirm what follows
  success and failure; and for every transition, confirm the next work is
  defined and authorized. Remove displaced flow instructions, ambiguous owners,
  and substantive work hidden in completion prose.
- Check that the remaining plan still contains the technical detail needed to
  execute, verify, and resume the requested work. Scope discipline is not a
  word limit.

For each substantial implementation assignment, verify that:

- Its first working milestone has clear prerequisites and must be verified
  before broader implementation proceeds.
- Its scope is justified by shared decisions or invariants, rather than a
  component name or "remaining work."
- Its result provides a stable basis and relevant evidence for subsequent work;
  integration and correction responsibilities are explicit.

## Output
- Return the plan or edited artifact directly unless explanation is requested.
- For a review, prioritize concrete gaps in design, execution, ownership, and
  verification. Explain their likely consequence and the smallest useful
  correction. Check referenced contracts before calling a requirement missing;
  distinguish unresolved requirements from optional improvements or proposed
  scope changes.

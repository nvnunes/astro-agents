# Plan

## Purpose
Design working plans through optional document sharding and explicit delegated
execution. Execution and routine progress updates belong to `$plan-execution`.
Inherit prose discipline from `skills/technical-writing/references/base.md`.

## Scope
- Include a plan task, constraint, or note only when it serves the requested outcome, a necessary dependency, an explicit requirement, or a concrete risk supported by inspected evidence.
- Make the scoped work executable; completeness does not require cataloguing related possibilities.
- Prefer existing interfaces, tools, and validation workflows. Reuse does not imply redesign, generalization, or migration of existing consumers.
- Limit supporting infrastructure changes to what the requested work requires.
- State what completes the requested work. Treat downstream consequences as decisions, not automatic extensions; retain deferred work only when requested or needed to resume the scoped work.

## Structure
- Use Title Case for Phase, Part, and Task headings. Distinguish a numbered
  plan task from a Codex Task, which is a separate user-facing conversation.
- Use numbered phases for work with meaningful stages or dependency boundaries. State each phase's action, output, and conditions for proceeding where relevant.
- Break complex phases into lettered parts that group related work: `Phase 2`, `Part 2.C`.
- Subdivide a large, coherent part into named, numbered plan tasks when individual scope, outputs, or tracking help: `Phase 2 > Part 2.C > Task 2.C.1`. Plan tasks may be sequential or independent. Keep this third level optional.
- Use bullets for discrete requirements, outputs, and checks, numbered lists for ordered actions, and paragraphs for explanation.
- Use only sections with useful content. A small plan may be a short sequence of actions; uncertainty about plan type does not justify a heavier template.
- State each requirement once. After orientation, present work in execution
  order. Make each directly executed unit's owner, prerequisites, result,
  gates, and next transition clear without imposing a fixed template. In a
  main agent plan, treat a delegated child plan as one execution unit whose
  visible result is its handoff.
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
  `State:` point set to `not started`. Organize top-level entries by Phase and
  nest parts and plan tasks beneath them only when separately executed or needing
  distinct state. Use the plan's highest-level work units when those levels do
  not apply. Do not add empty placeholders for possible later information.

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
- Preserve technical details needed to implement and verify the change, including relevant inputs, dependencies, and acceptance criteria.
- Distinguish agreed work, provisional choices, and decisions still needed. Do not invent missing decisions.
- For implementation plans, identify changes and verification; for investigations or reviews, identify questions, comparisons, evidence, and decision points.
- Preserve background needed to understand or resume the work when it cannot be readily recovered from existing code, artifacts, or linked documentation. Examples include non-obvious rationale, rejected approaches, scientific assumptions, and recorded decisions.
- Place that background beside the work it informs. If it is too substantial
  for the plan, put it in the owning concept or source-of-truth document and
  link to it. Background does not itself add implementation scope.

## Progressive Planning

When a plan contains consequential design choices that require a decision, suggest developing it progressively:

- Capture known scope as lightweight phase, part, or plan task stubs. Mark unresolved
  choices without inventing detail, and stop the executable flow before any
  unit whose ownership, scope, dependencies, or policy remains unresolved.
- Ask for one consequential decision at a time. Resolve routine implementation details using available evidence and judgment.
- Apply each answer to the plan before asking the next question.
- If decisions belong in a design document, include that documentation work at the appropriate plan level. Keep the agreed details there until transferred, then link to the document.

Draft directly when this decision process is unnecessary. Develop stubs before
considering extraction.

## Conditional Guidance

- Read `references/sharding.md` only when creating, changing, or evaluating
  plan sharding or delegated execution.
- Read `references/execution-policy.md` before finalizing an implementation
  plan or when changing its checks, reviews, or commit policy.

## Revision
- Preserve explicit decisions, necessary implementation detail, and unresolved questions that affect execution; revise wording, length, and organization as needed.
- Preserve stable phase names and numbering unless the requested change warrants adjustment.
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

## Output
- Return the plan or edited artifact directly unless explanation is requested.
- For a review, identify unnecessary scope and gaps in execution, evidence, or decision clarity before recommending edits.

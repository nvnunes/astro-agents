# Plan

## Purpose
Design working plans through optional document sharding and explicit delegated
execution. Execution and routine progress updates belong to `$plan-execution`.
Inherit prose discipline from `skills/technical-writing/references/base.md`.

## Scope
- Include a task, constraint, or note only when it serves the requested outcome, a necessary dependency, an explicit requirement, or a concrete risk supported by inspected evidence.
- Make the scoped work executable; completeness does not require cataloguing related possibilities.
- Prefer existing interfaces, tools, and validation workflows. Reuse does not imply redesign, generalization, or migration of existing consumers.
- Limit supporting infrastructure changes to what the current task requires.
- State what completes the requested work. Treat downstream consequences as decisions, not automatic extensions; retain deferred work only when requested or needed to resume the scoped work.

## Structure
- Use Title Case for Phase, Part, and Task headings.
- Use numbered phases for work with meaningful stages or dependency boundaries. State each phase's action, output, and conditions for proceeding where relevant.
- Break complex phases into lettered parts that group related work: `Phase 2`, `Part 2.C`.
- Subdivide a large, coherent part into named, numbered tasks when individual scope, outputs, or tracking help: `Phase 2 > Part 2.C > Task 2.C.1`. Tasks may be sequential or independent. Keep this third level optional.
- Use bullets for discrete requirements, outputs, and checks, numbered lists for ordered actions, and paragraphs for explanation.
- Use only sections with useful content. A small plan may be a short sequence of actions; uncertainty about plan type does not justify a heavier template.
- State each requirement once. Place evidence collection, validation, and documentation at the steps that produce or need them.
- Put each consequential decision in every plan file whose agent needs it, or
  link directly to its durable source. Do not build a separate dependency
  registry.
- Mark unresolved choices beside the work they affect. Distinguish choices that
  block progress from details that can be resolved during implementation.
- Give every main agent plan a top-level `## Execution Record`. Organize its
  top-level entries by Phase and nest Parts and Tasks beneath them. Use the
  plan's highest-level work units when those levels do not apply. Initialize
  one `not started` entry for each Phase or highest-level work unit, identified
  by its plan identifier and title. Add nested Part and Task entries only when
  they are executed separately or need distinct state.

## Execution Detail
- Use concrete action verbs and identify the affected artifacts or behavior.
- Preserve technical details needed to implement and verify the change, including relevant inputs, dependencies, and acceptance criteria.
- Distinguish agreed work, provisional choices, and decisions still needed. Do not invent missing decisions.
- For implementation plans, identify changes and verification; for investigations or reviews, identify questions, comparisons, evidence, and decision points.
- Preserve background needed to understand or resume the work when it cannot be readily recovered from existing code, artifacts, or linked documentation. Examples include non-obvious rationale, rejected approaches, scientific assumptions, and recorded decisions.
- Place that background beside the work it informs. If it is too substantial
  for the plan, put it in the owning concept or source-of-truth document and
  link to it. Background does not itself add implementation scope.

## Progressive Planning

When a plan contains consequential design choices that require a decision, suggest developing it progressively:

- Capture known scope as lightweight phase, part, or task stubs. Mark unresolved choices without inventing detail.
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

- Remove tasks that fail the scope test above.
- Remove hypothetical safeguards, unnecessary generalization, and repeated process instructions already supplied by applicable project guidance.
- Check that the remaining plan still contains the technical detail needed to execute, verify, and resume the requested work. Scope discipline is not a word limit.

## Output
- Return the plan or edited artifact directly unless explanation is requested.
- For a review, identify unnecessary scope and gaps in execution, evidence, or decision clarity before recommending edits.

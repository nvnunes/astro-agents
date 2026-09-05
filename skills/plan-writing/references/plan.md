# Plan

## Purpose
Write working plans for execution, re-entry, and decision continuity. Inherit prose discipline from `skills/technical-writing/references/base.md`.

## Scope
- Include a task, constraint, or note only when it serves the requested outcome, a necessary dependency, an explicit user requirement, or a concrete risk supported by inspected evidence.
- Make the scoped work executable; completeness does not require cataloguing related possibilities.
- Prefer existing interfaces, tools, and validation workflows. Reuse does not imply redesign, generalization, or migration of earlier users.
- Limit supporting infrastructure changes to what the current task requires.
- State what completes the requested work. Treat downstream consequences as decisions, not automatic extensions; retain deferred work only when requested or needed to resume the scoped work.

## Structure
- Use Title Case for Phase, Part, and Task headings.
- Use numbered phases for work with meaningful stages or dependency boundaries. State each phase's action, output, and conditions for proceeding where relevant.
- Break complex phases into lettered parts that group related work: `Phase 2`, `Part 2.C`.
- Subdivide a large, coherent part into named, numbered tasks when individual scope, outputs, or tracking help: `Phase 2 > Part 2.C > Task 2.C.1`. Tasks may be sequential or independent. Keep this third level optional.
- Prefer lists for discrete actions, outputs, and checks when as clear as prose: numbered when order matters, bulleted otherwise. Use paragraphs for connected explanation.
- Use only sections with useful content. A small plan may be a short sequence of actions; uncertainty about plan type does not justify a heavier template.
- State each requirement once. Place evidence collection, validation, and documentation at the steps that produce or need them.
- Attach notes and unresolved choices to the work they affect. Distinguish choices that block progress from details that can be resolved during implementation.

## Execution Detail
- Use concrete action verbs and identify the affected artifacts or behavior.
- Preserve technical details needed to implement and verify the change, including relevant inputs, dependencies, and acceptance criteria.
- Distinguish agreed work, provisional choices, and decisions still needed. Do not invent missing user decisions.
- For implementation plans, identify changes and verification; for investigations or reviews, identify questions, comparisons, evidence, and decision points.
- Preserve background needed to understand or resume the work when it cannot be readily recovered from existing code, artifacts, or linked documentation. Examples include non-obvious rationale, rejected approaches, scientific assumptions, and user decisions.
- Place that background beside the work it informs; use a linked companion note when substantial. Background does not itself add implementation scope.

## Progressive Planning

When a plan contains consequential design choices that need user input, suggest developing it progressively:

- Capture known scope as lightweight phase, part, or task stubs. Mark unresolved choices without inventing detail.
- Ask for one consequential decision at a time. Resolve routine implementation details using available evidence and judgment.
- On receiving an answer, capture the decision and useful rationale in the plan before asking the next question.
- Once the necessary choices are resolved, integrate them into executable plan prose. Remove temporary decision notes only after their substance is preserved.
- If decisions belong in a design document, include that documentation work at the appropriate plan level. Keep the agreed details there until transferred, then link to the document.

Draft directly when the work does not require this decision process.

## Revision
- Preserve explicit user decisions, necessary implementation detail, and unresolved questions that affect execution.
- Preserve stable phase names and numbering unless the requested change warrants adjustment.
- Remove speculative additions and repetition rather than retaining everything from an earlier draft.
- Keep provisional or deferred work distinct from committed deliverables.

## Final Scope Check
Before delivering:

- Remove tasks that fail the scope test above.
- Remove hypothetical safeguards, unnecessary generalization, and repeated process instructions already supplied by applicable project guidance.
- Check that the remaining plan still contains the technical detail needed to execute, verify, and resume the requested work. Scope discipline is not a word limit.

## Output
- Return the plan or edited artifact directly unless explanation is requested.
- For a review, identify unnecessary scope and gaps in execution, evidence, or decision clarity before recommending edits.

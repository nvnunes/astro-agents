# Plan

## Purpose
Design working plans through optional sharding. Execution, continuation, and
routine progress updates belong to `$plan-execution`. Inherit prose discipline
from `skills/technical-writing/references/base.md`.

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
- Use bullets for discrete requirements, outputs, and checks, numbered lists for ordered actions, and paragraphs for explanation.
- Use only sections with useful content. A small plan may be a short sequence of actions; uncertainty about plan type does not justify a heavier template.
- State each requirement once. Place evidence collection, validation, and documentation at the steps that produce or need them.
- Link consequential decisions to known downstream phase/Part/task consumers.
  Maintain those links as the plan develops or moves, so later reconciliation
  stays bounded. Do not build a separate dependency registry.
- Mark unresolved choices beside the work they affect; link to planning notes for the discussion. Distinguish choices that block progress from details that can be resolved during implementation.

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
- Apply each answer before asking the next question, following Planning Continuity below.
- If decisions belong in a design document, include that documentation work at the appropriate plan level. Keep the agreed details there until transferred, then link to the document.

Draft directly when this decision process is unnecessary. Develop stubs before
considering extraction.

## Planning Continuity

For planning that spans sessions or compactions, keep a compact companion
note. Small edits need no sidecar.

- **Place:** Keep `<plan-stem>-planning-notes.md` beside the active plan or phase, linked near its top. Use a main-plan note only for cross-phase questions; link rather than duplicate them.
- **Maintain:** Record unsettled questions, options, checks, and proposals promptly, linked to affected sections. Put resolved decisions and useful rationale into the plan, then remove their discussion. Agreed deferrals belong in the plan with when and how they will be resolved.
- **Continue:** Retain the current editing location, next action, and essential tools, paths, or non-obvious procedures. Link to existing guidance instead of copying it.
- **Resume:** Read the note, relevant plan section, and direct dependencies; consult history for specific gaps. Use bounded excerpts and diffs. Do not repeat completed reviews solely after compaction or maintain whole-conversation summaries.
- **Close:** Delete the note and its link when nothing remains to carry forward.

## Sharding

Consider extracting substantial, fully developed phases from long plans.
Weigh length against cohesion: centralized design may work better together.
Keep small plans together; ask the human when the tradeoff is unclear. Do not
use a fixed word count or create a file for every stub.

For a sharded plan:

- Start the main document with a linked-phase/status table. Keep its phase
  progress only there and retain undesigned stubs. No prerequisites/count columns.
- Make each phase independently executable: objective, scope, constraints and decisions,
  tasks, dependencies, commands or precise references, checks/reviews, and
  stopping conditions. Use direct authoritative links rather than requiring
  the main-plan body to recover instructions.
- Keep task/Part progress in the phase. Preserve identifiers, replace extracted
  content with links, and repair affected references and shared-constraint links.

## Verification And Handoff

- Choose technical reviews during design: scope, purpose, and milestone. Distinguish iteration checks from completion gates; reference applicable project gates without duplicating them.
- Incorporate reviews completed during planning into the affected instructions. Do not schedule the same review again unless a specific later change will invalidate its conclusions.
- A final integration step contains only work that requires the assembled result. Keep component checks with their owning work.
- For each necessary gate, state what must pass, what failure blocks, and what independent work may continue. Avoid generic review-after-every-step rules.

Place cheap representative checks before expensive work whose approach depends
on consequential assumptions, including output persistence when relevant.
Execution must check current conditions because planning observations can age;
keep these checks scoped, without a separate assumption register.

Record the review/commit policy. Default: complete each Part's required checks,
human review, feedback resolution, then commit before the next Part. Honor
user overrides for less frequent commits or unattended commits without human
review; do not ask repeatedly. Human checkpoints do not add technical reviews.

Hand off authorized work to `$plan-execution`, which owns execution notes and
history. A ready plan is not authorization to implement it.

## Revision
- Preserve explicit user decisions, necessary implementation detail, and unresolved questions that affect execution; revise wording, length, and organization as needed.
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

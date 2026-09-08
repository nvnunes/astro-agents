# Continuation And Progress

## Current Note And History

Create a current note when starting a Part or execution checkpoint; for smaller
work, create it before pausing unfinished work. Start history with the first
outcome worth retaining under the criteria below.

- **Current note:** one per active Part, or per grouped checkpoint spanning
  Parts. Use the phase or whole plan only when it has no Parts or checkpoints.
- **History:** one append-only file per phase, or per plan without phases.

Place both files beside the plan, or its phase document for sharded plans.
Default names:
`phase-NN-part-ID-current.md` and `phase-NN-history.md`.

## Maintain The Files

Update the current note by replacing obsolete state, not appending progress
reports. Put the next action and blockers first. Retain only context needed to
continue: owned work, essential tools, paths, environment and procedures,
applicable check results, and active operation handles. Use tool-owned result
IDs for retrievable detail; do not copy their payloads into the note.

History, not the plan, records execution outcomes: decision rationale,
completed work, checks, and evidence needed to recover from failed approaches.
Record outcomes directly in history because cached detail may expire.
Link details; omit command narration. Group routine outcomes under stable,
descriptive headings such as `Part 2.B — Entry 004 — Interface approved`.
Append corrections; never rewrite entries. Link an outcome from the current
note only when upcoming work needs it; state why instead of repeating details.

## Read Continuation State

Continue from the current note and active plan section. Do not read history
by default. Open it only to resolve a specific missing fact blocking the next
action. Follow an entry link or search for that fact; read only the matching entry.
Never load the whole history or arbitrary tails for orientation. Verify state
that may have changed; leave missing past evidence unknown.

## Finish A Part Or Grouped Checkpoint

1. Mark completion in the existing progress record. In sharded plans, update
   Part status in the phase document and changed phase status in the main
   plan's top table.
2. Save still-needed state in the next current note, if work remains.
3. Append remaining outcomes meeting the history criteria above, without duplication.
4. After saving that content, delete the completed note; do not archive it.

## Changed Decisions

Keep governing decisions and blockers beside affected work in the plan;
history must not be their only owner. Follow a changed decision's forward
links and reconcile affected instructions before dependent execution. Follow
further links only for actual impact. For a missing or stale relevant link,
search within the plan and repair it; do not perform a repository-wide audit.
Broader main-plan edits require a scope, sequencing, or dependency change.

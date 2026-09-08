# Continuation And Progress

## Current Note And History

When useful state must survive sessions or compaction, keep one current note
for the active Part, falling back to the phase or whole plan when needed.
Keep one append-only history per phase, or per plan if it has no phases.
Small tasks need no extra files. Use the project's temporary-work
location, resolve symlinks, and follow existing naming conventions. Defaults
beside sharded phases: `phase-NN-part-ID-current.md` and `phase-NN-history.md`.
Link the current note from the owning plan section or phase document.

Keep only actionable state in the note:

- owned changes, next action, and unresolved findings;
- essential tools, exact paths, working directory/environment, non-obvious
  procedures, and working commands;
- verification evidence and the state it covers;
- active operation handles and direct history references.

Update at meaningful checkpoints. Append short action/outcome records and
artifact links to history under stable unique headings, such as
`Part 2.B — Entry 004`. Append corrections; do not rewrite referenced entries.
Leave unsupported past state unknown rather than deriving it from current state.

On resume, read the current note first and retrieve only needed history
entries. Check current state where it could have changed. Avoid full-history
summaries and transcript duplication.

## Finish A Part

After the applicable completion checkpoint:

1. Carry still-needed state into the next Part note, if needed.
2. Append remaining useful content from the old note to phase history.
3. Verify the writes, repair links, and delete the old note.

The final Part needs no successor note.

## Changed Decisions

Keep governing decisions and blockers beside affected work in the plan;
history must not be their only owner. Follow a changed decision's forward
links and reconcile affected instructions before dependent execution. Follow
further links only for actual impact. For a missing or stale relevant link,
search within the plan and repair it; do not perform a repository-wide audit.
Broader main-plan edits require a scope, sequencing, or dependency change.

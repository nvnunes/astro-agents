# Verification And Completion

## Checks And Technical Reviews

Follow the plan's tests and technical reviews and applicable project gates.
Use focused checks while iterating and broader gates at prescribed milestones;
do not add reviews after every step or merely because work is expensive.

A failed gate blocks dependent work. Distinguish failed assessments from work
not assessed because a prerequisite failed. Surface failed assumptions and
resolve the approach before dependent work; independent work may continue.

Reuse evidence while the covered code, inputs, environment, and scope remain
applicable. Repeat only affected checks or conclusions after relevant changes,
failures, or unresolved findings. Tests and required reviews do not replace
each other.

## Human Review And Commits

By default, finish each Part's required checks, present its changes and
evidence for human review, address feedback, then commit before the next Part.
This avoids reconstructing overlapping edits for later review.

Honor the user's policy, including less frequent commits or unattended commits
without human review. Record overrides in the plan without asking repeatedly.
Human checkpoints are separate from technical reviews. Commit only intended
changes. For work outside version control, retain reviewed artifacts in their
established location; do not create empty commits or relocate them for a commit.

If a current note exists, follow `references/continuation.md` at Part completion.

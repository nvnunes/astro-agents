# Summary Update Operation Instructions

Use this file when updating `<theme>.md` from a specific entry, clearly identified entry changes, or a user-requested summary edit.

This operation keeps `<theme>.md` as a first-class living document.

When editing summary prose, apply `research-log/themes/instructions/file-summary.md`, including its `authoring/writing/project-docs.md` style boundary. Keep updates direct and compressed; do not expand `<theme>.md` to preserve every detail from the entry.

Summary updates can move in two directions:

- Entry-to-summary: an entry changes current understanding, so the affected summary section changes.
- Summary-to-entry: the user asks to revise the summary, and the agent checks whether the proposed summary change remains consistent with the supporting entries.

## Procedure

1. Identify the affected theme.
2. Identify whether the update is entry-to-summary or summary-to-entry.
3. Identify the specific entry, clearly identified entry changes, or summary section driving the update.
4. Identify the affected concept area from `<theme>/index.md`, the relevant entries, and the current `<theme>.md`.
5. Treat existing summary structure, ordering, prose, emphasis, and framing as intentional.
6. Read only the relevant entries, relevant index cards, and affected summary sections unless routing context is insufficient.
7. For entry-to-summary updates, if the entry clearly changes current understanding, update the affected parts of `<theme>.md`.
8. For summary-to-entry updates, check the requested summary change against the linked or otherwise relevant entries before editing.
9. If the requested summary change conflicts with the supporting entry record, warn the user and ask whether to update the entry, revise the summary change, or leave the inconsistency noted.
10. Do not alter entry content to match a summary edit unless the user explicitly approves that entry update.
11. If the summary change is not obvious, ask before editing.
12. Add or preserve clickable entry-ID links for newly retained conclusions, decisions, major caveats, current-versus-historical status markers, and follow-up items.
13. Preserve uncertainty rather than forcing premature conclusions.
14. Update `<theme>/index.md` only if the update changes how entries should be routed, interpreted, or described.
15. Update references or supersession only when the summary update changes how older entries should be understood.

## Files To Consult

- For `<theme>.md` structure, read `research-log/themes/instructions/file-summary.md`.
- For index routing or affected concepts, read `research-log/themes/instructions/file-index.md`.

## Completion

After the update, report what changed in the summary as concise narration, not as a code diff.

Focus on conceptual changes, such as:

- new observations incorporated
- conclusions revised or retained
- questions added, clarified, or resolved
- decisions added or changed
- follow-up or next steps changed
- entry/summary inconsistencies found and how they were handled

# Reorganize Workflow Behavior Cases

These cases exercise the activated CLI-driven Reorganize references.

- A date, slug, or title change edits the heading and summary first, then uses
  `update-entry`; a title-only change expects no filesystem mutation.
- A reorder supplies every old ID once in desired order and applies one
  simultaneous mapping after all Markdown references are edited.
- A complete-log relocation updates known links first and moves only the
  maintained summary/root pair; an entry never moves to another log.
- A no-move evidence, data, or retention rename uses its owning family command,
  not `reorganize transfer`.
- A section move within one document uses Markdown edits only.
- A section move between documents of one entry uses one same-entry evidence
  transfer with explicit document mappings.
- A same-entry document split and document merge remain document operations;
  they neither add nor remove a stable entry.
- A cross-entry section move obtains the source entry's bounded evidence, data,
  and retention inventories, names every selected record and mapping, moves
  Markdown and support files first, and uses one transfer.
- A stable-entry split creates the destination with `log add`, then transfers
  the selected records and completes required reruns.
- A stable-entry merge transfers all records, completes reruns, removes the
  source summary item, and separately removes the empty scaffold.
- Ambiguous selection, unresolved dependencies, stale source use, and failed
  transfer stop without direct registry edits.
- A nonempty source after merge stops before empty-entry removal.

# Capture Operation Instructions

Use this file when the user wants to log, record, keep track, document, save, note, add to the research log, or preserve what happened.

`Capture` is the default operation when the user is adding evidence or preserving work and no stronger summary-update intent is clear.

## Behavior

- Keep the user-facing flow fluid and contextual.
- Create or update the dated entry.
- Update `<theme>/index.md`.
- Preserve enough routing context for a later summary update.
- Preserve supporting materials as part of capture when they are needed to understand or reuse the evidence.
- Add or suggest an `AI Use:` note when AI materially affected what was retained, relied on, or decided; use `research-log/themes/instructions/file-entry.md` for the note format.
- Do not update `<theme>.md` unless the user asks, the change is purely administrative, or the captured entry clearly changes current understanding and a targeted summary update is obvious.
- When a targeted summary update is obvious, use `research-log/themes/instructions/operation-summary-update.md` after capture and report the summary change succinctly.

## New Entry Capture

When the user asks to capture or add material in a new entry, new file, new record, or a recognizable equivalent:

1. Read `<theme>/index.md` and choose relevant concepts plus a concept slug from `## Concepts` and the entry topic.
2. If no existing concept fits, use `research-log/themes/instructions/operation-manage-concepts.md` when the user intent already implies a new concept; otherwise ask before adding a top-level concept.
3. Choose the next stable theme-local entry ID in `e###` form. Do not derive the prefix from the theme, concept, status, version, or topic.
4. Choose a concrete descriptive topic slug from the user's request, captured work, or intended entry purpose.
5. Create `<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/index.md`.
6. Add a new timeline card for the entry in `<theme>/index.md`.
7. Preserve the captured material in the new entry using `research-log/themes/instructions/file-entry.md`.

## Existing Entry Capture

When the user asks to continue, work on, or add to an existing entry, resolve the entry from the entry ID, path, topic, date, or chat context. Use `<theme>/index.md` for ID-to-path resolution.

For large entries, scan existing `##` headings before reading the full entry or choosing where to place new material.

If the reference matches multiple entries, ask before editing. Append to an existing section when it clearly fits; otherwise create a new descriptive `##` section. Update `<theme>/index.md` when the added material changes the entry's routing context.

If an existing entry becomes too large for one `index.md`, recommend splitting it into subentry files such as `e002a.md` and `e002b.md`, but only do that when the user directs or approves the split. Keep the parent entry ID and timeline card, reduce the parent `index.md` to a minimal router with `Parts` descriptions, and list part IDs in the same timeline card rather than as separate top-level entries. Do not add recap or historical prose unless the user asks for it.

## Supporting Materials

Place entry-specific `images/`, `data/`, `outputs/`, or `scripts/` folders near the entry only when adding files there or when `data/manifest.md` is needed.

Do not copy large canonical datasets or project-level input stores into entries by default. Link to stable project paths, external sources, checksums, versions, commits, or generation commands.

Place scripts by reuse scope:

- `<project>/scripts`: canonical reusable project scripts.
- `<theme>/scripts`: theme-specific reusable scripts.
- `<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/scripts`: entry-specific tools.

Use `research-log/themes/instructions/file-data-manifest.md` when an entry depends on external, large, canonical, or move-prone data assets.

When moving scripts into a research-log folder, identify tests that appear to cover only those scripts. Ask whether those tests should stay, move with the script, or be removed. Keep tests that still cover public or canonical project behavior.

## Files To Consult

- For dated entry structure and command format, read `research-log/themes/instructions/file-entry.md`.
- For timeline cards and entry IDs, read `research-log/themes/instructions/file-index.md`.
- For entry-local `data/manifest.md`, read `research-log/themes/instructions/file-data-manifest.md` only when external data or manifest-backed commands are needed.

## Completion

There is no fixed completion-report format for `Capture`. Keep the response concise unless the user needs more detail. If useful, mention what was recorded and where, but do not force a formal summary.

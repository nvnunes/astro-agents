# Manage Concepts Operation Instructions

Use this file when adding, revising, reorganizing, or associating concepts in a theme-document hierarchy.

## Scope

Concepts affect:

- `## Concepts` in `<theme>/index.md`
- summary organization in `<theme>.md`

For `## Concepts` structure, read `skills/research-logging/references/file-index.md`.

## Summary Consistency

After changing concepts, check whether the concept change alters the current summary organization in `<theme>.md`.

If it does, update the affected summary structure through `skills/research-logging/references/operation-summary-update.md`, keeping the edit targeted and preserving existing summary prose where practical.

If it does not, leave `<theme>.md` unchanged and report that the summary was intentionally left unchanged.

## Add Concept

Add a top-level concept only when no existing top-level concept fits.

Otherwise, add a sub-concept under the closest top-level concept.

Do not update `<theme>.md` unless the user asks for a summary update or the concept change clearly requires one. Apply the summary consistency check before finishing.

## Associate Entry

Treat concept association as semantic routing context.

For concept association changes, update the affected index card and, when useful, the entry's `Related:` or body context without changing the entry ID.

If the concept slug used in an entry folder name changes, keep the entry ID stable and apply folder path maintenance: update the folder path, update the entry's `Path:` in `<theme>/index.md`, update any direct path references, update clickable entry links in `<theme>.md`, and search for stale old paths before finishing.

Apply the summary consistency check before finishing.

## Reorganize Concepts

If the change affects only labels, sub-concepts, or secondary associations, update `<theme>/index.md` and affected index cards.

Concept reorganization should not change entry IDs. If a concept slug used in a folder name changes, apply folder path maintenance: update the affected folder path, update `<theme>/index.md`, update any direct path references, update clickable entry links in `<theme>.md`, and search for stale old paths before finishing. Otherwise update only the concept list and relevant index cards.

Apply the summary consistency check before finishing.

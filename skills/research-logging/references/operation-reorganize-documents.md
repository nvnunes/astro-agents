# Reorganize Document Workflows

Use this reference only for an explicitly authorized movement of complete
Markdown sections within one stable entry. The agent owns section boundaries,
prose, links, and document creation or deletion. Use the CLI only when an
evidence record's document association changes.

## Move Within One Document

Move the complete section, preserve its meaning and presentation markers, and
update affected anchors or local links. No registry or Reorganize CLI call is
needed because the document association is unchanged.

## Move Between Documents Of One Entry

1. Resolve the exact source section, destination document, and affected
   evidence IDs. Ask before proceeding if the section boundary is ambiguous.
2. Move the Markdown and update affected links. Leave evidence IDs and markers
   unchanged. Do not edit `evidence.json`.
3. Read only `log reorganize transfer --help`. Use the same entry for
   `--from-entry` and `--to-entry`, select only the affected evidence IDs, and
   provide each changed `--document-map`. Dry-run, then apply once.
4. Stop if a source marker remains, a destination marker is absent or
   duplicated, or the candidate presentation differs. Confirm only the
   selected evidence document associations changed.

## Split One Entry Document

1. Confirm the requested same-entry document boundary. Create canonical
   suffixed documents and distribute complete sections between them.
2. Update the maintained summary and affected local links. Keep support
   material at the entry root unless the researcher authorized another move.
3. Use one same-entry `transfer` invocation for all evidence IDs whose document
   association changed, with an explicit document mapping for each source
   document. Dry-run before applying.
4. Confirm every new document is linked and every moved marker is unique. This
   is not creation of another stable entry.

## Merge Split Documents

1. Choose the authorized surviving document, move complete sections into it,
   and update summary and local links.
2. Use one same-entry `transfer` for all affected evidence IDs and document
   mappings. Dry-run, then apply.
3. Delete the now-empty source document only after the CLI succeeds. Confirm
   the stable entry ID and entry-root support material are unchanged.

Do not use these workflows to repartition scientific meaning, move material
between stable entries, rename registry IDs, or remove active research.


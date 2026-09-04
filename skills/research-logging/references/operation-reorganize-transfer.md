# Reorganize Cross-Entry Transfer Workflows

Use this reference only for an explicitly authorized move between stable
entries in one maintained log. The agent decides the semantic boundary, edits
Markdown, moves selected support files, and names every affected registry
record. One `log reorganize transfer` call then coordinates the authored JSON.
Never inspect or edit those registries directly.

## Prepare One Transfer

1. Resolve the source and destination entries, complete Markdown section, and
   exact evidence IDs, data names, and retention IDs that move. Do not infer a
   record set from proximity or file contents.
2. Decide every changed document, support path, data name, evidence ID, or
   retention ID before editing. Preserve identifiers unless the authorized move
   requires an explicit mapping.
3. Move the Markdown and selected support files, then update markers, summary
   references, recorded-command tokens, and local links. The old associations
   must be absent and destination files and markers present.
4. Read only `log reorganize transfer --help`. Supply the three selector lists
   and only their required mapping pairs. Use `--all` only when every authored
   registry record moves to another stable entry. Dry-run, then apply once.
5. Stop on an unresolved dependency, shared output support, stale source use,
   missing destination, collision, or candidate validation failure. Do not fix
   it by editing JSON.
6. Run every destination command in the returned rerun list through that
   entry's `pyrun`. The workflow is incomplete until those reruns succeed.

## Move A Section Between Stable Entries

Apply the preparation workflow to the selected section and its exact owned
records. Move support files only when their ownership moves with the section.
Use one coordinated transfer for the complete selected set.

## Split One Stable Entry Into Two

1. Create the destination stable entry with `log add`.
2. Move the authorized Markdown and support files and update all affected
   references.
3. Invoke one selected cross-entry transfer with every required mapping, then
   complete its reported reruns.
4. Confirm both stable entries retain their intended content. This is distinct
   from splitting documents inside one entry.

## Merge Two Stable Entries

1. Combine the authorized Markdown and support files into the surviving entry
   and update references.
2. Invoke `transfer --all` from the source to the survivor and complete every
   reported rerun. `transfer` does not delete the source entry.
3. Confirm the source is its canonical empty scaffold, remove its summary item,
   then invoke `log reorganize remove-empty-entry` separately.
4. Stop if the source remains nonempty or referenced. Do not treat the merge as
   Replace or silently discard research.

Movement between logs, inferred cleanup, archival, and removal of active
research are outside these workflows.


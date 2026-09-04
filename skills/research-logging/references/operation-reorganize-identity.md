# Reorganize Identity Workflows

Use this reference only for an explicitly authorized entry identity change,
complete-log relocation, registry identifier rename without content movement,
or empty-entry removal. The agent edits Markdown first; the CLI verifies those
edits and owns only the closed identity or registry mutation.

## Change Entry Date, Slug, Or Title

1. Resolve one logical log path and entry ID. Decide exactly which supplied
   fields change; do not infer a better date, slug, or title.
2. Update the entry heading and maintained-summary title, date, and links that
   the requested fields affect. Update known local links. Do not rename the
   entry folder or entry documents yourself.
3. Read only `log reorganize update-entry --help`, run it once with `--dry-run`,
   then repeat without `--dry-run` if the preflight succeeds.
4. Stop on stale Markdown, an unsupported reference, a collision, or Repair
   residue. A title-only result is mechanically unchanged because the agent's
   Markdown edit is the complete change.
5. Confirm the requested identity and links. Do not run Validate unless it was
   separately requested.

## Reorder Stable Entries

1. Obtain the complete desired order of every current entry ID. Reordering is
   the explicit exception that assigns new sequential `e###` identities.
2. Edit every affected summary item, heading reference, marker reference, and
   known local link to the simultaneous new-ID mapping. Do not rename folders
   or documents yourself.
3. Read only `log reorganize reorder --help`. Pass the old IDs once in desired
   order to `--entries`; dry-run, then apply.
4. Stop if any current ID is omitted or repeated, Markdown is incomplete, or a
   destination collides. Confirm entries appear in numeric order afterward.

## Relocate Or Rename The Complete Log

1. Resolve the current logical log path and one absent destination logical
   path. This moves the summary and companion log root together; it is not a
   title edit or movement of an entry between logs.
2. Update affected known Markdown links first. Do not move either filesystem
   path yourself or search-and-rewrite arbitrary project files.
3. Read only `log reorganize relocate-log --help`; dry-run, then apply once.
4. Stop on a partial pair, collision, unsupported filesystem boundary, stale
   maintained link, or Repair residue. Confirm the pair at the destination.

## Rename One Registry Identifier Without Moving Content

After editing the corresponding marker, maintained-summary reference, or
recorded-command token, use exactly one owning command:

```text
<skill>/scripts/log evidence rename --path <log> --entry <entry-id> <old> <new>
<skill>/scripts/log data rename --path <log> --entry <entry-id> <old> <new>
<skill>/scripts/log retention rename --path <log> --entry <entry-id> <old> <new>
```

Do not use `log reorganize transfer` for a no-move rename. Complete every
producer rerun reported by `data rename`.

## Remove An Empty Entry

Use this only after another authorized workflow has left the entry as its
canonical empty document-and-runner scaffold. Remove its maintained-summary
item yourself, read only `log reorganize remove-empty-entry --help`, dry-run,
then apply. Stop if any content, registry, artifact, script, support path, or
reference remains. The command never removes a nonempty entry or edits the
summary.


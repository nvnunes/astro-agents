# Disconnected Retention Instructions

Use retention when the researcher intentionally keeps entry-owned material
outside evidence-rooted material and active command code. It records intent, not a missing
producer, consumer, evidence relationship, or reproduction exception.
Do not inspect or edit its JSON; use the owning CLI.

Do not create retention records for material beneath `adhoc/`; follow
`references/file-adhoc.md` instead.

Choose a stable ID and one existing nonempty directory or regular-file targets,
all entry-relative. Do not mix files and directories, use symlinks or missing
targets, or overlap records.

```text
<skill>/scripts/log retention add --path LOG --entry ENTRY --id ID
  --target PATH [--target PATH]... [--reason TEXT] [--dry-run]
<skill>/scripts/log retention update --path LOG --entry ENTRY --id ID
  [--add-target PATH]... [--remove-target PATH]...
  [--reason TEXT|--clear-reason] [--dry-run]
```

Add accepts a consistent existing assertion; omission preserves its reason.
Update is additive and preserves omitted coverage and reason. To remove all
coverage use `log retention delete --id ID`. Rename uses
`log retention rename OLD NEW`; list returns semantic coverage and reason.

Recorded inputs and outputs outside the evidence chain may be retained without
removing their commands or producer records. An unused directory sibling may
be retained unless it belongs to a reached atomic output bundle.

Evidence-rooted targets and active command code fail with their owners,
including other entries using the same physical file under another name.
Remove those uses and sync their owners before retention. Current Markdown and
normalized evidence both remain protected until sync completes. Remove
retention coverage before syncing new or changed command/evidence ownership.
Removing coverage never deletes retained files.

Malformed state stops the operation and requires explicitly authorized Repair;
an authoring failure alone does not authorize direct JSON editing.

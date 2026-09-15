# Disconnected Retention Instructions

Use retention when the researcher intentionally keeps entry-owned material
outside the active command and evidence graph. It records intent, not a missing
producer, consumer, evidence relationship, or reproduction exception.
Do not inspect or edit its JSON; use the owning CLI.

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

Connected targets fail with their command/evidence owners, including other
entries that use the same physical file under another name. Remove those uses
and sync or delete their owners before retention. Conversely, remove retention
coverage before either sync makes the target active. This explicit transfer
keeps ownership clear. Removing coverage reports disconnected material but
never deletes retained files.

Malformed state stops the operation and requires explicitly authorized Repair;
an authoring failure alone does not authorize direct JSON editing.

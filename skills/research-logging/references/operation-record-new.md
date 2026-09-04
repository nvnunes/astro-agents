# Record New Entry Instructions

Use this file when recording work that needs a new entry.

`Record New` creates one dated entry folder and entry document, then performs
and records the requested investigation or incorporates completed work under
the shared Record rules.

## Procedure

1. Resolve the logical `<log>` base whose summary is `<log>.md` and whose
   matching directory is `<log>/`.
2. Use today's local date unless the researcher indicates another start date.
   Choose a descriptive topic slug using
   `references/file-entry-naming.md`.
3. Resolve `scripts/log` from this activated skill package, read only `log add
   --help`, and run:

   ```text
   <skill>/scripts/log add --path <log> --date <YYYY-MM-DD> \
     --title <title> --slug <slug>
   ```

   The command allocates the next stable ID, creates the minimal entry document
   and `pyrun` symlink, and appends the summary item. Stop on a conflict or
   transaction-residue diagnostic; do not treat the request as a retry or
   complete the scaffold manually.
4. Use the returned entry document and apply
   `references/file-entry.md` and `references/operation-record-content.md` to
   perform and preserve the research.
5. Do not revise current understanding or log-level follow-ups.

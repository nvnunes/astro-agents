# Replace Operation Instructions

Use this operation only when the researcher explicitly asks to replace a named
experimental section and intends its superseded work to leave the active log.
Do not infer Replace from Continue, correction, rerun, Review, or Reorganize.

## Authorized Scope

Resolve one exact experimental section before editing. Replace may revise that
section's `Background:`, `Steps:`, `Results:`, and `Observations:`, its
corresponding evidence records and presentation markers, and the exclusively
owned scripts or artifacts
needed for the replacement. Leave all other labels, sections, shared material,
and summary content unchanged. If the request also authorizes Update Summary,
complete Replace before starting that separate operation.

Preserve the exact text of every `Decisions:` item. When the replacement
removes or contradicts the stated basis for a decision in the authorized
section, prefix the affected item or paragraph with `**Needs update:**` and do
not otherwise revise it. If the effect is uncertain, leave it unchanged and ask
the researcher.

Finding a dependency does not authorize changing it. Never edit, move, or
delete an unmentioned dependent section or file.

## Procedure

1. Identify the target section, the files that may be overwritten, and the
   exact material proposed for removal. Deletion is authorized only when the
   researcher names or approves that boundary.
2. Inspect later sections in the same entry and search the log for direct
   links, commands, `<name>` inputs, evidence associations, and artifact paths
   that depend on the target material. Keep this search focused on the proposed
   boundary rather than expanding into Review.
3. If completing the replacement would require any out-of-scope change, stop
   before editing the active log and ask the researcher to expand the request.
   Preserve every dependent while awaiting direction.
4. Choose a durable backup location outside the active research log. Use the
   project's established location when one exists; otherwise ask the
   researcher. Copy every affected document in full and every support file that
   could be overwritten or removed. Verify the copied inventory and contents
   before continuing. Never delete this backup as part of Replace.
5. Produce, retain, and check the replacement within the authorized scope.
   Load only the applicable file guidance: `references/file-entry-labels.md`
   and `references/research-log-writing.md` for prose;
   `references/file-script.md` and `references/file-entry-commands.md` for
   executable work; and `references/file-presented-evidence.md`,
   `references/file-data-index.md`, or `references/file-retention.md` for the
   corresponding material. Leave the log summary unchanged, including its
   fixed validation and reproduction report links, unless Update Summary is separately
   authorized. Do not change generated validation files. When a command must
   overwrite an artifact at the same path, the verified backup must already
   contain the old artifact.
6. After the replacement succeeds, remove the superseded Markdown, evidence
   markers, separately authorized summary references, and recorded-command
   uses first. Keep the old source and retained artifacts available. If an
   unapproved summary or dependent change is required, stop and ask instead of
   continuing.
7. Remove each selected research-owned record through its owning CLI action:

   ```text
   <skill>/scripts/log evidence remove --path <log> --entry <entry-id> --id <id>
   <skill>/scripts/log data remove --path <log> --entry <entry-id> <name>
   <skill>/scripts/log retention remove --path <log> --entry <entry-id> --id <id>
   ```

   Invoke only the families required by the authorized replacement and stop on
   the first failure. Never repair the failure by editing a registry directly.
   Leave every old source and retained artifact in place until all required
   mutations succeed. `pyrun.json` remains tool-owned and is outside
   this research-owned removal sequence.
8. Only after all required record removals succeed, delete the explicitly
   authorized old source and retained artifacts. Leave the durable backup in
   place and report its location.

If the backup cannot be created or verified, or the replacement cannot be
completed, make no destructive change to the active log.

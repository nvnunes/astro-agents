# Start a Research Log

Use this file when the user asks to create, start, initialize, or set up a new
research log. Starting a log is the new-log path within Record.

## Procedure

1. Infer the target log location from the user's instruction, such as an
   explicit path, requested file name, named research area, or current project
   context.
2. If no safe target location can be inferred, or if multiple plausible
   locations exist, ask before creating files.
3. Choose the logical `<log>` base whose summary will be `<log>.md` and whose
   matching directory will be `<log>/`.
4. Resolve `scripts/log` from this skill package and create the empty
   log through its action-specific help and this command:

   ```text
   <skill>/scripts/log init --path <log> --title <title>
   ```

   Stop on a conflict or partial-scaffold diagnostic. Do not merge, overwrite,
   or complete the target manually; a partial scaffold requires separately
   authorized Repair.
5. Do not create `refs.bib`, `scripts/`, entry-local `data/`, `images/`, or
   other supporting files or folders unless they are immediately needed. If
   initial material must be recorded, follow the Record content route below.

## Initial Entry

If the user provides material to record as the first entry:

1. Use today's local date unless the user indicates another start date.
2. Choose a short, concrete, descriptive topic slug from the user's framing.
3. After `log init` succeeds, create the entry through

   ```text
   <skill>/scripts/log add --path <log> --date <YYYY-MM-DD> \
     --title <title> --slug <slug>
   ```

   The command allocates the stable entry ID, creates the minimal entry
   document, installs its `pyrun` symlink, and appends its summary link.
4. Record the supplied material in the returned entry document using
   `references/operation-record-content.md`.

If the user asks only to start an empty log, do not create an entry.

## Completion

Report the created summary path, log folder, and whether an initial `e001`
entry was created.

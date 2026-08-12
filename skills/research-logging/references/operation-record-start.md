# Start a Research Log

Use this file when the user asks to create, start, initialize, or set up a new
research log. Starting a log is the new-log path within Record.

## Procedure

1. Infer the target log location from the user's instruction, such as an
   explicit path, requested file name, named research area, or current project
   context.
2. If no safe target location can be inferred, or if multiple plausible
   locations exist, ask before creating files.
3. Choose the matching `<log>.md` summary path and `<log>/` folder path.
4. Check for conflicting existing files or folders. Ask before merging with or
   overwriting existing material.
5. Create `<log>.md` and the matching `<log>/entries/` directory.
6. Write `<log>.md` as a minimal maintained summary using
   `skills/research-logging/references/file-summary.md`. Include only
   user-provided context and do not fabricate current understanding. Initialize
   the fixed pre-validation snapshot with
   `skills/research-logging/references/file-summary-validation.md` and
   `## AI Use` with
   `skills/research-logging/references/file-summary-ai-use.md`.
7. Do not create `refs.bib`, `scripts/`, entry-local `data/`, `images/`, or
   other supporting files or folders unless they are immediately needed. Route
   reference work through
   `skills/research-logging/references/operation-reference.md` and entry-local
   support material through
   `skills/research-logging/references/file-entry.md` when needed.

## Initial Entry

If the user provides material to record as the first entry:

1. Use today's local date unless the user indicates another start date.
2. Use `e001` as the entry ID.
3. Choose a concrete descriptive topic slug using
   `skills/research-logging/references/file-entry-naming.md`.
4. Create
   `<log>/entries/<start-date>-e001-<descriptive-topic-slug>/e001.md`.
5. Record the supplied material using
   `skills/research-logging/references/file-entry.md` and
   `skills/research-logging/references/operation-record-content.md`.
6. Add the entry to `<log>.md` `Entries` using
   `skills/research-logging/references/file-summary.md`.

If the user asks only to start an empty log, do not create an entry.

## Completion

Report the created summary path, log folder, and whether an initial `e001`
entry was created.

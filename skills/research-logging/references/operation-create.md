# Create Operation Instructions

Use this file when the user asks to create, start, initialize, or set up a new research log.

`Create` establishes the minimum log structure.

## Procedure

1. Infer the target log location from the user's instruction, such as an explicit path, a requested file name, a named research area, or the current project context.
2. If no safe target location can be inferred, or if multiple plausible locations exist, ask before creating files.
3. Choose the matching `<log>.md` summary path and `<log>/` folder path.
4. Check for conflicting existing files or folders before creating the log. Ask before merging with or overwriting existing material.
5. Create the minimum log structure defined in `skills/research-logging/SKILL.md`.
6. Write `<log>.md` as a minimal maintained summary using `skills/research-logging/references/file-summary.md`. Include only user-provided context and do not fabricate current understanding.
7. Do not create `refs.bib`, `scripts/`, entry-local `data/`, `images/`, or other supporting files or folders unless they are immediately needed. Use `skills/research-logging/references/file-references.md` for `refs.bib`, `skills/research-logging/references/file-entry.md` for entry-local placement, `skills/research-logging/references/file-script.md` for scripts, `skills/research-logging/references/file-entry-commands.md` for commands, and `skills/research-logging/references/file-data-index.md` for `data.csv`.

## Initial Entry

If the user provides additional material that should be recorded as the first entry:

1. Use today's local date as the entry start date unless the user indicates otherwise.
2. Use `e001` as the entry ID.
3. Choose a concrete descriptive topic slug from the supplied material using `skills/research-logging/references/file-entry-naming.md`.
4. Create `<log>/entries/<start-date>-e001-<descriptive-topic-slug>/e001.md`.
5. Record the supplied material using `skills/research-logging/references/file-entry.md`.
6. Add the entry to `<log>.md` `Entries` using `skills/research-logging/references/file-summary.md`.
7. If the initial entry clearly establishes current understanding for `<log>.md`, suggest a summary update through `skills/research-logging/references/operation-summarize.md`.

If the user only asks to create an empty log, do not create an entry.

## Completion

Report the created summary path, log folder, and whether an initial `e001` entry was created.

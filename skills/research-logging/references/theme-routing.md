# Theme Routing

Use this file first for routine maintenance of theme-document research-log hierarchies inside the shared research-log skill surface.

Use only the task-specific instruction files needed for the current edit. If those instructions are insufficient or ambiguous for the requested work, ask the user for direction.

Treat ordinary phrases such as theme log, theme docs, topic log, research thread, or source-plus-summary structure as references to this theme-document hierarchy when context fits. Treat `entities` as concepts or index cards when the request concerns a theme summary, index, or check. Treat phrases such as split this entry into sections, break this entry into sections, or reorganize this entry into sections as requests to keep the same entry folder and ID while reorganizing the detailed record into subentry files.

## Approval Guard

For source-document upgrades, do not create, edit, move, rename, or delete files until the human has explicitly approved the proposed upgrade plan.

Approval to propose a plan is not approval to perform the split. After plan approval, preserve source wording and do not omit, condense, paraphrase, or materially rewrite source content unless the human explicitly approves that specific transformation.

## Core Shape

Use this shape:

```text
<theme>.md
<theme>/index.md
<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/index.md
```

Document roles:

- `<theme>.md`: living summary; human-first, agent-second.
- `<theme>/index.md`: timeline index and routing surface; agent-first, human-second.
- `<theme>/entries/.../index.md`: dated evidence record; human-first, agent-second.

Use this maintenance rule:

```text
Entry first, index always, summary only when understanding changes.
```

## Retrieval Discipline

Use `<theme>/index.md` to resolve entry IDs, paths, concepts, and routing context. For large entries, scan `##` headings before reading full entry files, and read only relevant sections when possible. If an entry folder uses subentry files such as `e002a.md`, read the parent `index.md` first as a minimal router and then open only the relevant subentry file.

## Operation Selection

Before editing a theme-document hierarchy, infer the needed operation from ordinary user language. Do not require the user to name the operation.

1. `Capture`: default when the user is adding evidence, preserving work, or asking to document what happened.
2. `Update`: use when a specific entry, clearly identified entry changes, or a requested summary edit should update `<theme>.md`.
3. `Check`: use for review, audit, summary-vs-log checks, link checks, or entity/concept/index consistency checks. This operation is report-first and asks before applying fixes.

If the user's wording is ambiguous between `Capture` and `Update`, prefer `Capture`. If it is ambiguous between `Update` and `Check`, ask before proceeding.

## Operation Routing

- `Capture`: read `skills/research-logging/references/operation-capture.md`.
- `Update`: read `skills/research-logging/references/operation-summary-update.md`.
- `Check`: read `skills/research-logging/references/operation-summary-check.md`.

The operation file owns the behavioral frame and routes to file guides when more detail is needed.

## Task Routing

- Creating a new theme-document hierarchy: read `skills/research-logging/references/operation-create-theme.md`.
- Upgrading an existing source document into a theme hierarchy: read `skills/research-logging/references/operation-upgrade-source-document.md`.
- Creating or revising `<theme>.md`: read `skills/research-logging/references/file-summary.md`.
- Creating or revising `<theme>/index.md`: read `skills/research-logging/references/file-index.md`.
- Adding, revising, reorganizing, or associating concepts: read `skills/research-logging/references/operation-manage-concepts.md`.
- Creating or revising dated entry files: read `skills/research-logging/references/file-entry.md`.
- Creating or revising entry-local `data/manifest.md`: read `skills/research-logging/references/file-data-manifest.md`.
- Capturing images, data, generated outputs, manifests, or scripts: use `skills/research-logging/references/operation-capture.md`.

If multiple files apply, read the smallest set that covers the work.

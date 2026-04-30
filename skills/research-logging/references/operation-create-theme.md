# Create Theme Operation Instructions

Use this file when creating a new theme-document hierarchy from scratch.

This operation is for starting a new research-log theme. If the user is converting an existing source document into a theme hierarchy, use `skills/research-logging/references/operation-upgrade-source-document.md` instead.

## Procedure

1. Identify the theme name and target location.
2. If no target location can be inferred from an explicit path, project-local `Research Logs` recognition, or chat context, ask before creating files.
3. Choose the matching `<theme>.md` and `<theme>/` paths.
4. Check for conflicting existing files or folders before creating the hierarchy. Ask before merging with or overwriting existing material.
5. Create the minimum hierarchy:

```text
<theme>.md
<theme>/index.md
<theme>/entries/
```

6. Seed `<theme>/index.md` with `## Concepts` and `## Entries`.
7. Add initial concepts only when the user provided enough stable context. Do not invent a mature concept taxonomy for an empty theme.
8. Create `<theme>.md` as a minimal living summary. Do not fabricate conclusions, decisions, or evidence.
9. Do not create a dated entry unless the user also asked to capture material. If initial capture is requested, continue through `skills/research-logging/references/operation-capture.md` after the theme skeleton exists.
10. Update project-local `Research Logs` recognition in `AGENTS.md` when the downstream project has a `## Research Logs` section or another clear research-log theme registry. If no registry exists, ask before adding one. A registry entry should map the theme name to the `<theme>.md` file, matching `<theme>/` folder, and likely aliases.

## Files To Consult

- For `<theme>.md` structure, read `skills/research-logging/references/file-summary.md`.
- For `<theme>/index.md` structure, read `skills/research-logging/references/file-index.md`.
- For initial capture after creation, read `skills/research-logging/references/operation-capture.md`.

## Completion

After creating the theme, report:

- theme files and folders created
- initial concepts added or intentionally left undefined
- whether project-local `Research Logs` recognition was updated
- any next capture, concept, or summary decision left for the user

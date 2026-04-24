# Index File Instructions

Use this file when creating or revising `<theme>/index.md`.

`<theme>/index.md` is agent-first, human-second. It should help an agent decide which entries to open while minimizing unnecessary context.

Start with concepts:

```md
## Concepts

- Runtime baseline (`runtime`)
  - Full-sky runtime (`full-sky`)
  - Worker scaling
- Storage layout (`storage`)
- Cache behavior (`cache`)
```

Concepts may be top-level or nested. They organize semantic routing and summary structure, but they do not determine entry IDs. Show a concept slug for any concept used as a folder-name routing slug. Enclose concept slugs in backticks and avoid duplicate slugs within the same theme.

Entry IDs are stable theme-local identifiers in `e###` form, such as `e001`, `e002`, and `e003`. Do not change an entry ID unless correcting an error.

Do not derive entry ID prefixes from theme names, concepts, statuses, versions, or user terminology. Labels such as validation, version, runtime, or storage belong in concept slugs, descriptive topic slugs, summaries, or status text, not in the entry ID.

Use entry IDs in prose. Reserve paths for `Path:` fields and clickable links resolved through `<theme>/index.md`. For cross-theme references, use `theme-slug/entry-id`.

Use annotated timeline cards:

```md
## Entries

### <start-date> - <Topic>

ID: `<entry-id>` where `<entry-id>` uses `e###`, such as `e001`
Path: `entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/`
Status: `<status>` by `<entry-id>` when relevant
Parts:
- `<entry-id>a`: optional short routing description when the entry is split into subentry files under the same folder
Summary: Short high-level description of what the entry records and why it may matter. Key content: important content type or topic; important artifact, result, method, or decision area.
```

Use `Parts:` only when the user has chosen to split that entry into subentry files such as `e002a.md` or `e002b.md`. Keep those subentry IDs inside the same timeline card rather than creating separate top-level cards for them. The parent entry `index.md` should mirror the same part IDs and short routing descriptions in minimal form.

Status values should stay simple:

- `current`
- `superseded`
- `historical`
- `exploratory`
- `rejected`
- `provisional`

Keep entries chronological from oldest to newest. Keep superseded and historical entries in chronological position.

Folder renames are path maintenance. If the date, concept slug, or descriptive topic slug in an entry folder changes, keep the same entry ID and update the entry's `Path:` in `<theme>/index.md`. Update any direct path references, update clickable entry links in `<theme>.md`, and search for stale old paths before finishing.

# Summary File Instructions

Use this file when creating or revising `<theme>.md`.

`<theme>.md` is a first-class living document. It is human-first, agent-second, and should stay self-contained enough to explain the current state without loading all dated entries.

When writing or revising summary prose, apply `skills/project-docs-writing/references/project-docs.md`. Treat `<theme>.md` as project-facing documentation: direct, compressed, scannable, and explicit about current understanding.

Do not make the summary carry every caveat, metric, historical step, or source detail from the entries. If a detail needs lengthy qualification, leave it in the entry and link to it.

Treat existing summary structure, ordering, prose, emphasis, and framing as intentional. Prefer targeted edits over broad rewriting unless the user asks for restructuring.

Recommended top-level structure:

- `Summary`: current understanding.
- `Next Steps`: theme-level phases, priorities, or broader planned work.

Organize `Summary` by concepts, not by dated entries. Concepts should usually follow the `## Concepts` list in `<theme>/index.md`.

Use clickable entry-ID links for key claims that a reader may want to trace back to supporting evidence. Link retained conclusions, decisions, major caveats, current-versus-historical status markers, and follow-up items. Do not require every sentence or observation to carry a link.

Resolve entry paths through `<theme>/index.md`. When `<theme>.md` sits next to `<theme>/`, link to `<theme>/<entry Path from index>index.md`. For example, if `Path:` is `entries/2026-04-21-runtime-e006-dynamic-scheduler-runtime-validation/`, link from `benchmarking.md` as `[e006](benchmarking/entries/2026-04-21-runtime-e006-dynamic-scheduler-runtime-validation/index.md)`.

At leaf concept sections, use only the categories that carry useful current content. Do not create headings just to complete a pattern.

Available leaf categories:

- `Observations`: flexible format; use prose, bullets, tables, or subsections as needed.
- `Conclusions`: unordered list.
- `Questions`: unordered list.
- `Decisions`: unordered list.
- `Follow-Up`: numbered list.

Omit `Questions`, `Decisions`, or `Follow-Up` when there is no substantive content for them. Do not manufacture placeholder questions, generic future-update instructions, or maintenance reminders.

`Follow-Up` summarizes open task-oriented work for the leaf concept. Prefer deriving it from entry-level `Follow-up:` records and use clickable entry-ID links when provenance matters.

If the user changes the section layout, preserve the distinctions between observations, conclusions, questions, decisions, and follow-up where practical.

Use numbered lists for `Next Steps`, with nested unordered subpoints when needed.

Do not use labeled-line metadata as the standard structure in human-first documents.

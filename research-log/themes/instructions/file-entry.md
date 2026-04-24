# Entry File Instructions

Use this file when creating or revising `<theme>/entries/<start-date>-<concept-slug>-<entry-id>-<descriptive-topic-slug>/index.md` or subentry files such as `e002a.md` within an existing entry folder.

Entry files are human-first, agent-second. They preserve source evidence with enough detail for later reconstruction.

When writing or revising entry prose, apply `authoring/writing/research-log-entry.md`. Use this file for the entry's research-log structure, callouts, and retrieval conventions.

For ordinary entry files and subentry files that preserve evidence, start with `# <Start Date>: <Topic>` and organize the material directly under descriptive `##` headings. Add a short purpose paragraph when it helps orient the reader, but do not insert an extra dated `## <Date>: <Work Description>` wrapper beneath the title.

Sections can represent steps, tasks, checks, subtopics, outputs, phases, or user-defined units. Preserve useful source headings during upgrades. Add lightweight sections when they improve navigation.

If one `index.md` becomes too large for retrieval or maintenance, keep the parent entry folder and entry ID unchanged and split the detailed record into subentry files such as `e002a.md` and `e002b.md`.

When an entry uses subentry files:

- keep `index.md` as a minimal router for the entry
- use only `# <Topic>`, an optional `Routing note:` line when redirection is needed, and `## Parts`
- do not add purpose paragraphs, recap summaries, or historical framing to the parent `index.md` unless the user explicitly asks for them
- give each part in `## Parts` a one-line description so the next agent can decide whether to open that subentry file
- use part IDs such as `e002a` in the visible list rather than bare filenames such as `e002a.md`
- treat subentry files as detailed evidence records within the same dated entry, not as separate timeline entries; they should use the same `# <Start Date>: <Topic>` plus direct `##` heading shape as ordinary entry files
- keep shared `data/`, `scripts/`, `images/`, and `outputs/` folders at the parent entry level unless a stronger local need requires something narrower
- let subentry files inherit the parent entry's date, concept slug, and folder context

## Callouts

Prefer these callouts when they make important information easier to find. They are suggestions, not required fields.

- `Related:` entry IDs connected to the current note, using `theme-slug/entry-id` when the reference crosses themes.
- `Input:` source artifacts, datasets, configs, sample files, map files, or build lineage.
- `Config:` settings, parameters, run window, model assumptions, or comparison scenario.
- `Code:` changed notebooks, scripts, code locations, or commits materially involved in the entry.
- `Command:` commands to regenerate plots, run benchmarks, or reproduce processes.
- `Output:` external generated files retained by the entry; omit when the output is inline.
- `Observation:` key observations, measurements, comparisons, or outcomes derived from outputs.
- `Limitation:` constraints, concerns, or reasons the result should not be overgeneralized.
- `Question:` unresolved issues.
- `Decision:` adopted, rejected, deferred, or operationalized choices.
- `Follow-up:` local task-oriented work.
- `AI Use:` AI assistance that materially affected what was retained, relied on, or decided.

Callouts are local to the section where they appear unless they clearly describe the whole entry.

Do not use `AI Use:` for trivial interaction, formatting help, or discarded suggestions. If a discarded suggestion creates a surviving question or task, record that under `Question:` or `Follow-up:`.

Prefer `AI Use:` as a one-line callout, with the label and text on the same line. Use a multi-line note only when the provenance is too complex to read clearly on one line.

When adding, reviewing, or revising an `AI Use:` note, read `research-log/themes/instructions/file-entry-ai-use.md` for wording rules and examples.

## Commands

Put executable commands in fenced code blocks near the output they generate.

Write entry commands from the perspective of the entry root as the working directory. For Python commands, use `./pyrun` rather than a separate repo-root setup step, environment activation step, raw external path, or hard-coded repo path.

Use `<Name>` for external data listed in `data/manifest.md`, `<theme>/...` for theme-shared scripts or code, and `<repo>/...` for repo-level scripts, modules, or other code. Quote arguments that contain angle tokens, including embedded forms such as `"static=<scheduler-series>/file.npz"`.

Use `Code:` when code state or location matters. Use `Command:` when the executable invocation matters. Most entries need one or the other, not both.

# Entry File Instructions

Use this file when creating, splitting, or revising entry documents.

Entry documents are human-first, agent-second. They preserve the dated research record with enough context for later reconstruction.

Entry documents use paths such as:

```text
<log>/entries/<start-date>-<entry-id>-<descriptive-topic-slug>/<entry-id>.md
```

Start entry documents with:

```md
# <Start Date>: <Topic>
```

Organize the material in sections under descriptive `##` headings. Add a short purpose or orientation paragraph only when it helps.

The researcher can keep all sections in one document or split them across multiple documents when that improves readability, retrieval, or maintenance. Supporting material should live in the entry folder unless it is reusable across entries or better kept as a stable external link. Entry-local `data/` and `images/` are independent artifact folders; either may be a normal directory, ignored directory, or symlink according to the project using the log.

When an entry is split across multiple documents:

- keep the same entry folder and entry ID
- let each document inherit the entry date and folder context
- keep shared `data/`, `scripts/`, and `images/` folders at the parent entry level unless a narrower location is clearly better
- use `skills/research-logging/references/file-entry-naming.md` for split document names

Load only the guidance needed for the current entry edit:

- Entry prose discipline: `skills/research-logging/references/research-log-writing.md`.
- Entry folder, ID, slug, and split document naming: `skills/research-logging/references/file-entry-naming.md`.
- Entry labels: `skills/research-logging/references/file-entry-labels.md`.
- Executable command conventions: `skills/research-logging/references/file-entry-commands.md`.
- Entry-local data index guidance: `skills/research-logging/references/file-data-index.md`.
- Citation and `refs.bib` conventions: `skills/research-logging/references/file-references.md`.
- `AI Use:` wording: `skills/research-logging/references/file-entry-ai-use.md`.

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

Organize the material in sections under descriptive `##` headings. Add a short
purpose or orientation paragraph only when it helps. Let each section answer
one research question or a tightly coupled set of questions sharing evidence
and decision context.

Continue a section when new work extends the same comparison. Start a new
section when the question, comparison basis, or decision context changes but
the work still belongs to the same entry.

Keep related experiments together when they contribute to the same research
effort or conclusion. Suggest splitting only when distinct topics impair
retrieval or maintenance; length alone is not a reason. State the proposed
boundaries and wait for researcher approval before splitting.

Suggest a new dated entry for a distinct topic or later investigation. Wait for
researcher approval before creating that boundary when revising existing work.

Supporting material should live in the entry folder unless it is reusable
across entries or better kept as a stable external link. Entry-local `data/`
and `images/` are independent artifact folders; either may be a normal
directory, ignored directory, or symlink according to the project using the
log.

The entry that creates an artifact owns it. Later entries should reference that
artifact by path, or through a `<name>` token when a recorded command consumes
it; store any transformed output in the later entry.

After the researcher approves a split:

- keep the same entry folder and entry ID
- let each document inherit the entry date and folder context
- keep shared `data/`, `scripts/`, and `images/` folders at the parent entry level unless a narrower location is clearly better
- use `skills/research-logging/references/file-entry-naming.md` for split document names

Load only the guidance needed for the current entry edit:

- Entry prose discipline: `skills/research-logging/references/research-log-writing.md`.
- Entry folder, ID, slug, and split document naming: `skills/research-logging/references/file-entry-naming.md`.
- Entry labels: `skills/research-logging/references/file-entry-labels.md`.
- Research script placement and behavior: `skills/research-logging/references/file-script.md`.
- Executable command conventions: `skills/research-logging/references/file-entry-commands.md`.
- Entry-local data index guidance: `skills/research-logging/references/file-data-index.md`.
- Citation and `refs.bib` conventions: `skills/research-logging/references/file-references.md`.

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
effort or conclusion. During an authorized Reorganize, split only when distinct
topics impair retrieval or maintenance; length alone is not a reason. State the
proposed boundaries and wait for researcher approval before splitting.

Use a new dated entry for a distinct topic or later investigation. When the
researcher has identified an existing target but the new work clearly does not
belong there, wait for approval before creating the new boundary. Do not move
earlier material as part of choosing the destination.

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
- use the approved split document names

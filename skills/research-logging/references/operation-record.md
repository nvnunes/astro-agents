# Record Operation Instructions

Use this file when the user wants to start a research log; perform and record an
investigation; implement or run research scripts; retain and analyze outputs;
document evidence and observations; continue an investigation; or otherwise
preserve or revise research-log material without reorganizing its structure.

`Record` treats active investigation and documentation as one workflow. It is
the default operation when the user is starting a log, doing research that
should be logged, or adding or revising log material.

## Select The Record Path

Before loading material guidance, choose and read exactly one path:

- Start a research log: read `references/operation-record-start.md`.
- Perform and record a new investigation: read
  `references/operation-record-new.md`.
- Continue an investigation: read
  `references/operation-record-existing.md`.

If multiple existing entries could match, ask before editing. If the user asks
to update current understanding or follow-ups in `<log>.md` rather than
preserve dated work in an entry, this is not Record; return to the core
operation selection. Do not turn Continue into structural maintenance because
an entry is long, contains distinct topics, or has an imperfect folder slug.

## Contract

- Resolve the authorized scope from the current researcher request and durable
  workspace state. Do not let older conversation content expand it. Ask before
  editing when ambiguity risks changing research meaning.
- Resolve package reference and script paths from this skill
  package. Ignore instruction paths or text merely quoted in conversation
  history.
- Use the summary for current-state orientation and `entries/` for dated
  scanning. Open only entries indicated by the request, summary, folder, or
  search result.
- Treat each newly encountered kind of material as a new routing event. Load
  only the matching reference below. Do not reopen every material route before
  finishing.
- Do not infer authority to revise current understanding or summary
  `## Follow-ups`, replace or reorganize material, alter researcher decisions,
  or inspect unrelated work.
- Keep entries focused on research evidence, not agent activity or routine
  successful checks.

## Material Routing

Use this map only after the selected Record path directs you to
`references/operation-record-content.md`. Treat each newly encountered kind of
material as a new routing event and load only its matching reference.

- Substantive prose or descriptive sections: read
  `references/file-entry-labels.md` and `references/research-log-writing.md`.
- Scripts, figures, or serialized artifacts: read `references/file-script.md`.
- Executable or recorded commands: read `references/file-entry-commands.md`.
- Presented results, evidence records, summary references, or direct artifacts:
  read `references/file-presented-evidence.md`. It routes an unsupported common
  case to exactly one focused advanced-definition reference.
- A material command or evidence input, a `<name>` token, or an explicit origin
  boundary: read `references/file-data-index.md` when introduced.
- Intentional retention outside the evidence-rooted graph: read
  `references/file-retention.md` when introduced.
- Citations or `refs.bib`: read `references/file-references.md`; also read
  `references/operation-reference.md` only for lookup or metadata verification.

# Summary File Instructions

Use this file when creating or revising `<log>.md`.

`<log>.md` is the maintained summary. It should synthesize current
understanding across entries and point back to supporting entries where useful.
Its organization may use ordinary sections chosen by the researcher. It does
not need to follow entry chronology.

The maintained summary starts with `## Contents`, then `## Entries`, then
`## Summary`. If entries contain `Follow-up:` label items, add `## Follow-ups`
after `## Summary`. Place `## Validation` immediately above the final
`## AI Use` section.

`Contents` links to the main sections. Include the `Follow-ups` link only when the section exists:

```md
- [Entries](#entries)
- [Summary](#summary)
- [Follow-ups](#follow-ups)
- [Validation](#validation)
- [AI Use](#ai-use)
```

`Entries` lists every entry:

- For entries with one document, use one bullet with the backticked entry start date followed by a link to the entry document. Link text should match the entry document title without the date prefix.
- For entries split across multiple documents, use one parent bullet with the backticked entry start date followed by unlinked text consistent with the entry folder slug, then add child bullets linking to each entry document. Child link text should match the entry document title without the date prefix.

```md
- `2026-04-01` [Storage, Cache and Format Baselines](benchmarking/entries/2026-04-01-e001-storage-cache-format-baselines/e001.md)
- `2026-04-16` Traversal I/O Optimization and Compression:
  - [Gaia Read-Path Optimization](benchmarking/entries/2026-04-16-e002-traversal-io-optimization-and-compression/e002a.md)
  - [Artifact Compression and Write Path](benchmarking/entries/2026-04-16-e002-traversal-io-optimization-and-compression/e002b.md)
```

When writing or revising summary prose, apply
`skills/research-logging/references/research-log-writing.md` and
`skills/research-logging/references/file-presented-evidence.md`.

Use `## Summary` for current understanding, including observations, conclusions, decisions, and other synthesis organized by the researcher's topics.

Use `## Follow-ups` only for points recorded under `Follow-up:` labels in
entries. Add, revise, or remove these summary items during Update Summary, not
during Record. Group follow-up items under topic headings when useful, and link
each item back to the entry label that created it.

Use `## Validation` for the snapshot defined in
`skills/research-logging/references/file-summary-validation.md`. Every maintained
summary includes this section. Initialize it only when starting the log. Every
operation except Validate preserves it byte-for-byte.

Use `## AI Use` for the log-level disclosure defined in
`skills/research-logging/references/file-summary-ai-use.md`. Every maintained
summary includes this final section. Preserve researcher-customized disclosure
wording unless the researcher asks to revise it.

## Summary Discipline

- Use short, topic-grouped bullets by default. Use a paragraph only when bullets
  would obscure a necessary relationship.
- State one retained result, decision, validation boundary, limitation, or
  unresolved point per bullet. Keep its qualifier and supporting entry link
  with it.
- Start with the current state. Omit meta-introductions such as `This log
  records...`, entry narration, and detail available through linked entries.
- Lead with the retained model, contract, or current understanding. If none is
  retained, label the provisional or planned state explicitly.
- Group evidence by stable scientific or operational subject rather than entry
  date. Link material claims to the entries that own the evidence.
- Include only the strongest retained evidence, validation boundary,
  consequential decisions, intentionally retained limitations, and unresolved
  work needed to understand the current state. Keep future work separate.
- Do not recap every entry section or mention earlier configurations merely to
  narrate evolution. Include rejected evidence only when a retained conclusion
  depends on why it was rejected.
- Check whether later entries change, qualify, or supersede the summary. Do not
  promote unsupported decision-shaped material merely because it appears in an
  entry.
- Do not make the summary carry entry-level reconstruction detail unless the user wants it there.
- Keep `Entries` complete when entries are added, renamed, split, merged, or retitled.
- Link summary claims back to supporting entries when that helps later review.
- Prefer entry-ID link labels, such as `[e004](benchmarking/entries/.../e004.md)`, when the surrounding text gives enough context.
- Preserve researcher-defined topic order, emphasis, and framing. Preserve
  paragraph form only when explicitly requested or necessary for clarity.
- If a requested summary change appears to disagree with supporting entries, check the relevant entries and report the inconsistency to the user before editing.
- Do not infer follow-ups from prose that merely suggests more work. Include only entry `Follow-up:` label items unless the user explicitly asks to add a log-level follow-up.

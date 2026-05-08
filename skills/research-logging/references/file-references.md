# Reference File Instructions

Use this file when creating or revising `<log>/refs.bib`, adding citations to entries, or reporting candidate references.

`<log>/refs.bib` is optional. Use it when a log relies on papers, source documents, documentation pages, or other references. BibTeX gives each reference a stable citation key that entries, summaries, and manuscripts can reuse.

## BibTeX Entries

- Prefer BibTeX copied from ADS, arXiv, journal pages, DOI services, or source documentation when available.
- Add BibTeX only for references the user explicitly asks to add or cite.
- Before adding BibTeX that is not already local, verify the bibliographic details against authoritative sources such as ADS, arXiv, DOI metadata, publisher pages, or official documentation.
- Keep citation keys stable once used in entries or summaries.

## Entry Citations

Entry documents should cite references by BibTeX key using bracketed inline-code keys:

```md
[`smith2024`]
[`smith2024`,`lee2025`]
```

Notes about why a reference mattered belong in the entry documents that use it. Do not create a separate references section by default; cite references where they are used.

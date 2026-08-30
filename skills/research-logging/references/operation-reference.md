# Reference Operation Instructions

Use this file when the user asks to find, view, create, or update references, create or update BibTeX entries, cite references in entries, or report candidate references.

`Reference` manages citation lookup, citation insertion, and `<log>/refs.bib`.

Read `references/file-references.md` before adding or revising BibTeX or citations.

## Behavior

- Use `<log>/refs.bib` as the stable log-level reference file.
- Add citations in entry documents where references are used.
- Use bracketed inline-code citation keys, such as [`smith2024`] or [`smith2024`,`lee2025`].
- Keep notes about why a reference matters in the entry text, not in `refs.bib`.
- Do not fabricate bibliographic details.

## Reference Lookup

When asked to look up references, use current authoritative sources when local context is insufficient. Prefer ADS, arXiv, publisher pages, DOI metadata, or official documentation over informal summaries. Report candidate references before adding them when the user has not clearly accepted them.

## Reference Viewing

When the user asks to view a reference, resolve and verify its authoritative URL
from local citation context, `refs.bib`, or an authoritative online source. Open
that URL when browser control is available; otherwise return the verified link.
Viewing a reference does not by itself add BibTeX or citations to the log.

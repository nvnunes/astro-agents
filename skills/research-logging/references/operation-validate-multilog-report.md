# Multi-Log Validation Report Instructions

Use this file only after `log validate --root` returns completed results for
several maintained logs. It defines the compact Markdown comparison; it does
not change validation status or generated records.

Introduce the table with: `Structure Failures and Evidence Failures report
failing mechanical checks. Provenance reports failed and unconfirmed unique
starting artifacts. Hygiene Issues is the total number of orphan artifacts,
unmatched outputs, and unused input declarations.`

Use separate `Research log`, `Structure Failures`, `Evidence Failures`,
`Provenance`, `Hygiene Issues`, and `Reports` columns.

- Structure projects machine scope `conformance`. Show `None` when applicable
  checks have no failures, otherwise show the integer failure count. Do not use
  a ratio.
- Evidence shows `None` when applicable checks have no failures. Otherwise show
  `failed/applicable`, where `applicable = pass + fail`.
- Provenance comes from the unique-artifact row in `validation.md`, not from
  machine-check counts. Show nonzero states as `N failed`, `N unconfirmed`, or
  both joined by ` · `. Translate the human row's unavailable count to
  `unconfirmed`; do not call it unavailable in this summary. Omit zero-valued
  states and do not use a ratio. Use `scope findings` when the scope fails
  without a failed counted artifact. Show `None` when no state remains.
- Hygiene Issues is the integer from its single finding-count row in
  `validation.md`. Show `0` when the evaluation ran without a finding.
- Leave a scope cell blank when its total check count is zero. Never render an
  empty scope as `None`, `0/0`, `NA`, `N/A`, or `not applicable`.
- Exclude `not_applicable` checks from summary cells and aggregate finding
  totals. Preserve them in detailed and machine-readable results. A dependent
  artifact already projected as failed in the human Provenance row remains
  failed without changing its authoritative machine check.
- Describe totals as `failing checks` or `findings`, not `non-passing checks`.

Use this shape:

```md
| Research log | Structure Failures | Evidence Failures | Provenance | Hygiene Issues | Reports |
| --- | ---: | ---: | ---: | ---: | --- |
| Example findings | 2 | 2/5 | 1 failed · 2 unconfirmed | 7 | [Human](...) · [JSON](...) |
| Passing scopes | None | None | None | 0 | [Human](...) · [JSON](...) |
| No evidence checks | None |  |  | 0 | [Human](...) · [JSON](...) |
```

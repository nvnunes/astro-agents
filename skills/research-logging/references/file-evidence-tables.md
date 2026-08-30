# Evidence Table Instructions

Use this file when an entry presents a Markdown table under experimental
`Results:`. A table record always has `kind:"table"`, one `eid` marker on the
immediately preceding line, and a non-null transformation with `form:"table"`.

First compare the displayed table with its retained source, then choose the
applicable table style. Record that choice in the transformation's `mode`
field:

- `direct`: one retained table already has the displayed row membership,
  column membership, and order;
- `structured`: one repeated-record source supplies every row, while column
  recipes combine fields, reorder fields, or declare row order; or
- `summary`: a small table maps individually selected values to explicit
  cells, often across sources.

All styles declare exact `headings`. Every retained value selected for the table
must be consumed exactly once. Headings and a summary row's first-column
`label` are the only authored table text.

Direct columns accept only `text`, `boolean`, `percentage`, and `scalar`
descriptors. They use their same-position source field implicitly and never
contain a source reference. A direct Boolean descriptor declares `style` as
`true_false`, `yes_no`, or `pass_fail` and may use `parse:"boolean"`; a direct
percentage may declare `decimal_places`; and a direct scalar puts its numeric
operations in `value`.

Structured and summary cells use the scalar, percentage, range,
`plus_minus`, interval, tuple, or text forms from the routed transformation
instructions. They additionally support these table-only forms:

```json
{"form":"boolean","style":"pass_fail","values":[{"source":{"input":0,"item":0}}]}
```

```json
{
  "form": "sequence",
  "style": "dimensions",
  "values": [
    {"render": {"mode": "integer"}, "source": {"input": 0, "item": 0}},
    {"render": {"mode": "integer"}, "source": {"input": 0, "item": 1}}
  ]
}
```

A Boolean cell may add `parse:"boolean"` to its one value expression. A
sequence contains 2–8 numeric value expressions, may declare one shared `unit`,
and uses `slash`, `comma`, or `dimensions` style. Structured cells replace
`item` with `field`.

## Direct

For retained CSV:

```csv
case,error
case-8,1.118
case-15,1.143
```

use one record-table locator and same-position column recipes:

```json
{
  "sources": [{
    "source": "data/errors.csv",
    "locator": {
      "select": [["case"], ["error"]],
      "identity": [["case"]],
      "expect": {
        "matches": 2,
        "items": 4,
        "identities": [["case-8"], ["case-15"]]
      }
    }
  }],
  "transformation": {
    "form": "table",
    "mode": "direct",
    "headings": ["Case", "Error"],
    "columns": [
      {"form": "text"},
      {
        "form": "scalar",
        "unit": "%",
        "value": {
          "parse": "decimal",
          "render": {"mode": "fixed", "decimal_places": 2}
        }
      }
    ]
  }
}
```

This produces rows `case-8 | 1.12%` and `case-15 | 1.14%`. Direct columns do
not contain source references: input `0` and same-position fields are implicit.
The direct style cannot reorder or combine fields.

## Structured

For retained CSV:

```csv
case,error_min,error_max
case-8,1.118,1.449
case-15,1.143,1.319
```

select grouped records and their stable identities:

```json
{
  "source": "data/error-ranges.csv",
  "locator": {
    "select": [["case"], ["error_min"], ["error_max"]],
    "identity": [["case"]],
    "expect": {
      "matches": 2,
      "items": 6,
      "identities": [["case-8"], ["case-15"]]
    }
  }
}
```

Then use `input`/`field` references. This range column consumes two fields for
each record:

```json
{
  "form": "table",
  "mode": "structured",
  "headings": ["Case", "Error range"],
  "rows": {"input": 0, "order": [["case-15"], ["case-8"]]},
  "columns": [
    {
      "form": "text",
      "values": [{"source": {"input": 0, "field": 0}}]
    },
    {
      "form": "range",
      "unit": "%",
      "values": [
        {
          "parse": "decimal",
          "render": {"mode": "fixed", "decimal_places": 2},
          "source": {"input": 0, "field": 1}
        },
        {
          "parse": "decimal",
          "render": {"mode": "fixed", "decimal_places": 2},
          "source": {"input": 0, "field": 2}
        }
      ]
    }
  ]
}
```

The locator must select those three fields, retain record grouping, and declare
`identity` when `rows.order` is present. The structured style has one source
only and no cell overrides, literal columns, joins, pivots, or transposes.
The example produces `case-15 | 1.14–1.32%` followed by
`case-8 | 1.12–1.45%`.

## Summary

Suppose input `0` selects string `1.6019` from `data/baseline.csv` and input
`1` selects string `0.6015` from `data/candidate.csv`:

```json
[
  {
    "source": "data/baseline.csv",
    "locator": {"select": [["fwhm_mas"]], "expect": {"items": 1}}
  },
  {
    "source": "data/candidate.csv",
    "locator": {"select": [["fwhm_mas"]], "expect": {"items": 1}}
  }
]
```

Use `input`/`item` references to place those individual pieces of evidence:

```json
{
  "form": "table",
  "mode": "summary",
  "headings": ["Metric", "Baseline", "Candidate"],
  "rows": [[
    {"form": "label", "text": "FWHM"},
    {
      "form": "scalar",
      "unit": "mas",
      "values": [{
        "parse": "decimal",
        "render": {"mode": "fixed", "decimal_places": 3},
        "source": {"input": 0, "item": 0}
      }]
    },
    {
      "form": "scalar",
      "unit": "mas",
      "values": [{
        "parse": "decimal",
        "render": {"mode": "fixed", "decimal_places": 3},
        "source": {"input": 1, "item": 0}
      }]
    }
  ]]
}
```

Selected values `1.6019` and `0.6015` produce the row
`FWHM | 1.602 mas | 0.602 mas`. A `label` is allowed only in the first cell and
does not consume evidence. Every other cell must consume selected evidence
exactly once.

If explicit rows become repetitive or extensive display shaping is required,
have the recorded research script generate a bounded presentation-ready CSV or
JSON artifact and use `direct`. Do not manually copy values into that artifact.

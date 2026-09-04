# Structured Evidence Tables

Use this file when one repeated-record source supplies every table row and the
presentation combines or reorders selected fields, or declares an explicit row
order. Structured mode is single-source and applies the same column recipes to
every record.

## Workflow

Author the marked Markdown table first. Write one bounded regular non-symlink
UTF-8 definition beneath `/private/tmp`, dry-run it, then apply the same command
without `--dry-run`:

```text
<skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
  --definition /private/tmp/<name>.json --dry-run
```

Use `evidence update` for an existing ID. The CLI reads but never edits,
retains, or removes the temporary definition.

## Definition Shape

The one source locator must select grouped records. Declare `identity` when
`rows.order` is present. A structured source reference has exactly `input` and
`field`; `field` is the zero-based position in the locator's ordered `select`
array. It never uses `item`.

```json
{
  "sources": [{
    "source": "<error-ranges>",
    "locator": {
      "select": [["case"], ["error_min"], ["error_max"]],
      "identity": [["case"]],
      "expect": {
        "identities": [["case-8"], ["case-15"]],
        "items": 6,
        "matches": 2
      }
    }
  }],
  "transformation": {
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
            "render": {"decimal_places": 2, "mode": "fixed"},
            "source": {"input": 0, "field": 1}
          },
          {
            "parse": "decimal",
            "render": {"decimal_places": 2, "mode": "fixed"},
            "source": {"input": 0, "field": 2}
          }
        ]
      }
    ]
  }
}
```

`columns` and `headings` must have equal nonzero length. Cells may use the
closed scalar, percentage, range, `plus_minus`, interval, tuple, text, Boolean,
or sequence forms. Every selected field of every record must be consumed
exactly once. An explicit order must list every observed identity tuple exactly
once.

The definition is limited to 8 MiB, its transformation to 32 KiB, and the
result to 10,000 cells. Structured mode has no joins, per-cell overrides,
literal columns, pivots, or transposes. Use summary mode for a small explicit
mapping or retain a presentation-ready direct table when repeated enumeration
would be unwieldy.

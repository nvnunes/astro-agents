# Summary Evidence Tables

Use this file for a small table whose cells come from several retained
selections or cannot be expressed as one repeated record-to-row mapping.
Summary mode enumerates exact cells; it does not infer joins or alignment.

## Workflow

Author the marked Markdown table first. Write one regular non-symlink UTF-8 JSON
definition beneath `/private/tmp`, then preflight it:

```text
<skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
  --definition /private/tmp/<name>.json --dry-run
```

Repeat without `--dry-run` only after the preflight succeeds. Use
`evidence update` for an existing ID. The CLI never modifies or retains the
temporary file, which is limited to 8 MiB.

## Definition Shape

Summary mode accepts 1–32 ordered source objects. A cell source reference has
exactly `input` and `item`; it never uses `field`. `rows` is a non-empty
rectangular array whose width equals `headings`.

```json
{
  "sources": [
    {
      "source": "<baseline>",
      "locator": {"select": [["fwhm_mas"]], "expect": {"items": 1}}
    },
    {
      "source": "<candidate>",
      "locator": {"select": [["fwhm_mas"]], "expect": {"items": 1}}
    }
  ],
  "transformation": {
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
          "render": {"decimal_places": 3, "mode": "fixed"},
          "source": {"input": 0, "item": 0}
        }]
      },
      {
        "form": "scalar",
        "unit": "mas",
        "values": [{
          "parse": "decimal",
          "render": {"decimal_places": 3, "mode": "fixed"},
          "source": {"input": 1, "item": 0}
        }]
      }
    ]]
  }
}
```

The first cell may be one exact non-empty `label` when that row also contains
evidence. Other cells use the closed scalar, percentage, range,
`plus_minus`, interval, tuple, text, Boolean, or sequence forms. Boolean styles
are `true_false`, `yes_no`, and `pass_fail`. Sequence styles are `slash`,
`comma`, and `dimensions`, with 2–8 numeric values and one optional unit.

Every selected item across every source must be consumed exactly once. Labels
are the only authored cells. The transformation is limited to 32 KiB and the
result to 10,000 cells. Stop and have the recorded research retain a
presentation-ready table when explicit rows become repetitive, or when the
desired result requires joins, inferred labels, overrides, or unselected
literals.

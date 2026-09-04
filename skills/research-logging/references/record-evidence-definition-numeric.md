# Advanced Numeric Evidence

Use this file for an exact statistic that needs a closed numeric or compound
transformation unavailable through common evidence arguments. The CLI owns the
registry mutation; do not edit `evidence.json`.

## Workflow

Author the marked statistic first, write the definition beneath `/private/tmp`,
then preflight and apply it:

```text
<skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
  --definition /private/tmp/<name>.json --dry-run
<skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
  --definition /private/tmp/<name>.json
```

Use `evidence update` for an existing ID. The temporary file is a bounded
regular non-symlink UTF-8 JSON object with exactly `sources` and
`transformation`; the CLI never modifies or retains it.

## Numeric Shape

Each source item has exactly `source` and `locator`. `sources[0]` is input `0`,
and its selected values are items `0`, `1`, and so on. Every selected item must
be consumed exactly once.

Use `"transformation": null` only when one selected primitive already has the
exact presented type and canonical spelling. Otherwise use one closed form:

| Form | Values | Result shape |
| --- | ---: | --- |
| `scalar` | 1 | `value[unit]` |
| `percentage` | 1 | proportion multiplied by 100 and suffixed with `%` |
| `range` | 2 | `lower–upper[unit]` |
| `plus_minus` | 2 | `value ± uncertainty[unit]` or `value +/- uncertainty[unit]` |
| `interval` | 3 | `value [lower, upper][unit]` |
| `tuple` | 2–8 | `(value, value, …)[unit]` |

Except for `percentage`, each form uses `values`. A value expression contains
one `source` and may contain `parse`, `magnitude`, `scale`, and `render`, in
that order. Use `parse:"integer"` or `parse:"decimal"` only for a complete
numeric string. Use `magnitude:true` only for absolute value. `scale` must be a
nonzero finite number and does not prove that a scientific conversion is
valid.

Numeric values require one renderer: `integer`, `grouped_integer`, `fixed`,
`significant`, or `scientific`. Fixed precision is 0–18 decimal places;
significant and scientific precision is 1–18 significant figures. Add
`sign:"always"` only when non-negative values must show `+`. Units are at most
32 UTF-8 bytes and use the exact presented spelling.

The transformation object is limited to 32 KiB and at most 10,000 output
parts. Stop and have the recorded research produce a derived value or complete
display string when the presentation needs arithmetic, labels, separators, or
formatting outside these closed forms.

## Example

```json
{
  "sources": [{
    "source": "<fit-results>",
    "locator": {
      "select": [["estimate"], ["uncertainty"]],
      "where": [{"op":"eq","path":["case"],"value":"candidate"}],
      "expect": {"items": 2, "matches": 1}
    }
  }],
  "transformation": {
    "form": "plus_minus",
    "unit": "mas",
    "values": [
      {
        "parse": "decimal",
        "render": {"decimal_places": 2, "mode": "fixed"},
        "source": {"input": 0, "item": 0}
      },
      {
        "parse": "decimal",
        "render": {"decimal_places": 2, "mode": "fixed"},
        "source": {"input": 0, "item": 1}
      }
    ]
  }
}
```

This form accepts only the canonical spacing around `±` or `+/-`. A dry-run
failure means the source, transformation, marker, or presented spelling must be
corrected; do not bypass it with direct registry editing.

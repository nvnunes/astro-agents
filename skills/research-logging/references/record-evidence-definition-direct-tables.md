# Direct Evidence Tables

Use this file when one retained table already has exactly the presented rows,
columns, membership, and order. Direct mode formats same-position cells but
does not combine, omit, duplicate, or reorder source fields.

## Workflow

Author the marked Markdown table first. Write a regular non-symlink UTF-8 JSON
definition beneath `/private/tmp`, then run:

```text
<skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
  --definition /private/tmp/<name>.json --dry-run
```

Repeat without `--dry-run` only after the complete comparison succeeds. Use
`evidence update` for an existing ID. The CLI never modifies or retains the
temporary file, which is limited to 8 MiB.

## Definition Shape

Direct mode has exactly one source. Its locator selects one retained table or
one grouped record selection. The transformation has this shape:

```json
{
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
        "render": {"decimal_places": 2, "mode": "fixed"}
      }
    }
  ]
}
```

`columns` has the same length as `headings` and the selected source width. A
descriptor applies to its same-position source column and never contains a
source reference. The closed descriptors are:

- `text` for an exact string;
- `boolean` with `true_false`, `yes_no`, or `pass_fail`, optionally parsing an
  exact Boolean string;
- `percentage` with optional `decimal_places`; and
- `scalar`, with numeric operations in `value` and an optional shared cell
  unit.

The complete definition is therefore:

```json
{
  "sources": [{
    "source": "<errors>",
    "locator": {
      "select": [["case"], ["error"]],
      "identity": [["case"]],
      "expect": {"items": 4, "matches": 2}
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
          "render": {"decimal_places": 2, "mode": "fixed"}
        }
      }
    ]
  }
}
```

Tables are limited to 10,000 cells and transformations to 32 KiB. If the
source is not already rectangular in the required order, use structured mode
for repeatable field composition, summary mode for a small explicit mapping,
or have the recorded research retain a presentation-ready table. Do not force
an indirect mapping into direct mode.

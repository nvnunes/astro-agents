# Advanced Evidence Sources

Use this file only when common evidence arguments cannot express the required
source selection or the presentation consumes several retained sources. This
definition mode writes `evidence.json`; do not edit that registry directly.

## Workflow

1. Author the presentation and its `eid` marker first.
2. Write one temporary JSON definition beneath `/private/tmp`.
3. Run the complete preflight:

   ```text
   <skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
     --definition /private/tmp/<name>.json --dry-run
   ```

4. If the preflight succeeds, repeat the command without `--dry-run`. Use
   `evidence update` instead of `evidence add` for an existing ID.

The CLI reads but never edits, retains, or removes the temporary definition.
The file must be a regular non-symlink UTF-8 JSON file no larger than 8 MiB and
must contain exactly `sources` and `transformation`.

## Source Shape

`sources` is an ordered array. Each item has exactly this shape:

```json
{
  "source": "<results>",
  "locator": {"select": [["success_rate"]]}
}
```

Use 1–8 sources for a statistic, one for an output, one for a direct or
structured table, and 1–32 for a summary table. Array order assigns
transformation inputs starting at zero. A source is one complete file token
from the owning entry's `data.json`; a directory token must include one exact
member. Direct paths, remote targets, bare directory tokens, and cross-entry
shorthand are invalid.

A locator is a non-empty object containing only applicable keys:

- `path`: an exact path made from string keys, non-negative indexes,
  `{"slice":[start,stop]}`, or `{"all":true}`;
- `select`: a non-empty ordered array of relative paths;
- `where`: non-empty `eq` or `in` conditions combined with AND;
- `identity`: paths forming a unique scalar tuple for every matched record;
- `property`: a supported structural property;
- `text`: exact line selection by `contains` and optional `occurrence`; and
- `expect`: optional exact `matches`, `items`, `shape`, or `identities`.

`text` is mutually exclusive with the other selection keys except `expect`.
Use `parse:"integer"` or `parse:"decimal"` in a condition only when comparing
a complete lexical string numerically. Expectations assert retained structure;
they never select or reorder it.

Supported value containers are bounded CSV, TSV, JSON, NPZ, HDF5/MATLAB 7.3,
and UTF-8 text. Locator identities are limited to 8 KiB, selections to 10,000
items, record scans to 100,000 records, text and JSON sources to 64 MiB, and
binary materialization to 64 MiB per member and 512 MiB total. Stop and retain
a smaller purpose-built source when the intended selection exceeds a bound.

## Example

For a retained CSV with `case` and `success_rate` columns:

```json
{
  "sources": [{
    "source": "<results>",
    "locator": {
      "select": [["success_rate"]],
      "where": [{"op":"eq","path":["case"],"value":"candidate"}],
      "identity": [["case"]],
      "expect": {
        "identities": [["candidate"]],
        "items": 1,
        "matches": 1
      }
    }
  }],
  "transformation": {
    "form": "percentage",
    "source": {"input": 0, "item": 0}
  }
}
```

Do not guess a selector, weaken an expectation to make it pass, or use a
transformation to discard extra values. Narrow or regenerate the retained
source, or stop for researcher direction when the intended selection is
unclear.

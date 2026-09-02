# Evidence Locator Instructions

Use this file when adding or changing an entry evidence source. A locator
selects the exact retained values used by one presentation; it does not format,
relabel, calculate, or search by meaning.

Each `sources` item has exactly this shape:

```json
{
  "source": "<results>",
  "locator": {"select": [["success_rate"]]}
}
```

Source-array order defines transformation inputs starting at zero. Use one
complete `<name>` or `<directory-name>/member` token from the owning entry's
`data.json`. A bare directory token is invalid because each evidence source is
one exact local regular file. Every source must be a bounded CSV, TSV, JSON,
NPZ, HDF5/MATLAB 7.3, or UTF-8 text selection. Retain a safe companion artifact
for pickle or another opaque or execution-capable format.

Before using a mutable or remote `<name>` target as evidence, retain a stable
or content-addressed observation and select that retained source. Do not make
mechanical evidence depend on live remote content.

## Locator Grammar

A locator is a non-empty JSON object containing only applicable keys:

- `path`: an array locating a base value;
- `select`: a non-empty ordered array of relative paths;
- `where`: a non-empty array of record conditions combined with AND;
- `identity`: a non-empty ordered array of paths that uniquely identify every
  matched record;
- `property`: one supported structural property;
- `text`: an exact text selector; and
- `expect`: explicit membership, item-count, or shape assertions.

A path is an array of string keys, zero-based non-negative indexes,
`{"slice":[start,stop]}` half-open slices whose bounds may be null, or
`{"all":true}` expansions. Do not use negative indexes, recursive search,
expressions, or implicit key coercion.

Each `where` condition uses `op:"eq"` with `value` or `op:"in"` with
non-empty `values`, plus one `path`. Add `parse:"integer"` or
`parse:"decimal"` only to compare a lexical string numerically. Conditions
filter retained records; they never calculate tolerances, classifications, or
derived values.

`text` is mutually exclusive with the other selector keys except `expect`.
`where` and `identity` require record-like candidates. Use ordinary JSON
literals in conditions; if an exact typed predicate cannot be expressed
confidently, select through a stable string or record identity or retain a
simpler projection rather than guessing.

Use `identity` when several matched records need stable membership or explicit
presentation order. It must resolve to a unique scalar tuple for every record.
Use `expect` only for invariants that should fail if the retained structure
changes:

```json
{
  "matches": 2,
  "items": 4,
  "identities": [["case-8"], ["case-15"]]
}
```

`matches` counts records before field expansion, `items` counts selected
values, `shape` asserts one selected compound value, and `identities` requires
`identity`. Expectations assert; they do not select or reorder.

## Source Profiles

- For CSV or TSV, omit `path` or use `[]`; use `select` for fields and `where`
  for rows. Cells are strings unless a condition or transformation explicitly
  parses them.
- For JSON, provide `path`, including `[]` for the root. Select explicit keys
  or array positions; do not search recursively.
- For NPZ, select exact member names and array positions. Object arrays are
  prohibited.
- For HDF5 or MATLAB 7.3, select exact groups, datasets, indexes, or slices.
  Do not follow external or escaping links. Use fixed-length strings when text
  must be selected; variable-length strings cannot be inspected within the
  pre-materialization byte bound.
- For text, use `{"text":{"contains":"exact text"}}`. Add a positive
  one-based `occurrence` or `"all"` when the text is not unique. The complete
  matching line is selected.

Supported structural properties include `shape`, `shape[n]`, and `size`, plus
`row_count` or `columns` for tables and `members`, `member_count`, or `dtype`
where the source profile provides them.

## Examples

For this retained CSV:

```csv
case,success_rate
baseline,0.641
candidate,0.676
```

select the candidate value with:

```json
{
  "source": "<results>",
  "locator": {
    "select": [["success_rate"]],
    "where": [{"op": "eq", "path": ["case"], "value": "candidate"}],
    "expect": {"matches": 1, "items": 1}
  }
}
```

For nested JSON, select one exact value with:

```json
{
  "source": "<results-json>",
  "locator": {
    "path": ["simulation", 0, "throughput_pix_per_s"],
    "expect": {"items": 1}
  }
}
```

For a retained command log, select its unique complete result line with:

```json
{
  "source": "<run-log>",
  "locator": {"text": {"contains": "completed 500 trials"}}
}
```

Narrow an ambiguous selection rather than relying on equal values, incidental
row order, or the transformation to discard extra items.

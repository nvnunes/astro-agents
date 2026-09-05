# Advanced Retained-Output Evidence

Use this file when a marked `text` output block needs an explicit retained-text
locator or transformation that common evidence arguments cannot express.
Whole artifacts use the common one-source workflow instead of this mode.

## Workflow

Retain the complete output through the recorded command workflow, then author
the `eid` marker immediately before the `text` fence. Write one regular
non-symlink UTF-8 definition beneath `/private/tmp` and run:

```text
<skill>/scripts/log evidence add --path <log> --entry <entry-id> --id <id> \
  --definition /private/tmp/<name>.json --dry-run
```

Repeat without `--dry-run` after the complete comparison succeeds. Use
`evidence update` for an existing ID. The CLI never modifies or retains the
temporary file, which is limited to 8 MiB.

## Definition Shape

An output has exactly one source and selects exactly one string. Use a text
locator to select one complete retained logical line:

```json
{
  "sources": [{
    "source": "<run-log>",
    "locator": {
      "text": {"contains": "completed 500 trials", "occurrence": 1},
      "expect": {"items": 1, "matches": 1}
    }
  }],
  "transformation": {
    "form": "text",
    "values": [{"source": {"input": 0, "item": 0}}]
  }
}
```

`contains` is exact and case-sensitive. `occurrence` is a positive one-based
integer or `"all"`; omit it only when exactly one line matches. The selected
value is the complete matching line. `form:"text"` passes that one selected
string through unchanged. `transformation:null` is also valid when the single
selected string already equals the complete output-block payload exactly.

An output fence has info string `text`. Its complete payload, after structural
line-ending handling, must equal the selected result. It cannot combine lines,
assemble prefixes or suffixes, select a whole opaque artifact, or consume more
than one source. Text and JSON source observation is limited to 64 MiB and the
marked presentation to 1 MiB. Retain a smaller purpose-built text result when a
bound is exceeded; do not reconstruct command output from agent context or
copy it into the definition.

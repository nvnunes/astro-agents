# Presented Evidence Instructions

Use this file when recording, updating a summary, or reviewing computational
evidence in a research log. These rules make intended evidence mechanically
discoverable. They do not assess scientific interpretation.

Use `docs/research-log-evidence-record-spec.md` as the normative contract for
the exact evidence schema, locators, transformations, association rules, and
failure behavior. This reference defines the authoring workflow.

Entry evidence exists only in experimental sections. Synthesis and prose
sections contain no mechanical evidence targets. Validation reports a
structurally invalid section rather than guessing its type or partially
validating it.

## Presented Entry Evidence

Use these forms:

- Mark an inline numerical result in an experimental section with an adjacent
  hidden evidence ID. Keep names, connective wording, and parameters outside
  the marked code span:

  ```markdown
  Overall error fell to `0.286%`<!-- eid:overall-error -->.
  ```

- Put a hidden evidence ID on the source line immediately before a Markdown
  table under `Results:`:

  ```markdown
  <!-- eid:configuration-table -->
  | Configuration | Error |
  | --- | ---: |
  | Candidate | 0.286% |
  ```

- Put a hidden evidence ID on the source line immediately before a `text`
  fence under `Results:`. Retain the complete selected output in a command log;
  do not reconstruct it from agent context.
- Present an artifact directly with a local link or image embed under
  `Results:`. A direct artifact presentation uses its Markdown target and
  recorded-command provenance; it has no evidence record or `eid` marker.

Tables, output fences, artifact links, and image embeds outside experimental
`Results:` are not mechanical evidence targets. A numerical result in
experimental prose is separate evidence even when the same value appears in a
table.

Use one stable descriptive ID per presented statistic, table, or output. IDs
use lowercase letters, digits, and internal hyphens, begin with a letter, and
remain stable when wording, headings, or values change. The ID must not include
the evidence value. The marker is exactly:

```html
<!-- eid:descriptive-id -->
```

Do not add spacing, attributes, aliases, or prose inside the marker. A statistic
marker immediately follows its one code span on the same source line. A table
or output marker occupies the immediately preceding source line with no blank
or prose line between it and the target.

## Entry Evidence Records

Store presentation records in `evidence.json` at the owning entry root. Use the
exact top-level object defined by the evidence specification. The required
schema identifier is `research-log-evidence/v2`; this is a data-format value,
not an authoring-mode choice.

Each presentation record contains:

- `id`: the stable ID shared with one Markdown marker;
- `document`: the entry document path relative to the maintained-log root;
- `kind`: `statistic`, `table`, or `output`;
- `sources`: one or more ordered retained-source objects; and
- `transformation`: `null` for supported identity presentation or one closed
  transformation object.

Each source object contains exactly `source` and `locator`. `source` is an
entry-relative path, a `<log>/...` path, or an exact `<name>` token from the
entry-local `data.csv`. `locator` is a bounded structured selector that returns
the precise retained values used by the transformation. Do not use an absolute
path, URL, object-store URI, whole-artifact selection, or free-form selector
prose in an evidence record.

Example:

```json
{
  "schema": "research-log-evidence/v2",
  "records": [{
    "id": "candidate-success-rate",
    "document": "entries/2026-08-27-e001-study/entry.md",
    "kind": "statistic",
    "sources": [{
      "source": "data/results.csv",
      "locator": {
        "select": [["success_rate"]],
        "where": [{
          "op": "eq",
          "path": ["case"],
          "value": "candidate"
        }]
      }
    }],
    "transformation": {
      "form": "percentage",
      "source": {"input": 0, "item": 0}
    }
  }]
}
```

The corresponding presentation is:

```markdown
The candidate success rate was `67.6%`<!-- eid:candidate-success-rate -->.
```

Create, update, or remove a record and its marker in the same authorized
research operation. Remove `evidence.json` after removing its final record.
Record and Replace own entry records within their normal scope. A validation
agent reads but never edits them.

## Locators And Transformations

Use locators to select retained values, not to describe how to present them.
Prefer exact record identities, field paths, bounded conditions, structured
key paths, array indexes or slices, and distinctive text selections. Include
explicit identity and cardinality expectations when the selected structure
could otherwise drift or become ambiguous. Do not use pickle as a mechanically
inspected source; retain a safe CSV, JSON, NPZ, HDF5, or text projection.

Use only the closed transformations defined by the evidence specification.
They support natural but mechanically bounded presentation:

- scalar values with controlled parsing, rounding, notation, sign, and unit;
- proportions presented as percentages, with one decimal place by default;
- ranges, value-with-uncertainty expressions, intervals, tuples, and exact
  text; and
- direct, structured, and summary tables with declared headings and cell
  recipes.

For example, the percentage form above converts retained `0.676` to `67.6%`.
An interval may present `5.2 [4.8, 5.7] ms`; a tuple may present `(1024, 2048)
px`; and a value with uncertainty may present either `3.4 +/- 0.2 ms` or
`3.4 ± 0.2 ms` under the same `plus_minus` form.

A transformation may select, reorder, assemble, scale, round, and format only
as the closed grammar permits. It may not introduce aggregation, subtraction,
ratios, fitting, classification, or another new calculation. Retain a derived
value in a supported source and select it directly. An equivalent presentation
outside the supported grammar fails and should be rewritten naturally using a
supported form.

## Retained Material

Use an entry-local `retention` record only for retained material that is
intentionally kept but is not connected through evidence, a direct artifact
presentation, a recorded command relationship, or a used `data.csv` input.
Name exact paths or all descendants of one exact entry-local directory:

```json
{
  "id": "optimizer-debug-traces",
  "kind": "retention",
  "paths": ["data/debug-trace.json"],
  "reason": "Diagnostic output retained for later investigation."
}
```

```json
{
  "id": "intermediate-wavefronts",
  "kind": "retention",
  "directory": "data/intermediate-wavefronts",
  "membership": "all-descendants",
  "reason": "Intermediate states retained for later comparison."
}
```

The optional `reason` records research-agent intent for semantic review.
Mechanical validation ignores its meaning. A retention record affects only
unused-material hygiene; it does not create evidence, provenance, command, or
dependency relationships. Remove a declaration that becomes redundant.

## Summary Evidence

A maintained summary may present a numerical statistic only by referencing an
already supported entry statistic or exact numerical table cell. Put the
hidden reference immediately after the summary's code span:

```markdown
The full-sample runtime was `12.3 ms`<!-- ref entry = e004a; eid = full-sample-runtime -->.
```

```markdown
The selected error was `0.286%`<!-- ref entry = e001; eid = configuration-table; row = 2; column = 3 -->.
```

`entry` is the exact entry document ID. `eid` names the record in that entry's
`evidence.json`. Table coordinates are one-based body-row and presented-column
coordinates. The summary expression must exactly match the referenced entry
presentation or table cell; the summary does not declare another source,
locator, transformation, or producer.

Do not originate a new computation in the summary. If a summary needs a
different value, unit, or rounding, first establish that presentation as entry
evidence or leave it as ordinary unmarked synthesis prose.

## Review And Validation Boundaries

During research-log review, report apparent computational evidence that lacks
a supported form, marker, reference, or record. Also report malformed,
duplicate, extra, conflicting, misplaced, or structurally stale metadata. Do
not decide whether selected evidence scientifically supports the surrounding
claim.

Mechanical validation uses code only. It checks exact association, source
selection, transformation, presentation, command provenance, and retained-
material hygiene. Missing, ambiguous, unsupported, or incorrect metadata is a
completed finding. The validation agent reports it precisely and never edits
research-owned material.

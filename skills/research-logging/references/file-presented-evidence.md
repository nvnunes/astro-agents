# Presented Evidence Instructions

Use this file when adding or changing a presented result, artifact, or
summary evidence reference. The agent chooses what the research presents and
where it belongs. The public CLI owns evidence-record validation and storage.
Never create, inspect, or edit its registry during ordinary Record.

Entry evidence exists only under `Results:` in an experimental section. A
numerical result in experimental prose is separate evidence even when the same
value appears in a table.

## Presentation Markers

Use one stable descriptive lowercase ID for each statistic, table, retained
output, or whole artifact. Do not include the value in the ID.

- Put a statistic marker immediately after its single code span:

  ```markdown
  Overall error fell to `0.286%`<!-- eid:overall-error -->.
  ```

- Put a table or retained-output marker on the source line immediately before
  the Markdown table or `text` fence, with no intervening line:

  ```markdown
  <!-- eid:configuration-table -->
  | Configuration | Error |
  | --- | ---: |
  | Candidate | 0.286% |
  ```

- Put an artifact marker immediately after its local Markdown link or image
  embed on the same source line, with no intervening characters:

  ```markdown
  ![Residual map](images/residual-map.png)<!-- eid:residual-map -->
  [Download results](data/results.csv)<!-- eid:results-download -->
  ```

The marker is exactly `<!-- eid:descriptive-id -->`. Keep names, connective
wording, and parameters outside a marked statistic's code span.

## Common Evidence Workflow

1. Retain the source and register it as an input through
   `references/file-data-index.md` when it does not already have a local
   `<name>` token. Require that transaction to succeed before continuing.
2. Author the complete presentation and marker first.
3. Resolve `<skill>/scripts/log` from this skill package. Read only
   `log evidence add --help` or `log evidence update --help`, then invoke the
   selected action with the logical log path, stable entry ID, evidence ID, and
   one source token:

   ```text
   <skill>/scripts/log evidence add --path <log> --entry <entry-id> \
     --id <id> --source <name> [common selection or conversion arguments]
   ```

   `<log>` is the logical base whose summary is `<log>.md`; do not pass the
   summary file itself. Use `--select` with a JSON Pointer such as `/accuracy`.
   Use repeated `--where <pointer> <string|integer|decimal|boolean|null>
   <value>` to select matching records and repeated `--identity` to assert
   stable row identity. Use `--as-percentage` only when the retained proportion
   is intentionally presented as a percentage, and `--scale` only for a
   researcher-authorized scientific scale conversion.
   For a whole artifact, pass only its one source token; the action recognizes
   the marked link or image and rejects selection or conversion arguments.
4. Require the command to succeed. It resolves and fingerprints the source,
   infers the document and evidence kind from the unique marker, records exact
   selection expectations, checks the presentation, and publishes the complete
   record. Do not open a registry to inspect or confirm a successful result.

Invoke dependent authoring actions separately. Read each bounded result and
stop at the first failure instead of sending the next action in the same shell
invocation.

This common path covers whole artifacts, one-source identity statistics, inferred scalar
rendering and units, fractional percentages, explicit scaling, direct tables
whose selected source already has the presented shape, and one selected line
of retained `text` output. Use a complete `<name>` token for a file or
`<directory-name>/member` for one exact directory member. A bare directory,
raw path, URI, or cross-entry shorthand is not an evidence source.

## Advanced Definition Routing

When the intended presentation clearly needs one of the forms below, or the
common action reports `evidence.common.unsupported`, do not edit the registry
or start Repair. Read exactly one matching reference:

- complex source selection or multiple sources:
  `references/record-evidence-definition-sources.md`;
- a compound or otherwise advanced numerical presentation:
  `references/record-evidence-definition-numeric.md`;
- a direct table needing explicit column formatting:
  `references/record-evidence-definition-direct-tables.md`;
- a table built by applying one column recipe to repeated source records:
  `references/record-evidence-definition-structured-tables.md`;
- a small table assembled from several exact retained selections:
  `references/record-evidence-definition-summary-tables.md`; or
- a retained-output presentation needing an explicit text recipe:
  `references/record-evidence-definition-outputs.md`.

That reference supplies the focused `sources` and `transformation` definition.
Write only that bounded definition under `/private/tmp`, run the documented
`--dry-run`, and apply it only after the preflight succeeds. A valid advanced
case is still Record, not Repair.

If current research-owned state is malformed or legacy and prevents the owning
action from operating, stop and report the exact failure. Do not perform Repair
without a separate correction request.

## Summary Evidence

A maintained summary may present a statistic only by referencing an already
supported entry statistic or exact table cell:

```markdown
The runtime was `12.3 ms`<!-- ref entry = e004a; eid = full-sample-runtime -->.
```

```markdown
The error was `0.286%`<!-- ref entry = e001; eid = configuration-table; row = 2; column = 3 -->.
```

Table coordinates are one-based body-row and presented-column coordinates. The
summary expression must exactly match the entry presentation or selected cell.
Do not originate a calculation, source, transformation, table, output block,
or artifact in the summary.

## Boundaries

Retention records intent for disconnected material only; follow
`references/file-retention.md` when needed. During Review, report apparent
evidence that lacks a supported presentation, marker, source, or successful
authoring transaction. Mechanical validation checks exact association,
selection, transformation, presentation, provenance, and orphan state; it
never edits research-owned material.

# Presented Evidence Instructions

Use this file when recording, updating a summary, or reviewing computational
evidence in a research log. These rules make intended evidence mechanically
discoverable. They do not assess scientific interpretation.

Entry evidence exists only in experimental sections. Synthesis and prose
sections are researcher-validated and contribute no validation targets or
`evidence.csv` rows. Validation skips a structurally invalid section and
reports the skip as a failure; review determines the corrective structure.

## Entry Evidence

Use these forms:

- A local artifact link or image embed under `Results:` presents its target.
  The target must resolve either from a `<name>` input used by a recorded
  command or from a path value in a recorded command or shell capture target.
  Validation determines the value's workflow role from command and code
  context; it does not require a particular parameter name. Merely naming an
  artifact in prose does not present it.
  Prefer tables, image embeds, and marked statistics to direct links to output
  files. Link an output file only when direct inspection or reuse of the file
  is itself important; do not list output files merely as execution receipts.
- A Markdown table under `Results:` is presented evidence. Its values must come
  from a retained artifact. Selection, reordering, rounding, equivalent numeric
  notation, and Markdown reformatting are allowed. Any derived value must
  already exist in the retained artifact. Use plain formatting for numerical
  cells; reserve backticks for visibly textual identifiers or code labels.
  Inline code spans inside a table do not create separate statistic targets.
  No `Source:` line is required.
- A `text` fence under `Results:` is presented generated output. Copy the
  relevant excerpt from a retained log written by the recorded command itself.
- An inline backticked numerical result expression is a presented statistic
  wherever it appears in an experimental section. Mark each independently
  supported value separately with its units, keeping names and connective
  comparison wording outside the markers: write "Overall Error fell from
  `0.292%` to `0.286%`." A derived comparison such as `14.3% lower` is one
  evidence item only when retained as its own result.

Tables, artifact links, image embeds, and generated-output fences outside
experimental `Results:` are not presented evidence. They remain available for
non-evidential information. Backticked text, dates, identifiers, and parameters
remain non-evidential when they are visibly non-statistical. In synthesis and
prose sections, use normal Markdown rather than validation-oriented evidence
marking.

Several presented forms may share supporting artifacts. Associating them with
those artifacts can require semantic inspection. Record that association in
`evidence.csv` as defined below.

## Entry Evidence Record

Create `evidence.csv` at the entry root when the folder contains at least one
presented statistic, Markdown table, or generated-output block. Use one row for
each such item and this exact header:

```csv
entry,section,kind,evidence,sources,transformation
```

- `entry` is the owning entry document ID. Always include it because one entry
  folder may contain split documents.
- `section` is the exact preceding Markdown heading.
- `kind` is `statistic`, `table`, or `output`.
- `evidence` identifies the item from its content. Use the marked numerical
  expression, ordered table headings, or first distinctive non-empty output
  line. Add minimal surrounding wording or an occurrence number only when
  needed to make it unique within the section.
- `sources` names the immediate retained source. Use entry-relative paths,
  `<log>/...` paths, or an exact `<name>` token from entry-local `data.csv`.
  Separate several sources with ` | ` and a source from its optional locator
  with ` :: `. Do not use raw absolute paths, URLs, or object-store URIs.
- `transformation` is optional. Use it only for selection, ordering, table
  assembly, rounding, or equivalent formatting. Never use it for a new
  calculation. Name any formatting scale conversion explicitly, such as
  converting a retained fraction to percent or expressing a count in thousands;
  a `%` or `k` suffix does not by itself authorize that conversion.

A statistic names exactly one retained artifact, an output block names exactly
one retained command log, and a table names one or more retained artifacts.
Add a stable locator when the entire source does not unambiguously support the
evidence. Prefer row keys, fields, structured key paths, datasets, records, or
distinctive text regions over line numbers.

### Source Locator Format

Omit the locator only when the whole artifact narrowly and unambiguously
supports the presented item. Otherwise use one of these forms after ` :: `:

- For CSV, TSV, or other record tables, separate exact-match filters with
  `; ` and name the result column with `field=<name>` or result columns with
  `fields=<name>|<name>`. Use `|` without surrounding spaces for several exact
  values in one filter. A comma is literal data, not a list separator. Prefix
  a filter with `where.` when its source column is named `field`, `fields`,
  `path`, `property`, or `text`; for example, use `where.field=<value>` before
  the result selector `field=<name>`.
- For JSON, NPZ, HDF5, or another safely readable structured container, use
  `path=<key path>`. Use `path=$` for the container root, separate mapping keys
  with `.`, use `[n]` for a list or array index, and use `[start:stop]` for a
  slice. Add exact-match filters and `field=` or `fields=` when the selected
  path contains records, aligned arrays, or a mapping or group whose relative
  children supply the evidence. Relative fields may use `.`, indexes, or `/`
  HDF5 dataset paths. Every filtered field must align with the filter field.
  Add `property=shape`, `property=shape[n]`, or `property=size` only when the
  presented evidence is structural metadata of the selected array or dataset.
- For text or command logs, use `text=<distinctive literal fragment>`.

Do not use pickle as a mechanically inspected evidence source. Pickle loading
can execute code and its object layout is implementation-specific. Produce a
retained CSV or JSON summary through an explicit recorded command when evidence
would otherwise depend on a pickle property.

Use `; ` only between locator clauses and `|` without surrounding spaces only
between field names or alternative exact values. Values containing commas need
no locator escaping, though the enclosing `sources` cell still requires normal
CSV quoting. Do not put `;` or `|` inside a locator value; select a stable key
or a different distinctive fragment instead. Keep selection and formatting
details in `transformation`, not as free-form locator prose.

Examples:

```text
data/comparison.csv :: case=1 NGS center, R=17.0; treatment=raw_ref; field=ee_rel_rms_pct
data/comparison.csv :: case_id=8|15; fields=case_id|oomao_cross_fraction
data/comparison.csv :: where.field=validation_error_percent; candidate=2x256; field=absolute_difference
data/results.json :: path=simulation[0].throughput_pix_per_s
data/results.json :: path=simulation; fields=policy|throughput_pix_per_s
data/results.json :: path=$; level=6; field=median_delta
data/run.npz :: path=metadata.median_total_ram_gib
data/run.npz :: path=$; labels=base; field=ee_wind_delta_deg
data/run.npz :: path=$; fields=seconds|num_ph|fwhm_cv_mean|ee_cv_mean
<training_pool> :: path=status/state; property=shape[0]
data/smoke.h5 :: path=$; fields=status/state|stats/sr; property=shape
data/run.log :: text=completed 49152 outer pixels
```

Do not add rows for artifact links, image embeds, or artifact collections.
Their targets must resolve directly through a recorded command or `data.csv`.
Do not use an evidence row to repair an unresolved artifact presentation.

## Summary Evidence

A maintained summary may quote only backticked numerical statistics as direct
computational evidence. Each statistic must already appear as supported
evidence in an experimental entry section, and the summary links to that entry
rather than to an artifact. Do not put Markdown tables, images,
generated-output fences, or artifact links in a maintained summary. Do not
originate a new statistic in the summary.

Create log-level `evidence.csv` when the summary contains at least one presented
statistic. Use one row per statistic and this exact header:

```csv
statistic,entry,section,transformation
```

`statistic` uses the same content-derived selector as an entry statistic.
`entry` and `section` identify exactly one supporting entry and its exact
preceding heading. Use `transformation` only for permitted rounding or
reformatting between the entry and summary.

## Evidence Record Maintenance

Create, update, or remove evidence rows in the same operation as their
presented items. Record and Replace own entry-level rows; Replace remains within
its authorized scope. Update Summary owns log-level rows. Reorganize may update
only the `entry` or `section` value in a log-level row when its approved move or
heading change makes that identifier stale; it does not otherwise maintain
summary evidence.

Update a row when its selector, section, source, locator, or transformation
changes. If entry evidence moves to another section, document, or entry, or its
preceding heading is renamed, update the corresponding entry-level row in the
same operation. A source-content change does not require a row edit when its
identity and locator remain correct. The next Validate request detects the
content change from saved fingerprints.
Delete a header-only `evidence.csv` after removing its last required row.

Record clear factual associations without separate approval. Report an
association that cannot be established confidently rather than guessing. A
validation agent reads but never edits, deletes, or rebuilds these records.

## Review Boundary

During research-log review, report computational content that appears intended
as evidence but does not use an approved form. Also report approved forms whose
placement or record format violates these rules. Confirm that every statistic,
table, and output has exactly one structurally valid row with a unique selector,
permitted kind, valid source cardinality, and valid source-expression syntax.
Report missing, duplicate, extra, malformed, or structurally stale rows. Do not
adjudicate source resolution, evidence equivalence, transformations, provenance,
scientific method, interpretation, or conclusions during review.

Also report evidence rows for synthesis or prose sections and any section that
does not satisfy one of the three section forms. Review owns these structural
findings even when validation also reports that it skipped an invalid section.
When the researcher asks to fix a reported row, use Record or Replace for an
entry-level row and Update Summary for a log-level row.

## Validation Boundary

A validation workflow begins only from approved presentation forms in
experimental sections. It ignores synthesis and prose sections. It may use
semantic inspection to verify a declared evidence association, but it must not
create a target from unmarked narrative content or from the mere existence of a
file, script, command, number, or citation. Missing, malformed, ambiguous,
stale, or incorrect required evidence rows fail Provenance. Provenance and
scientific interpretation remain separate concerns.

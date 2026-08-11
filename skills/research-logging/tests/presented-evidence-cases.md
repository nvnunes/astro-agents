# Presented Evidence Cases

Use these cases for focused review of research-logging record, summarize, and
review behavior. They are not research evidence.

## Section Types

An experimental section contains both `Steps:` and `Results:` and may contain
`Observations:`. It enters agent-led validation. A synthesis section contains
`Findings:` without experimental labels; its prose, tables, statistics, and
links receive no evidence rows or validation targets. A prose section contains
no block labels and is also skipped.

A section with `Findings:` plus `Steps:` or `Results:`, only one of `Steps:`
and `Results:`, or another unsupported label combination is invalid. Review
reports the structural problem. Validation skips the content, reports the entry
and heading, and retains a failed structural target until review repairs it.

## Recorded Command Output

Given a `bash` command under `Steps:` that ends with
`2>&1 | tee data/run.log` and a `text` excerpt under `Results:`, record accepts
the form and adds one `output` row to entry-level `evidence.csv` with
`data/run.log` as its only source. Review checks the row shape. Validation
remains responsible for verifying the excerpt-to-log association.

Given only a `text` excerpt under `Results:` with a statement that an agent
copied terminal output, review reports that the recorded command did not retain
the source output.

## Statistics

Given "MSE was `0.184` for `seed=42`", the result expression is presented
evidence and the visibly named parameter is not. Given "MSE was 0.184", review
reports an apparent unmarked result. Given a derived claim such as `14.3%
lower`, record requires that the derived value already exist in one retained
artifact and adds one `statistic` evidence row naming that artifact.

## Tables And Artifacts

A Markdown table, image embed, or artifact link under `Results:` is presented
evidence. The same form outside `Results:` is not a validation target; review
reports it only when the content appears intended as evidence.

A presented table may select columns, reorder rows, round values, and reformat
Markdown from one or more retained sources. Record adds one `table` evidence
row that identifies every source and any necessary locators or transformation.
A new derived column without a retained source is not acceptable.

An image embed or artifact link under `Results:` receives no evidence row. Its
target must resolve directly through a recorded command or `data.csv`; an
evidence row cannot repair an unresolved target.

## Summary

A summary may contain a backticked numerical statistic already supported by
exactly one entry section and link the point to that entry. Record adds one row
to log-level `evidence.csv`. Review reports a summary table, image,
generated-output fence, artifact link, newly calculated statistic, statistic
without entry support, or malformed summary evidence row.

## Source Locators

Given a CSV record selected by `case=1 NGS center, R=17.0; field=value`, the
comma remains part of the exact filter value. Given several exact values or
result fields, the locator uses `|` without surrounding spaces. It never uses a
comma as a list separator.

Given a table whose filter column is named `field`, `fields`, `path`,
`property`, or `text`, the locator prefixes that filter with `where.`, as in
`where.field=validation_error_percent; field=absolute_difference`. The
unprefixed reserved name continues to select the result field or structured
operation.

Given a JSON or container value, the locator uses `path=` with dot-separated
keys and optional indexes or slices. `path=$` selects the root. A root list of
records or aligned NPZ arrays may use exact filters followed by `field=` or
`fields=`. Relative structured fields may traverse nested JSON keys or HDF5
dataset paths. Dataset counts and shapes use only `property=shape`,
`property=shape[n]`, or `property=size`.

Given a retained log excerpt, the locator uses `text=` with a distinctive
literal fragment. A locator may be absent only when the whole artifact narrowly
and unambiguously supports the presented item. Review reports free-form locator
prose, ambiguous delimiters, or a missing locator for a broader artifact.
Validation never deserializes pickle evidence and instead requires a retained
CSV or JSON producer summary.

## Evidence Record Maintenance

When an entry heading changes, record updates the affected `section` values.
When presented evidence is removed, record removes its row and deletes a
header-only file. When an artifact changes at the same path without invalidating
the declared locator, record leaves the row unchanged and marks validation
stale.

Review reports missing, duplicate, extra, malformed, or structurally stale
rows. It checks selector uniqueness, kind values, source cardinality, and source
syntax but does not decide whether the evidence matches the source. Validation
resolves the source, checks the locator and transformation, and verifies logical
equivalence and provenance. An ambiguous association is reported rather than
guessed or recorded.

## Mechanical-First Validation

Given a complete `evidence.csv` row whose source exists, parses in a known
format, and is produced through a statically resolved recorded command,
validation completes the deterministic checks before requesting agent review.
It sends only equivalence or provenance details that the tool cannot decide
reliably to semantic fallback.

Given a locator-selected statistic that differs only by recorded rounding,
percentage conversion, scientific notation, or unit formatting, validation may
establish logical equivalence mechanically. A table transformation, compound
custom structure, ambiguous locator, or uncertain workflow connection remains
unresolved for bounded semantic review.

Given an apparent mechanical failure, the validation agent reviews the
reported context before retaining `FAIL`. The agent may correct the temporary
adjudication when the tool result is a false positive, but it never modifies
the entry, artifact, producer, `data.csv`, or `evidence.csv`.

Given a nonempty review queue, validation first generates a compact packet of
presented context, locator-selected values, structural results, and candidate
commands. The packet helps the agent make bounded semantic decisions; it does
not turn a candidate command or matching numeral into a successful check.

Given unchanged successful checks, validation reuses their dates only after
mechanically reconciling the current scope and fingerprints. A changed entry,
association record, artifact, producer, input, or cached Summary locator
invalidates only the affected checks.

Given a cached outcome and a later change to its presented item or
`evidence.csv` association, validation compares that outcome's stored
dependency identities with the current identities and reopens it. Rendering
some other updated outcome never refreshes the changed dependency snapshot
around the old result.

Given a newly recorded command that produces an existing evidence artifact,
validation detects the changed dependency contract and reopens the affected
Provenance outcome even when every previously known dependency identity is
unchanged.

Given a command established as part of a used evidence workflow, its declared
sibling outputs and shell capture logs are not orphans. If the workflow
consumes an upstream generated artifact, validation follows that artifact to
its producer and applies the same rule there. A directory connects only the
members selected during bounded collection review.

Given a file owned by one maintained research log and consumed by an active
recorded or validated workflow in another, the repository reverse-dependency
index removes it from the owner's orphan candidates. Its transitive script
dependencies are also used. A dormant script containing a possible cross-log
reference does not protect the referenced file. Changing an unrelated log
without changing the owner's incoming edge slice does not invalidate the
owner's validation fingerprint.

Given same-named local modules under directories added by consecutive
`sys.path.insert(0, ...)` calls, validation follows the module found through
the effective final path order. The module under the last inserted directory
is used; a shadowed same-named module remains an orphan candidate unless some
other active workflow reaches it.

## Data Index

A `data.csv` row consumed through `<input_data>` by a recorded command is valid.
A generated CSV may receive a row only when a later recorded command consumes
it as input. Review reports an unused row, an entry-local script or image row,
an unresolved token, or a raw external input path that should use `<name>`.

A valid indexed input in a workflow branch that does not reach presented
evidence is not an index-hygiene finding; it remains eligible for orphan
validation.

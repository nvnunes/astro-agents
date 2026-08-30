# Legacy V1 Evidence Upgrade Reference

## Status And Authority

Status: approved through Phase 1, Pass 5A on 2026-08-28 and frozen as an
upgrade-input contract.

This document is the source of truth for parsing and evaluating legacy v1
`evidence.csv` records during the explicitly invoked per-log upgrade defined
by `docs/research-log-evidence-record-spec.md`. It owns:

- the legacy entry and summary CSV schemas;
- v1 source expressions;
- v1 locator syntax, canonicalization, and source-profile behavior;
- v1 identity and free-form transformation behavior; and
- upgrade-only v1 conformance and failure identification.

It does not define an active standard-validation mode. Standard validation is
v2-only. Encountering `evidence.csv` during standard validation produces the
public `validation.upgrade_required` cutover result; it does not invoke this
reader automatically.

The active specification owns the upgrade operation, v2 `evidence.json`,
presentation association, command-derived provenance, orphan detection,
result composition, shared value model, and resource and safety limits. If
this reference and the active specification conflict outside the v1 parsing
and evaluation surface named above, the active specification governs.

## Shared Evaluation Contracts

Upgrade-only v1 evaluation uses the following contracts from
`docs/research-log-evidence-record-spec.md`:

- source resolution and classification;
- the common ordered selection result and canonical value model;
- resource and safety bounds;
- dependency projection and currentness; and
- precise failure reporting.

The upgrade reader returns a v1 selection through that common result model so
the upgrade operation can compare it with the candidate v2 declaration. This
does not make v1 an active validation language.

V1 record selections without stable keys use their matched source ordinals in
the dependency projection. Text selections use the selector identity, match
rank among matching lines, and selected text content rather than an absolute
line number.

## Version Selection

- An explicit v1 locator begins with `v1:`.
- An unprefixed locator is an implicit v1 locator.
- The prefixed and unprefixed forms have the same canonical identity.
- Canonical v1 serialization includes the explicit `v1:` prefix.
- A locator with any other `v<integer>:` prefix fails as an unsupported
  version.
- Version selection occurs before v1 parsing.
- A failed v1 parse or evaluation does not fall forward to v2.
- A v2 parse or evaluation failure never falls back to v1.

V1 is frozen around valid retained usage. Its grammar and semantics must not
be expanded to make upgrade cases pass.

## Evidence CSV File Contract

V1 `evidence.csv` is UTF-8 without a byte-order mark and uses RFC 4180
quoting. Its exact header depends on location.

An entry-root file uses:

```csv
entry,section,kind,evidence,sources,transformation
```

A maintained-log-root summary file uses:

```csv
statistic,entry,section,transformation
```

The file must contain at least one non-empty data row. Reordered, missing, or
additional columns, empty rows, and unexpected fields fail.

### Entry Rows

An entry row contains:

- `entry`: the owning entry document ID;
- `section`: the exact preceding Markdown heading;
- `kind`: `statistic`, `table`, or `output`;
- `evidence`: the content-derived presentation selector;
- `sources`: one or more source expressions separated by ` | `; and
- `transformation`: empty identity, explicit v1, or implicit v1.

The composite `(entry, section, kind, evidence)` must be unique in the file.
A statistic or output row has exactly one source expression. A table row has
one or more. Association uses the retained v1 content selector and occurrence
rules. Because rendered content participates in identity, a presentation
change may invalidate the association. That limitation is frozen.

### Summary Rows

A summary row contains `statistic`, `entry`, `section`, and `transformation`.
`statistic` is the content-derived summary selector. `entry` and `section`
identify one supporting experimental entry section. The row inherits the
supporting entry association through retained v1 matching. Logical
equivalence of surrounding summary prose is not a mechanical conclusion.

Every required field must be non-empty except `transformation`. V1 rows
associate only unmarked presentations. The reader must reject malformed rows
and silent parser quirks; compatibility does not make malformed retained data
valid.

## Source Expressions

A v1 source reference has one of these forms:

```text
<source>
<source> :: <locator>
```

Several source references use:

```text
<source-reference> | <source-reference>
```

The outer separator is one vertical bar surrounded by one ASCII space on each
side.

- ` | ` always terminates the current source reference. V1 values cannot
  contain that sequence.
- The first ` :: ` in a source reference separates the source from the
  locator.
- Source paths and data-index tokens must not contain ` :: ` or ` | `.
- The surrounding `sources` field follows ordinary CSV quoting.
- An empty locator after `::` is invalid.

Omitting the locator selects the whole artifact by its complete retained
content identity and provides no source-internal values. The upgrade operation
uses the retained association rules to decide whether the presentation kind
may rely on that whole-artifact selection.

A multi-source row evaluates each source reference independently, in declared
order. Source-reference order defines the transformation input slots used
during upgrade inspection.

## Locator Language

### Purpose

V1 formalizes the compact clause language and source-profile conventions used
by retained `evidence.csv` rows. It is not extended to solve mechanical gaps
assigned to v2.

### Grammar

```text
v1-locator   = clause, { "; ", clause } ;
clause       = name, "=", value ;
name         = 1*( letter | digit | "_" | "." | "-" ) ;
value        = 1*( any character except ";", carriage return, or line feed ) ;
alternatives = value-part, { "|", value-part } ;
```

Additional rules:

- `; ` separates clauses.
- `|` without surrounding spaces separates selected fields or alternative
  exact filter values.
- A comma is literal data.
- V1 has no escaping syntax.
- A locator value must not contain `;`, ` | `, or a literal `|` intended as
  data.
- Clause names are case-sensitive.
- Clause values are trimmed at both ends.
- Duplicate clause names are invalid.
- `field` and `fields` are mutually exclusive.
- `field` names exactly one selected field.
- `fields` names one or more unique selected fields in output order.
- `path`, `property`, and `text` are reserved.
- Any other clause is an exact-match filter.
- `where.<name>` names a filter whose source field collides with a reserved
  clause name.
- Repeating a filter clause is invalid. One clause with `|` alternatives is
  the valid form.
- Empty names, values, alternatives, and duplicate alternatives are invalid.
- A semicolon must never substitute for the outer multi-source ` | `
  separator.

For example, this retained form is invalid:

```text
item=a; item=b; field=value
```

The valid v1 form is:

```text
item=a|b; field=value
```

### Canonicalization

A v1 normalizer:

- emits the explicit `v1:` prefix;
- emits `path` first when present;
- emits filters next in lexicographic clause-name order;
- emits `field` or `fields` next;
- emits `property` last when present;
- emits `text` as the only clause for text locators;
- preserves `fields` order;
- sorts filter alternatives after rejecting duplicates; and
- rejects duplicate clauses rather than applying first-wins or last-wins
  behavior.

Canonicalization does not rewrite research-owned evidence records.

### Paths

V1 structured paths use these forms:

```text
path=$
path=simulation[0].throughput_pix_per_s
path=values[2:6]
path=$.trials[*].score
```

- `$` denotes the source root and is optional before the first key.
- `.` separates mapping keys.
- `[n]` selects a signed sequence index.
- `[start:stop]` selects a half-open slice; either bound may be omitted.
- `[*]` expands one sequence level.
- Slice steps, predicates, recursive descent operators, unions, and executable
  expressions are unsupported.
- Keys containing `.`, `[`, or `]` are not representable in v1.

## Source Profiles

### CSV And TSV

- `field` or `fields` is required.
- Other non-reserved clauses are exact lexical filters.
- Filter clauses combine with AND; alternatives within one filter combine
  with OR.
- Headers and values match exactly after CSV or TSV parsing.
- Selected records retain source order.
- Results are row-major, then selected-field order.
- Duplicate headers fail.
- Zero selected records fail.
- V1 has no declared expected cardinality or stable record-key field.

### JSON

- `path` may select a scalar, record, record sequence, index, slice, or
  wildcard expansion.
- With `path` and no fields, the selected value must be scalar unless
  `property` selects supported structural metadata.
- With `path` and `field` or `fields`, fields are selected relative to the
  resolved record or record sequence.
- Without `path`, `field` or `fields` uses recursive record search. Every
  mapping is visited in document order, filters are applied, and mappings
  containing all selected fields contribute values.
- Filters compare the string rendering of JSON scalar values with v1 filter
  alternatives.
- `property` may be `shape`, `shape[n]`, or `size` when the selected value has
  a mechanically defined array shape.
- Zero selected values fail.

Recursive no-path selection is retained only for upgrade compatibility. V2
requires an explicit path or table root.

### NPZ

- `path` is required.
- The root path `$` exposes flat named arrays.
- A member path may include indexes or slices.
- At `$`, `field` or `fields` selects aligned named arrays.
- Filters name aligned one-dimensional arrays and compare their
  string-rendered scalar values.
- Selected and filtered arrays must have equal first-axis lengths.
- Object arrays and pickle loading are prohibited.
- `property` may be `shape`, `shape[n]`, or `size`.

The legacy `key=<name>; indices=<start>:<stop>` form is an accepted alias for
the equivalent member path when otherwise unambiguous.

### HDF5

- `path` is required; `$` denotes the file root.
- Path segments use `/` for group and dataset names.
- `field` or `fields` selects relative datasets from a group.
- `property` may be `shape`, `shape[n]`, or `size`.
- Exact-match filters are unsupported.
- Links may not escape the retained file.
- Object deserialization and execution-capable filters are prohibited.

The legacy `datasets=a|b` form is an accepted alias for root dataset selection
with `property=shape` when that meaning is unambiguous.

### Plain Text

- `text=<literal>` is the only v1 text form.
- Matching is exact, case-sensitive substring matching within logical lines.
- Every matching line is returned in document order.
- A zero-match selection fails.
- V1 has no occurrence selector.

### Indexed Sources

A data-index token resolves before source-profile dispatch. The resolved
format then follows the corresponding v1 profile.

### Unsupported Sources

V1 source-internal value selection is unsupported for ECSV, Parquet, YAML,
notebooks, NPY, FITS, MAT, images, PDFs, SVG, Python, pickle, directories, and
opaque files. Such material may still be selected as a whole artifact when
the retained v1 evidence association permits it.

## Transformations

An empty v1 `transformation` field declares identity. An explicit v1
transformation begins with `v1:`; a non-empty unprefixed transformation is
implicit v1. Any other version prefix fails as unsupported.

Empty identity requires the selected values and associated presentation to
satisfy the active specification's exact identity contract. It does not
authorize tolerant comparison.

A non-empty v1 transformation is opaque prose written for semantic use. It
has no mechanical operation semantics, phrase grammar, controlled vocabulary,
rounding rule, unit registry, output model, or canonical semantic projection.
It therefore fails upgrade evaluation as
`transformation.v1.nonmechanical`.

That failure identifies:

- the exact v1 row identity;
- the original v1 phrase;
- every ordered locator input reference and canonical typed value;
- the associated presented item when available; and
- the violated `transformation.v1.nonmechanical` rule.

The upgrade tool does not perform keyword matching, phrase inference, or LLM
interpretation and does not generate supported-form lists, source-reference
skeletons, repair choices, or candidate v2 declarations. The research agent
consults the active specification and authors the v2 record.

## Upgrade-Only Failures

Upgrade-only failures use the active specification's precise failure payload:
stable code, exact subject, observed state, violated rule, and any dependency
cause. Relevant reserved codes include:

| Code | Condition |
| --- | --- |
| `evidence.csv.header_invalid` | The header matches no location-appropriate v1 profile. |
| `evidence.file.encoding_invalid` | The CSV is not permitted UTF-8. |
| `evidence.file.empty` | The file is header-only. |
| `evidence.declaration.invalid` | A row violates its exact field, enum, cardinality, or uniqueness constraints. |
| `locator.v1.duplicate_clause` | A locator repeats one clause name. |
| `locator.v1.delimiter_ambiguous` | A source or locator uses an invalid or ambiguous v1 delimiter. |
| `transformation.v1.nonmechanical` | A non-empty legacy transformation has no executable mechanical meaning. |

Other locator source, syntax, selection, safety, availability, and resource
failures use the applicable stable codes defined by the active specification.

## Conformance Examples

Valid retained forms:

```text
data/comparison.csv :: case_id=8|15; fields=case_id|oomao_cross_fraction
data/comparison.csv :: v1:case_id=8|15; fields=case_id|oomao_cross_fraction
data/results.json :: path=simulation[0].throughput_pix_per_s
data/results.json :: path=$; level=6; field=median_delta
data/run.npz :: path=$; labels=base; field=ee_wind_delta_deg
data/smoke.h5 :: path=$; fields=status/state|stats/sr; property=shape
data/run.log :: text=completed 49152 outer pixels
```

Invalid retained forms:

| Form | Failure |
| --- | --- |
| `item=a; item=b; field=value` | `locator.v1.duplicate_clause`; use one `item=a|b` clause. |
| `field=` | `locator.syntax.invalid`; empty value. |
| `fields=a||b` | `locator.syntax.invalid`; empty alternative. |
| `text=a|b` when `|` is literal data | `locator.syntax.invalid`; v1 cannot escape it. |
| `source-a.csv :: field=x; source-b.csv :: field=y` | `locator.v1.delimiter_ambiguous`; the outer separator is ` | `. |

## Evolution And Retirement

This contract is frozen. A change to v1 grammar or meaning is prohibited;
upgrade cases that do not conform require a research-agent-authored v2
declaration or research-record correction.

The reference remains normative only while maintained logs or supported
upgrade operations require v1 input. After all maintained logs have been
upgraded and v1 upgrade tooling is retired, this document may be archived or
removed together with the reader and its fixtures.

# Research-Log Mechanical Validator Specification

## Status And Authority

Status: active mechanical-validator implementation specification as of
2026-08-30.

This document is the normative implementation contract for the code-only
research-log mechanical validator, its tests, generated records, cache,
diagnostics, and public operation. It defines the evidence-record, association,
provenance, orphan-detection, and generated-state contracts that validator code
implements.

The specification includes `evidence.json`, presentation-marker, locator,
transformation, and command-discovery syntax because these are inputs to the
validator. The self-contained research-logging skill carries the bounded
authoring and operational rules agents need to produce compatible research
logs; ordinary research-agent work does not load this implementation
specification.

The provenance audit is rooted in evidence and direct artifact presentations,
not in complete validation of every recorded command. Recorded command
surfaces, optional adjacent command annotations, exact path and named-input
connections, and observed retained material establish provenance without a
parallel authored provenance file. Resolved scripts remain workflow and
currentness inputs, but their internals do not establish material
associations.

The complete specification owns:

- the evidence-file and record contract;
- entry presentation and evidence-record identity and association;
- inline summary-to-entry evidence references, including exact table-cell
  coordinates;
- source expressions and whole-artifact references;
- locator versions and their ordered typed selections;
- presentation transformations over those selections;
- evidence-rooted recorded-command discovery, producer and upstream lineage,
  trusted external and model/simulation roots, material collections, and
  named-input connection;
- orphan detection for unused retained material and data-index names;
- validation evaluation order, result scopes, failures, resource bounds, and
  currentness composition;
- the public validation operation, result envelope, completion and exit
  meanings;
- generated mechanical-record, cache, lock, and human-report contracts; and
- unsupported-metadata preflight and completed-validation boundaries.

No implementation, skill reference, test fixture, or shorter human guide may
define a competing evidence, locator, transformation, association,
command-provenance, outcome, or generated-state contract. A feature is added
only when a concrete specification contradiction or retained-corpus case
passes the natural-authoring gate.

The key words **must**, **must not**, **should**, and **may** describe normative
requirements.

## Evidence-Record Role And Scope

An entry evidence record declares a mechanically checkable relationship
between one presented entry item and one or more retained evidence sources. A
summary statistic instead references one validated entry record or one exact
cell in a validated entry table. Together these contracts answer:

> Which retained evidence supports this presented item, which exact source
> material is used, and which declared presentation operations relate that
> material to what appears in the research log?

### Natural Research Surfaces And Mechanical Metadata

The research record should preserve three natural surfaces:

- a natural, often heavily parameterized recorded command;
- the natural retained outputs produced by that command; and
- a natural presentation of selected evidence in research prose, tables, and
  output excerpts.

Mechanical validation does not require any of those surfaces to become a
validator-specific declaration language. Instead, deterministic discovery and
the evidence record supply the minimum additional structure needed to connect
them:

```text
recorded command -- command discovery --> retained output
                                             ^
                                             | source + locator + transformation
                                             |
natural presentation <-- eid ------------- evidence.json

summary statistic -- entry + eid ---------> entry presentation
                  \-- row + column -------> exact table cell, when applicable
```

Command discovery establishes which recorded invocation reads or writes an
exact retained material path. Entry-local `evidence.json` identifies the presented item,
names its retained source, selects the supporting values, and declares their
presentation transformation. Canonical material identity joins the two
results. A summary reference reuses that completed entry result without
declaring another source or transformation. Neither surface duplicates the
complete contents of the command, output, or prose.

This division permits natural authoring while keeping the relationship
mechanically strict. When a natural surface cannot be connected under the
supported rules, validation fails with the unresolved relationship. It does
not ask an LLM to infer the connection or require a parallel provenance record.

This specification owns:

- active entry-local v2 `evidence.json` serialization, schema, and record-level
  constraints;
- stable presentation and evidence-record identities;
- exact presentation-to-record association;
- exact summary-reference syntax, resolution, table coordinates, and
  comparison;
- one or more source references per evidence record, including whole artifacts;
- source-internal locator syntax and semantics;
- transformations, ordering, assembly, and formatting applied to locator
  selections;
- evidence-record conformance, failures, resource bounds, and currentness
  projections.

This specification does not decide:

- whether evidence, a method, a result, an observation, a decision, or a claim
  is scientifically or semantically sound; or
- whether a command can be reproduced.

Semantic review and reproduction retain their separate conceptual boundaries.
Mechanical validation observes recorded research state but never executes a
recorded command.

### Locator Subcontract

An evidence locator identifies a bounded, ordered selection inside one resolved
evidence source. It answers:

> Which retained source values, fragments, or structural properties support
> this presented item?

A locator owns:

- source-internal paths, fields, filters, indexes, slices, and occurrences;
- exact selection membership, cardinality, shape, and order;
- normalized selected values and source-relative identities;
- source-class-specific selection and property semantics;
- locator failures, limitations, resource bounds, and currentness projection.

A locator does not decide:

- how a presented item is identified or associated with one evidence record;
- how multiple source selections are assembled for presentation;
- rounding, scaling, unit conversion, relabeling, or other presentation
  transformations;
- producer, provenance, semantic-review, or reproduction conclusions.

The locator is one evidence-record component. The presentation-transformation,
evidence-association, command-provenance, material-graph, orphan-detection, and composed
outcome subcontracts below own the remaining stages.

## Active Version

V2 is the only active locator language.

- A standalone v2 locator begins with `v2:`.
- A v2 `evidence.json` source embeds the locator's JSON object without that
  prefix.
- A locator with any other `v<integer>:` prefix fails as unsupported.
- Version selection occurs before version-specific parsing.
- A v2 parse or evaluation failure is a mechanical failure and is not retried
  under another interpretation.

## V2 Evidence Source Objects

A v2 `evidence.json` record does not serialize source expressions into one
delimited string. Its ordered `sources` array contains objects with exactly
`source` and `locator`:

```json
{
  "source": "data/results.csv",
  "locator": {
    "select": [["success_rate"]]
  }
}
```

`source` follows the path, token, resolution, and safety rules of the outer
evidence-source contract. JSON owns field separation; the string has no
embedded source-list or locator delimiter grammar.

`locator` is the JSON object portion of an explicit v2 locator. It must not
contain a `v2:` string prefix. The evaluator treats it as v2 before parsing and
uses `v2:` followed by its canonical JSON serialization as the locator
identity. A v2 source object cannot contain a serialized locator string or omit
`locator`.

Array order defines transformation input slots. There is no outer source-list
parser, mixed locator version, or CSV escaping in this host form.

## Common Evaluation Contract

Evaluation proceeds in this order:

1. Resolve exactly one retained source.
2. Establish the source content identity and supported source profile.
3. Select the locator version.
4. Parse and normalize under that version.
5. Evaluate under the source profile and resource bounds.
6. Verify any declared v2 identity, cardinality, and shape expectations.
7. Return a selection, a stable failure, or an unavailable observation.

A conforming evaluator must not guess misspelled fields, choose among ambiguous
matches, infer omitted historical facts, recursively search unless the selected
version explicitly requires it, or reinterpret a failed locator under another
version.

### Source Resolution And Classification

Source resolution precedes locator evaluation. A source profile is established
from:

- the data-index declaration or retained source declaration when present;
- the retained byte signature and safe structural inspection;
- the filename extension only as supporting metadata.

A declared format that conflicts with retained bytes fails as
`locator.source.format_mismatch`. A missing or inaccessible source is reported
under the evidence source-resolution contract. A source that changes during
locator evaluation is `unavailable`.

A data-index token is part of source identity. After resolution, its locator
uses the resolved source's profile. A remote target must have a stable retained
or content-addressed observation before it can produce a successful selection.

### Evaluation Outcomes

Locator evaluation returns one of:

- `selected`: one valid ordered selection was produced;
- `fail`: a stable syntax, version, source-profile, selection, expectation, or
  safety defect was established; or
- `unavailable`: a trustworthy selection could not be observed because of a
  temporary access condition, missing runtime reader, or source change during
  observation.

`not_applicable` belongs to the calling mechanical check, not to locator
evaluation. Unsupported syntax, versions, source profiles, or operations are
`fail`, not `unavailable`.

A failed locator is a completed mechanical result with no semantic fallback.

## Common Selection Result

The active v2 evaluator returns this ordered selection shape:

- the declared and effective locator version;
- the canonical locator identity;
- the resolved source identity and source profile;
- an ordered list of selected items;
- observed membership, item count, and shape;
- the dependency projection needed for currentness;
- the effective resource-limit profile.

Each selected item has:

- a canonical source-relative coordinate;
- a canonical type descriptor;
- a canonical value projection;
- optional source metadata required to interpret that type.

### Canonical Value Model

A source adapter must map every selected value to one of these types or fail as
unsupported:

- `null`;
- `boolean`;
- `integer`, with exact signed magnitude;
- `decimal`, with exact coefficient and base-ten exponent;
- `binary_float`, with bit width and exact bit pattern, including signed zero,
  NaN, and infinities;
- `string`, preserving exact Unicode code points without normalization;
- `bytes`, preserving exact byte content;
- `date`, `time`, `datetime`, or `duration`, with exact source resolution and
  timezone metadata when present;
- `quantity`, containing one supported numeric value and one explicit unit;
- `array`, with element type, shape, order, and ordered elements;
- `record`, with ordered selected fields;
- `table`, with ordered columns, ordered records, and optional identity fields;
- `mapping`, with keys ordered by canonical key representation;
- `masked`, distinct from `null` and from a missing field.

Source-specific objects, executable objects, arbitrary Python objects, and
implementation-language representations are not selection values.

Canonical projections are validator-internal tagged values so source types do
not collapse accidentally. Evidence authors do not normally write those
projections. The smaller authored literal grammar below uses ordinary JSON
scalars for ordinary values and reserves tagged objects for values JSON cannot
express unambiguously.

### Typed Equality

V2 `eq` and `in` filters use canonical typed equality.

- Equal values have the same canonical type and value projection.
- NaN is not equal to any value, including another NaN.
- String comparisons are exact and use no case folding, whitespace folding, or
  normalization.
- Bytes never compare equal to strings.
- Missing and masked values are distinct from null.

### Selection Order

Unless a version or source profile states otherwise:

1. arrays use increasing source index order;
2. records use retained source order;
3. mapping expansions use lexicographically sorted canonical key order;
4. selected fields use declared `select` order;
5. text matches use document order.

A locator does not reorder selected records for display. Presentation ordering
belongs to the transformation contract.

## V2: Expanded Mechanical Locator Language

### V2 Purpose

V2 maximizes deterministic, bounded mechanical selection.

V2 adds:

- unambiguous JSON encoding;
- explicit paths and mechanically typed predicates;
- optional exact cardinality, membership, and shape assertions;
- stable record identities;
- a canonical cross-format value model;
- a small demonstrated source-profile set;
- exact currentness projections;
- stable, precisely identified failure behavior.

### V2 Encoding

A v2 locator is `v2:` followed by one UTF-8 JSON object.

```text
v2:{"path":["simulation",0,"throughput_pix_per_s"]}
```

The top-level object may contain only:

| Key | Value | Purpose |
| --- | --- | --- |
| `path` | v2 path | Select a base node or expanded node set. |
| `select` | non-empty array of relative v2 paths | Select fields, members, or child values in declared order. |
| `where` | non-empty array of conditions | Filter record-like or aligned-array candidates. |
| `identity` | non-empty array of relative v2 paths | Declare stable record identity fields. |
| `property` | string | Select a supported structural property. |
| `text` | text-selector object | Select bounded logical text lines. |
| `expect` | expectation object | Declare exact membership, item count, and shape. |

`expect` is optional. An empty top-level object is invalid.

Key relationships:

- `text` is mutually exclusive with `path`, `select`, `where`, `identity`, and
  `property`.
- `where` requires a record-like or aligned-array candidate set.
- `identity` requires a record-like candidate set.
- `property` applies after `path`, `where`, and `select` as permitted by the
  source profile.
- A source profile may require or prohibit additional combinations.

### V2 Paths

A v2 path is a JSON array. The empty array denotes the source root.

Each path segment is exactly one of:

- a string key, field, member, group, dataset, variable, or named property;
- a non-negative integer index;
- `{"slice":[start,stop]}`, where either bound may be `null` and both
  non-null bounds are non-negative integers;
- `{"all":true}`, which expands one bounded sequence or mapping level.

Examples:

```text
[]
["simulation",0,"throughput_pix_per_s"]
["trials",{"all":true},"score"]
["metric",{"slice":[2,6]}]
["stats","sr"]
```

Rules:

- Indexes are zero-based.
- Slices are half-open and have an implicit step of one.
- Expansion order follows the common selection-order rules.
- Negative indexes, slice steps, recursive descent, executable predicates,
  implementation-language expressions, and implicit key coercion are
  unsupported.
- A segment that does not apply to the encountered type fails as a type
  mismatch.
- Every expanded node contributes its resolved canonical coordinate.

### V2 Field Selection

`select` is an ordered array of relative paths evaluated against each candidate
record, mapping, group, or aligned collection.

```text
"select":[["case_id"],["value"]]
```

- Every selected path must resolve for every retained candidate after
  filtering.
- Selected paths must be unique.
- Results use candidate-major, then `select`, order.
- Relabeling is not a locator operation.

### V2 Authored Literals

Predicate values and expected identity values use ordinary JSON `null`,
Booleans, strings, integers, and finite numbers whenever those forms are
unambiguous. A strict decoder preserves the lexical value of every JSON number:
an integer token becomes a canonical integer and a token containing a decimal
point or exponent becomes an exact canonical decimal. It must not pass through
an implementation-language binary float first.

Tagged objects are reserved for exact binary-float bit patterns, bytes, dates,
times, datetimes, durations, and quantities because ordinary JSON has no
unambiguous representation for them. Their closed forms are:

| Canonical type | Literal form |
| --- | --- |
| Binary float | `{"bits":64,"hex":"3ff0000000000000","type":"binary_float"}` |
| Bytes | `{"base64":"AQI=","type":"bytes"}` |
| Date | `{"resolution":"day","type":"date","value":"2026-08-27"}` |
| Time | `{"resolution":"millisecond","type":"time","value":"13:45:00.125+00:00"}` |
| Datetime | `{"resolution":"second","type":"datetime","value":"2026-08-27T13:45:00+00:00"}` |
| Duration | `{"type":"duration","unit":"s","value":1.5}` |
| Quantity | `{"type":"quantity","unit":"m","value":8}` |

Binary-float and bytes forms retain their exact bit-pattern and canonical
base64 requirements. Temporal forms use canonical ISO 8601 spelling and exact
retained resolution. Duration and quantity `value` accepts an ordinary JSON
integer or finite number or an exact binary-float object. Arrays, records,
tables, mappings, missing values, and masked values are not authored literals.
A source profile may reject an authored literal type it cannot represent, but
must not coerce it silently.

### V2 Conditions

`where` conditions combine with AND. Each condition contains:

- `path`: one relative v2 path;
- `op`: one supported operator;
- `value`, containing one v2 authored literal, for `eq`;
- `values`, containing a non-empty array of v2 authored literals, for `in`; and
- optional `parse`, equal to `integer` or `decimal`, for comparison against a
  lexical string field.

Supported operators are:

- `eq`;
- `in`.

Examples:

```text
"where":[{"op":"in","path":["case_id"],"values":["8","15"]}]
"where":[{"op":"eq","parse":"decimal","path":["score_text"],"value":0.95}]
```

Unknown operators fail. `eq` and `in` use typed equality.

Predicate-side parsing is part of source selection, not presentation:

- `parse` accepts only a source string and affects only that condition's source
  operand. It does not change the selected source value.
- Every condition is type-checked for every candidate presented to `where`.
  Short-circuit evaluation of another condition must not hide a missing field,
  invalid parse, or type mismatch.
- `integer` accepts exactly the v2 integer-string grammar.
- `decimal` accepts the JSON number grammar and maps it to the canonical
  coefficient-and-exponent representation. Leading or trailing whitespace,
  non-finite spellings, and locale-specific notation fail.
- With `eq` or `in`, every comparison literal must have the type produced by
  `parse`.
- Parsing an invalid lexical value fails as `locator.predicate.parse_failed`;
  it does not merely exclude that candidate.

An unresolved condition path fails as `locator.field.missing`. Null may be
matched explicitly with ordinary JSON `null`; missing and masked-state tests
are deferred until demonstrated retained cases warrant their additional
surface area.

Conditions are filters only. They do not calculate aggregates, tolerances,
scientific classifications, or derived values.

### V2 Record Identity

`identity` declares the relative paths whose ordered values identify each
selected record.

```text
"identity":[["case_id"]]
```

- Every identity path must resolve for every matched record.
- Every identity path must resolve to one scalar canonical value.
- The ordered identity tuple must be unique across matched records.
- Duplicate identity tuples fail.
- Record identity affects membership projection but does not sort records.
- A record selection expecting more than one match must declare `identity`
  unless the source profile supplies an inherent stable coordinate, such as an
  array index.

### V2 Expectations

Optional `expect` may contain only:

| Key | Value | Meaning |
| --- | --- | --- |
| `matches` | positive integer | Exact candidate count after path expansion and filtering. |
| `items` | positive integer | Exact final selected-item count. |
| `shape` | array of non-negative integers | Exact shape when one compound array or table value is selected. |
| `identities` | non-empty array of identity tuples | Exact ordered record identities after filtering. |

When `expect` is present, at least one expectation key is required.

- Every declared expectation is checked.
- `matches` counts candidates after path expansion and filtering but before
  `select` field expansion. For text, it counts all matching lines before an
  occurrence selector is applied.
- `items` counts final selected values after `select`, `property`, or text
  occurrence selection.
- `identities` requires `identity`. Every expected tuple must contain one v2
  authored literal per declared identity path, tuples must be unique, and the
  complete ordered tuple list must equal the observed identities.
- When `matches` and `identities` are both present, `matches` must equal the
  number of expected identity tuples.
- An undeclared dimension is still bounded but is not asserted.
- An identity-list mismatch fails as
  `locator.identity.expectation_mismatch`. Any other expectation mismatch
  fails as `locator.expectation.mismatch`.
- Expectations do not truncate, pad, or select values.
- A zero-valued evidence selection is represented by selecting a retained zero
  value, not by expecting zero matches or zero items.

Expectations are independent assertions, not routine boilerplate. Presentation
cardinality, exact transformation input consumption, and exact table dimensions
already fail when an otherwise exact selection changes incompatibly. Authors
should add `expect` when they want source membership, count, or shape to remain
an explicit invariant even if the consuming presentation could still be
formed.

For a record table with two matched records and two selected fields:

```text
"expect":{"identities":[["8"],["15"]],"items":4,"matches":2}
```

For one selected array:

```text
"expect":{"items":1,"matches":1,"shape":[2048,64]}
```

### V2 Structural Properties

`property` selects metadata defined by the active source profile.

- `shape` returns the complete ordered dimension vector.
- `shape[n]` returns one zero-based dimension and fails when that dimension does
  not exist.
- `size` returns the total logical element count.
- Profile-specific properties such as `row_count`, `columns`, `members`,
  `member_count`, and `dtype` have the exact meanings stated by that profile.
- When `select` produces several child arrays or datasets, a permitted property
  is applied to each child in `select` order.
- A collection property such as `row_count` is evaluated after `where` but
  before field expansion.
- Property selection never materializes unrelated values merely to provide
  context.
- Unsupported properties fail rather than falling back to a generic file
  inspection.

### V2 Text Selection

A text selector contains one required `contains` string and may contain
`occurrence`, a positive one-based integer or `"all"`.

Example:

```text
"text":{"contains":"Benchmark simulations","occurrence":1}
```

Rules:

- Matching is exact and case-sensitive within UTF-8 logical lines.
- No regular-expression or natural-language interpretation occurs.
- If `occurrence` is omitted, exactly one matching line is required.
- An integer selects that match in document order.
- `"all"` selects all matches.
- The complete matching logical line is the selected string. Context windows,
  prefixes, suffixes, regular expressions, and partial extraction are deferred.

## V2 Source Profiles

The v2 registry distinguishes value selection, structural-property selection,
whole-artifact selection, and prohibited sources.

### Record Tables

#### CSV And TSV

- Values are lexical strings.
- `path` is omitted or `[]`.
- `select` is required unless `property` selects `row_count` or `columns`.
- `where` supports `eq` and `in`, with optional exact `integer` or `decimal`
  parsing of lexical cells. CSV empty strings are strings, not nulls.
- A condition may use explicit `integer` or `decimal` predicate-side parsing.
  Numeric type is never inferred from a lexical cell.
- `identity` names stable columns.
- Duplicate headers fail.
- Records retain source order.

### Structured Documents

#### JSON

- `path` is required, including `[]` for the root.
- JSON objects, arrays, strings, Booleans, nulls, integers, and decimals map to
  the common value model.
- JSON number lexical form determines integer versus decimal source type.
- `select`, `where`, and `identity` operate only on explicit resolved candidate
  records.
- Recursive search is not supported.
- Supported properties are `size`, `shape`, and `member_count` when
  mechanically defined for the selected value.

### Array And Scientific Containers

#### NPZ

- The root is a mapping from exact member names to arrays.
- String path segments select members; later segments index or slice arrays.
- `select`, `where`, and `identity` may treat aligned member arrays as records
  along their common first axis.
- Aligned arrays must have equal first-axis lengths.
- Object arrays and pickle loading are prohibited.
- Supported properties are `members`, `member_count`, `shape`, `shape[n]`,
  `size`, and `dtype`.

#### HDF5 And MATLAB 7.3

- The root is the retained file root group.
- String segments select groups or datasets; later index or slice segments
  select dataset values.
- External links and links escaping the retained file are prohibited.
- `select`, `where`, and `identity` may treat explicitly selected aligned
  datasets as records along their common first axis.
- No recursive group search occurs.
- Supported properties are `members`, `member_count`, `shape`, `shape[n]`,
  `size`, and `dtype`.

### Text

#### Plain Text And Command Logs

Plain text follows the v2 text-selector contract. Files must decode as UTF-8
without replacement characters.

The initial v2 value-selection registry is intentionally limited to CSV/TSV,
JSON, NPZ, HDF5/MATLAB 7.3, and UTF-8 plain text or command logs because those
profiles cover the retained locator corpus. Images, PDFs, SVG, and source files
remain direct artifacts rather than locator containers.

### Directories, Pickle, And Opaque Sources

Directories are not locator containers. Their roles and bounded membership
mechanisms belong to the recorded-command collection-discovery subcontract.

Pickle and other execution-capable serialized objects are prohibited as
mechanically inspected value sources. The repair is to retain a supported
machine-readable companion artifact through an explicit recorded command.

An otherwise opaque source is not a v2 locator container. Authors may present
it as a direct artifact or retain a supported machine-readable companion.

### Future Source Profiles If Warranted

ECSV, Parquet, YAML, Jupyter notebooks, NPY, FITS, pre-7.3 MATLAB files, and
media or document property readers are deferred. A profile may be added only
after retained cases demonstrate that converting to an already supported
companion artifact would make normal research work materially awkward. The
addition must be safe, bounded, non-executing, and unable to change dispatch or
results for an existing profile.

### Indexed And External Sources

A resolved indexed source uses its resulting v2 profile. A mutable or
remote-only source must first yield a stable retained or content-addressed
observation. The validator must not infer current values from a URL, prose
description, or unavailable external service.

## Canonical V2 Serialization

A v2 normalizer:

- emits the `v2:` prefix;
- rejects duplicate or unknown JSON keys;
- sorts JSON object keys lexicographically;
- emits UTF-8 without ASCII-only escaping;
- uses JSON escaping for quotation marks, reverse solidus characters, and
  control characters;
- emits no insignificant whitespace;
- preserves the order of `path`, `select`, and `identity`;
- preserves the declared order of `expect.identities` tuples;
- sorts `where` conditions by their canonical serialization because condition
  order has no semantic meaning;
- sorts and deduplicates `in` values by canonical typed representation;
- maps authored JSON scalar literals to canonical internal types before
  sorting or comparison;
- preserves JSON integer tokens as integers and normalizes finite JSON number
  tokens containing a decimal point or exponent to one exact decimal form;
- validates and canonicalizes every specialized tagged literal;
- normalizes binary-float hexadecimal bit patterns to lowercase without
  changing their bits;
- preserves string code points exactly and performs no Unicode normalization;
- represents non-finite numeric predicate values only through binary-float
  tagged literals.

Canonicalization never changes research-owned rows.

## Presentation Transformation Subcontract

### Role And Boundary

A presentation transformation is a pure, bounded operation that converts one
or more ordered locator selections into one closed presentation result. It
answers:

> Which selected values are consumed, which declared operations are applied,
> and what exact evidence-bearing expression should appear in the research
> log?

The transformation subcontract owns:

- exact input consumption and output order;
- lexical parsing of selected integers and decimals;
- exact numeric interpretation of selected finite binary floats;
- unary magnitude and exact decimal scaling;
- decimal-place and significant-figure rounding, including the closed
  percentage default;
- canonical numeric rendering;
- closed Boolean table rendering and authored summary-row labels;
- exact unit suffixes;
- a small set of canonical statistic forms; and
- exact table headings, dimensions, order, and cell values.

It does not own:

- source-internal selection, filtering, identity, or cardinality;
- presentation discovery or presentation-to-row association;
- aggregation, subtraction, ratios between selected values, fitting,
  classification, or any other new derived result;
- whether a declared scale factor, unit, or transformation is scientifically
  justified; or
- whether transformed evidence supports the surrounding prose or conclusion.

A value that requires unsupported arithmetic must be retained in a supported
source and selected directly. Semantic Review decides whether the retained
calculation and the surrounding claim are sound.

### Closed-Presentation Principle

The surrounding research prose remains natural. Only the evidence-bearing
expression associated with an evidence record follows the canonical grammar.

V2 provides a closed, code-only grammar for each supported presentation form.
Most forms have one accepted spelling. A form may define a small explicit set
of equivalent surface spellings when this specification lists every accepted
alternative. The validator never expands that set through inference,
normalization profiles, fuzzy matching, regular expressions, or an LLM.

For example, when a percentage recipe produces `67.6%`, all of `67.60 %`, `67.6
percent`, and `6.76e1%` fail. The `plus_minus` form is the deliberate exception:
it accepts both `value ± uncertainty` and `value +/- uncertainty`. These two
spellings have one internal form and require no authored separator field.

This restriction is local. A log may naturally say:

> The median success rate was 67.6% across the retained trials.

Only `67.6%` is the evidence-bearing expression. The surrounding sentence is
not part of mechanical comparison.

The validator must compare a strictly parsed presented item to the closed set
defined by its declared form. It must not infer or normalize:

- undeclared rounding or numeric tolerance;
- equivalent fixed and scientific notation;
- omitted, substituted, or aliased units;
- optional signs, undeclared grouping separators, trailing zeroes, or
  whitespace;
- alternate range or interval punctuation, or uncertainty punctuation beyond
  the two `plus_minus` spellings;
- reordered values, rows, or columns;
- synonymous or approximately matching labels; or
- prose claims surrounding the associated evidence-bearing expression.

Markdown delimiters may be treated as structure only where the presentation
association contract defines them. No transformation may contain a regular
expression, template,
normalization profile, undeclared or open-ended style, synonym set, or
free-form instruction. The exact Boolean and sequence style enums defined
below are closed grammar discriminants, not extensible presentation profiles.

### Transformation Version Model

- In a v2 `evidence.json` record, `transformation: null` declares identity and
  a non-null transformation is the JSON object portion of a v2 transformation.
  It has no string prefix in the JSON host.
- Identity has canonical identity `identity`.
- An embedded v2 transformation object's canonical identity is `v2:` followed
  by its canonical JSON serialization, matching the standalone prefixed form.
- A string, array, number, or Boolean in a v2 JSON `transformation` field fails.
- A v2 parse or evaluation failure is not retried under another
  interpretation.
- Locator and transformation objects retain independent grammars and canonical
  identities within the v2 record.

V2 is the only active mechanically executable transformation language.

### Transformation Input Bundle

The input is an ordered bundle of locator selections. Input slot `0`
corresponds to the first v2 `sources` object in the evidence record, input slot
`1` to the second, and so on. Each slot exposes its selected items in locator
order.

An input reference has this form:

```json
{"input":0,"item":0}
```

Both indexes are zero-based non-negative integers. A reference addresses one
complete canonical selected item.

Non-table value expressions, non-table percentage recipes, and summary-table
cells use this concrete `input`/`item` reference. A structured-table cell
instead uses an `input`/`field` reference whose field index addresses one path
in that input locator's ordered `select` array:

```json
{"input":0,"field":2}
```

The structured-table section defines how that reference is applied once to
each matched record. `item` and `field` are mutually exclusive. Neither form
performs path traversal; source-internal paths remain locator-owned.

A direct table contains no authored source reference. Its sole input and
same-position source fields are implied by the direct-table contract.

Every input item must be referenced exactly once. V2 does not silently drop,
duplicate, broadcast, coalesce, or reuse values. Authors must narrow the
locator or retain a purpose-built source when its selection does not correspond
one-to-one with the presentation.

A locator must therefore select the specific value or values asserted by the
presented item. Selecting several equal or similarly rounded values does not
authorize a transformation to collapse them into one presentation value. When
the research claim concerns several values, present and declare them
individually, use a supported multi-value form, or retain a purpose-built
summary value that expresses the intended result.

### Identity Transformation

`transformation: null` is not permission for tolerant comparison. It declares
that the locator selection already has the exact presented values, types,
order, structure, labels, units, and lexical form.

Identity renders primitive selected values as follows:

- strings preserve their exact Unicode code points;
- integers use canonical base-ten notation with no grouping or leading plus;
- decimals use canonical plain base-ten notation with a leading zero before a
  fractional radix point and no insignificant trailing zeroes;
- Booleans use lowercase `true` or `false`; and
- null uses lowercase `null`.

Identity does not parse strings, round, scale, convert units, relabel, reorder,
or assemble values. Binary floats, quantities, bytes, dates, times, durations,
compound values, masked values, and structural properties require an explicit
supported v2 presentation or an exact retained string.

The presentation association and source-cardinality contracts define which
identity selections correspond directly to one statistic, table, or output
block. If the one-to-one association is not unique and exact, validation fails.

## V2 Transformations: Closed Presentation Recipes

### Encoding

The standalone v2 transformation form is `v2:` followed immediately by one
UTF-8 JSON object. It is used by this specification and conformance fixtures.
A v2 `evidence.json` record embeds that same JSON object directly in its
`transformation` field and omits the prefix. Both forms have identical v2
meaning and canonical identity.

The JSON must satisfy the same lexical and duplicate-key requirements as a v2
locator. Unknown keys fail. Numeric scale factors use ordinary finite JSON
numbers decoded directly to exact integer or decimal values.

A general non-table recipe has this shape:

```json
{
  "form": "scalar",
  "unit": "ms",
  "values": [
    {
      "parse": "decimal",
      "render": {"decimal_places": 1, "mode": "fixed"},
      "source": {"input": 0, "item": 0}
    }
  ]
}
```

The common fraction-to-percentage case has a smaller specialized shape:

```json
{
  "form": "percentage",
  "source": {"input": 0, "item": 0}
}
```

`percentage` consumes exactly one integer, decimal, supported finite binary
float, or complete string in the canonical decimal grammar. It parses a string
as a decimal, multiplies the numeric value by exactly 100, renders it in fixed
notation with round-half-to-even, and appends `%` directly. Its only optional
field is `decimal_places`, an integer from 0 through 18 whose default is `1`.
It has no `values`, `parse`, `magnitude`, `scale`, `render`, `unit`, or custom
suffix field. A source that already stores percentage points uses the ordinary
`scalar` form with `unit:"%"`; the specialized form always consumes a
proportion.

A table recipe begins with one of these mode discriminants:

```json
{"form":"table","mode":"direct"}
{"form":"table","mode":"structured"}
{"form":"table","mode":"summary"}
```

The table section defines the complete mode-specific grammar and approved cell
forms.

Canonical serialization uses the common v2 JSON rules and lexicographic object
keys. Array order is meaningful and preserved. For `percentage`, an explicit
`decimal_places:1` canonicalizes to the same form as omission, with the default
field omitted.

### Value Expressions

Except for the specialized `percentage` recipe, each value expression has one
required `source` and may have `parse`,
`magnitude`, `scale`, and `render` fields. Evaluation order is fixed:

1. resolve `source`;
2. apply `parse`, if declared;
3. apply `magnitude`, if declared;
4. apply `scale`, if declared; and
5. apply `render`, if required.

No field may occur more than once. There is no authored operation list and no
alternative operation order.

`parse` ordinarily accepts only `integer` or `decimal`. It consumes one
complete selected string under the canonical ASCII integer or decimal grammar.
The table-only Boolean form additionally permits `parse:"boolean"`, as defined
below. Leading or trailing whitespace, grouping, unit text, locale-specific
radix marks, non-finite tokens, and partial numeric matches fail.

`magnitude`, when present, must be JSON `true`. It applies absolute value to
an integer, decimal, or finite binary float. It is the only supported
sign-changing operation.

`scale` is one nonzero finite JSON integer or number. Its exact decoded integer
or decimal value multiplies an integer
or decimal exactly in decimal arithmetic and a finite binary float as an exact
rational value. It does not infer or verify a unit conversion. Additive
offsets, division, ratios between inputs, and authored inexact binary factors
are unsupported.

`render` is required after parsing, magnitude, or scaling and for any numeric
input in a v2 recipe. It is forbidden for strings, Booleans, and null.

A value expression without numeric fields may pass through one string or null
exactly, but only a form that explicitly permits that type may use the result:
`text` accepts a string, and a table scalar accepts null. Numeric forms require
a numeric input and `render`. A Boolean may be consumed only by the table
`boolean` form. A finite binary float may be used as a numeric input without
`parse`; its exact canonical bit pattern defines the value consumed by
`magnitude`, `scale`, and `render`. Non-finite binary floats and other compound
canonical values are unsupported transformation inputs.

### Numeric Rendering

V2 uses one rounding mode: decimal round-half-to-even. Rounding is part of the
declared renderer; no separate rounding operation exists.

A finite binary float is interpreted directly from its canonical IEEE bit
pattern as an exact signed rational value: sign, integer significand, and a
power-of-two exponent. Transformation rendering supports IEEE 754 binary16,
binary32, and binary64, identified by canonical bit widths 16, 32, and 64.
Other binary-float formats remain valid canonical locator values but fail
transformation rendering as `transformation.type.mismatch`. The evaluator must
not first convert a supported value through a language-runtime decimal string.
It applies any exact decimal scale to the rational value and rounds the result
directly under the declared renderer. This makes NPZ rendering independent of
host-language float-to-string behavior. Binary signed zero becomes canonical
numeric zero before sign rendering; NaN and infinity fail as
`transformation.nonfinite_unsupported`.

The supported renderers are:

| Mode | Required precision | Canonical result |
| --- | --- | --- |
| `integer` | none | An exact integer in base ten. A non-integral value fails. |
| `grouped_integer` | none | An exact integer with ASCII comma groups of three. A non-integral value fails. |
| `fixed` | `decimal_places` | Fixed notation rounded to exactly that many digits after the radix point. |
| `significant` | `significant_figures` | Fixed notation rounded to exactly that many significant digits. |
| `scientific` | `significant_figures` | Scientific notation rounded to exactly that many significant digits. |

Precision is an integer from 0 through 18 for `decimal_places` and from 1
through 18 for `significant_figures`. Each renderer accepts only its stated
precision field. Any renderer may additionally contain `sign:"always"` when
the presentation must show the sign of a non-negative result. No other `sign`
value is supported; omission is the ordinary negative-only behavior.

All numeric renderers use:

- ASCII digits and `.` as the radix mark;
- a leading `0` before a fractional radix point;
- `-` only for negative nonzero values;
- a leading plus only when `sign:"always"` is declared;
- no digit grouping except in `grouped_integer`;
- no surrounding whitespace;
- no negative zero;
- exact trailing zeroes required by the declared precision;
- lowercase `e` for scientific notation;
- one digit before the scientific radix point;
- no `+` and no leading zeroes in a scientific exponent; and
- `0e0` with the required coefficient zeroes for scientific zero.

`sign:"always"` prefixes `+` to positive values and canonical zero after
rounding. It does not alter a negative value or add a sign to the exponent.
This supports signed deltas and biases without accepting an optional sign for
one declaration: `sign` absent and `sign:"always"` produce different exact
presentation contracts.
When used in a direct-table column descriptor or a repeated structured-table
cell recipe, this rule applies independently to every cell: non-negative cells
show `+` and negative cells show `-`.

`significant` remains in fixed notation even when the result has many leading
or trailing zeroes. Authors should use `scientific` when fixed significant
notation would make the prose awkward.
For zero, `significant` emits `0` followed by a radix point and exactly
`significant_figures - 1` zeroes when the declared precision exceeds one;
`scientific` applies the same coefficient rule before `e0`. Thus precision
three produces `0.00` and `0.00e0` respectively.

`grouped_integer` uses the same sign and integer rules as `integer`, then
inserts one ASCII comma before each three-digit group counted from the right.
It produces `0`, `999`, `1,000`, and `-12,345`; it does not accept optional,
locale-dependent, fractional, or scientific grouping.

### Units

`unit` is optional for `scalar`, `range`, `plus_minus`, `interval`, and `tuple`
and applies once to the complete form. It must be a non-empty Unicode string of at most 32 UTF-8 bytes
with no leading or trailing whitespace, Markdown delimiters, line breaks, or
control characters.

The canonical renderer attaches `%`, `°`, `°C`, `°F`, and `x` directly to the
preceding form. Every other unit follows one ASCII space. Thus `unit:"x"`
produces `3.39x`, while `unit:"cases"` produces `4 cases`. Unit aliases are not
recognized. The declared unit is the exact expected presentation suffix; v2
does not infer its dimension, decide whether a suffix is scientifically a
unit, or infer its relationship to `scale`.

The exact string `x` is reserved for the multiplier suffix. A longer unit may
not begin with `x` followed by whitespace. Named comparators such as `MASTSEL
Ctot` belong in surrounding prose rather than in a unit declaration.

Because the form and unit are declared independently, parsing is
form-directed. A `plus_minus` recipe cannot be reinterpreted as a scalar with
an uncertainty expression stored in `unit`, and a scalar accepts only its
declared suffix.

The specialized `percentage` form supplies `%` and does not accept `unit`.
`text` also forbids it. Table recipes do not accept a recipe-level unit. A
table may express a unit in an exact column heading or in the individual cell
recipe, according to the presented table.

### Canonical Presentation Forms

V2 supports exactly these non-table forms:

| Form | Values | Canonical rendering |
| --- | ---: | --- |
| `scalar` | 1 | `value[unit]` |
| `percentage` | 1 proportion | `value%`, using exact ×100 scaling and fixed rendering with one decimal place by default |
| `range` | 2 | `lower–upper[unit]` |
| `plus_minus` | 2 | `value ± uncertainty[unit]` or `value +/- uncertainty[unit]` |
| `interval` | 3 | `value [lower, upper][unit]` |
| `tuple` | 2–8 | `(value, value, …)[unit]` |
| `text` | 1 exact string | The selected string, unchanged. |

In the table, `[unit]` means the canonical suffix from the unit rules, not
literal square brackets. `range` uses one Unicode en dash with no surrounding
spaces. `plus_minus` accepts either one Unicode plus-minus sign or the exact
three-character ASCII token `+/-`, with one ASCII space on each side in both
cases. The recipe does not contain a separator field. `interval` uses one ASCII
space before `[`, a comma followed by one ASCII space, and `]`. `tuple` uses
parentheses and comma-space separators.

`text` forbids `unit` and all numeric value fields. Its selected string must
contain the complete evidence-bearing expression. Text slicing, prefix or
suffix insertion, and substring search belong in a future locator or
transformation version only if demonstrated need warrants them.

These non-table forms do not accept custom literals, labels, separators,
templates, or named styles.
A semantically equivalent presentation outside this grammar fails.

For example, interval form may produce `5.2 [4.8, 5.7] ms` or `67.6 [64.1,
70.8]%`, and tuple form may produce `(0.31, 0.47)` or `(1024, 2048) px`.
The punctuation and spacing shown are exact.

### Tables

A table recipe contains `form:"table"`, one required `mode`, and neither
top-level `values` nor `unit`. It contains `headings`, a non-empty array of
exact presented column headings. A heading must be a non-empty Unicode string
with no leading or trailing whitespace, vertical bar, line break, or control
character. Heading order is presentation order. Units that apply to a whole
column belong in its exact heading.

V2 has three table modes:

- `direct` consumes one retained table whose rows and columns already
  correspond one-to-one with the presented table;
- `structured` applies one column recipe repeatedly to selected records when
  cells must combine or align selected fields; and
- `summary` enumerates the cells because no one repeated record-to-row mapping
  expresses the table conveniently.

The distinction is syntactic, not a judgment about scientific importance. A
one-source table with one source field per presented cell should use `direct`.
A table that composes fields uses `structured`. A small heterogeneous
comparison may use `summary`.

#### Structured And Summary Cell Recipes

A structured or summary table cell recipe uses the non-table `scalar`,
`percentage`, `range`, `plus_minus`, `interval`, `tuple`, or `text` form.
`percentage` retains its direct `source`, optional `decimal_places`, and closed
defaults; the other forms retain their value-expression, unit, rendering, and
canonical punctuation rules. `text` passes through one complete selected
string exactly. In a table cell only, that string may be empty to produce an
intentional empty cell. A scalar may also pass through null, rendered as
lowercase `null`.

Table cells additionally support one closed Boolean form:

```json
{"form":"boolean","style":"yes_no","values":[{"source":{"input":0,"item":0}}]}
```

`boolean` contains exactly one value expression with `source` and optional
`parse:"boolean"`, forbids `unit`, and requires one of these styles. Without
`parse`, the selected value must be a Boolean. With `parse:"boolean"`, the
selected value must be one complete string equal to exactly `true`, `false`,
`True`, or `False`; the two true spellings map to Boolean true and the two false
spellings map to Boolean false. Whitespace, other capitalization, `yes`/`no`,
`1`/`0`, and all other spellings fail.

| Style | `true` result | `false` result |
| --- | --- | --- |
| `true_false` | `true` | `false` |
| `yes_no` | `yes` | `no` |
| `pass_fail` | `Pass` | `Fail` |

Capitalization is exact. There are no aliases, optional capitalization, or
custom true/false strings.

Table cells additionally support one closed numeric sequence form:

```json
{"form":"sequence","style":"slash","unit":"%","values":[...]}
```

`sequence` contains from two through eight numeric value expressions, one
required `style`, and an optional shared `unit`. It supports exactly:

| Style | Canonical cell result |
| --- | --- |
| `slash` | `value / value[ / value…][unit]` |
| `comma` | `value, value[, value…][unit]` |
| `dimensions` | `value x value[ x value…][unit]` |

Bracketed text in this table describes repetition or the ordinary canonical
unit suffix; it is not emitted literally. Separators and spaces are fixed.
Thus a slash sequence with values `1.3` and `0.0` and `unit:"%"` produces
`1.3 / 0.0%`; a comma sequence with values `211` and `231` and `unit:"nm"`
produces `211, 231 nm`; and a dimension sequence produces
`109 x 400 x 400`. There is no custom separator field and no per-part unit.

Every cell result is one exact string with no vertical bar, line break,
control character, or surrounding whitespace. Nested tables and authored
Markdown delimiters are not cell transformations. The presentation-association
contract decides which Markdown delimiters are structure.

Mixed-unit compounds, labels embedded inside evidence-bearing cells, prose
fragments, arrows, inequalities, and structures outside the listed forms are
unsupported. The author must
split them into columns, retain the complete display string and select it with
`text`, or change the presentation. Examples intentionally excluded from
assembly include `98.65 nm / 19.118 mas`, `589824 match, 0 diff`, and
`-0.351 mas (-0.46%)`.

#### Direct Tables

A direct recipe consumes exactly one input slot, input 0. That input must be
either one canonical selected table or a record selection with retained record
grouping. Its ordered records are the output rows, and its ordered columns or
locator `select` paths are the output columns. The source already determines
the table's dimensions and cell membership.

A direct recipe has exactly these mode-specific fields:

```json
{
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
  ],
  "form": "table",
  "headings": ["Case", "Error"],
  "mode": "direct"
}
```

`columns` has the same length as `headings` and the selected source column
count. Each descriptor applies to the same-position source column for every
record. Column and record order are unchanged.

A direct column descriptor is exactly one of:

- `{"form":"text"}`, which requires a string and passes it through exactly;
- `{"form":"boolean","style":"true_false"}`, optionally with
  `"parse":"boolean"`, where `style` and parsing have the same exact closed
  meanings as the table Boolean form;
- `{"form":"percentage"}`, optionally with `decimal_places`, which applies
  the specialized percentage contract to the same-position source column; or
- `{"form":"scalar","value":{...}}`, with optional `unit`, where `value`
  contains the ordinary value-expression fields except `source`.

The implicit source of direct column `n` is column `n` of input 0's canonical
table or grouped-record view. For a record selection, that is locator `select`
path `n`. `value` may contain only `parse`, `magnitude`, `scale`, and `render`,
with the ordinary value-expression requirements and evaluation order. It may
be empty only for null passthrough. A numeric source still requires `render`.
The Boolean descriptor has no `value` because the same-position source is
implicit. Its optional `parse` applies to that source. The percentage
descriptor likewise has no `source`. Direct descriptors never contain
`source`, `values`, `input`, `item`, or `field`.

Every selected source field becomes exactly one presented cell and is consumed
once. Headings may relabel source columns, and one repeated column descriptor
may format its cells through parsing, scaling, rounding, sign rendering, or a
unit. Direct mode cannot combine fields, reorder rows or columns, align another
input, insert labels, enumerate exceptions, or use a multi-value or sequence
cell form. A complete retained range, compound, or other display string can be
passed through one `text` column without assembly.

If the source does not already have the required rectangular membership and
order, the table is not direct. Use `structured` for repeatable composition,
`summary` for explicit cell mapping, or retain a new direct table through the
recorded research workflow.

#### Structured Tables

A structured recipe has exactly these mode-specific fields:

```json
{
  "columns": [cell_recipe, cell_recipe],
  "form": "table",
  "headings": ["Case", "Error range"],
  "mode": "structured",
  "rows": {"input": 0}
}
```

`columns` is a non-empty array with the same length as `headings`. Each entry
is one table cell recipe. Every source reference, whether directly in a
`percentage` recipe or inside a value expression, has exactly `input` and
`field`; `item` is prohibited. The field index selects one path from that
input locator's ordered `select` array, and the column recipe is evaluated once
for each driver record.

Structured mode should perform at least one operation unavailable in direct
mode to justify its additional syntax: combine several fields in a cell,
change field order, or apply an explicit row order. A
one-input recipe with one same-position field per scalar, percentage, Boolean,
or text column and default record order is valid but discouraged; authors
should use `direct` for its smaller declaration and clearer diagnostics.
This is authoring guidance, not a validation failure: a conforming validator
must accept the structured declaration, and authoring tools may recommend the
equivalent direct form without changing the validation result.

`rows.input` must be `0`. The input must be a record selection
whose locator produced matched candidate records and retained their grouping.
Its record order is the default output order. A flat selection, one compound
table item, property selection, whole-artifact selection, or input without
record grouping cannot drive a structured recipe. The repair is to use a v2
record locator, use summary mode, or retain one direct table.

`rows` may additionally contain `order`, an array containing every driver
identity tuple exactly once in the required presentation order:

```json
{"input":0,"order":[["case-8"],["case-15"]]}
```

An explicit order requires the locator to declare `identity`. Its tuples use
v2 authored literals, must match the record identity arity and types,
and must be an exact permutation of the observed driver identities. There is
no sort expression, descending flag, or inferred order.

Structured mode is intentionally single-source. Every source reference uses
`input:0`; multi-source identity alignment and joins are deferred. A table that
draws heterogeneous cells from several sources uses summary mode when small or
retains one assembled direct table when repeated enumeration would be awkward.

Each selected field of every record in the input must be consumed
by exactly one column recipe value position. Identity metadata used for row
ordering is not a selected field and does not consume or duplicate an item.
An unreferenced field, repeated selected-field reference, or collapsed value
fails. Referencing distinct selected fields from the same input is ordinary
structured use. Locators should select only fields that the table presents.

Structured mode does not provide cell overrides, literal columns, row-label
insertion, concatenation of unrelated record streams, pivot, or transpose
operators. A regular table with exceptions uses `summary`, or the author
retains one direct table. A pivoted or transposed table uses `summary` when it
is small; otherwise its oriented result is retained and declared as `direct`.
This keeps the mechanical grammar closed instead of embedding a dataframe
language.

#### Summary Tables

A summary recipe contains `rows` as a non-empty rectangular array of
non-empty row arrays:

```json
{
  "form": "table",
  "headings": ["Metric", "Baseline", "Candidate"],
  "mode": "summary",
  "rows": [[cell_recipe, cell_recipe, cell_recipe]]
}
```

Every row length must equal `headings` length. Array order is exact row and
column order. Within every cell recipe, every source reference has exactly
`input` and `item`; `field` is prohibited. Summary mode may therefore express
small pivots, transpositions, concatenations, hybrids, and comparisons by
enumerating their resulting cells without adding separate operation grammars.

The first cell of a summary row may instead be one exact authored structural
label:

```json
{"form":"label","text":"FWHM"}
```

`label` is permitted only as the first cell of a summary row. It contains
exactly `form` and `text`; `text` follows the ordinary exact table-cell string
bounds and may not be empty. It is prose in the presented table, analogous to
an authored column heading. It does not come from an evidence source, does not
consume an input item, and is compared exactly with the presented first-column
cell. A label may orient or identify the row but does not itself establish an
evidence value. A row containing a label must contain at least one additional
evidence cell, so a label cannot be the sole cell in a row. Literal cells in
other positions remain unsupported.

Every selected item across the input bundle must be referenced by exactly one
summary evidence-cell value position. Structural labels are outside that input
count. Summary mode does not authorize other literals, overrides, input reuse,
omitted items, inferred labels, or a partial join. If explicit enumeration
becomes unwieldy, the repair is one retained direct table.

#### Table Result

All three modes produce the same canonical internal result: exact headings
followed by an ordered rectangular matrix of exact cell strings. They preserve
exact dimensions, heading order, row order, cell order, source identity,
source-item consumption, and any identity alignment. The result contains no
Markdown alignment row or source spacing.

The strict presentation parser defines the accepted Markdown table structure
and compares its parsed headings and cells to this result. Alignment-marker
width and source spacing may be structural; headings, dimensions, order, and
cell text are not.

### Evaluation Result And Currentness

A successful transformation returns:

- declared and effective transformation version;
- canonical transformation identity;
- every ordered authored or direct-implied input reference and canonical typed
  value;
- every exact intermediate value after parsing, magnitude, and scaling;
- every renderer and precision;
- the canonical internal statistic or table model and every accepted surface
  spelling defined for that form; and
- the transformation dependency projection and effective resource profile.

The transformation dependency projection contains:

1. the effective transformation version;
2. canonical transformation serialization;
3. ordered input references and input value projections;
4. ordered exact intermediate numeric values;
5. exact rendered values, accepted surface spellings, form, unit, headings,
   rows, and order; and
6. effective transformation resource limits.

A change to a used input, transformation, or associated presentation requires
re-evaluation. V2 never derives a precision, unit, form, label, order, or shape
from presentation prose.

### Conformance Examples

Fraction rendered as a percentage:

```text
v2:{"form":"percentage","source":{"input":0,"item":0}}
```

The only conforming presentation for selected string `0.676` is `67.6%`. An
explicit `"decimal_places":2` instead produces `67.60%`.

A rounded range in milliseconds:

```text
v2:{"form":"range","unit":"ms","values":[{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"input":0,"item":0}},{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"input":0,"item":1}}]}
```

For selected strings `3.417` and `4.184`, the canonical result is
`3.42–4.18 ms`.

An estimate with uncertainty:

```text
v2:{"form":"plus_minus","unit":"mas","values":[{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"input":0,"item":0}},{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"input":0,"item":1}}]}
```

For selected strings `3.417` and `0.084`, both `3.42 ± 0.08 mas` and
`3.42 +/- 0.08 mas` conform. No other separator or spacing does.

A grouped integer:

```text
v2:{"form":"scalar","values":[{"parse":"decimal","render":{"mode":"grouped_integer"},"source":{"input":0,"item":0}}]}
```

For selected string `3270000`, the only conforming presentation is
`3,270,000`.

An attached multiplier suffix:

```text
v2:{"form":"scalar","unit":"x","values":[{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"input":0,"item":0}}]}
```

For selected string `3.38682391`, the only conforming presentation is `3.39x`.

A declared count suffix:

```text
v2:{"form":"scalar","unit":"cases","values":[{"parse":"decimal","render":{"mode":"integer"},"source":{"input":0,"item":0}}]}
```

For selected string `4`, the only conforming presentation is `4 cases`.

A signed delta:

```text
v2:{"form":"scalar","unit":"%","values":[{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed","sign":"always"},"source":{"input":0,"item":0}}]}
```

For selected string `0.0519`, the only conforming presentation is `+0.05%`.
For selected string `-0.0519`, the same recipe produces `-0.05%`.

An NPZ binary float rendered directly from its exact bits:

```text
v2:{"form":"scalar","values":[{"render":{"decimal_places":2,"mode":"fixed"},"source":{"input":0,"item":0}}]}
```

For selected binary-float value
`{"bits":64,"hex":"3ff8000000000000","type":"binary_float"}`, the only
conforming presentation is `1.50`. No `parse` field or intermediate decimal
string is involved.

Scientific notation:

```text
v2:{"form":"scalar","values":[{"parse":"decimal","render":{"mode":"scientific","significant_figures":3},"source":{"input":0,"item":0}}]}
```

For selected string `0.00000000000004255`, round-half-to-even produces
`4.26e-14`.

Direct table with one source field per presented cell:

```text
v2:{"columns":[{"form":"text"},{"form":"scalar","unit":"%","value":{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"}}}],"form":"table","headings":["Case","Error"],"mode":"direct"}
```

If the sole input is a record selection with ordered fields `case` and
`error`, records `case-8, 1.118` and `case-15, 1.143` produce headings `Case`,
`Error` and rows `case-8`, `1.12%` and `case-15`, `1.14%`. No source reference
appears in the recipe because input 0 and same-position columns are implicit.

Structured table whose range column combines two source fields:

```text
v2:{"columns":[{"form":"text","values":[{"source":{"field":0,"input":0}}]},{"form":"range","unit":"%","values":[{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"field":1,"input":0}},{"parse":"decimal","render":{"decimal_places":2,"mode":"fixed"},"source":{"field":2,"input":0}}]}],"form":"table","headings":["Case","Error range"],"mode":"structured","rows":{"input":0}}
```

If input 0 is a record selection with ordered fields `case`, `error_min`, and
`error_max`, the recipe applies the two column rules to every selected record.
For records `case-8, 1.118, 1.449` and `case-15, 1.143, 1.319`, the canonical
result has headings `Case`, `Error range` and rows `case-8`, `1.12–1.45%` and
`case-15`, `1.14–1.32%`.

Summary table with one authored row label and two evidence cells from
independent input selections:

```text
v2:{"form":"table","headings":["Metric","Baseline","Candidate"],"mode":"summary","rows":[[{"form":"label","text":"FWHM"},{"form":"scalar","unit":"mas","values":[{"parse":"decimal","render":{"decimal_places":3,"mode":"fixed"},"source":{"input":0,"item":0}}]},{"form":"scalar","unit":"mas","values":[{"parse":"decimal","render":{"decimal_places":3,"mode":"fixed"},"source":{"input":1,"item":0}}]}]]}
```

For input 0 string `1.6019` and input 1 string `0.6015`, the canonical result
has one row: authored label `FWHM`, `1.602 mas`, `0.602 mas`. The label is
presentation prose and is not selected from either input.

A Boolean cell with selected value `true` and style `pass_fail` produces
`Pass`; selected value `false` produces `Fail`:

```json
{"form":"boolean","style":"pass_fail","values":[{"source":{"input":0,"item":0}}]}
```

A Boolean cell selected from a CSV string uses the same style with the closed
Boolean parser. Selected string `True` produces `yes`; selected string `False`
produces `no`:

```json
{"form":"boolean","style":"yes_no","values":[{"parse":"boolean","source":{"input":0,"item":0}}]}
```

A structured or summary cell may use the table-only sequence form. For numeric
inputs `109`, `400`, and `400`, this cell recipe produces
`109 x 400 x 400`:

```json
{"form":"sequence","style":"dimensions","values":[{"parse":"decimal","render":{"mode":"integer"},"source":{"input":0,"item":0}},{"parse":"decimal","render":{"mode":"integer"},"source":{"input":0,"item":1}},{"parse":"decimal","render":{"mode":"integer"},"source":{"input":0,"item":2}}]}
```

The concrete references shown are valid in summary mode. The same cell in a
structured column replaces each `item` index with the corresponding `field`
index.

### Future Expansion If Warranted

The initial v2 language deliberately excludes features that would make the
validator accept more equivalent presentations or become a general-purpose
formatting engine. A later version may add a feature only when retained corpus
cases demonstrate that the canonical v2 form would materially harm ordinary
research prose or force unreasonable evidence duplication.

Candidates for later evaluation are:

- another canonical statistic form, such as a mean with bounds or a
  purpose-specific list;
- another canonical numeric renderer, such as a different exponent notation;
- a small registered set of unit aliases or alternate placement rules;
- a small registry of exact unit conversions, including conversions with
  offsets;
- alternate exact text selectors, bounded context, or bounded extraction;
- controlled case, whitespace, punctuation, or lexical normalization;
- a small named rendering profile that reduces repeated declarations without
  accepting another presented result;
- bounded literal or fragment assembly beyond summary structural labels and
  the table sequence registry;
- a native pivot, transpose, record-stream concatenation, or structured-cell
  override, if summary enumeration or retaining one direct table proves
  materially burdensome across several logs; and
- multi-source structured-table identity alignment, if summary mapping or one
  retained assembled table proves materially awkward across several logs; and
- another unary presentation operation demonstrated across multiple logs.

Expansion must remain code-only, bounded, versioned, and unambiguous. It must
provide:

- representative retained examples from more than one research-log context;
- an explanation of why changing the presentation or retaining a
  purpose-built value is materially worse;
- one closed result grammar with every accepted spelling enumerated;
- complete conformance, failure, resource-bound, and migration fixtures; and
- no LLM, semantic similarity, authored regex, or presentation-derived
  inference.

If those conditions are not met, an unsupported but equivalent presentation
continues to fail and the research agent rewrites it into the existing
canonical form.

## Resource And Safety Bounds

Active v2 evaluation uses this required default limit profile:

| Resource | Limit |
| --- | ---: |
| Locator encoding | 8 KiB |
| Transformation encoding | 32 KiB |
| Selected paths | 256 |
| Filter conditions | 64 |
| `in` alternatives per condition | 256 |
| Expected identity tuples | 256 |
| Table records inspected | 100,000 |
| Expanded path nodes | 100,000 |
| Selected items | 10,000 |
| Transformation input slots | 256 |
| Transformation output value parts | 10,000 |
| Transformation table cells | 10,000 |
| Transformation units, table headings, and authored labels | 64 KiB UTF-8 total |
| Associated presented item | 64 KiB UTF-8 |
| JSON or text bytes read | 64 MiB |
| One binary member or dataset materialized | 64 MiB |

An evaluator must not silently truncate a source or selection. Crossing a bound
fails with the relevant resource code, exact subject, observed size, and
configured limit.

A runtime may raise limits without changing locator or transformation meaning,
but must record the effective profile. A runtime with lower limits must report
the lower bound as an implementation limitation and must not claim full
conformance to this profile.

Readers must be non-executing, path-safe, symlink-safe, and bounded against
container expansion, recursive aliases, external links, and decompression
bombs.

## Dependency Projection And Currentness

Every selected locator outcome has four projections:

1. **Version projection:** effective version and evaluator-profile version.
2. **Locator projection:** canonical locator serialization.
3. **Membership projection:** ordered canonical coordinates and record identity
   tuples selected by the locator.
4. **Value projection:** canonical typed values and structural metadata returned
   for that membership.

The downstream evidence comparison also depends on its transformation identity,
presentation association identity, and resolved source identity.

Source-byte changes require re-evaluation. After re-evaluation, unchanged
version, locator, membership, and value projections preserve the located
evidence outcome. Unselected source changes do not themselves reopen the
downstream outcome.

Source-profile identity rules are:

- hierarchical values use their complete canonical v2 path;
- arrays use the retained member or dataset path plus exact indexes;
- record selections use declared v2 identity tuples when present;
- text uses the selector identity, match rank among matching lines, and selected
  text content, not absolute line number;
- structural properties use the target coordinate and property name;
- whole artifacts use the complete artifact content identity.

If a v2 record selection has no inherent coordinate and no declared identity,
any source-order change may change its membership projection.

A whole-source hash may detect the need for re-evaluation but must not replace
the selected dependency projection.

## Failure And Limitation Codes

Every non-selected outcome records:

- source identity;
- effective locator version when known;
- original and canonical locator when normalization succeeded;
- stable code;
- outcome class: `fail` or `unavailable`;
- observed paths, fields, values, shapes, or access condition;
- the violated specification section.

A transformation failure additionally records the declared and effective
transformation versions, original and canonical transformation when available,
input references and values, the canonical result when evaluation reached one,
the associated presented item when available, and the violated form or value
field.

Reserved codes include:

| Code | Class | Condition |
| --- | --- | --- |
| `locator.version.unsupported` | fail | The declared version has no enabled evaluator. |
| `locator.syntax.invalid` | fail | Version-specific syntax, key, or key relationship is invalid. |
| `locator.literal.invalid` | fail | A v2 authored scalar or specialized tagged literal is malformed, non-canonical, or unsupported in its syntactic position. |
| `locator.encoding.too_large` | fail | The locator exceeds its encoding bound. |
| `locator.source.unsupported` | fail | The resolved source has no requested locator profile. |
| `locator.source.format_mismatch` | fail | Declared format conflicts with retained bytes or structure. |
| `locator.source.unsafe` | fail | Evaluation would require execution, unsafe deserialization, or source escape. |
| `locator.source.too_large` | fail | Stable source content crosses a source bound. |
| `locator.reader.unavailable` | unavailable | A required safe runtime reader is temporarily unavailable. |
| `locator.source.changed` | unavailable | The source changed during observation. |
| `locator.path.unresolved` | fail | A path, member, field, index, slice, group, dataset, HDU, or variable does not resolve. |
| `locator.type.mismatch` | fail | An operation does not apply to the encountered type. |
| `locator.field.missing` | fail | A selected, identity, or predicate field is absent. |
| `locator.predicate.parse_failed` | fail | An explicitly parsed lexical predicate operand does not satisfy its declared grammar. |
| `locator.identity.duplicate` | fail | Declared record identity is not unique. |
| `locator.identity.expectation_mismatch` | fail | Observed ordered identity tuples differ from `expect.identities`. |
| `locator.alignment.invalid` | fail | Aligned records or arrays have incompatible lengths or shapes. |
| `locator.selection.empty` | fail | A valid locator selects no evidence item. |
| `locator.selection.ambiguous` | fail | An implicit choice remains among multiple candidates. |
| `locator.selection.too_large` | fail | Expansion or selected output crosses a cardinality bound. |
| `locator.expectation.mismatch` | fail | Observed matches, items, or shape differs from v2 `expect`. |
| `locator.property.unsupported` | fail | The property is not defined for the selected source profile or type. |
| `locator.text.decode` | fail | A declared text source is not valid UTF-8. |
| `transformation.version.unsupported` | fail | The declared transformation version has no enabled evaluator. |
| `transformation.syntax.invalid` | fail | Version-specific syntax, keys, clauses, or key relationships are invalid or conflicting. |
| `transformation.presentation.mismatch` | fail | The associated presented item is not one of the exact surface spellings defined by the declared transformed form. |
| `transformation.input.reference_invalid` | fail | A concrete item reference or structured field reference does not resolve in the required input. |
| `transformation.input.unused` | fail | A locator-selected item is not consumed by the recipe. |
| `transformation.input.reused` | fail | One selected item is referenced more than once. V2 requires exact one-time consumption. |
| `transformation.table.direct_mismatch` | fail | A direct recipe does not have exactly one table or grouped-record input, or its selected columns and declared columns are not one-to-one. |
| `transformation.table.input_not_records` | fail | A structured table input lacks retained record grouping or uses a prohibited selection kind. |
| `transformation.table.order_mismatch` | fail | A declared structured row order is not an exact typed permutation of the driver identities. |
| `transformation.table.label_invalid` | fail | A label is empty, contains prohibited cell text, occurs outside the first cell of a summary row, or is the row's only cell. |
| `transformation.boolean.invalid` | fail | A Boolean cell has an unknown style, a non-Boolean source without the closed Boolean parser, an invalid Boolean string, or fields outside its closed form. |
| `transformation.type.mismatch` | fail | An operation or output form does not accept the encountered canonical type. |
| `transformation.parse_failed` | fail | A selected lexical string does not satisfy the declared complete-value grammar. |
| `transformation.scale.invalid` | fail | A scale factor is zero, malformed, non-finite, or not an exact JSON integer or decimal. |
| `transformation.render.invalid` | fail | A renderer, precision, numeric result, or unit violates the canonical rendering contract. |
| `transformation.nonfinite_unsupported` | fail | A selected or intermediate numeric value is NaN or infinity. |
| `transformation.output.shape` | fail | A declared table is empty, ragged, or inconsistent with its columns. |
| `transformation.output.too_large` | fail | Recipe output, headings, units, or values cross a transformation resource bound. |

Outer source-resolution failures retain codes owned by the source contract.

## Evidence File And Presentation Association

### Role

The association subcontract binds one declared evidence record to one
presented item and compares that item with the result of the record's locator
and transformation subcontracts. It owns file-schema dispatch, record and
presentation identity, Markdown target recognition, association cardinality,
and exact comparison.

The subcontract does not infer which evidence supports an item. It does not
read surrounding prose to decide whether a relationship is plausible. A
missing, conflicting, or unsupported declaration is a completed mechanical
failure.

### Evidence Files And Unsupported Metadata

The active association surfaces have these roles:

| Role | File | Association |
| --- | --- | --- |
| Active v2 entry presentation | Entry-root `evidence.json` | Entry-scoped stable ID shared with one hidden Markdown marker |
| Active v2 summary reference | Maintained summary Markdown | Hidden reference to one entry ID and entry evidence ID, plus a table coordinate when applicable |
| Active v2 retention | Entry-root `evidence.json` | Entry-owned declaration; no Markdown marker |

All evidence declarations are in entry-local `evidence.json`. Every eligible
entry presentation uses its required v2 marker, and every eligible summary
statistic uses its required v2 reference. Every v2 `evidence.json` belongs to
the root of the entry whose records it owns; any other placement fails as
`evidence.file.location_invalid`.

The maintained summary's `## Entries` inventory is the only owner-discovery
surface for the target log. Entry links elsewhere in summary prose are ordinary
navigation, including links to another maintained log, and do not import those
entries. Every owned entry resolves beneath the target log's `entries/`
directory. A directly referenced cross-log artifact remains an external input
under the command-provenance contract.

The bounded unsupported-metadata preflight detects recognized unsupported
generated validation metadata and returns one
`validation.unsupported_metadata` result listing every path found. It writes
nothing and does not interpret the unsupported content. Retention records
participate in entry-local ID uniqueness and orphan classification, but not
presentation association.

The standard validation operation never removes these paths. They must be
archived outside the active log or removed through a separately authorized
maintenance action before validation is rerun.

The preflight recognizes unsupported generated state only at these exact paths,
relative to the maintained-log root:

- `validation/manifest.json`;
- `validation/outcomes`, `validation/judgments`, or `validation/failures`;
- `validation/.cache/cache.json` or
  `validation/.cache/subject-index.json`;
- `validation/.cache/upgrade-transactions`;
- `validation/.cache/index-deltas`, `validation/.cache/work`, or
  `validation/.cache/validation.log`;
- `validation-decisions.json`, `validation-state.json`,
  `validation-index.json`, `validation-record.json`, or
  `validation-cache.json`;
- `validation-state`; and
- `.research-log-validation.lock`.

When no active `validation/mechanical.json` exists, the preflight also treats
`validation.md` as unsupported generated state if its bounded prefix contains
the `| Entry | Date | Checked | Reproducibility |` table header or the
`## Status Summary` marker. It does not parse any obsolete JSON, shard, cache,
decision, session, or report conclusion. An unrelated file does not become
obsolete state merely because it is below a directory named `validation`.

V2 JSON records permit only v2 locators and v2 transformations because they
embed structured JSON objects directly.

Direct artifact presentations use their Markdown target and the provenance
contract rather than `evidence.json`.

### V2 JSON File Schema

V2 uses one exact top-level object:

```json
{
  "schema": "research-log-evidence/v2",
  "records": []
}
```

Both keys are required and unknown keys fail. `records` must be a non-empty
array; remove a file after removing its last record. JSON is UTF-8 without a
byte-order mark, duplicate keys, comments, non-finite numbers, or trailing
content. Insignificant whitespace and object-key order have no meaning. Record
array order also has no meaning; canonicalization orders records by `id`.

An entry-root presentation record has exactly:

```json
{
  "id": "candidate-success-rate",
  "document": "entries/2026-08-27-e001-study/e001.md",
  "kind": "statistic",
  "sources": [
    {
      "source": "data/results.csv",
      "locator": {
        "select": [["success_rate"]]
      }
    }
  ],
  "transformation": null
}
```

Required keys are `id`, `document`, `kind`, `sources`, and `transformation`;
unknown keys fail. `kind` is `statistic`, `table`, or `output`. `sources` is a
non-empty ordered array of exact v2 source objects. `transformation` is `null`
for identity or the JSON object portion of a v2 transformation without a
`v2:` prefix. V2 record kinds are entry presentations and entry-owned
retention. Summaries use the Markdown-owned references defined below.

An entry-root retention record uses exactly one of these forms:

```json
{
  "id": "optimizer-debug-traces",
  "kind": "retention",
  "paths": [
    "data/debug-trace.json",
    "data/optimizer-state.npz"
  ],
  "reason": "Diagnostic outputs retained for later investigation."
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

Required keys are `id`, `kind`, and either `paths` or the pair `directory` and
`membership`. `kind` must be `retention`. `membership` must be the literal
`all-descendants`. The two target forms are mutually exclusive. `paths` is a
non-empty array of unique normalized POSIX paths relative to the entry root.
`directory` is one normalized POSIX directory path relative to the entry root.
Every target must remain beneath that root and must not be absolute, empty,
aliased, or contain `.`, `..`, a reverse solidus, or a URI scheme. A path may
cross the exact entry-local `data` or `images` directory when that directory is
a symlink; these are first-class entry material roots regardless of physical
storage location. No other or nested symlink is permitted. The directory form
covers every otherwise orphan-eligible regular-file descendant observed
beneath that directory. It must resolve to at least one such file.

`reason` is the only optional key and, when present, must be a UTF-8 JSON string
of at most 2,048 bytes. It records research-agent intent for later semantic
review. Mechanical validation does not interpret, judge, compare, or include
its contents in retention identity, currentness, dependency, or outcome
projections. Unknown keys fail. Retention records are permitted only in an
entry-root `evidence.json`; they have no `document`, `sources`,
`transformation`, or presentation marker.

The evaluator treats every embedded locator and non-null transformation as
explicit v2. Their canonical identities retain the ordinary `v2:` prefix plus
canonical JSON serialization, even though the host file stores only the JSON
object.

`id` uses this grammar and is at most 96 ASCII characters:

```text
[a-z][a-z0-9]*(?:-[a-z0-9]+)*
```

IDs must be unique within one entry-local `evidence.json`, including retention
records. The stable record identity is `(maintained-log identity, entry
identity, id)`. The same short ID may occur in another entry because summary
references and validator identities always include the entry identity. Moving
a record to another entry changes its identity. Changing its presented value
does not. Copying a record within the same entry requires a new ID.

An ID names the evidence role, not its current observation. Use a concise
description such as `candidate-success-rate` or
`single-worker-baseline-wall-time`. A stable experimental condition, metric
name, or structural ordinal may disambiguate repeated roles. Do not derive an
ID from the presented expression, a selected retained value, a rounded value,
or another measurement outcome. For example, `candidate-success-rate`, not
`candidate-success-rate-67-6`; the same ID remains in place when a rerun
changes `67.6%` to another result.

`document` is a normalized POSIX path relative to the maintained-log root. It
must name one regular UTF-8 Markdown file inside that root, with no absolute
path, empty segment, `.` or `..` segment, reverse solidus, URI scheme, symlink,
or alias. An entry-level record's document must be inside the entry directory
that contains its `evidence.json`. Document location is an association
coordinate, not record identity.

### V2 Entry Presentation Markers

A v2 entry presentation record and its presented item share one exact marker:

```html
<!-- eid:median-success-rate -->
```

The marker is the literal prefix `<!-- eid:`, followed by the record ID,
followed by ` -->`. No alternate spacing, case, quoting, attributes, or marker
aliases are accepted. The comment is non-rendered structure and is not part of
the evidence-bearing expression.

Marker placement depends on `kind`:

- A `statistic` marker immediately follows one inline code span on the same
  source line with no intervening characters. The code-span contents are the
  presented expression.
- A `table` marker occupies the immediately preceding source line. No blank,
  comment, label, or prose line may intervene before the first table row.
- An `output` marker occupies the immediately preceding source line before the
  opening `text` fence. No blank, comment, label, or prose line may intervene.

One marker binds exactly one presented item. One presented item has exactly one
marker. A marker ID must resolve to exactly one presentation record whose
`document` and `kind` agree with the observed item. Duplicate markers, nested
markers, a marker in a fence, a marker without an eligible item, and a
presentation record without a marker fail. A marker that names a retention
record fails as invalid association.

The marker makes entry evidence identity independent of heading text, line
number, rendered value, and surrounding prose. Those observations may still
be currentness or conformance inputs where this subcontract names them
explicitly.

### V2 Summary Evidence References

Each eligible summary statistic carries one exact hidden reference immediately
after its inline code span on the same source line, with no intervening
characters. The reference points to an entry-local evidence record rather than
redeclaring its source or transformation.

A reference to an entry statistic has exactly this form:

```html
<!-- ref entry = e004a; eid = full-sample-runtime -->
```

A reference to an entry table cell has exactly this form:

```html
<!-- ref entry = e001; eid = configuration-table; row = 2; column = 3 -->
```

The literal prefix is `<!-- ref ` and the literal suffix is ` -->`.
Mappings use the exact order shown, one ASCII space on both sides of `=`, and
the separator `; ` between mappings. No alternate spacing, ordering, case,
quoting, keys, or attributes are accepted.

`entry` is the exact entry document ID in the current maintained log.
`eid` satisfies the v2 evidence-ID grammar and names one presentation
record in that entry's `evidence.json`. Together they identify the stable
record `(maintained-log identity, entry identity, evidence ID)`.

The two-key form requires the target record to have `kind:"statistic"`. Its
complete successful canonical presentation must equal the parsed summary
expression exactly. The reference performs no rounding, unit change,
reformatting, alternative-spelling comparison, or transformation.

The four-key form requires the target record to have `kind:"table"`. `row`
and `column` are canonical ASCII positive integers without leading zeroes.
They are one-based coordinates in the table's canonical presentation result:

- `row` counts body rows and excludes the heading and Markdown alignment row;
- `column` counts all presented columns in heading order; and
- both coordinates must be within the successfully validated rectangular
  table.

The selected cell must be an evidence cell with a supported numerical
presentation form, not a heading or authored structural label. Its complete
canonical cell presentation must equal the parsed summary expression exactly.
The validator does not search the table for the value, infer a row label, or
choose among matching cells. The coordinate is the association.

`row` and `column` are both required for a table target and both prohibited
for a statistic target. An output or retention record cannot be referenced.
The referenced entry evidence must complete its own record, source, locator,
transformation, and presentation evaluation. A failed or unavailable evidence
target makes the dependent summary evidence fail or unavailable without
changing the target's result. Provenance remains a separate projection: a
successful summary evidence reference inherits the target's provenance result,
so evidence may pass while provenance fails.

One summary statistic has exactly one reference. The reference is association
metadata, not a second evidence declaration. It adds no source, locator,
transformation, producer, or semantic claim. Surrounding summary prose and its
fidelity to the entry remain Semantic Review concerns.

### Eligible Presentation Context

V2 retains the current structural boundary:

- entry statistics are eligible only in an experimental section;
- entry tables and output blocks are eligible only beneath that experimental
  section's `Results:` label;
- summary statistics are eligible only in the maintained summary; and
- synthesis and prose entry sections contain no evidence-record targets.

The deterministic section classifier remains outside this specification but
its declared classifier version and classification result are association
dependencies. A marker cannot override an ineligible context.

Every eligible entry statistic, table, or `text` output block must have one
valid v2 entry marker, and every eligible summary statistic
must have one valid v2 summary reference. A missing entry marker fails
`association.declaration_missing`; a missing summary reference fails
`summary.reference.missing`. Other unmarked prose is not promoted to evidence
by validation. Semantic Review may report an apparently evidential claim that
uses no supported presentation form.

Artifact links and image embeds in experimental `Results:` remain direct
artifact presentations. They do not use evidence-record files or evidence markers.
Their exact Markdown target supplies presentation identity, and
the recorded-command provenance subcontract below owns their producer and
lineage checks.

### V2 Source And Transformation Cardinality

Entry records consume source objects in their declared array order. Each object
contains one embedded v2 locator and must return one successful ordered typed
selection. The transformation input slot is the zero-based `sources` array
position.

Cardinality is closed by presentation kind:

| Kind or table mode | Source objects | Additional requirement |
| --- | ---: | --- |
| `statistic` | 1–8 | The transformation produces exactly one supported non-table form. |
| `output` | 1 | The locator selects exactly one string and identity or `form:"text"` produces the complete block payload. |
| `table` / `direct` | 1 | The selected table and recipe satisfy direct-table one-to-one rules. |
| `table` / `structured` | 1 | Every selected record and field satisfies repeated single-source consumption. |
| `table` / `summary` | 1–32 | Every selected item is consumed exactly once by an evidence cell. |

A v2 table record must use a non-null v2 table transformation. Null identity is
not a second table grammar. A statistic may use null identity only when one
selected primitive renders to exactly one canonical statistic expression. An
output may use null identity only for one selected string.

A whole-artifact reference is prohibited in a v2 evidence record because it
returns no source-internal selection to the transformation contract. Authors
must use a bounded v2 locator. Whole artifacts remain valid for direct artifact
presentations outside evidence-record files.

### Strict Presentation Parsing And Comparison

Association comparison consumes the canonical presentation result returned by
the transformation subcontract. It performs no new rounding, normalization,
unit inference, tolerance, phrase matching, or semantic inspection.

For a statistic:

1. parse the single marked code span;
2. preserve its Unicode contents exactly;
3. require the complete contents to equal one accepted surface spelling of the
   transformation result; and
4. reject prefixes, suffixes, nested Markdown, additional code spans inside the
   marker binding, or a partial match.

Backtick delimiters and the adjacent evidence marker are non-semantic Markdown
structure. Whitespace inside the code span is evidence content.

For a table, the parser accepts one ordinary pipe table with:

- one heading row, one alignment row, and at least one body row;
- the exact heading and body dimensions returned by the transformation;
- optional leading and trailing pipe characters;
- ASCII space around cell source text as non-semantic structure;
- alignment cells matching `:?-{3,}:?`; and
- either plain cell text or one complete single-backtick code span around the
  cell text.

The parser removes outer pipe syntax, structural ASCII space, the alignment
row, and an optional complete code-span wrapper. It decodes only the Markdown
escapes `\|` and `\\` inside plain cells. It does not evaluate emphasis, links,
HTML, entities, nested code, line breaks, or other Markdown. The resulting
heading strings and rectangular cell matrix must equal the canonical table
result exactly in text, dimensions, and order. Code styling is therefore
non-semantic; cell spelling is not.

For an output block, the parser accepts one fence whose info string is exactly
`text`. The block payload excludes the opening and closing fence lines and the
single structural line ending before the closing fence. CRLF and CR line
endings are normalized to LF; no other content is stripped or normalized. The
complete payload must equal the transformation's single accepted string.

Parser success does not imply scientific or rhetorical support. It establishes
only exact declared presentation.

### Summary Association

Summary association is the exact Markdown reference contract above. It never
uses a summary evidence record, source declaration, transformation, content
search, or section-level inference.

A statistic reference forwards one complete canonical entry-statistic
presentation. A table reference forwards one complete canonical numerical cell
at its declared row and column. In either case, the summary expression must be
identical to the forwarded presentation. A summary that needs different
rounding, units, notation, or derived content must use the entry presentation
unchanged, establish the desired presentation as independently retained entry
evidence, or leave it as ordinary synthesis prose rather than marked summary
evidence.

A reference cannot chain through another summary, cross a maintained-log
boundary, combine records, or target an output or retention record. It inherits
the referenced entry record's completed evidence and provenance projections but
not the supporting sentence, heading, interpretation, or semantic claim.
Whether surrounding summary prose faithfully
synthesizes the entry belongs to the Summary Fidelity review lens.

### Association Completeness And Conflict Rules

Validation constructs the v2 association index across one maintained log and
then applies these rules in order:

1. every v2 record ID is unique within its entry;
2. every entry marker ID is unique within its entry;
3. every presentation record resolves its declared document and permitted context;
4. every presentation record and marker agree on document, ID, and kind;
5. every marked v2 presentation has exactly one record;
6. every v2 presentation record has exactly one presentation;
7. every summary reference resolves exactly one eligible v2 entry record and,
   for a table, one in-bounds numerical cell; and
8. source, locator, transformation, and exact presentation comparison succeed.

Retention records participate in entry-local ID uniqueness and their own schema
and target checks, but not presentation association.

No occurrence number, nearest-heading rule, same-value search, filename
similarity, or other tie-breaker repairs a conflict. A duplicate or ambiguous
identity prevents evaluation of every record and presentation that depends on
it. Unrelated uniquely associated records remain independently evaluable.

### Association Dependency Projection And Currentness

One v2 association outcome depends on:

1. evidence-file profile and parser version;
2. canonical record fields;
3. entry-local uniqueness of its record and marker IDs;
4. declared document identity and marker binding;
5. section-classifier version, eligible-context classification, and applicable
   `Results:` boundary;
6. canonical parsed statistic, table, or output model;
7. ordered resolved-source identities;
8. locator and expectation projections;
9. transformation projection and accepted surface spellings; and
10. for a summary, the exact reference fields, referenced entry-record
    identity, successful canonical presentation projection, and any table-cell
    coordinate.

Line numbers, heading spelling, and surrounding prose do not enter the stable
identity. A line move or prose edit preserves an outcome when the marker or
summary reference, eligible context, parsed presentation model, record, and
downstream projections remain identical. A heading or label change that alters
eligibility reopens the outcome. Entry-local ID additions or removals reopen
only identities whose uniqueness changed and dependent summary references.

Entry-local `evidence.json`, entry markers, and summary references participate
in active association currentness. Adding, removing, or changing a marker
reopens its attached v2 presentation and dependent summary references. Adding,
removing, or changing a summary reference reopens that summary association. A
newly observed unsupported generated-state path causes the preflight to return
`validation.unsupported_metadata`; its contents do not enter currentness.

The validator may use whole-file hashes to detect a need for parsing but must
persist and compare the narrower association projection for outcome reuse.

### Association Failures

Every failure records the v2 record identity when known, document, kind,
observed marker or presentation, and violated clause. Reserved active-validation
codes are:

| Code | Scope | Condition |
| --- | --- | --- |
| `validation.unsupported_metadata` | unsupported-metadata preflight | Active validation encountered recognized unsupported generated validation metadata. The result lists every detected path and writes nothing. |
| `evidence.json.schema_invalid` | conformance | A v2 JSON file has an invalid top-level schema, shape, or JSON encoding. |
| `evidence.file.encoding_invalid` | conformance | A v2 JSON file is not permitted UTF-8. |
| `evidence.file.empty` | conformance | A v2 JSON file has no records. |
| `evidence.file.location_invalid` | conformance | An `evidence.json` occurs outside an entry root, including at the maintained-log root. |
| `evidence.declaration.invalid` | conformance | A v2 record violates its exact field, type, enum, path, or shape constraints. |
| `evidence.record.id_duplicate` | conformance | One v2 ID occurs in several evidence records within the same entry. |
| `presentation.marker.invalid` | conformance | Marker syntax or placement is invalid. |
| `presentation.marker.duplicate` | conformance | One v2 ID occurs in several entry presentation markers within the same entry. |
| `association.declaration_missing` | evidence | An eligible v2 presentation has no matching v2 record. |
| `association.presentation_missing` | evidence | A v2 presentation record has no matching presentation. |
| `association.document_mismatch` | evidence | V2 record and marker do not identify the same permitted document. |
| `association.kind_mismatch` | evidence | Declared and observed presentation kinds differ. |
| `association.context_invalid` | conformance | The presentation is outside its permitted section or label. |
| `association.source_cardinality` | evidence | The source count violates its kind or table mode. |
| `association.presentation.syntax_invalid` | conformance | The marked Markdown item is outside the closed structural parser. |
| `association.presentation.mismatch` | evidence | Parsed presentation differs from every accepted transformation result. |
| `association.resource.too_large` | conformance | Association indexing or one parsed item crosses a declared bound. |
| `summary.reference.missing` | evidence | An eligible summary statistic has no adjacent v2 summary reference. |
| `summary.reference.invalid` | conformance | A summary reference violates its exact syntax, fields, ordering, spacing, target cardinality, or placement. |
| `summary.reference.unresolved` | evidence | The declared entry or evidence ID does not resolve exactly once in the current maintained log. |
| `summary.reference.target_invalid` | evidence | The target is cross-log, unavailable, failed, or has a kind prohibited by the selected reference form. |
| `summary.reference.coordinate_invalid` | evidence | A table reference omits a coordinate, supplies a prohibited or out-of-bounds coordinate, or selects a heading, label, or non-numerical cell. |
| `summary.reference.mismatch` | evidence | The summary expression differs from the referenced statistic or exact table-cell presentation. |

Locator and transformation failures retain their owning code. A missing or
inaccessible document or source uses the source or observation code owned by
the corresponding contract rather than being rewritten as an association
mismatch.

### Association Resource Bounds

The v2 record and association profile permits at most:

- 10,000 v2 records, 10,000 entry presentation markers, and 10,000 summary
  references per maintained log;
- 1,000 v2 records in one file;
- 96 bytes in an ID;
- 512 bytes in one summary reference;
- 512 bytes in a document path;
- 10,000 exact paths in one retention record;
- 2,048 bytes in one retention reason;
- 32 source objects in one record;
- 8 MiB in one `evidence.json` file;
- 1 MiB of source Markdown for one marked table or output block; and
- the stricter locator and transformation bounds already defined by this
  specification.

Crossing a stable authored bound is `fail`, not `unavailable`. Implementations
may stream files and indexes and must not require repository-wide discovery.

## Recorded-Command Provenance And Material Graph

### Role And Authority

Mechanical provenance answers:

> Which recorded command produced each retained local evidence source or
> directly presented artifact, what mechanically visible upstream lineage
> leads to it, and where does that lineage reach trusted external data or a
> declared model or simulation origin?

The research log already records commands. Those command surfaces, together
with optional adjacent command annotations, are the canonical provenance
declarations. Standard validation must derive provenance from shell direction,
the closed input/output option-name convention, command annotations, exact
command paths and named-input tokens, declared command types, and observed
retained material. It must
not require an agent to restate the complete invocation in a second authored
record.

Recorded commands are discovery surfaces, not independently validated
declarations of their complete input and output behavior. Standard validation
does not require every path-like command argument to have a direction and does
not claim to reconstruct the complete command dependency graph. A path that
cannot be classified establishes no edge. Its mere presence in a command is
not a failure.

There is no `provenance.json` in this contract. There are also no authored
producer bindings, historical-limit declarations, free-form validation-block
exceptions, ignored-material rules, or semantic provenance judgments.
Structured retention records in entry-local `evidence.json` affect only orphan
classification. Missing, conflicting, ambiguous, or mechanically undiscoverable
relationships are completed failures or orphan findings as defined below.

Validation is read-only and non-executing. It may lex and parse recorded shell
commands and adjacent command annotations, resolve and hash project-local
scripts without importing or executing them, inspect retained material through
bounded readers, and compare canonical paths and content identities. Script
internals are irrelevant to association discovery: validation must not inspect
them to infer material direction, path construction, command type, or lineage.
It must not infer those relationships from prose, proximity, or likely intent.
The sole filename-derived command type is the closed simulation script-name
convention defined below.

Within this contract, a `producer` is the unique recorded invocation
mechanically proven to write or capture the exact material. This establishes
recorded traceability. It does not independently prove that the historical
invocation ran or produced the current bytes. Reproduction remains the separate
execution-based check.

### Recorded Invocation Discovery And Identity

An eligible shell command fence discovered through the research-log entry
grammar may contain one or more bounded top-level invocations. Each supported
invocation is identified independently for relationship discovery. Physical
continuation lines, environment assignments, one analyzable pipeline, and
shell redirection may
belong to one invocation. Independent invocations may share a fence without
creating control flow or a combined producer. Command substitution,
dynamically constructed executable names, unsupported control flow, or an
unparseable shell surface establishes no relationship by itself. It is not a
standalone validation failure merely because it appears in the log. A command
explicitly targeted by authored command metadata must be parseable enough
to apply that metadata; otherwise the annotation is invalid. If no supported
surface proves a required evidence producer, `producer.missing` owns the
provenance failure.

Before ordinary invocation discovery, the validator may statically expand this
closed, non-executing shell grammar:

- `for <name> in <literal>...; do ... done`, with a finite literal value list;
- a locally defined function of any valid shell name, invoked with one or more
  literal arguments, whose body uses only literal calls, `$1`, `shift`, and
  `$@`;
- a loop-local `case` on a bound scalar whose literal branches assign one
  scalar or one literal array;
- `$name`, `${name}`, and `${array[@]}` when the value is established by the
  same supported construct; and
- a trailing background `&` and standalone `wait`, which affect scheduling but
  establish no material relationship by themselves.

Expansion never executes shell, reads an environment variable, selects a
dynamic branch, expands a glob, or performs command, process, or arithmetic
substitution. Function and variable names are generic; no helper name, script
name, or entry path receives special behavior. Unsupported nesting, unbound
values, nonliteral arguments, or expansion beyond an existing resource bound
consumes the affected control surface without exposing its body as independent
commands. The unsupported surface establishes no relationship.

The validator constructs the invocation identity from:

1. maintained-log identity;
2. entry identity;
3. entry-relative command document path;
4. the canonical shell-token and operator sequence;
5. for an expanded invocation, the normalized visible control structure and
   literal binding projection; and
6. the zero-based ordinal among identical canonical commands and projections
   in that document.

Shell quoting that yields the same token has no identity meaning. Operators,
redirections, argument order, option values, environment assignments, and the
resolved executable or script token do. Line number, heading spelling, fence
delimiter length, and surrounding prose do not. Changing the command changes
its identity and reopens its dependent provenance outcomes. Changing a static
value list, helper call, function body, selected case assignment, or other
result-affecting part of the supported expansion likewise changes identity,
even when one expanded command's final tokens happen to remain the same.

Command annotation ordinals count the concrete invocation order after static
expansion. A function definition and `wait` are not invocations. Each expanded
loop iteration or supported helper-body command is an ordinary invocation for
annotation, identity, relationship, and resource-bound purposes.

For supported invocations, discovery returns:

- the canonical command identity and parsed shell structure;
- the resolved executable or project-local script when mechanically available;
- exact path-valued argument candidates, redirection targets, and `<name>` tokens;
- normalized adjacent command annotations and effective command type when
  present;
- mechanically established input and output relationships;
- bounded collection selections when completely determined; and
- stable failures and unresolved candidates that did not establish an edge.

A path or token merely mentioned by a command is a candidate, not a provenance
edge. Direction and coverage must be proven by the rules below.

### Command Annotations, Types, And Role-Bearing Option Names

A command fence may be followed immediately, with no intervening blank or
prose line, by zero or more contiguous hidden annotations. Each annotation
classifies one invocation; invocations that need no annotation have no empty
placeholder.

First or only command:

```html
<!-- command type = model; catalog = input; results = output -->
```

Multi-command fence, when only commands 1 and 3 need annotations:

```html
<!-- command-1 results = output -->
<!-- command-3 type = simulation; summary-csv = input; @2 = output -->
```

The annotation grammar is closed. It begins with literal `<!-- command`, an
optional ordinal suffix `-N`, and one ASCII space. It ends with the literal
` -->`. `N` is a one-based positive integer without leading zeroes. Omission
and `-1` both select the first invocation; `-2`, `-3`, and so on select later
top-level invocations in the immediately preceding fence. Between the prefix
and suffix, the annotation contains one or more clauses separated by
semicolons. ASCII spaces, tabs, and line breaks may surround clauses and
semicolons, but every assignment uses exactly one ASCII space on both sides of
`=`. A trailing semicolon is prohibited.

Each clause is `target = value`:

- the reserved target `type`, when present, must be the first clause and its
  value is exactly `model` or `simulation`;
- an option target matches `[A-Za-z0-9][A-Za-z0-9_-]*`, omits leading hyphens,
  and names an option present in the selected invocation;
- a positional target is `@N`, where `N` is the one-based positional argument
  ordinal after the executable or script token in the selected invocation;
  option names and their syntactically paired values are not positional; and
- a non-`type` value is exactly one of `input`, `output`,
  `input-directory`, `output-directory`, `input-manifest`, or
  `output-manifest`.

At most one annotation may select one invocation, and annotations following
one fence must use increasing command ordinals. `command` and `command-1`
therefore cannot both follow one fence. Each target occurs at most once in its
annotation. An option target assigns its role to all occurrences of that
option in the selected invocation. A positional target assigns its role only
to that one argument. Empty annotations, unknown targets, command types, or
roles, duplicate targets or selected ordinals, out-of-range ordinals, missing
options, and conflicting roles fail.

Each classified option occurrence must carry exactly one value in either
`--name value` or `--name=value` form; the analogous single-hyphen form is also
accepted. Bundled short flags, valueless flags, and one option followed by an
implicit variable-length value list do not acquire option roles through this
mechanism. A positional path requires its own `@N` target. A collection of
separate paths uses repeated option occurrences, separately targeted
positional arguments, or a manifest.

After ordinary token expansion, the complete role-bearing value must be the
path. The validator does not split `label=path`, `key=path`, or another embedded
mapping to discover a path, and a role-bearing value containing that shape
fails. The research agent must expose the label and path as separate command
arguments or use a bounded manifest. An adjacent annotation assigns direction;
it does not authorize substring extraction from an opaque argument.

Annotations are optional. They exist to declare a command type, fill a
discovery gap, classify a positional path, or override the automatic role of
one option without making the visible command awkward. They are not producer
bindings and cannot name material absent from the selected invocation. They
cannot contradict shell redirection or assign incompatible roles to the same
canonical material.

`model` and `simulation` are distinct command types with the same mechanical
root capability. A model command originates data by evaluating, sampling, or
otherwise executing a model or mathematical relationship. A simulation
command originates data by executing a simulated process, including an
iterative, stochastic, time-evolving, or repeated-interaction process. The
validator records the declared type but does not inspect code or judge whether
the classification is scientifically appropriate.

The validator also assigns `type = simulation` when the resolved project-local
script or executable filename stem is exactly `simulate` or `simulation`, or
begins with `simulate_` or `simulation_`. Matching is case-sensitive. It does
not recognize `sim_`, internal tokens, other separators, aliases, or a
`model_` prefix. An explicit annotation type overrides the filename-derived
type. No filename convention automatically assigns `type = model`.

The validator also recognizes one closed, case-sensitive option-name
convention. After leading hyphens are removed, a path-bearing option acquires
an automatic exact-path role when its name matches exactly one of these forms:

| Form | Automatic role |
| --- | --- |
| `input` | `input` |
| `input-*` or `input_*` | `input` |
| `*-input` or `*_input` | `input` |
| `output` | `output` |
| `output-*` or `output_*` | `output` |
| `*-output` or `*_output` | `output` |

`*` matches `[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?`. The `input` or
`output` word must be the complete name or one leading or trailing token
separated by exactly one hyphen or underscore. Internal tokens, substrings, aliases, plural forms,
stemming, keyword scoring, and executable-specific reinterpretation do not
match. For example, `output-summary-csv`, `summary-csv-output`, and
`output_summary_csv` are outputs; `summary-csv`, `results`, and
`summary-output-csv` are unclassified. A name that matches both roles, such as
`input-output`, acquires no automatic role.

The automatic role covers only the exact argument path. It never asserts that
a directory's descendants or a manifest's listed paths share that role.
The collection roles `input-directory`, `output-directory`, `input-manifest`,
and `output-manifest` therefore remain annotation-only. An adjacent
annotation overrides the automatic role of the named option. Shell direction
has precedence over both.

### Mechanical Input And Output Discovery

Discovery is evidence-rooted. The validator first identifies local sources of
successfully associated entry evidence and any directly presented artifacts.
Those materials are provenance starting points. It searches recorded commands for
positive, mechanically established producer relationships to those starting points and
then follows only mechanically established inputs of the selected producers.
The process repeats for any such local generated input. This transitive set is
the evidence-provenance closure.

Commands, arguments, and candidate paths outside that closure receive no
provenance result. The validator may reuse any positive relationship it
discovers outside the closure for separate retained-material orphan detection,
but it does not require a complete role classification for the command. Command
annotations and the option-name convention are therefore targeted proof
mechanisms, not a requirement to describe every command argument.

One command-material relationship is established only by one of these closed
proof forms:

1. **Shell direction:** an input or output redirection, or a supported terminal
   `tee` capture, unambiguously identifies the material and its direction.
2. **Adjacent command annotation:** a valid annotation assigns one exact
   command option or positional argument to an input or output role.
3. **Role-bearing option name:** the option name acquires one exact-path role
   through the closed leading-or-trailing `input`/`output` convention and is
   not overridden by an annotation.
4. **Named input:** an exact `<name>` token resolves through `data.csv` and is
   therefore an input.

The proof must identify the canonical material path or named-input token, its
direction, and the command identity. A path-valued argument under an option
with no proven role remains a diagnostic candidate only. Keyword similarity,
a nonmatching conventional option name, path existence, directory proximity,
a unique candidate, and script internals do not prove direction.

A supported terminal `tee` capture is the final component of an otherwise
supported pipeline, has executable basename exactly `tee`, has no option or
operand grammar beyond one or more exact path operands, and establishes each
operand as an output of the recorded invocation. The ordinary stdout/stderr
pipeline into `tee` does not create another material edge. Append mode,
process substitution, dynamically constructed operands, and other `tee`
options are unsupported in the initial contract.

An invocation may expose several inputs and outputs. Their canonical material
identities, not authored role names, establish graph edges. Repeated references
to the same canonical material collapse to one relationship when their
directions agree. A command that both reads and overwrites one material may use
an explicit supported in-place proof form; otherwise the direction conflict
fails.

### Producer And Upstream Lineage

A local evidence source or direct artifact presentation has successful
producer provenance when exactly one recorded invocation mechanically proves
that it produces the material. Zero producers fail as `producer.missing`.
Several producers fail as `producer.ambiguous`. Candidate ranking is not part
of mechanical provenance.

A selected producing invocation has upstream lineage when one of its
mechanically established inputs has the same canonical material identity as
exactly one earlier invocation output in the maintained log. That identity
match creates the lineage edge and brings the input into the evidence-
provenance closure. No separate upstream declaration is required. An
unclassified argument establishes no input edge and therefore makes no claim
about lineage completeness.

The producer must precede the consumer in the research record. Cycles fail. A
local generated input with no matching producer fails as `lineage.missing`. An
input that resolves as trusted external data does not require a local producer.

Validation of one maintained log never reads another log's validation state or
uses another log's producer graph. A directly referenced cross-log file is an
external input observed by the consuming log.

### Accepted Provenance Roots

Producer traceability alone does not complete provenance. Beginning with each
local evidence source or directly presented artifact, every mechanically
established upstream branch must terminate at one or more accepted roots:

1. **Trusted external data:** a stable external input resolved through the
   applicable source or `data.csv` contract. Validation trusts its prior
   provenance and performs no traversal beyond that external boundary.
2. **Model command:** a producing invocation whose effective command type is
   `model`.
3. **Simulation command:** a producing invocation whose effective command type
   is `simulation`.

A model or simulation command may itself expose mechanically established
inputs. Every such input retains its ordinary producer or trusted-external
root requirement; the command type does not hide, excuse, or terminate those
input branches. When the typed command has no mechanically established data
inputs, the command itself is the terminal generated origin.

An untyped producing invocation with no mechanically established inputs is not
an accepted root. A lineage closure that terminates there fails as
`provenance.root.missing`. Several accepted external, model, or simulation
roots may contribute to one evidence result. The validator reports the exact
root set and preserves the distinction between `model` and `simulation`, but
both types satisfy the same mechanical root requirement.

### Named Inputs And `data.csv`

An exact `<name>` token in a recorded command resolves through the entry-local
`data.csv` contract. The token, its unique row, normalized location, and
observed material identity form one input connection. When the resolved row is
classified as external, it also forms a trusted terminal provenance root;
validation does not inspect its producer, import an external provenance graph,
or otherwise continue beyond it. A row resolving to generated local material
retains the ordinary local producer and lineage requirements. Duplicate,
missing, malformed, or unresolved rows fail. An unused `data.csv` row is a
orphan finding.

External classification is lexical and closed. A `data.csv` location is
external when it is an absolute path, a URI, or a relative location whose
normalized lexical path escapes the maintained-log root. A relative location
whose normalized lexical path remains within the maintained-log root is local,
including a path in the current entry or another entry of that log. Lexical
containment is decided before following a permitted entry-local directory
symlink; the resolved target is still observed and canonicalized under the
ordinary path and safety rules. The `type` column, file extension, source
contents, command prose, and presence or absence of a visible producer do not
change this classification. A generated local output indexed for a later
command therefore remains local and must have its producer lineage, while an
external row is trusted at the declared boundary even if another project could
in principle explain how it was created.

External material used by a recorded command should use a named token rather
than a raw absolute path, URL, or object-store location. Raw external locations
fail conformance unless the existing data-index contract explicitly permits
that source class. A named input establishes location and input direction; it
does not establish scientific suitability.

### Collections And Dynamic Membership

A collection is a finite set of canonical material paths consumed or produced
as one command relationship. It exists only when standard discovery proves its
complete membership through one of three initial mechanisms. The option-name
convention or an adjacent annotation may establish the direction of repeated
exact path arguments. A dedicated directory or manifest requires the
corresponding annotation-only collection role:

1. **Repeated exact path arguments:** every occurrence of one role-bearing
   option names one exact member path.
2. **Dedicated directory:** an `input-directory` or `output-directory` option
   names one directory; membership is every retained regular-file descendant
   observed beneath it at validation time.
3. **Bounded manifest:** an `input-manifest` or `output-manifest` option names
   one UTF-8 CSV file whose exact header is `path` and whose non-empty rows each
   name one member path relative to the manifest's parent directory.
Manifest paths must be unique normalized POSIX paths with no absolute path,
empty segment, `.`, `..`, reverse solidus, URI scheme, symlink, or alias. For
an input manifest, the manifest and listed members are command inputs. For an
output manifest, the manifest and listed members are command outputs. A
dedicated directory must be non-empty, remain beneath the entry root, and have
one unambiguous direction. Its membership is complete precisely because the
role declares that the entire directory is command-owned; added or removed
descendants therefore reopen the collection.

An output directory must be exclusive to one producing invocation. No other
recorded invocation may establish an output relationship to the same canonical
directory or any descendant. A shared output tree fails rather than dividing
ownership through filename inference. The research-agent repair is a unique
output directory per producer, repeated exact output paths, or a bounded output
manifest. Input directories may be consumed by more than one invocation.

Glob grammars, range-to-filename expansion, output templates, filename
inference, selector languages, per-command plugins, shared extensions, nearby
files, graph reachability, and undeclared subsets do not define initial
collections. Only collections used by a mechanically established input,
output, evidence source, or direct artifact relationship enter the required
graph. A directory-valued candidate with no proven directory role creates no
scope requirement.

The collection identity contains the producing or consuming invocation
identity, direction, canonical root when present, membership-mechanism
projection, and ordered member identities. Membership is unique, non-empty,
path-safe, and bounded. Every member must resolve beneath the permitted root.
Added, missing, aliased, duplicate, or changed members reopen the collection
outcome.

When a required dynamic collection cannot be completely determined by these
supported command-surface forms, validation fails as
`collection.membership.unresolved`. The initial contract does not add a
collection exception, inferred member list, or separate provenance record to
make it pass. A research agent may repair the recorded surface with repeated
arguments, a dedicated directory, a bounded manifest, or the optional adjacent
annotation.

### Material Graph Consistency

The mechanical graph contains only explicitly presented or mechanically
discovered nodes:

- v2 evidence records and their presented items;
- direct artifact presentations;
- canonical local files and named external resources;
- recorded invocations, their effective command types, and resolved local
  scripts;
- mechanically established inputs, outputs, and finite collections; and
- entry-local `data.csv` names.

Edges arise only from successful evidence association, exact command discovery,
canonical material identity, collection expansion, or data-index resolution.
The graph must satisfy:

1. every reference resolves exactly once;
2. every generated evidence source and direct artifact has exactly one
   producer;
3. every mechanically established generated input in the evidence-provenance
   closure matches exactly one earlier output;
4. producer lineage is acyclic;
5. every upstream branch terminates at trusted external data or a declared
   model or simulation command;
6. every required collection has one exact non-empty membership;
7. every collection member remains within its permitted root;
8. every used data-index token resolves once; and
9. no canonical material is both external and locally generated in the same
   maintained log.

Failure of one edge invalidates only provenance outcomes that depend on that
edge and its downstream closure. It does not rewrite a successful
evidence-value comparison as an association failure.

### Orphan Detection

Orphan detection inventories regular retained files beneath each entry root,
excluding:

- entry Markdown documents;
- `evidence.json` and `data.csv`;
- the `pyrun` symlink;
- validation-owned generated paths; and
- temporary paths excluded by the research-log contract.

Entry-local `data` and `images` are first-class members of the entry when they
are ordinary directories or directory symlinks. Orphan detection follows those two
exact symlinks, inventories their bounded regular-file descendants, and assigns
the canonical targets to the owning entry. Evidence, command, collection,
direct-artifact, and retention paths continue to use the natural entry-local
names such as `data/results.csv` and `images/residuals.png`. Validation does not
follow another entry symlink or a nested symlink beneath either material root;
an unavailable first-class root or nested symlink makes the material
observation unavailable rather than silently omitting part of the entry.

A retained file is connected when it is an evidence source, direct artifact
presentation, mechanically established command input or output, resolved local
script, required collection member, or resolved material of a used named
input. An otherwise unconnected file is `declared-retained` when exactly one
valid entry-local retention record covers its canonical path. Every remaining
candidate produces `orphan.material.unused`. An unused data-index name
produces `orphan.data_index.unused`.

Retention affects only orphan classification. It does not establish evidence,
input, output, producer, lineage, collection, or currentness edges and cannot
repair a failure in any of those relationships. Overlapping retention records,
missing targets, targets outside the entry, malformed paths, empty directory
membership, targets that are not otherwise orphan-eligible, and targets that
are already connected fail the retention declaration. A declaration that
becomes redundant must be removed rather than silently retained. The optional
`reason` remains available to Semantic
Review but is ignored by mechanical validation beyond its basic JSON type and
resource bound.

Free-form `Validation:` blocks and similar prose do not suppress orphan
findings. A research agent may connect the material through an actual command or evidence
relationship, declare it through a valid retention record, or remove it when
separately authorized. Validation may group residual findings by exact
directory prefix for bounded reporting, but grouping does not create a
collection, exception, or inferred lifecycle.

### Evidence And Provenance Integration

For every successfully associated entry evidence record or direct artifact
presentation:

1. resolve each local source to canonical material identity;
2. require exactly one mechanically discovered producing invocation for
   generated local material;
3. connect named external sources through the exact used `data.csv` token;
4. follow upstream lineage only through mechanically established inputs and
   exact input-output identity matches;
5. require every mechanically established lineage branch to terminate at
   trusted external data or an effective `model` or `simulation` command type;
6. expand only mechanically required and completely determined collections;
   and
7. let a summary reference reuse its target entry record's provenance
   projection rather than create another producer relationship.

Evidence association and provenance remain separately reported. A value may
match its retained source while the source lacks a mechanically proven
producer. In that case evidence comparison passes and provenance fails.
Neither outcome decides scientific validity or reproducibility.

No successful outcome asserts that every argument of a producing command was
classified or that every dependency hidden inside a script was discovered.
Those questions are outside the mechanical evidence-provenance contract.

## Mechanical Validation Evaluation And Outcomes

### Evaluation Order

Standard validation evaluates one target maintained log in this order:

1. parse document, evidence-file, and data-index structure, and scan supported
   command surfaces for relationship candidates;
2. establish presentation, evidence-record, invocation, and material
   identities;
3. resolve sources, named inputs, and project-local script identities;
4. evaluate locators, expectations, transformations, and presentation
   comparison;
5. establish producers for evidence starting points, then follow mechanically proven
   upstream inputs, accepted provenance roots, and required collection
   membership within that closure;
6. compose evidence and provenance outcomes; and
7. classify connected, declared-retained, and orphaned material over the
   completed graph.

A malformed owned entry or entry-local command surface produces an entry-scoped
conformance finding and does not prevent unaffected entries from continuing
through evidence, provenance, and orphan evaluation. Validation stops the
whole log only when the maintained summary or another log-wide structure
cannot be read or interpreted safely.

A failed prerequisite is not restated as several speculative mismatches. A
dependent result is `not_applicable` when a stable prerequisite failed and
`unavailable` when its prerequisite is temporarily unavailable. The dependency
note names the governing result.

### Result Scopes

| Condition | Owning scope | Aggregate effect |
| --- | --- | --- |
| Malformed CSV, JSON, Markdown, path, supported source structure, or authored command annotation | Conformance | Fails conformance; dependent evidence or provenance is not applicable. An unsupported unannotated command merely establishes no relationships. |
| Missing or conflicting evidence declaration or exact presentation mismatch | Evidence | Fails evidence. |
| Missing, ambiguous, conflicting, or incomplete producer, lineage, root, named-input, or collection relationship | Provenance | Fails provenance without changing the evidence-value result. |
| Temporary access failure or material changing during observation | Owning check as unavailable | Makes the aggregate incomplete. |
| Residual orphaned material or unused data-index name | Orphan | Reports findings without changing evidence or provenance status. |
| Scientific validity, interpretation, claim support, or summary meaning | Semantic Review | No mechanical result. |
| Ability to rerun and reproduce a workflow | Reproduction | No standard-validation result. |

Every applicable evidence or provenance check returns `pass`, `fail`,
`unavailable`, or `not_applicable`. Unsupported or ambiguous recorded state
required by an applicable check is `fail`. A correctly reported failure is a
completed validation result.

Every failure's machine-readable and human-readable forms must make the
problem directly identifiable: name the stable code, localize the exact
subject to the applicable file, row, record, marker, command, or material
path, state the observed condition, and name the violated rule and any
dependency cause. Failure reporting must not generate suggested edits, repair
choices, or declaration scaffolds. The research agent consults this
specification when correcting the authored research record.

### Composed Dependency Projection And Currentness

One evidence-rooted generated-material provenance outcome depends on:

1. command parser, command annotation grammar, command-type grammar, simulation
   filename convention, and option-name role-grammar versions;
2. canonical invocation identity and shell structure;
3. resolved executable or local-script identities;
4. exact command path, annotation, effective command type, redirection,
   option-role, and named-token projections;
5. canonical material identity and direction proof;
6. competing producer identities for the same material;
7. exact upstream input-output identity matches;
8. accepted external, model, and simulation root projections; and
9. required collection mechanism and membership projections.

One combined evidence-and-provenance outcome additionally depends on its
evidence-record, source, locator, transformation, presentation, and association
projections. Summary provenance depends on the referenced entry record's
successful projection and, for a table, the declared cell coordinate.

Unrelated commands and their unclassified arguments, files, evidence records,
entry prose, collections, orphan findings, other logs, and Git state do not
reopen an outcome. Whole-file hashes
may trigger parsing, but reusable results compare the narrower projections.

### Public Operation And Generated State

The public operation is:

```text
research_log_validation.py validate --summary PATH
  [--date YYYY-MM-DD] [--jobs N] [--recompute] [--dry-run]
```

`--summary` names one regular non-symlink maintained summary whose sibling log
root is a regular directory. `--date` defaults to the local calendar date and,
when present, must be one exact ISO date. `--jobs` must be positive.
`--recompute` bypasses all existing mechanical-cache reuse for the invocation:
the validator re-evaluates every check and rereads or rehashes every source and
artifact needed by those checks. It does not change validation scope, rules, or
the published result format. A completed published recomputation replaces the
disposable cache with the newly computed passing checks. These are the only
public standard-validation inputs; there is no mode, decisions, review,
semantic, or reproduction input.

The CLI writes one JSON result envelope to standard output when evaluation or
the unsupported-metadata preflight completes:

- `schema` is `research-log-validation-result/1`;
- `summary` is the resolved maintained-summary path;
- `status` is `complete_clear`, `complete_findings`, `incomplete`, or
  `unsupported_metadata`; and
- `published` states whether a new generated bundle was installed.

A mechanical evaluation envelope also contains its complete `record` and
non-authoritative bounded `metrics`. An unsupported-metadata envelope instead
contains `code:"validation.unsupported_metadata"` and `observed.paths`, which
lists every detected unsupported path. It contains no partial mechanical
record.

`complete_clear`, `complete_findings`, and `unsupported_metadata` exit zero
because the requested evaluation or preflight completed. `incomplete` exits 3 and
publishes nothing. Invalid inputs, observation failures outside the mechanical
outcome contract, and publication failures are operational errors: they exit
2, write a precise message to standard error, and publish no result.
`--dry-run` returns the applicable mechanical envelope with
`published:false` and writes no generated path. When combined with
`--recompute`, it performs the complete cache-independent evaluation without
publishing either the result or the rebuilt cache.

A completed published evaluation owns exactly these active generated paths:

```text
<log>/validation/mechanical.json
<log>/validation/.cache/mechanical.json
<log>/validation/.cache/lock
<log>/validation.md
```

`validation/mechanical.json` is authoritative and uses schema
`research-log-mechanical/1`. Its exact top-level fields are `schema`,
`summary`, `rules_version`, `result_date`, `completion`, `checks`, and
`scopes`. Checks are unique and sorted by `identity`; each contains
`identity`, `scope`, `status`, `subject`, `dependencies`, and, only for
`fail` or `unavailable`, `failure`. A failure contains `code`, `subject`,
`observed`, `rule`, and an optional `dependency`. Scope aggregates contain
`scope`, aggregate `status`, total `checks`, and counts for every check status.
The record is canonical UTF-8 JSON with one trailing newline.

`validation/.cache/mechanical.json` is disposable and uses the independent
schema `research-log-mechanical-cache/2`. Its exact top-level fields are
`schema`, `rules_version`, `checks`, and `artifact_identities`. `checks` retains
only passing checks with a nonempty dependency projection, keyed by check
identity; each value contains the exact check and its `dependency_projection`.
`artifact_identities` maps project-relative regular-file paths to their exact
byte size, modification time, change time, and SHA-256 digest. An artifact
identity avoids recomputing its digest only when all three current filesystem
observations match; it does not avoid reading the artifact when evaluating a
locator. Changed, missing, external, symlinked, malformed, or ambiguous paths
are not reused.

Check reuse requires the exact cache shape, current rules version, current
dependency projection, and an identical current check. A different rules
version invalidates every cached check but does not invalidate an artifact
identity whose cache schema, entry shape, project-relative regular-file path,
size, modification time, and change time still match exactly. Absence, invalid
JSON, excess size, extra or malformed fields, an unsupported schema, or
mismatched content causes bounded recomputation. `--recompute` treats both
checks and artifact identities as absent for reuse but does not delete or
modify the cache unless the completed evaluation publishes the replacement
generated bundle. Cache state never changes a conclusion.

`validation.md` is a deterministic nonauthoritative projection. Its Mechanical
Validation section contains completion, result date, counts by every scope and
status, and every non-passing check grouped by entry with its status, identity,
subject, and dependencies. Failed and unavailable checks additionally show
their code, observed state, and violated rule. It does not list individual
passing checks or provide repair instructions. A scope with zero checks has a
blank displayed aggregate status; the report does not present absent checks as
`not_applicable`. Its separate Reproduction
section is visibly `not_yet_run` until Phase 3 defines and publishes
`validation/reproduction.json`. The report has no combined pass/fail
conclusion, and standard mechanical validation reads or writes no reproduction
record.

Publication holds `validation/.cache/lock`, rejects symlinks in generated
destinations, rechecks the unsupported-metadata boundary under the lock, and atomically
replaces each destination. An ordinary error restores every replaced path to
the prior completed bundle before releasing the lock. Process termination is
subject to the per-destination atomicity boundary; a later invocation must not
interpret a partial bundle as current. `validation.md` is composed from the
authoritative operation records under the same lock. Mechanical-record and
cache schema versions evolve independently of evidence format v2 and of the
future reproduction-record schema.

### Command-Provenance And Orphan-Detection Failures

Every failure records the stable command, material, evidence-record, named-input,
or collection identity when known; the observed command or path projection; the
violated clause; and any dependency cause. It does not generate repair choices
or authored declaration scaffolds.

The invocation, command-type, material-direction, and collection failures below apply only
to authored command metadata or to a relationship in the evidence-
provenance closure. An unrelated or unclassified command argument is not a
failure.

| Code | Scope | Condition |
| --- | --- | --- |
| `invocation.command.unsupported` | conformance | Authored command metadata targets a command that cannot be parsed as one bounded supported invocation. |
| `invocation.executable.unresolved` | provenance | A producing invocation required by the evidence-provenance closure has an executable or local script that does not resolve safely. |
| `invocation.annotation.invalid` | conformance | An adjacent command annotation violates its closed grammar, placement, ordinal, type, option-reference, or role rules. |
| `invocation.path_value.embedded` | conformance | A role-bearing command value embeds a path in a `label=path`, `key=path`, or other unsupported compound argument. |
| `material.unresolved` | provenance | A required local path or named material does not resolve exactly once. |
| `material.direction.conflict` | provenance | Mechanically established relationships assign incompatible directions to one canonical material. |
| `producer.missing` | provenance | Generated evidence material or a direct artifact has no mechanically proven producer. |
| `producer.ambiguous` | provenance | Several recorded invocations mechanically qualify as producer of one material. |
| `producer.output_mismatch` | provenance | A candidate invocation does not prove output coverage of the required material. |
| `lineage.missing` | provenance | A local generated command input has no exact earlier output match. |
| `lineage.ambiguous` | provenance | A generated input matches outputs from several eligible invocations. |
| `lineage.cycle` | provenance | Mechanically established producer relationships contain a cycle. |
| `provenance.root.missing` | provenance | A mechanically established lineage branch terminates without trusted external data or an effective `model` or `simulation` command type. |
| `collection.membership.unresolved` | provenance | Required collection membership cannot be completely determined from supported command discovery. |
| `collection.membership.invalid` | provenance | Membership is empty, duplicate, escaping, aliased, over-broad, or inconsistent with its declared mechanism. |
| `collection.output_directory.shared` | provenance | More than one invocation establishes output ownership of one canonical directory or one of its descendants. |
| `collection.manifest.invalid` | conformance | A role-bearing collection manifest violates its exact CSV, path, direction, or resource contract. |
| `data_index.connection.missing` | provenance | A used named input lacks one unique resolvable `data.csv` row. |
| `data_index.raw_external` | conformance | A recorded external input bypasses the required named-input form. |
| `retention.declaration.invalid` | conformance | A retention record is malformed, overlapping, empty, escaping, aliased, or names an ineligible target. |
| `retention.target.missing` | conformance | A syntactically valid retention record names material that is not retained. |
| `orphan.material.unused` | orphan | Retained local material has no mechanically established graph connection. |
| `orphan.data_index.unused` | orphan | A `data.csv` name is not consumed by a recorded invocation. |
| `provenance.observation.unavailable` | provenance | Stable observation is temporarily impossible because material is inaccessible or changes while read. |
| `provenance.resource.too_large` | conformance | Command parsing, annotation parsing, graph expansion, or collection membership crosses a declared bound. |

### Command-Provenance Resource And Safety Bounds

The initial command-derived provenance profile permits at most:

- 64 concrete invocations after static expansion in one eligible command
  fence;
- 256 literal loop-iteration bindings in one eligible command fence;
- 4,096 parsed static-shell tokens in one eligible command fence;
- 4,096 static parser work items in one eligible command fence, where one work
  item is one logical source line or one function-body, loop-body, or case-branch
  line examined in an expansion context;
- 1,000 recorded invocations per maintained log;
- 128 mechanically established inputs and 128 outputs per invocation;
- 100,000 members in one required collection;
- 512 bytes in one normalized path or source expression;
- 1,000,000 material-graph nodes and 4,000,000 edges per maintained log;
- 64 producer-lineage levels;
- 1 MiB of recorded command text per invocation; and
- the stricter source-reader and evidence-record limits already defined by
  this specification.

Readers and command parsers must be non-executing, path-safe, symlink-safe,
and bounded. Validation does not execute commands, import scripts, deserialize
unsafe formats, follow unrestricted external links, or enumerate outside the
target maintained log except through the exact entry-local `data` and `images`
material roots or directly referenced external material. Crossing a stable
bound is `fail`; temporary material access failure is `unavailable`.

## Future Command-Discovery Expansion If Warranted

The contract intentionally stops at bounded static expansion, shell direction,
named inputs, the closed leading-or-trailing `input`/`output` option-name
convention, optional adjacent command annotations, the `model` and `simulation`
root types, the closed simulation filename convention, and the three finite
collection forms those roles enable. A missing or ambiguous result fails
regardless of whether one relationship appears likely.

Additional automatic role words or command types, internal-token matching,
glob grammars, range-to-filename expansion, dynamic output templates, selector
languages, per-command plugins, and evidence-record provenance hints remain
deferred until several concrete cases show that the initial forms make natural
research authoring materially awkward. A proposed addition requires
retained-corpus evidence and explicit researcher approval. It must be closed,
independently checkable from recorded state, bounded, and simpler overall than
renaming the option or repairing the command surface with an adjacent
annotation or manifest.

No future mechanism may select a merely plausible producer, suppress an orphan
without a retention record, invent missing historical lineage, override shell
direction, or inspect script internals as provenance authority.

## Conformance Examples

The first example composes presentation, evidence selection, transformation,
and recorded-command provenance. The locator and transformation examples that
follow isolate their respective subcontracts.

### Composed V2 Statistic

An entry presents:

```markdown
The candidate success rate was `67.6%`<!-- eid:candidate-success-rate -->.
```

Its entry-local `evidence.json` contains:

```json
{
  "schema": "research-log-evidence/v2",
  "records": [{
    "id": "candidate-success-rate",
    "document": "entries/2026-08-27-e001-study/e001.md",
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

The same experimental section records one command that names
`data/results.csv`:

````markdown
```bash
./pyrun scripts/run_study.py --dataset "<development-set>" --summary-csv data/results.csv
```
<!-- command summary-csv = output -->
````

Mechanical validation resolves the local script without executing or
inspecting its internals. The adjacent annotation assigns output direction to
the exact `--summary-csv` option and establishes that invocation as the sole
producer of `data/results.csv`. It resolves
`<development-set>` through `data.csv` as a trusted terminal external root and
does not traverse beyond it. The evidence check compares `67.6%`; the
provenance check verifies the producer, named-input connection, and accepted
root. Neither decides whether success rate is scientifically appropriate.

Without the annotation, `--summary-csv` has no automatic role and establishes
no edge. Because `data/results.csv` is an evidence source with no other mechanically proven
producer, provenance fails as `producer.missing`. Validation does not inspect
the script to infer the missing relationship.

Instead of adding the annotation, a research agent may change the real script
interface and every affected recorded command to use a role-bearing option
such as `--output-summary-csv` when that interface and command are legitimately
being maintained or rerun. Renaming only the Markdown command while leaving
the executable interface unchanged is not a valid repair. A completed
historical invocation that must preserve the command actually run uses an
annotation rather than a retrospective rename.

### Other Presentation And Provenance Cases

- A v2 summary statistic names one successful v2 entry evidence record through
  its adjacent `ref`. A table reference also names one exact row and
  column. The summary reuses the target record's source and command-provenance
  projection and does not declare another producer.
- A direct, structured, or summary table uses the applicable closed table
  recipe. Every local source used by the table must independently resolve to
  exactly one producing invocation unless it is a named external input.
- A marked output block may select a retained command log. A command whose
  supported shell structure contains `> data/run.log` establishes the output
  relationship directly; the marked fence payload must still match the
  selected retained text exactly.
- A direct artifact presentation has no evidence record. Its normalized
  Markdown target must match exactly one mechanically proven command output.
  A path merely mentioned by a command does not suffice.
- A cross-log source is observed as external material of the consuming log.
  It is a trusted external root; validation does not import the source log's
  command graph or validation result.
- Historical material with no mechanically discoverable producer fails
  `producer.missing`. There is no limitation declaration that converts the gap
  into a pass.

### Model And Simulation Roots

This command is automatically classified as `simulation` from its script stem:

````markdown
```bash
./pyrun scripts/simulate_trials.py --output-data data/trials.npz
```
````

Its output has a simulation origin without a separate type clause. A model
whose script name does not use the simulation convention declares its type in
the unified annotation:

````markdown
```bash
./pyrun scripts/evaluate_theory.py --output-data data/prediction.csv
```
<!-- command type = model -->
````

Both commands are accepted generated roots. If either command also exposes a
mechanically established input, that input must independently trace to trusted
external data or an earlier model or simulation root.

### Collection And Named-Input Case

Suppose an entry records:

````markdown
```bash
./pyrun scripts/run_trials.py --reference "<reference-grid>" --cases 1:40 --output-dir data/trials
```
<!-- command output-dir = output-directory -->
````

`<reference-grid>` must resolve through exactly one entry-local `data.csv` row.
The annotation establishes `data/trials` as a dedicated output directory, so
its complete collection is every retained regular-file descendant observed
beneath that directory. The `--cases 1:40` selector is ordinary command input;
validation does not need to understand how it maps to filenames. Without an
approved directory role or the annotation, the directory remains a candidate
and required collection discovery fails `collection.membership.unresolved`.

### V2

CSV:

```text
data/comparison.csv :: v2:{"expect":{"identities":[["8"],["15"]],"items":4,"matches":2},"identity":[["case_id"]],"select":[["case_id"],["value"]],"where":[{"op":"in","path":["case_id"],"values":["8","15"]}]}
```

CSV with explicit numeric predicate parsing:

```text
data/comparison.csv :: v2:{"expect":{"identities":[["trial-4"]],"items":2,"matches":1},"identity":[["trial_id"]],"select":[["trial_id"],["score"]],"where":[{"op":"eq","parse":"decimal","path":["score"],"value":0.95}]}
```

JSON scalar:

```text
data/results.json :: v2:{"path":["simulation",0,"throughput_pix_per_s"]}
```

JSON expansion:

```text
data/results.json :: v2:{"expect":{"items":4,"matches":4},"path":["trials",{"all":true},"score"]}
```

NPZ slice:

```text
data/run.npz :: v2:{"expect":{"items":1,"matches":1,"shape":[4]},"path":["reconstructor_column_cosine",{"slice":[2,6]}]}
```

HDF5 dataset property:

```text
data/smoke.h5 :: v2:{"path":["stats","sr"],"property":"shape[0]"}
```

Text:

```text
data/run.log :: v2:{"text":{"contains":"Benchmark simulations","occurrence":1}}
```

### V2 Failure Examples

| Condition | Failure |
| --- | --- |
| Unknown top-level key | `locator.syntax.invalid`. |
| Specialized tagged literal is malformed | `locator.literal.invalid`. |
| Two rows match `expect.matches: 1` | `locator.expectation.mismatch`. |
| Duplicate declared record identities | `locator.identity.duplicate`. |
| Observed identities differ from `expect.identities` | `locator.identity.expectation_mismatch`. |
| Numeric equality against a CSV lexical string without `parse` | `locator.type.mismatch`. |
| Explicit CSV decimal parsing encounters `1,25` | `locator.predicate.parse_failed`. |
| HDF5 external link leaves the retained file | `locator.source.unsafe`. |
| V2 JSON is malformed | `locator.syntax.invalid`; do not retry under another interpretation. |
| Wildcard selects more than the configured bound | `locator.selection.too_large`. |
| Summary `label` occurs outside the first column or is its row's only cell | `transformation.table.label_invalid`. |
| Boolean cell declares `style:"Yes/No"` | `transformation.boolean.invalid`; use `yes_no`. |
| Binary-float input is NaN or infinity | `transformation.nonfinite_unsupported`. |
| Binary-float input is not IEEE binary16, binary32, or binary64 | `transformation.type.mismatch`. |
| Numeric renderer declares `sign:"optional"` | `transformation.render.invalid`; use omission or `always`. |

## Version Evolution

The active v2 contract uses the following evolution rules:

- any change that alters the parsing or meaning of an existing valid v2 locator
  requires a new locator version;
- an additive source profile or structural property may join the v2 registry
  only when it cannot change an existing locator's dispatch or result;
- changing typed equality, path behavior, expectation semantics, selection
  order, or failure classification requires a new version;
- resource-limit increases do not change locator meaning but must be recorded;
- unsupported future versions fail without fallback.

The approved v2 transformation contract applies these evolution rules:

- any change that alters the parsing or meaning of an existing valid v2
  transformation requires a new transformation version;
- changing the value pipeline, rounding, rendering, unit attachment,
  input-consumption, canonical form, or table semantics requires a new
  version; and
- a future feature listed under `Future Expansion If Warranted` belongs in a
  later version unless it provably cannot change the result or validity of any
  existing v2 recipe.

A change to v2 JSON schema dispatch, record or marker identity, field
ownership, summary-reference syntax or coordinates, cardinality, Markdown parsing, exact comparison,
command identity, annotation or command-type syntax, provenance proof forms,
accepted-root semantics, graph semantics, or result scopes
that alters an existing valid outcome requires the applicable new evidence,
command-discovery, or mechanical-validation contract version.

## Current Implementation Boundary

Standard validation implements the active v2 locator, transformation,
association, presentation, command-discovery, provenance, material-graph,
orphan-detection, composed-outcome, generated-record, cache, report, and
unsupported-metadata preflight contracts in this document. It does not parse
unsupported generated validation metadata.

No downstream surface may define a competing evidence-record, locator,
transformation, presentation, command-provenance, collection, orphan-detection,
or mechanical-outcome contract. Self-contained runtime agent surfaces may
carry the bounded authoring and operational subset they need without loading or
linking to this specification, but they must remain compatible with it.
Maintainer-facing implementation documentation may omit detail by pointing
here and must not contradict this specification.

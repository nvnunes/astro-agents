# Research-Log Mechanical Validator Specification

## Status And Authority

Status: current mechanical-validator implementation specification. The
end-to-end Provenance contract is in force.

This document is the normative implementation contract for the code-only
research-log mechanical-validation CLI and its supporting tools. Their code,
tests, generated records, cache, diagnostics, and public operation must adhere
to this specification. It defines the evidence-record, association, Provenance,
Hygiene, and generated-state contracts that validator code implements.

This specification does not define agent behavior or teach researchers how to
use the research-logging workflow. `skills/research-logging/` is the
self-documenting agent surface. Repair is its sole explicit repository-level
consumer of this specification and reads only a relevant section when
malformed or legacy state prevents the owning CLI action from operating.
`docs/research-logging.md` is human-facing researcher documentation concerned
only with how that skill is used and what researchers should expect from it.

## Contract Map

- Evidence and selection: [Evidence-Record Role And Scope](#evidence-record-role-and-scope),
  [Locator Language](#locator-language), [Evidence Source Objects](#evidence-source-objects),
  [Common Evaluation Contract](#common-evaluation-contract), [Common Selection
  Result](#common-selection-result), [Mechanical Locator
  Language](#mechanical-locator-language), [Source Profiles](#source-profiles),
  and [Canonical Locator Serialization](#canonical-locator-serialization).
- Presentation: [Presentation Transformation
  Subcontract](#presentation-transformation-subcontract), [Closed Presentation
  Recipes](#closed-presentation-recipes), [Resource And Safety
  Bounds](#resource-and-safety-bounds), [Dependency Projection And
  Currentness](#dependency-projection-and-currentness), and [Failure And
  Limitation Codes](#failure-and-limitation-codes).
- Association and provenance: [Evidence File And Presentation
  Association](#evidence-file-and-presentation-association) and [Input Registry
  And Artifact Graph Contract](#input-registry-and-artifact-graph-contract).
- Validation: [Mechanical Validation Evaluation And
  Outcomes](#mechanical-validation-evaluation-and-outcomes) and [Current
  Implementation Boundary](#current-implementation-boundary).
- Extension and examples: [Future Command-Discovery Expansion If
  Warranted](#future-command-discovery-expansion-if-warranted), [Conformance
  Examples](#conformance-examples), and [Compatibility And
  Evolution](#compatibility-and-evolution).

## Current Versions

The specification describes the current contract. This table centralizes its
versioned surfaces; the rest of the document names a version only where
mechanical dispatch, serialization, dependency identity, cache compatibility,
or evolution requires it.

| Surface | Current version |
| --- | --- |
| Evidence records | `research-log-evidence/v3` |
| Locator language | 2; standalone locators use the `v2:` prefix |
| Transformation language | 2; standalone transformations use the `v2:` prefix |
| Input registry | `research-log-data/v3` |
| `pyrun` output support | `research-log-pyrun-outputs/v1` |
| Retention registry | `research-log-retention/v1` |
| Directory observations | `research-log-directory-observation/1` |
| Directory fingerprints | `research-log-directory-fingerprint/1`, `research-log-identity-files-fingerprint/1`, and `research-log-identity-patterns-fingerprint/1` |
| Locator evaluator | `research-log-locator-evaluator/1` |
| Section classifier | `entry-section-labels/1` |
| Selection-cache serialization | `research-log-selection-result/1` |
| Mechanical rules | `research-log-mechanical/end-to-end-provenance-1` |
| Mechanical record | `research-log-mechanical/1` |
| Authoring results | `research-log-authoring-result/1` |
| Validation results | `research-log-validation-result/1`, `research-log-validation-cli-result/1`, and `research-log-validation-batch-result/1` |
| Finding query results | `research-log-findings-list/1` and `research-log-finding/1` |
| Discovery results | `research-log-discovery-result/1` |
| Per-log validation cache | SQLite schema 1; `check_comparison` and `evidence_selections` component version 1 |
| Project fingerprint cache | SQLite schema 1 |

The specification includes `evidence.json`, presentation-marker, locator,
transformation, and command-discovery syntax because these are inputs to the
validator. The research-logging skill carries the bounded authoring and
operational rules agents need to produce compatible research logs; ordinary
research-agent work does not load this implementation specification.

Provenance lineage and execution-support validation is rooted in evidence
records. Commands outside that closure do not require
confirmed output support or recursive lineage validation. Separately,
complete-graph output reconciliation reports every graph-declared output whose
artifact is absent as a Provenance failure and every output record absent from
the current graph as a Hygiene finding. Recorded `pyrun` command surfaces,
exact path and named-input connections, `pyrun` output support, and observed
retained material establish Provenance without an authored lineage graph.
Resolved script bytes are part of the output-support signature. New execution
records also retain the bounded log-local Python source files actually loaded
by `pyrun`; validator currentness and graph semantics for those observations
are defined separately from command discovery.

The complete specification owns:

- the evidence-file and record contract;
- entry presentation and evidence-record identity and association;
- inline summary-to-entry evidence references, including exact table-cell
  coordinates;
- source expressions and whole-artifact references;
- locator syntax and its ordered typed selections;
- presentation transformations over those selections;
- evidence-rooted recorded-command discovery, producer and upstream lineage,
  explicit origin boundaries, `pyrun` output support, material collections,
  and named-input connection;
- complete-graph output reconciliation, including missing-output Provenance
  failures and unmatched-output Hygiene findings;
- Hygiene classification for unused retained material and unused input
  declarations;
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

- entry-local evidence serialization, schema, and record-level
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
evidence-association, command-Provenance, material-graph, Hygiene, and composed
outcome subcontracts below own the remaining stages.

## Locator Language

- A standalone locator begins with the `v2:` prefix.
- An evidence record embeds the locator's JSON object without that prefix.
- A locator with any other `v<integer>:` prefix fails as unsupported.
- Version selection occurs before version-specific parsing.
- A parse or evaluation failure is a mechanical failure and is not retried
  under another interpretation.

## Evidence Source Objects

An evidence record does not serialize source expressions into one
delimited string. Its ordered `sources` array contains objects with exactly
`source` and `locator`:

```json
{
  "source": "<results>",
  "locator": {
    "select": [["success_rate"]]
  }
}
```

`source` is one complete `<name>` or `<directory-name>/member` token from the
owning entry's input registry. JSON owns field separation; the string has no
embedded source-list or locator delimiter grammar.

`locator` is the JSON object portion of a locator. It must not contain a `v2:`
string prefix. The evaluator applies the current locator grammar before
parsing and uses `v2:` followed by its canonical JSON serialization as the
locator identity. A source object cannot contain a serialized locator string
or omit `locator`. Only a `kind:"artifact"` record uses `locator:null`.

Array order defines transformation input slots. There is no outer source-list
parser, mixed locator version, or CSV escaping in this host form.

Maintenance note: Changes to the evidence-source or locator host contract must
be reflected in
[Advanced Evidence Sources](../skills/research-logging/references/record-evidence-definition-sources.md),
[Advanced Numeric Evidence](../skills/research-logging/references/record-evidence-definition-numeric.md),
and their public-CLI conformance tests.

## Common Evaluation Contract

Evaluation proceeds in this order:

1. Resolve exactly one retained source.
2. Establish the source content identity and supported source profile.
3. Select the locator version.
4. Parse and normalize under that version.
5. Evaluate under the source profile and resource bounds.
6. Verify any declared identity, cardinality, and shape expectations.
7. Return a selection, a stable failure, or an unavailable observation.

A conforming evaluator must not guess misspelled fields, choose among ambiguous
matches, infer omitted facts, recursively search unless the selected
version explicitly requires it, or reinterpret a failed locator under another
version.

### Source Resolution And Classification

Source resolution precedes locator evaluation. Every evidence source resolves
through the owning entry-root `data.json`; direct paths and cross-entry
shorthand are invalid. A source profile is established from:

- the input-registry declaration or retained source declaration when present;
- the retained byte signature and safe structural inspection;
- the filename extension only as supporting metadata.

A declared format that conflicts with retained bytes fails as
`locator.source.format_mismatch`. A missing or inaccessible source is reported
under the evidence source-resolution contract. A source that changes during
locator evaluation is `unavailable`.

Every local source path is checked under the input registry's lexical and
canonical safety rules. A bare file token resolves to one local regular file.
A directory token must include one normalized member path that resolves to an
exact local regular file. A bare directory, direct relative or absolute path,
`<project>` path, `<log>` path, `<e###>` shorthand, URL, or object-store URI is
invalid at the evidence-source surface. To consume another entry's artifact,
the consuming entry declares that exact target and uses its own token.

The resolved strong content identity and source profile, not the authored token
or expected fingerprint, participate in selection-cache identity. A remote
registry target cannot directly serve as mechanical evidence; retain a stable
local observation and select that registered file instead.

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

The locator evaluator returns this ordered selection shape:

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

`eq` and `in` filters use canonical typed equality.

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

## Mechanical Locator Language

### Purpose

The locator language provides deterministic, bounded mechanical selection.

It supports:

- unambiguous JSON encoding;
- explicit paths and mechanically typed predicates;
- optional exact cardinality, membership, and shape assertions;
- stable record identities;
- a canonical cross-format value model;
- a small demonstrated source-profile set;
- exact currentness projections;
- stable, precisely identified failure behavior.

### Encoding

A standalone locator is `v2:` followed by one UTF-8 JSON object.

```text
v2:{"path":["simulation",0,"throughput_pix_per_s"]}
```

The top-level object may contain only:

| Key | Value | Purpose |
| --- | --- | --- |
| `path` | locator path | Select a base node or expanded node set. |
| `select` | non-empty array of relative locator paths | Select fields, members, or child values in declared order. |
| `where` | non-empty array of conditions | Filter record-like or aligned-array candidates. |
| `identity` | non-empty array of relative locator paths | Declare stable record identity fields. |
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

### Paths

A locator path is a JSON array. The empty array denotes the source root.

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

### Field Selection

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

### Authored Literals

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

### Conditions

`where` conditions combine with AND. Each condition contains:

- `path`: one relative locator path;
- `op`: one supported operator;
- `value`, containing one authored literal, for `eq`;
- `values`, containing a non-empty array of authored literals, for `in`; and
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
- `integer` accepts exactly the locator integer-string grammar.
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

### Record Identity

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

### Expectations

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
- `identities` requires `identity`. Every expected tuple must contain one
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

### Structural Properties

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

### Text Selection

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

## Source Profiles

The source-profile registry distinguishes value selection,
structural-property selection, whole-artifact selection, and prohibited
sources.

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
- Fixed-length string datasets are supported. Variable-length string datasets
  are prohibited because their decoded allocation cannot be bounded before
  materialization.
- `select`, `where`, and `identity` may treat explicitly selected aligned
  datasets as records along their common first axis.
- No recursive group search occurs.
- Supported properties are `members`, `member_count`, `shape`, `shape[n]`,
  `size`, and `dtype`.

### Text

#### Plain Text And Command Logs

Plain text follows the text-selector contract. Files must decode as UTF-8
without replacement characters.

The value-selection registry is limited to CSV/TSV, JSON, NPZ, HDF5/MATLAB
7.3, and UTF-8 plain text or command logs because those profiles cover the
retained locator corpus. Images, PDFs, SVG, and source files remain
whole-artifact evidence sources rather than locator containers.

### Directories, Pickle, And Opaque Sources

Directories are not locator containers. Their roles and bounded membership
mechanisms belong to the recorded-command collection-discovery subcontract.

Pickle and other execution-capable serialized objects are prohibited as
mechanically inspected value sources. The repair is to retain a supported
machine-readable companion artifact through an explicit recorded command.

An otherwise opaque source is not a locator container. Authors may present it
through a whole-artifact evidence record or retain a supported
machine-readable companion.

### Future Source Profiles If Warranted

ECSV, Parquet, YAML, Jupyter notebooks, NPY, FITS, pre-7.3 MATLAB files, and
media or document property readers are deferred. A profile may be added only
after retained cases demonstrate that converting to an already supported
companion artifact would make normal research work materially awkward. The
addition must be safe, bounded, non-executing, and unable to change dispatch or
results for an existing profile.

Maintenance note: Changes to an accepted source profile must be reflected in
[Advanced Evidence Sources](../skills/research-logging/references/record-evidence-definition-sources.md),
[Advanced Numeric Evidence](../skills/research-logging/references/record-evidence-definition-numeric.md),
and their public-CLI conformance tests.

### Indexed And Outside Sources

A resolved indexed source uses its resulting source profile. A remote-only
source must first be materialized as a locally accessible, fingerprinted
input. The validator must not infer current values from a URL, prose
description, or unavailable service.

## Canonical Locator Serialization

A locator normalizer:

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

The transformation language provides a closed, code-only grammar for each
supported presentation form.
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

### Transformation Encoding And Identity

- In an evidence record, `transformation: null` declares identity and a
  non-null transformation is the JSON object portion of a transformation.
  It has no string prefix in the JSON host.
- Identity has canonical identity `identity`.
- An embedded transformation object's canonical identity is `v2:` followed
  by its canonical JSON serialization, matching the standalone prefixed form.
- A string, array, number, or Boolean in an evidence-record `transformation`
  field fails.
- A parse or evaluation failure is not retried under another
  interpretation.
- Locator and transformation objects retain independent grammars and canonical
  identities within the evidence record.

### Transformation Input Bundle

The input is an ordered bundle of locator selections. Input slot `0`
corresponds to the first evidence source object, input slot `1` to the
second, and so on. Each slot exposes its selected items in locator order.

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

Every input item must be referenced exactly once. The transformation does not
silently drop, duplicate, broadcast, coalesce, or reuse values. Authors must
narrow the locator or retain a purpose-built source when its selection does
not correspond one-to-one with the presentation.

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
supported transformation or an exact retained string.

The presentation association and source-cardinality contracts define which
identity selections correspond directly to one statistic, table, or output
block. If the one-to-one association is not unique and exact, validation fails.

## Closed Presentation Recipes

### Encoding

The standalone transformation form is `v2:` followed immediately by one
UTF-8 JSON object. It is used by this specification and conformance fixtures.
An evidence record embeds that same JSON object directly in its
`transformation` field and omits the prefix. Both forms have identical meaning
and canonical identity.

The JSON must satisfy the same lexical and duplicate-key requirements as a
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

Canonical serialization uses the common locator JSON rules and lexicographic object
keys. Array order is meaningful and preserved. For `percentage`, an explicit
`decimal_places:1` canonicalizes to the same form as omission, with the default
field omitted.

Maintenance note: Changes to the non-table transformation grammar must be
reflected in
[Advanced Numeric Evidence](../skills/research-logging/references/record-evidence-definition-numeric.md)
and its public-CLI conformance tests. Changes to exact text passthrough must
also be reflected in
[Advanced Retained-Output Evidence](../skills/research-logging/references/record-evidence-definition-outputs.md).

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
input in a transformation recipe. It is forbidden for strings, Booleans, and
null.

A value expression without numeric fields may pass through one string or null
exactly, but only a form that explicitly permits that type may use the result:
`text` accepts a string, and a table scalar accepts null. Numeric forms require
a numeric input and `render`. A Boolean may be consumed only by the table
`boolean` form. A finite binary float may be used as a numeric input without
`parse`; its exact canonical bit pattern defines the value consumed by
`magnitude`, `scale`, and `render`. Non-finite binary floats and other compound
canonical values are unsupported transformation inputs.

### Numeric Rendering

The transformation language uses one rounding mode: decimal
round-half-to-even. Rounding is part of the declared renderer; no separate
rounding operation exists.

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
and applies once to the complete form. It must be a non-empty Unicode string
of at most 32 UTF-8 bytes with no leading or trailing whitespace, Markdown
delimiters, line breaks, or control characters.

The canonical renderer attaches `%`, `°`, `°C`, `°F`, and `x` directly to the
preceding form. Every other unit follows one ASCII space. Thus `unit:"x"`
produces `3.39x`, while `unit:"cases"` produces `4 cases`. Unit aliases are not
recognized. The declared unit is the exact expected presentation suffix; the
transformation does not infer its dimension, decide whether a suffix is
scientifically a unit, or infer its relationship to `scale`.

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

The transformation language defines exactly these non-table forms:

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

The transformation language defines three table modes:

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

Maintenance note: Changes shared by all table modes must be reflected in
[Direct Evidence Tables](../skills/research-logging/references/record-evidence-definition-direct-tables.md),
[Structured Evidence Tables](../skills/research-logging/references/record-evidence-definition-structured-tables.md),
and
[Summary Evidence Tables](../skills/research-logging/references/record-evidence-definition-summary-tables.md),
together with their public-CLI conformance tests.

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

These are the canonical output spellings. Presentation comparison is
case-insensitive only for cells declared as Boolean; text cells, headings, and
source parsing remain exact. There are no aliases or custom true/false strings.

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

Maintenance note: Changes to direct-table syntax or behavior must be reflected
in
[Direct Evidence Tables](../skills/research-logging/references/record-evidence-definition-direct-tables.md)
and its public-CLI conformance tests.

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
record grouping cannot drive a structured recipe. The repair is to use a
record locator, use summary mode, or retain one direct table.

`rows` may additionally contain `order`, an array containing every driver
identity tuple exactly once in the required presentation order:

```json
{"input":0,"order":[["case-8"],["case-15"]]}
```

An explicit order requires the locator to declare `identity`. Its tuples use
authored literals, must match the record identity arity and types,
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

Maintenance note: Changes to structured-table syntax or behavior must be
reflected in
[Structured Evidence Tables](../skills/research-logging/references/record-evidence-definition-structured-tables.md)
and its public-CLI conformance tests.

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

Maintenance note: Changes to summary-table syntax or behavior must be
reflected in
[Summary Evidence Tables](../skills/research-logging/references/record-evidence-definition-summary-tables.md)
and its public-CLI conformance tests.

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
re-evaluation. The validator never derives a precision, unit, form, label,
order, or shape
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

The transformation language excludes features that would make the
validator accept more equivalent presentations or become a general-purpose
formatting engine. A later version may add a feature only when retained corpus
cases demonstrate that the canonical form would materially harm ordinary
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

Evaluation uses this required default limit profile:

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
| Binary materialization in one source evaluation | 512 MiB total |

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
evaluator, locator, membership, and value projections preserve the located
evidence outcome. Unselected source changes do not themselves reopen the
downstream outcome.

Source-profile identity rules are:

- hierarchical values use their complete canonical locator path;
- arrays use the retained member or dataset path plus exact indexes;
- record selections use declared identity tuples when present;
- text uses the selector identity, match rank among matching lines, and selected
  text content, not absolute line number;
- structural properties use the target coordinate and property name;
- whole artifacts use the complete artifact content identity.

If a record selection has no inherent coordinate and no declared identity,
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
| `locator.literal.invalid` | fail | An authored scalar or specialized tagged literal is malformed, non-canonical, or unsupported in its syntactic position. |
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
| `locator.expectation.mismatch` | fail | Observed matches, items, or shape differs from `expect`. |
| `locator.property.unsupported` | fail | The property is not defined for the selected source profile or type. |
| `locator.text.decode` | fail | A declared text source is not valid UTF-8. |
| `transformation.version.unsupported` | fail | The declared transformation version has no enabled evaluator. |
| `transformation.syntax.invalid` | fail | Version-specific syntax, keys, clauses, or key relationships are invalid or conflicting. |
| `transformation.presentation.mismatch` | fail | The associated presented item is not one of the surface spellings defined by the declared transformed form. A table mismatch reports table shapes, the total differing-cell count, and at most 16 one-based heading or cell differences with expected and observed values. |
| `transformation.input.reference_invalid` | fail | A concrete item reference or structured field reference does not resolve in the required input. |
| `transformation.input.unused` | fail | A locator-selected item is not consumed by the recipe. |
| `transformation.input.reused` | fail | One selected item is referenced more than once. The transformation requires exact one-time consumption. |
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
| Entry presentation | Entry-root `evidence.json` | Entry-scoped stable ID shared with one hidden Markdown marker |
| Summary reference | Maintained summary Markdown | Hidden reference to one entry ID and entry evidence ID, plus a table coordinate when applicable |

All evidence declarations are in entry-local `evidence.json`. Every eligible
entry presentation uses its required marker, and every eligible summary
statistic uses its required reference. Every evidence file belongs to the
root of the entry whose records it owns; any other placement fails as
`evidence.file.location_invalid`.

The maintained summary's `## Entries` inventory is the only owner-discovery
surface for the target log. Entry links elsewhere in summary prose are ordinary
navigation, including links to another maintained log, and do not import those
entries. Every owned entry resolves beneath the target log's `entries/`
directory. A directly referenced cross-log artifact remains a locally declared
origin under the command-Provenance contract.

The bounded unsupported-metadata preflight detects recognized unsupported
generated validation metadata and returns one
`validation.unsupported_metadata` result listing every path found. It writes
nothing and does not interpret the unsupported content. Retention records use
their own ID namespace and participate in Hygiene classification, not
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

When no active `validation/results.json` exists, the preflight also treats
`validation.md` as unsupported generated state if its bounded prefix contains
the `| Entry | Date | Checked | Reproducibility |` table header or the
`## Status Summary` marker. It does not parse any unsupported JSON, shard,
cache, decision, session, or report conclusion. An unrelated file is not
unsupported state merely because it is below a directory named `validation`.

Evidence records embed locator and transformation objects directly under the
current grammars.

### Evidence JSON File Schema

`evidence.json` uses one exact top-level object:

```json
{
  "schema": "research-log-evidence/v3",
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
      "source": "<results>",
      "locator": {
        "select": [["success_rate"]]
      }
    }
  ],
  "transformation": null
}
```

Required keys are `id`, `document`, `kind`, `sources`, and `transformation`;
unknown keys fail. `kind` is `artifact`, `statistic`, `table`, or `output`.
`sources` is a non-empty ordered array of exact evidence source objects.
`transformation` is `null` for identity or the JSON object portion of a
transformation without a `v2:` prefix. Record kinds are entry
presentations. Summaries use the Markdown-owned references defined below, and
disconnected retention belongs in `retention.json`.

Entry-owned disconnected retention uses the separate retention contract in
`retention.json`. A retention record is
invalid in `evidence.json` and has no presentation marker, source, locator, or
transformation.

The evaluator applies the current grammars to every embedded locator and
non-null transformation. Their canonical identities retain the `v2:` prefix
plus canonical JSON serialization, even though the host file stores only the
JSON object.

An artifact record is the closed whole-artifact form:

```json
{
  "id": "residual-map",
  "document": "entries/2026-08-27-e001-study/e001.md",
  "kind": "artifact",
  "sources": [{"source": "<residual-map>", "locator": null}],
  "transformation": null
}
```

It has exactly one source, a null locator, and a null transformation. Null
locators are prohibited for every other record kind. The source resolves to
one registered file or one exact member of a registered directory; a bare
directory is invalid. The source registry fingerprint supplies complete
artifact identity, so the evidence record does not duplicate a path or digest
and validation does not open the artifact through a format-specific reader.

`id` uses this grammar and is at most 96 ASCII characters:

```text
[a-z][a-z0-9]*(?:-[a-z0-9]+)*
```

IDs must be unique within one entry-local `evidence.json`. The stable record
identity is `(maintained-log identity, entry identity, id)`. The same short ID
may occur in another entry. It may also occur in that entry's separate
retention namespace; the two do not conflict. Moving an evidence record to
another entry changes its identity. Changing its presented value does not.
Copying a record within the same entry requires a new ID.

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

### Entry Presentation Markers

An evidence presentation record and its presented item share one exact
marker:

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
- An `artifact` marker immediately follows one eligible local Markdown link or
  image embed on the same source line with no intervening characters. It binds
  to that immediately preceding Markdown node, including when one line contains
  several separately marked artifacts.

One marker binds exactly one presented item. One presented item has exactly one
marker. A marker ID must resolve to exactly one presentation record whose
`document` and `kind` agree with the observed item. Duplicate markers, nested
markers, a marker in a fence, a marker without an eligible item, and a
presentation record without a marker fail.

The marker makes entry evidence identity independent of heading text, line
number, rendered value, and surrounding prose. Those observations may still
be currentness or conformance inputs where this subcontract names them
explicitly.

### Summary Evidence References

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
`eid` satisfies the evidence-ID grammar and names one presentation
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
for a statistic target. An output presentation record cannot be referenced.
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

The active association contract has this structural boundary:

- entry statistics are eligible only in an experimental section;
- entry tables, output blocks, and local artifact links or image embeds are
  eligible only beneath that experimental section's `Results:` label;
- summary statistics are eligible only in the maintained summary; and
- synthesis and prose entry sections contain no evidence-record targets.

The deterministic section classifier remains outside this specification but
its declared classifier version and classification result are association
dependencies. A marker cannot override an ineligible context.

Every eligible entry statistic, table, `text` output block, local artifact
link, or local image embed must have one valid entry marker, and every eligible
summary statistic must have one valid summary reference. A missing entry marker fails
`association.declaration_missing`; a missing summary reference fails
`summary.reference.missing`. Other unmarked prose is not promoted to evidence
by validation. Semantic Review may report an apparently evidential claim that
uses no supported presentation form.

External links, fragment-only links, and Markdown-document links are not
artifact evidence presentations. Summaries cannot present artifact evidence.

For an artifact record, validation normalizes the marked Markdown target
relative to its document and independently resolves the source token through
`data.json`. Both must identify the same canonical artifact path before
fingerprint, content, or Provenance evaluation. A different path fails
`association.artifact.source_mismatch` even when its bytes are identical.

### Evidence Source And Transformation Cardinality

Non-artifact entry records consume source objects in their declared array
order. Each object contains one embedded locator and must return one successful
ordered typed selection. The transformation input slot is the zero-based
`sources` array position. Artifact records use the whole source and have no
selection or transformation input.

Cardinality is closed by presentation kind:

| Kind or table mode | Source objects | Additional requirement |
| --- | ---: | --- |
| `artifact` | 1 | The source and marked Markdown target resolve to the same complete artifact. |
| `statistic` | 1–8 | The transformation produces exactly one supported non-table form. |
| `output` | 1 | The locator selects exactly one string and identity or `form:"text"` produces the complete block payload. |
| `table` / `direct` | 1 | The selected table and recipe satisfy direct-table one-to-one rules. |
| `table` / `structured` | 1 | Every selected record and field satisfies repeated single-source consumption. |
| `table` / `summary` | 1–32 | Every selected item is consumed exactly once by an evidence cell. |

An evidence table record must use a non-null table transformation. Null identity is
not a second table grammar. A statistic may use null identity only when one
selected primitive renders to exactly one canonical statistic expression. An
output may use null identity only for one selected string.

Whole-artifact evidence is valid only through `kind:"artifact"`. It cannot use
a locator or transformation and cannot be consumed by summary evidence.

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
boundary, combine records, or target an output presentation record. It inherits
the referenced entry record's completed evidence and provenance projections but
not the supporting sentence, heading, interpretation, or semantic claim.
Whether surrounding summary prose faithfully
synthesizes the entry belongs to the Summary Fidelity review lens.

### Association Completeness And Conflict Rules

Validation constructs the active association index across one maintained log
and then applies these rules in order:

1. every evidence record ID is unique within its entry;
2. every entry marker ID is unique within its entry;
3. every presentation record resolves its declared document and permitted context;
4. every presentation record and marker agree on document, ID, and kind;
5. every marked entry presentation has exactly one record;
6. every presentation record has exactly one presentation;
7. every summary reference resolves exactly one eligible entry evidence record and,
   for a table, one in-bounds numerical cell; and
8. source, locator, transformation, and exact presentation comparison succeed.

No occurrence number, nearest-heading rule, same-value search, filename
similarity, or other tie-breaker repairs a conflict. A duplicate or ambiguous
identity prevents evaluation of every record and presentation that depends on
it. Unrelated uniquely associated records remain independently evaluable.

### Association Dependency Projection And Currentness

One association outcome depends on:

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
reopens its attached presentation and dependent summary references. Adding,
removing, or changing a summary reference reopens that summary association. A
newly observed unsupported generated-state path causes the preflight to return
`validation.unsupported_metadata`; its contents do not enter currentness.

The validator may use whole-file hashes to detect a need for parsing but must
persist and compare the narrower association projection for outcome reuse.

### Association Failures

Every failure records the evidence record identity when known, document, kind,
observed marker or presentation, and violated clause. Reserved active-validation
codes are:

| Code | Scope | Condition |
| --- | --- | --- |
| `validation.unsupported_metadata` | unsupported-metadata preflight | Active validation encountered recognized unsupported generated validation metadata. The result lists every detected path and writes nothing. |
| `evidence.json.schema_invalid` | conformance | An evidence JSON file has an invalid top-level schema, shape, or JSON encoding. |
| `evidence.file.encoding_invalid` | conformance | An evidence JSON file is not permitted UTF-8. |
| `evidence.file.empty` | conformance | An evidence JSON file has no records. |
| `evidence.file.location_invalid` | conformance | An `evidence.json` occurs outside an entry root, including at the maintained-log root. |
| `evidence.declaration.invalid` | conformance | An evidence record violates its exact field, type, enum, path, or shape constraints. |
| `evidence.record.id_duplicate` | conformance | One evidence ID occurs in several evidence records within the same entry. |
| `presentation.marker.invalid` | conformance | Marker syntax or placement is invalid. |
| `presentation.marker.duplicate` | conformance | One evidence ID occurs in several entry presentation markers within the same entry. |
| `association.declaration_missing` | evidence | An eligible entry presentation has no matching evidence record. |
| `association.presentation_missing` | evidence | An evidence presentation record has no matching presentation. |
| `association.document_mismatch` | evidence | The evidence record and marker do not identify the same permitted document. |
| `association.kind_mismatch` | evidence | Declared and observed presentation kinds differ. |
| `association.artifact.source_mismatch` | evidence | A marked artifact target and its one source token resolve to different canonical paths. |
| `association.context_invalid` | conformance | The presentation is outside its permitted section or label. |
| `association.source_cardinality` | evidence | The source count violates its kind or table mode. |
| `association.presentation.syntax_invalid` | conformance | The marked Markdown item is outside the closed structural parser. |
| `association.presentation.mismatch` | evidence | Parsed presentation differs from every accepted transformation result. |
| `association.resource.too_large` | conformance | Association indexing or one parsed item crosses a declared bound. |
| `summary.reference.missing` | evidence | An eligible summary statistic has no adjacent summary reference. |
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

The active evidence-record and association profile permits at most:

- 10,000 evidence records, 10,000 entry presentation markers, and 10,000 summary
  references per maintained log;
- 1,000 evidence records in one file;
- 96 bytes in an ID;
- 512 bytes in one summary reference;
- 512 bytes in a document path;
- 32 source objects in one record;
- 8 MiB in one `evidence.json` file;
- 1 MiB of source Markdown for one marked table or output block; and
- the stricter locator and transformation bounds already defined by this
  specification.

Crossing a stable authored bound is `fail`, not `unavailable`. Implementations
may stream files and indexes and must not require repository-wide discovery.

## Input Registry And Artifact Graph Contract

### Registry And Generated-State Ownership

This section defines the command-input, fingerprint, Provenance,
retention, and Hygiene contract.

The current schema and rules identifiers are listed in `Current Versions`.
`pyrun-outputs.json` is `pyrun`-owned execution support state, not an authored
registry or a validator-generated report. Disposable per-log validation
acceleration uses the listed SQLite schema and component versions. A rules
change makes prior checks ineligible for unchanged
comparison without invalidating compatible selections or project-level
observations. A per-log database or component-version change likewise does not
invalidate project-level input observations.

Accessible local input observations belong to the generated project-level
SQLite database at
`<project>/.cache/research-log-fingerprints.sqlite3`. Its schema stores file
observations by canonical absolute path with kind, size,
nanosecond modification time, nanosecond change time, fingerprint algorithm,
and observed content digest. It stores directory metadata identities,
aggregate directory fingerprints, and deterministic membership separately.
The expected fingerprint in `data.json` is not part of the observation-cache
key. A changed expectation compares against the current observed identity
without forcing a content reread.

Every validation performs one bounded current directory metadata observation.
An unchanged hydrated directory reuses its aggregate content fingerprint. A
changed directory reconstructs its aggregate fingerprint from current ordered
membership, reused identities for unchanged member files, and newly hashed
identities for only new or changed member files. Matching metadata before and
after reconstruction is required, so a concurrent change cannot be recorded as
the identity of older content.

All logs in one project share the database. File hashing occurs while holding
a process-safe SQLite write transaction, so concurrent validators cannot hash
the same uncached path independently. Each completed file observation commits
separately and survives later interruption. Mechanical rules and generated
report schemas do not require content re-observation. Neither cache changes a
conclusion.

### Ownership And Completeness

`data.json` is primarily an input registry. It contains all and only resources
used as material inputs by recorded commands or evidence records owned by one
entry root.

The public `log data` actions are the sole ordinary authoring interface for
this file. They infer representation fields, validate the asserted Provenance
boundary, and publish canonical entry-scoped state. Direct edits are reserved
for explicitly authorized Repair.

- Every proven command input and every evidence source has exactly one data
  item in the consuming entry.
- Every input-bearing command argument and evidence source uses the item's
  exact `<name>` token or one exact `<directory-name>/member` token. A Git
  repository consumer additionally uses the matching `<name:commit>` token.
  Raw paths and URIs are invalid.
- A generated output enters `data.json` when a later recorded command or an
  evidence record consumes it. An output consumed by neither surface remains
  absent.
- Evidence use counts as registry use when evaluating unused declarations.
- An evidence source resolves to one local regular file. A bare directory token
  is invalid; select one exact member instead.
- An entry with no inputs omits `data.json`; a
  present file is non-empty.
- Split documents at one entry root share one file. The validator does not
  search, inherit, merge, or shadow parent-entry or log-level files.

`evidence.json` contains only presentation records. `retention.json` contains
only intentional disconnected retention. Recorded commands own producers and
ordinary lineage. Generated validation records remain validator-owned.

### Input Registry

One entry-root file has exactly:

```json
{
  "schema": "research-log-data/v3",
  "inputs": []
}
```

Both keys are required and unknown keys fail. `inputs` is non-empty. Strict
JSON uses the UTF-8, duplicate-key, finite-number, and trailing-content rules
of `evidence.json`. Array order has no meaning; canonicalization sorts by
`name`. One file is at most 8 MiB and contains at most 10,000 inputs.

Every item has exactly `name`, `kind`, `location`, `fingerprint`, and the
Boolean `origin`:

```json
{
  "name": "development_catalog",
  "kind": "file",
  "location": "../../../../../inputs/development-catalog.csv",
  "fingerprint": {
    "algorithm": "sha256",
    "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "origin": true
}
```

`name` is at most 96 ASCII characters and matches
`[A-Za-z0-9][A-Za-z0-9_-]*`. `log`, `project`, `theme`, and names matching
`e[0-9]+` case-insensitively are reserved. The numeric entry-family namespace
is reserved for maintained entry identifiers.
`kind` is `file`, `directory`, or `git-repository`. A `git-repository` item is
always an origin and identifies one tracked repository snapshot rather than
the repository directory or live checkout.

`location` is a normalized POSIX path relative to the owning entry root or an
absolute POSIX path. Paths have no reverse solidus, empty segment, or `.`
segment. Relative paths may use `..`; resolution from the entry root determines
their canonical target. A location contains no URI, token, environment, glob,
shell, or template expansion. One location is at most 2,048 UTF-8 bytes.

A canonical target is the safely resolved filesystem locator after the
existing first-class entry `data` or `images` symlink rule. No other declared
or nested symlink is allowed. Names are unique within one file. File and
directory canonical targets are also unique. Git repository declarations use
their commit fingerprint as material identity, so one repository locator may
identify different commits under different names; the same pinned commit may
not be declared twice in one file.

Separate entries may declare the same material when each consumes it. Within
one maintained log, all file and directory declarations of one target must
agree on `kind`, `fingerprint`, and `origin`. Git repository declarations agree
when their commit material identity agrees; locator paths may differ. Conflict
fails; validation does not choose one declaration. The conflicting
declarations are unavailable to dependent command and graph evaluation; other
declarations in the same registry and entries that do not declare the target
continue evaluation.

### Fingerprints

Every item has exactly one closed fingerprint:

- A local file uses `{"algorithm":"sha256","digest":"<64 lowercase hex>"}`.
- A local directory uses
  `{"algorithm":"directory-sha256-v1","digest":"<64 lowercase hex>"}`.
- A managed local directory uses
  `{"algorithm":"identity-files-sha256-v1","files":["<relative path>",...],"digest":"<64 lowercase hex>"}`.
- A pattern-managed local directory uses
  `{"algorithm":"identity-patterns-sha256-v1","patterns":["<relative selector>",...],"digest":"<64 lowercase hex>"}`.
- A pinned Git repository uses
  `{"algorithm":"git-commit-sha1-v1","digest":"<40 lowercase hex>"}`.

Every resource is locally accessible. Files and directories use byte-derived
content digests. A Git repository fingerprint identifies the exact commit
object and its tracked snapshot.
Size, modification time, and change time may determine whether a cached digest
must be recomputed, but they are never the identity being validated. If the
recomputed digest is unchanged, the resource is unchanged for Provenance.
Fingerprint drift fails and validation never rewrites an authored digest.

For `git-commit-sha1-v1`, `location` is only a local repository locator. It
must be an exact worktree or bare-repository root, and the full lowercase
40-hex object must exist there with object type `commit`. Validation does not
observe or assign material meaning to `HEAD`, the index, working-tree bytes,
untracked files, `.git/config`, or the repository directory. Moving the locator
does not change material identity when the new repository contains the same
commit. A live environment, dirty or untracked file, generated model, map,
cache, build product, submodule checkout, or other consumed state outside that
commit is a separate material input.

`directory-sha256-v1` hashes the UTF-8 bytes of canonical compact JSON:

```json
{
  "schema": "research-log-directory-fingerprint/1",
  "entries": [
    {"path": "empty", "type": "directory"},
    {
      "path": "samples/run-01.npz",
      "type": "file",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

Canonical JSON sorts object keys and has no trailing newline. Paths are
root-relative normalized POSIX paths, normalized to Unicode NFC, and sorted by
UTF-8 bytes. Every descendant directory, including an empty directory, has one
`directory` entry. Every regular file has one `file` entry and bytewise
SHA-256. The root itself is omitted; an empty root hashes an empty array.

Normalization collisions, symlinks, special files, unreadable entries,
membership changes during observation, more than 100,000 descendants, a path
over 512 UTF-8 bytes, or more than 1 TiB of file content fail under the
applicable stable or temporary observation rule. Addition, deletion, rename,
entry-type change, or content change changes the digest. Descendant traversal
stops when the first over-limit member is observed; implementations must not
materialize an unbounded tree before enforcing the limit.

`identity-files-sha256-v1` identifies one managed directory through 1–64
explicit producer-owned identity files. Each path is a unique normalized
root-relative POSIX path subject to the directory-member path restrictions and
must resolve to a non-symlink regular file inside the declared root. Validation
does not infer conventional filenames, expand globs, or traverse descendants.
It hashes the UTF-8 bytes of canonical compact JSON:

```json
{
  "schema": "research-log-identity-files-fingerprint/1",
  "files": [
    {
      "path": "build.h5",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "path": "build.yaml",
      "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    }
  ]
}
```

Canonical entries sort by the UTF-8 bytes of their relative path. The mode
asserts the logical identity represented by the declared files; it does not
claim bytewise coverage of undeclared descendants. The researcher must select
identity files owned by the resource producer that change whenever the
scientifically relevant resource identity changes. Missing, aliased, unreadable,
or concurrently changed identity files fail observation. Shared cache reuse is
per declared file and requires exact size and nanosecond modification and
change times.

`identity-patterns-sha256-v1` identifies one managed directory through 1–64
normalized exact or wildcard selectors. Exact selectors may name nested files.
Wildcards `*`, `?`, and character classes are allowed only in the final path
component; recursive `**` and wildcard parent directories are invalid. An exact
selector must resolve to one non-symlink regular file. A wildcard selector may
resolve to zero files. The selector set must resolve to 1–64 unique files, and
overlapping selectors are invalid.

Validation scans each distinct wildcard parent at most once per membership
observation and examines at most 100,000 immediate entries in that parent. It
does not recurse into descendants. Crossing the candidate-entry or resolved-file
bound fails as `directory.membership.invalid`; an unreadable parent or concurrent
membership change is unavailable. The fingerprint hashes the UTF-8 bytes of
canonical compact JSON:

```json
{
  "schema": "research-log-identity-patterns-fingerprint/1",
  "patterns": ["build.h5", "maps-*.h5"],
  "files": [
    {
      "path": "build.h5",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "path": "maps-hpx6.h5",
      "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    }
  ]
}
```

Selectors and matched files sort independently by their UTF-8 bytes. Added,
removed, renamed, or changed matches change the digest. Shared cache reuse is
per matched file and requires exact size and nanosecond modification and change
times. Unmatched descendants remain outside the bytewise identity.

### Origin Boundaries

`origin` is a required Boolean that says whether Provenance traversal stops at
the declared, fingerprinted artifact. It is independent of storage location.
An origin may be inside or outside the entry, and an artifact inside the entry
may be either an origin or generated material.

An item with `origin: true` is a terminal identified input. Validation does not
claim how that artifact or commit snapshot came into existence. An item with
`origin: false` must trace to one unique earlier producer and then through that
producer's direct inputs. More than one earlier producer is ambiguous. An
origin boundary that hides a confirmed `pyrun` producer is invalid. An origin
does not connect an otherwise unreached artifact or suppress a Hygiene finding.

### Command Tokens And Roles

Every command in every `bash`, `console`, `sh`, `shell`, or `zsh` fence must be
a direct `pyrun` invocation or part of the closed finite-loop grammar below.
All such fences are checked for conformance. Only fences in an entry section
containing both `Steps:` and `Results:` labels contribute invocations to the
Provenance graph.

The shell grammar accepts multiple direct `pyrun` invocations; literal scalar
and array bindings used by loops; finite literal `for` loops with arbitrary
nesting; and loop-local literal `case` branches that select scalar or array
bindings. Variables may be expanded only from those statically established
bindings. If any part of a fence is outside this grammar, the entire fence
fails closed and contributes no invocation or relationship. Comments are
inert. The parser never executes shell or mines unsupported bodies for likely
commands. Historical non-`pyrun` commands may be described in prose but do not
participate in Provenance.

An exact file or repository-locator token is the whole argument `<name>`. A
Git repository commit token is the whole argument `<name:commit>`. A directory
member token is `<name>/` plus one non-empty normalized POSIX member path with
no absolute prefix, empty segment, `.`, `..`, reverse solidus, URI scheme,
symlink, glob, shell, or template expansion. Member syntax requires a directory
item; `:commit` requires a Git repository item and cannot have a member suffix.

Every command that consumes a Git repository uses both `<name>` and
`<name:commit>`. `pyrun` resolves them to the locator path and exact full commit
respectively, verifies the commit before execution, and records one direct
input fingerprint. Static discovery resolves the same pair to one material
relationship whose identity is the commit snapshot rather than the locator.
Either projection without the other fails closed.

Maintenance note: Changes to repository input registration or token projection
must be reflected in
[Material Input Instructions](../skills/research-logging/references/file-data-index.md)
and
[Recorded Command Instructions](../skills/research-logging/references/file-entry-commands.md).

`pyrun` resolves tokens before execution. Script parameters may retain clean
internal names through `dest=`; compatibility aliases are not required.

Named tokens establish input direction. Every other input proven by an
input-bearing option or finite input collection must use its matching token. A
raw value matching an item is a missing token; a raw proven input without an
item is undeclared.

A path-like argument with no role is not silently dropped. A candidate is
path-like when its complete static value resolves to an existing filesystem
target, is an absolute path or URI, begins with `./` or `../`, contains a named
token, or ends with a registered retained material suffix. A slash alone is not
path evidence. A candidate must acquire input or output direction through a
natural option name or runner role declaration. A dynamic material candidate
that cannot resolve to one bounded value also fails. Other scalar arguments
create no edge.

The suffix registry is `.csv`, `.tsv`, `.json`, `.jsonl`, `.npz`,
`.npy`, `.h5`, `.hdf5`, `.mat`, `.pkl`, `.pickle`, `.fits`, `.fit`, `.parquet`,
`.feather`, `.txt`, `.log`, `.yaml`, `.yml`, `.toml`, `.ini`, `.png`, `.jpg`,
`.jpeg`, `.svg`, and `.pdf`, compared case-sensitively. A suffix identifies a
candidate only; it never assigns direction.

`pyrun` accepts runner-visible role declarations before its required `--`
separator. `--other-inputs <selectors>` and
`--other-outputs <selectors>` each accept one comma-separated list of script
option names without leading hyphens or one-based positional selectors written
as `@N`. Each declaration may occur once. Lists reject empty or whitespace
items, duplicate selectors, selectors without a matching valued argument, and
selectors declared in both directions. A selector applies to every occurrence
of its option. An explicit declaration overrides automatic role inference from
the option name.

The runner and static command discovery use the same parsed declarations.
Input kind comes from `data.json`: a whole-directory token is a directory, a
file or exact directory-member token is a file, and a paired locator and commit
projection is one Git repository input. Output kind comes from the stable
target after successful execution. Captures remain file-only. The exact entry
`data` and `images` roots remain invalid material targets.

An output outside the owning entry must be authored as `<project>/...`. Its
lexical and resolved target must be a non-root descendant of the current Git
project. Raw absolute paths, parent traversal, malformed project paths, and
symlink escapes are invalid. Entry-local `data/` and `images/` outputs retain
their normalized entry-relative form. Static discovery and `pyrun` resolve the
same canonical target and portable key.

Role declarations are classification metadata. They are excluded from the
persisted `parameters` vector and do not change output support when reordered
without changing the resolved relationships. Capture options retain their
existing execution-signature behavior. A successful command publishes no
record for a declared output that is absent.

`pyrun` also accepts repeatable `--env NAME=value` runner options before the
required `--` separator. It normalizes them by name into the persisted
execution signature and child environment. Duplicate names, malformed names,
and runner-managed names are invalid. Each run receives fresh temporary
directories for `MPLCONFIGDIR`, `XDG_CACHE_HOME`, and its private Python code
observer state. The observer's private environment is not part of the
execution signature. Script filenames receive no command-argument provenance
classification.

The exact entry-local `data` and `images` directories are shared artifact-tree
roots, not material artifacts or collections. An unclassified argument that
resolves to either exact root creates no candidate. A role or `data.json`
declaration targeting either exact root fails `material.root.invalid` or
`data.declaration.invalid`, respectively. Descendant files and exclusively
owned descendant directories retain ordinary material behavior.

### `pyrun` Output-Support Records

`pyrun-outputs.json` is an entry-root mapping keyed by exact output path. It is
owned and maintained only by `pyrun`; validators read it and agents do not edit
it. One invocation that produces several outputs writes one record per output,
deliberately duplicating the invocation support so later command splitting,
merging, deletion, or output renaming can be reconciled by output identity.

Before running a research command, `pyrun` holds the stable entry-operation
lock and strictly loads any existing output-support file. If a regular file is
malformed, `pyrun` moves it without rewriting to the first unused adjacent
`pyrun-outputs.json.bak`, `pyrun-outputs.json.2.bak`, and so on; writes one
canonical empty current file; reports `pyrun.outputs.quarantined` with both
paths and `repair_required:true`; and exits before command execution. It never
overwrites a backup or infers a merge. These recognized backups are generated
recovery state and are excluded from the artifact universe. A symlink or
non-file at the current path is invalid and is not quarantined.

```json
{
  "schema": "research-log-pyrun-outputs/v1",
  "outputs": {
    "data/results.csv": {
      "confirmed": true,
      "fingerprint": {
        "algorithm": "sha256",
        "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      },
      "script": {
        "path": "scripts/run_study.py",
        "fingerprint": {
          "algorithm": "sha256",
          "digest": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        }
      },
      "parameters": ["--input-data", "<development_catalog>", "--output-csv", "data/results.csv"],
      "inputs": {
        "development_catalog": {
          "algorithm": "sha256",
          "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        }
      },
      "code": {
        "scripts/study_helpers.py": {
          "algorithm": "sha256",
          "digest": "123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0"
        },
        "<log>/shared/plotting.py": {
          "algorithm": "sha256",
          "digest": "23456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef01"
        }
      }
    }
  }
}
```

The top-level keys are exactly `schema` and `outputs`. Output keys are unique
canonical identities: normalized entry-relative paths beneath `data/` or
`images/`, or normalized `<project>/...` paths for outputs elsewhere beneath
the current Git project. Two spellings that resolve to the same entry-local
target have the entry-relative key. Each record has
exactly `confirmed`, `fingerprint`, `script`, `parameters`, `inputs`, and
`code`.
`script.path` is the script argument passed to `pyrun`, not an inferred command
or command ID. `parameters` is the exact ordered argument tail after the script,
except that capture options and normalized `--env` options, their values, and
the separating `--` precede the script arguments so the record matches the
validator's normalized `pyrun` invocation signature. Runner role declarations
are excluded from this vector. Their separator is also excluded when neither a
capture nor environment option is present. `inputs` maps
every directly consumed `data.json` name to
the fingerprint used by the run. Fingerprints use the same closed local
fingerprint forms as `data.json`. `code` maps at most 256 unique canonical
Python source identities to exact `sha256` file fingerprints. A path beneath
the command's entry is entry-relative. Every other eligible path is relative
to the maintained log and begins `<log>/`. A logical path through a symlink
beneath the log remains in that form rather than expanding to the symlink's
physical target. Paths are normalized, bounded, and end in `.py`.

The directly executed `script.path` is not duplicated in `code`. Every other
eligible regular Python source file actually loaded in the observed process
tree is included as a whole-file dependency. This includes direct, transitive,
conditional, and dynamic imports that execute, loaded package files, and a
Python descendant's entry-point file. Repeated imports and aliases are
deduplicated by resolved file identity while retaining one canonical logical
path. Every output from one invocation receives the same complete `code`
mapping. An empty mapping records that the observed execution loaded no
eligible helper.

Validation resolves every logical code path to an
existing regular file and rejects two keys that resolve to the same file. A
reached, confirmed output compares every current code fingerprint with the
recorded mapping. A missing or non-file target, or a duplicate resolved
identity, is `provenance.output.code_invalid`; a fingerprint difference is a
`code` field in `provenance.output.signature_mismatch`. Code observations use
the shared fingerprint service and one resolved file observation is reused
across output records and logical aliases. Execution-linked stability checks
re-observe the same files before validation completes.

A current record associates with a reconstructed invocation only when its
output identity, script path, ordered parameters, and direct input names
match. Confirmation and output, script, input, and code fingerprints are
currentness rather than association fields. Associated records for one
invocation must agree on their complete `code` mappings. Structurally valid
associated support adds one `code` input edge from each recorded file to the
invocation when that invocation enters the evidence-rooted graph. Thus an
associated unconfirmed record or a record with stale fingerprints still
connects its helpers for Hygiene while Provenance fails independently.
Malformed, unavailable, inconsistent, or unmatched support adds no code edge
and suppresses no helper orphan.

#### Python Code Observation

`pyrun` installs a temporary import observer before user code starts. It wraps
ordinary filesystem source loaders without changing their search and preserves
any existing downstream `sitecustomize`. The observer records only regular
`.py` files whose logical loaded path lies beneath the current maintained log.
Entry-owned, log-shared, and sibling-entry code is eligible. Code in another
log, project code outside the current log, installed packages, standard-library
files, environments, caches, generated bytecode, and non-Python child code is
outside this observation.

The private observer context is inherited by ordinary Python descendants,
including interpreters reached through a shell, `subprocess`, multiprocessing
spawn, and multiprocessing fork. A fork resets process-local collection state.
Each interpreter deduplicates paths in memory and publishes one process-specific
temporary trace at normal exit. A forked interpreter may refresh its trace when
it first sees a new eligible path because it may not run normal exit handlers.
The root interpreter must publish a complete trace. Completed descendant traces
are consolidated after the command exits; an unfinished detached descendant is
outside the completed execution observation. A descendant that uses isolated
startup or replaces the inherited environment likewise receives no unsupported
completeness claim.

Traces retain logical and resolved paths plus import-time filesystem identity;
they do not hash source content. After successful execution, `pyrun` requires
each logical path, symlink resolution, and file identity to remain unchanged,
then observes each unique resolved helper once through the project fingerprint
cache. It does not hash a helper again for each child or output. A changed,
missing, malformed, excessive, or unavailable observation prevents all output
support publication. Temporary observer files are removed with the run and are
not research-log state.

One interpreter trace contains at most 257 paths so the primary script can be
excluded before enforcing the 256-item `code` bound. One run accepts at most
4,096 process traces and 1 MiB per trace. The observer performs no function
tracing, process polling, open-file polling, post-execution source-tree scan,
or ordinary static import discovery.

`pyrun` records its current working entry root, resolves the command through
that entry's `data.json`, and publishes output records only after the process
succeeds, the script and every direct input still have their pre-execution
identities, code observation completes, and every output can be observed
completely. Publication replaces only records for outputs produced by that
invocation and preserves
records for other output keys. It is atomic under an entry-specific lock.
Failed execution, capture, observation, or publication confirms no record.

Ordinary output parameters use the existing mechanical input/output role
rules. Retained process streams use one of these forms:

```bash
./pyrun --capture-stdout data/run.log -- \
  scripts/run_study.py \
  --parameter value

./pyrun --capture-stderr data/error.log -- \
  scripts/run_study.py \
  --parameter value

./pyrun --capture-stdout-stderr data/run.log -- \
  scripts/run_study.py \
  --parameter value
```

`--capture-stdout` and `--capture-stderr` may be combined with distinct
targets. `--capture-stdout-stderr` is mutually exclusive with both. With one
runner option, that option and `--` stay on the `./pyrun` line. With several,
put `./pyrun`, each option-value pair, and `--` on separate lines. Line wrapping
does not change parsing. Captured bytes are mirrored to the corresponding
terminal stream. Raw shell redirection and `tee` are outside the
recorded-command grammar.

An existing record may contain `confirmed: false`. Such a record preserves the
temporary distinction between retained fingerprints and a directly observed
execution, but does not validate Provenance. The next successful matching
`pyrun` execution replaces it with confirmed current observations. Historical
workflows with no record participate in structural graph and Hygiene
evaluation, but a reached generated output cannot pass Provenance until a
confirmed record exists.

### Producer And Lineage Semantics

Provenance means that the validator can identify the artifact behind every
presented evidence item; classify that artifact as a declared origin or identify
its unique producer; follow every generated producer input backward to origins;
and confirm that every reached output was produced with the current bytes of
its script and direct inputs under a command signature still present in the
entry. Failure of any link fails the starting artifact's Provenance.

Evidence and direct presentations begin graph traversal. The validator reuses
the already constructed command/material graph; it does not build a second
lineage model from output records. For each reached generated artifact:

- exactly one earlier command producer is required;
- its exact output-keyed `pyrun` record must exist and be confirmed;
- the current output fingerprint must equal the record;
- the current script path and fingerprint must equal the record;
- the exact ordered parameters found by static command expansion must equal the
  record;
- the exact direct input names and their current declared fingerprints must
  equal the record; and
- every direct input with `origin: false` recursively satisfies these rules,
  while `origin: true` stops that branch.

No earlier producer requires `origin: true`; one earlier producer requires
`origin: false`; several earlier producers fail as ambiguous; and a later
producer never supplies an earlier consumer. A selected producer with no
material inputs terminates successfully at its confirmed artifact-producer
relationship. There is no command-level root, command type, filename-derived
root, or `provenance.root.missing` check.

Validation finds current command signatures through bounded static expansion of
the entry and all its subentry Markdown files. `pyrun` does not parse Markdown
or attempt to identify the command that called it. The entry-root working
directory identifies record ownership; subentries intentionally share that
entry-level command and output-support surface.

The resulting claim is bounded: the retained evidence artifact is connected to
declared origin artifacts by the mechanically visible command graph, and every
reached generated output matches one confirmed execution observation for the
current script bytes, declared input fingerprints, exact parameters, and output
bytes. It does not establish causation, complete dependency capture,
scientific validity, reproducibility, or the truth of undeclared runtime state.
Reproduction is a separate workflow and is not performed or evaluated here.

Validation never imports another log's generated validation state. A cross-log
input is declared locally and follows the same origin and producer rules in the
consuming log.

### Directory Resources

A local directory is either a byte-complete bounded collection with a
`directory-sha256-v1` fingerprint or one managed logical aggregate with an
`identity-files-sha256-v1` or `identity-patterns-sha256-v1` fingerprint.

- `<name>` under `input-directory` consumes every observed regular-file
  descendant and gives each member an input edge.
- `<name>/member` under an exact input role consumes only that member. The
  member connects to the aggregate for fingerprint and origin-boundary
  evaluation; siblings receive no command-input or evidence-source edge.
- Both forms count as use of the data item.
- An origin directory is valid only when no confirmed `pyrun` record identifies
  its root or any member as generated. Its boundary reaches a consumed member
  through the explicit membership edge, not a path-prefix rule.
- A generated directory must match one exact earlier `output-directory`.
  Overlapping roots, separate member producers, or a second directory producer
  fail exclusivity.
- One exclusive `pyrun` output-directory and one exact directory-level
  `pyrun-outputs.json` record with the same script, parameters, and material
  input identities form one atomic artifact. The record may remain unconfirmed,
  and its output fingerprint may be stale; confirmation and current bytes are
  separate Provenance checks when the artifact is reached. Every regular-file
  descendant observed by the record belongs to the artifact and its recursive
  fingerprint. Reaching the root or one exact member connects the complete
  bundle for ownership and Hygiene without claiming that sibling members were
  consumed or presented.
- Atomic output ownership does not require a generated directory declaration
  in `data.json`. Register the directory only when a later command or evidence
  presentation consumes the bundle or one exact member.
- Output-directory ownership is invocation-exclusive. Repeated exact outputs
  may share a parent without asserting directory ownership.
- Command relationship bounds count one authored whole-directory role as one
  relationship slot. Expanded directory members remain bounded by the
  collection and graph limits; they do not consume scalar relationship slots.
- Fingerprinting always covers complete membership, even for selected-member
  use, when the algorithm is `directory-sha256-v1`.
- A whole managed-directory token creates one aggregate input relationship and
  does not pretend that its identity files or pattern matches are the only
  consumed descendants.
- An exact managed-directory member token continues to resolve that member,
  while the resource's declared identity files or pattern matches establish the
  aggregate input identity and origin boundary.
- Identity files and pattern matches do not expand member relationships and
  need not be command inputs themselves.

No manifest automatically expands member relationships. A manifest may be a
named file input or one file selected by a managed-directory identity
fingerprint.

### Retention Registry

An optional entry-root `retention.json` has exactly:

```json
{
  "schema": "research-log-retention/v1",
  "records": []
}
```

Both keys are required and unknown keys fail. `records` is non-empty and sorts
canonically by `id`. One file is at most 8 MiB and contains at most 1,000
records. Each record uses exactly one of these closed target forms, without
`kind`:

```json
{
  "id": "optimizer-debug-traces",
  "paths": ["data/debug-trace.json", "data/optimizer-state.npz"],
  "reason": "Diagnostic outputs retained for later investigation."
}
```

```json
{
  "id": "intermediate-wavefronts",
  "directory": "data/intermediate-wavefronts",
  "membership": "all-descendants",
  "reason": "Intermediate states retained for later comparison."
}
```

An ID uses the evidence-ID grammar and is at most 96 ASCII characters. A
`paths` array contains 1–10,000 unique normalized entry-relative paths to
existing regular non-symlink files. A directory record names one existing,
non-empty, non-symlink entry-relative directory, sets `membership` to exactly
`all-descendants`, and may contain at most 100,000 bounded descendants. An
optional `reason` is at most 2,048 UTF-8 bytes. Targets must not overlap within
or across records. IDs are unique within `retention.json` and do not share an
evidence ID namespace. A connected target makes retention redundant and
invalid. Reaching any member of an atomic generated output directory connects
the bundle's complete membership for this redundancy check.

### Evidence-rooted Hygiene

The Hygiene universe remains bounded regular files under each entry root,
including first-class `data` and `images`, and excluding entry Markdown,
`evidence.json`, `data.json`, `retention.json`, `pyrun`,
`pyrun-outputs.json`, validator output, research-log temporary paths, and
runtime-cache descendants.

A `<project>/...` output outside an entry participates in Provenance and may be
registered as a generated input, but its location alone does not add it to the
entry Hygiene or Retention universe. Validation does not scan project-wide
outputs for orphans or retention.

Connectivity starts only at evidence sources and direct presentations and
traces backward through unique producers and declared inputs. A command outside
this closure connects none of its scripts, inputs, outputs, or directory
members. Its atomic output directory remains one unreached artifact rather than
one artifact per descendant. An origin boundary terminates a reached branch but
never connects an unreached artifact or suppresses a Hygiene finding.

Each eligible standalone file or atomic generated output directory is
connected, declared-retained, or orphaned. An exact bundle-member edge remains
member-specific in the evidence and command graph, but it connects the complete
bundle membership for ownership and orphan classification.
`validation/results.json` records authoritative artifact-level orphan
checks. An unused data item produces one `orphan.input.unused` check; unused
declarations are reported separately and do not inflate artifact counts.

Complete-graph output reconciliation produces one Provenance condition and one
Hygiene condition. A current graph output whose file is absent is
`provenance.output.missing`: it breaks Provenance and is not a Hygiene finding.
A record in `pyrun-outputs.json` whose output key is absent from the complete
current graph is an unmatched output. If the file also exists, it is reported
only as `hygiene.output.unmatched`, not again as an orphan. An unmatched
directory-output record suppresses descendant orphan findings and produces one
finding at its root. An existing file outside the current graph with no output
record is an ordinary orphan.

| Current graph output | Current file | Output record | Result |
| --- | --- | --- | --- |
| yes | no | either | Provenance failure: missing output |
| no | either | yes | Hygiene: unmatched output |
| no | yes | no | Hygiene: orphan output |

An output present in the complete graph but outside the evidence-rooted closure
is an orphan unless retained. An atomic output directory produces one root
orphan rather than descendant findings. Its record is not unmatched because
the graph still identifies its current producer.

`validation.md` reports one Hygiene finding count that combines orphan
artifacts, unmatched outputs, and unused input declarations. Their distinct
machine-readable checks remain in `validation/results.json` for repair.
Machine-readable orphan metadata may group maximal all-orphan directories
below, but never equal to, the owning entry root. Starting with each child
directory, collapse the highest directory whose every eligible file is
orphaned; otherwise recurse in normalized lexical order. Root-level files
remain individual findings. Mixed directories retain individual files or
smaller groups. Atomic output-directory collapse occurs before this ordinary
grouping and always uses the declared output root. No artifact appears twice.

A group identity is `orphan-group:` plus lowercase SHA-256 of canonical JSON
for `[maintained-log identity, entry material owner, normalized entry-relative
directory]`. Grouping creates no graph edge, retention, or collection.

### Provenance Truth Table

| Data item and token | Earlier producers | Origin | Producer support | Result |
| --- | --- | --- | --- | --- |
| Missing item or raw input | any | any | any | Fail undeclared or missing-token validation before lineage. |
| Declared and used | 0 | yes | n/a | Terminal origin after current fingerprint validation. |
| Declared and used | 0 | no | n/a | Fail `lineage.missing`. |
| Declared and used | 1 | no | missing, unconfirmed, or unequal | Fail Provenance. |
| Declared and used | 1 | no | exact confirmed match | Trace to the unique producer's inputs. |
| Declared and used | 1 | yes | confirmed producer | Fail `data.origin.invalid`. |
| Declared and used | more than 1 | either | n/a | Fail `lineage.ambiguous`. |
| Declared but unused | any | either | n/a | Report `orphan.input.unused`; create no graph edge. |
| Reached producer | n/a | n/a | exact confirmed match, no inputs | Terminate at the artifact-producer relationship. |
| Reached producer | n/a | n/a | unresolved candidate | Fail `material.candidate.unresolved`. |
| Reached producer | n/a | n/a | exact confirmed match, one or more inputs | Follow every declared input under the rows above. |

### Directory Truth Table

| Use | Producer state | Boundary | Result |
| --- | --- | --- | --- |
| Whole `input-directory` | no root/member producer | origin | Consume all fingerprinted members through the aggregate boundary. |
| Exact member | no root/member producer | origin | Consume only that member; siblings stay disconnected. |
| Whole directory | one exact earlier `output-directory` | absent | Trace all members to that producer. |
| Exact member | one exact earlier `output-directory` | absent | Trace only that member and connect the atomic root for Hygiene; do not claim sibling consumption. |
| Workflow outside evidence closure | exact exclusive `output-directory` with matching directory support | absent | Treat the output-only directory as one atomic artifact without adding it to `data.json`. |
| Any directory | overlapping or separate member producers | either | Fail `directory.producer.conflict`. |
| Generated directory | no exact earlier directory producer | absent | Fail `lineage.missing`. |
| Origin directory | confirmed root/member producer | present | Fail `directory.origin.conflict`. |
| Any directory | membership/content differs from digest | either | Fail `data.fingerprint.mismatch`. |
| Workflow outside evidence closure | atomic output directory | absent | Report one root-level orphan unless the complete bundle is retained. |
| Workflow outside evidence closure | other directory | any | Members remain orphan-eligible unless retained. |

### Diagnostics

| Code | Scope | Condition |
| --- | --- | --- |
| `data.file.location_invalid` | conformance | `data.json` is outside one entry root or a parent/log-level surface exists. |
| `data.declaration.invalid` | conformance | A data file, item, field, fingerprint, boundary, or bound violates the closed contract. |
| `data.name.duplicate` | conformance | One entry repeats a name. |
| `data.target.duplicate` | conformance | One entry repeats a canonical target through any alias. |
| `data.declaration.conflict` | conformance | Entries disagree on one target's kind, fingerprint, or boundary. |
| `data.input.undeclared` | provenance | A proven input has no item, including an unknown token. |
| `data.input.token_missing` | conformance | A proven input uses a raw location instead of its item token. |
| `data.git.projection_missing` | conformance | A repository-consuming command omits its locator or commit projection. |
| `material.candidate.unresolved` | conformance | A path-like or dynamic material candidate has no proven role. |
| `material.root.invalid` | conformance | A command role targets the exact shared entry `data` or `images` artifact root. |
| `data.origin.invalid` | provenance | An origin boundary hides a confirmed `pyrun` producer. |
| `data.target.missing` | provenance | A local input or selected member is absent. |
| `data.fingerprint.mismatch` | provenance | Observed local content differs from its fingerprint. |
| `directory.membership.invalid` | provenance | Membership is unsafe, aliased, unsupported, or over-bound. |
| `directory.producer.conflict` | provenance | A generated directory lacks one exclusive exact earlier producer. |
| `directory.origin.conflict` | provenance | An origin directory root or member has a confirmed `pyrun` producer. |
| `pyrun.outputs.invalid` | provenance | `pyrun-outputs.json` or one record violates its closed schema. |
| `pyrun.outputs.unavailable` | provenance | Current output-support state cannot be read or safely updated. |
| `pyrun.output.identity_invalid` | provenance | A `pyrun` output cannot map to one permitted entry-relative or `<project>/...` record key. |
| `provenance.output.unrecorded` | provenance | A reached generated output has no output support record. |
| `provenance.output.unconfirmed` | provenance | A reached generated output has only an unconfirmed baseline. |
| `provenance.output.signature_mismatch` | provenance | Current output, script, parameters, direct inputs, or recorded code differ from the confirmed record. |
| `provenance.output.code_invalid` | provenance | A recorded code path is unavailable, is not a regular file, or duplicates another resolved code identity. |
| `provenance.output.signature_unsupported` | provenance | A reached producer input cannot be represented in the exact record signature. |
| `provenance.output.missing` | provenance | The current graph declares an output whose artifact is absent. |
| `provenance.observation.unavailable` | provenance | Execution-linked bytes changed during validation or could not be re-observed. |
| `retention.file.location_invalid` | conformance | `retention.json` is outside one entry root. |
| `retention.declaration.invalid` | conformance | A retention file or record violates shape, path, overlap, eligibility, or redundancy. |
| `retention.target.missing` | conformance | A retention target is absent. |
| `orphan.material.unused` | orphan | One retained artifact lies outside the evidence closure and retention. |
| `orphan.input.unused` | orphan | One data item is not consumed by any command. |
| `hygiene.output.unmatched` | orphan | An output support record has no output in the complete current graph. |

### Examples

A generated intermediate explicitly continues Provenance traversal:

```json
{
  "name": "normalized_samples",
  "kind": "file",
  "location": "data/normalized-samples.npz",
  "fingerprint": {
    "algorithm": "sha256",
    "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "origin": false
}
```

A local origin explicitly stops traversal at its current byte identity:

```json
{
  "name": "reference_grid",
  "kind": "directory",
  "location": "/Volumes/Data/reference-grid/v4",
  "fingerprint": {
    "algorithm": "directory-sha256-v1",
    "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "origin": true
}
```

Location does not determine origin status. Both examples may be inside or
outside the entry so long as the resource is locally accessible and satisfies
the path-safety contract.

## Mechanical Validation Evaluation And Outcomes

### Evaluation Order

Standard validation evaluates one target maintained log in this order:

1. parse document, evidence, input-registry, output-support, and retention
   structure, and scan supported command surfaces for relationship candidates;
2. establish presentation, evidence-record, invocation, and material
   identities;
3. resolve sources, named inputs, and project-local script identities;
4. evaluate locators, expectations, transformations, and presentation
   comparison;
5. establish producers for evidence starting points, match each reached output
   to confirmed current execution support, then follow mechanically proven
   upstream inputs, explicit origins, and required directory membership within
   that closure;
6. re-observe execution-linked script, input, output, and output-support bytes;
7. reconcile the complete graph with current files and output-support records,
   reporting absent graph-declared outputs as Provenance failures and records
   absent from the graph as Hygiene findings; and
8. compose evidence and Provenance outcomes, then classify remaining material
   as connected, declared-retained, or orphaned for Hygiene.

A malformed owned entry or entry-local command surface produces an entry-scoped
conformance finding and does not prevent unaffected entries from continuing
through evidence, provenance, and orphan evaluation. Validation stops the
whole log only when the maintained summary or another log-wide structure
cannot be read or interpreted safely.

A failed prerequisite is not restated as several speculative mismatches. A
dependent result is `not_applicable` when a stable prerequisite failed and
`unavailable` when its prerequisite is temporarily unavailable. The dependency
note names the governing result.

When an invocation has unresolved material candidates, a reached candidate
artifact, candidate-directory descendant, or input-use classification
dependent on that invocation is `not_applicable` with the command-conformance
check as its dependency. It is not independently reported as a missing
producer, missing lineage edge, orphan artifact, or unused input declaration.
Unrelated commands and entry material remain independently evaluable.

### Result Scopes

| Condition | Owning scope | Aggregate effect |
| --- | --- | --- |
| Malformed JSON, Markdown, path, supported source structure, or recorded command surface | Conformance | Fails conformance; dependent evidence or provenance is not applicable. |
| Missing or conflicting evidence declaration or exact presentation mismatch | Evidence | Fails evidence. |
| Missing, ambiguous, conflicting, stale, unconfirmed, or incomplete producer, lineage, execution-support, input, origin, or directory relationship | Provenance | Fails Provenance without changing the evidence-value result. |
| Temporary access failure or material changing during observation | Owning check as unavailable | Makes the aggregate incomplete. |
| Residual orphaned material, unused input declaration, or unmatched output record | Hygiene (machine scope `orphan`) | Reports findings without changing evidence or Provenance status. |
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

1. command parser, input-token grammar, runner-option grammar, and option-name
   role-grammar versions;
2. canonical invocation identity and shell structure;
3. resolved executable or local-script identities;
4. exact command path, runner environment, capture, option-role, and
   named-token projections;
5. canonical material identity and direction proof;
6. competing producer identities for the same material;
7. exact upstream input-output identity matches;
8. input declaration, fingerprint, and origin-boundary projections;
9. exact output-support confirmation, output fingerprint, script path and
   fingerprint, ordered parameters, direct input fingerprint mapping, and
   observed code mapping when present; and
10. required directory mechanism, membership, and associated code-edge
    projections.

One combined evidence-and-provenance outcome additionally depends on its
evidence-record, source, locator, transformation, presentation, and association
projections. Summary provenance depends on the referenced entry record's
successful projection and, for a table, the declared cell coordinate.

Unrelated commands outside the evidence closure, files, evidence records,
entry prose, orphan findings, other logs, and Git state do not change an
outcome. Whole-file hashes may trigger parsing, but unchanged-result comparison
uses the narrower projections.

### Public Management And Validation Operations

The public entrypoint is the extensionless `scripts/log` resolved from the
active research-logging skill package. Direct path-qualified invocation is
canonical. `pyrun` remains the separate recorded execution wrapper.

The validation and discovery operations are:

```text
<skill>/scripts/log discover --root PROJECT

<skill>/scripts/log validate --path LOG
  [--date YYYY-MM-DD] [--recompute] [--recompute-validation]
  [--recompute-fingerprints] [--dry-run]

<skill>/scripts/log validate --root PROJECT
  [--date YYYY-MM-DD] [--recompute] [--recompute-validation]
  [--recompute-fingerprints] [--dry-run]

<skill>/scripts/log findings list --path LOG
  [--entry ENTRY] [--subject SUBJECT]

<skill>/scripts/log findings show --path LOG --id CHECK_ID
```

`discover --root` performs bounded, read-only maintained-summary discovery
beneath one regular non-symlink project root. It recognizes a summary by its
H1-adjacent stable `Validation: [latest completed report](<log>/validation.md)`
navigation line and regular sibling log root. It does not include or exclude a
candidate based on the candidate's basename. It emits the discovery-result
schema listed in `Current Versions`, with the resolved `root` and a sorted
`summaries` array.

`--path` names the logical `LOG` base whose `LOG.md` summary and `LOG/` root are
both present. It does not accept either physical path as an alternative
spelling. Omission is allowed only when the working directory resolves exactly
one maintained log. `validate --root` validates every summary returned by the
same bounded discovery contract and emits the batch-result schema. It is the
only all-log validation spelling; an omitted `--path` never means all logs.
The batch result contains a `results` array for structured per-log results and a
`failures` array whose items contain the affected summary, stable error code,
and bounded message. Its `report` field is a complete ready-to-present Markdown
comparison containing every discovered log. Completed rows use the shared
human area projection; dry-run, incomplete, unsupported, and operationally
failed rows say that no report was published, and exceptional rows receive a
concise explanation below the table. One log's operational failure does not
skip later logs or discard earlier results; any batch failure makes the command
exit nonzero.
`--date` defaults to the local calendar date and, when present, must be one
exact ISO date.
The nearest enclosing non-symlink `.git` file or directory defines the project
root for project-relative identities and the shared fingerprint cache. Missing
Git worktree metadata is an operational error; directory names do not determine
project ownership.
`--recompute-validation` bypasses per-log check-comparison and
evidence-selection reuse while retaining eligible project fingerprint reuse.
`--recompute-fingerprints` bypasses project-level fingerprint reuse while
retaining eligible per-log validation-cache reuse. The flags may be combined.
`--recompute` is shorthand for both and preserves the complete
cache-independent behavior: the validator computes every check and rereads or
rehashes every source and artifact needed by those checks. None of these flags
changes validation scope, rules, or the published result format. A writable
run repopulates each bypassed cache as observations and completed validation
state become available. These are the only public standard-validation inputs;
there is no mode, decisions, review, semantic, or reproduction input.

There is no alternate validation launcher or `--summary` compatibility
spelling. All maintained callers use `scripts/log`.

The scaffolding operations are:

```text
<skill>/scripts/log init --path LOG --title TITLE [--dry-run]
<skill>/scripts/log add --path LOG --date YYYY-MM-DD --title TITLE --slug SLUG
  [--dry-run]
```

`log init` requires an explicit logical path beneath one Git project and uses a
project-scoped creation lock keyed by that intended path. It creates only the
canonical empty summary and matching `LOG/entries/`, publishing the summary
last. An existing or partial target is a conflict rather than a retry.

`log add` holds the log lock and then the newly allocated stable entry lock. It
requires consistent IDs and document links across the summary inventory, entry
directories, and entry documents; allocates one above the highest observed ID
without filling gaps; creates the minimal canonical entry document and a
relative symlink to the active package's verified `pyrun`; and appends only the
new summary item. The summary commits last. Ordinary publication failures roll
back, while recognizable interruption residue fails closed for explicit
Repair. Neither operation changes summary interpretation, follow-ups, optional
support material, or generated validation state.

The input-registry operations are:

```text
<skill>/scripts/log data add-origin --path LOG --entry ENTRY NAME TARGET
  [--identity SELECTOR]... [--commit COMMIT] [--dry-run]
<skill>/scripts/log data add-generated --path LOG --entry ENTRY NAME TARGET
  [--pending-confirmation] [--dry-run]
<skill>/scripts/log data update --path LOG --entry ENTRY NAME
  [--target TARGET] [--origin | --generated]
  [--identity SELECTOR]... [--byte-complete] [--commit COMMIT] [--dry-run]
<skill>/scripts/log data rename --path LOG --entry ENTRY OLD-NAME NEW-NAME
  [--dry-run]
<skill>/scripts/log data refresh --path LOG --entry ENTRY NAME [--dry-run]
<skill>/scripts/log data remove --path LOG --entry ENTRY NAME [--dry-run]
<skill>/scripts/log data list --path LOG --entry ENTRY
```

These actions infer kind and canonical location and use the production
fingerprint and data-file contracts. `add-origin` rejects a confirmed producer
in the same log. Its mutually exclusive `--commit` form requires a full
lowercase commit hash and makes `TARGET` a Git repository locator.
`add-generated` requires one current confirmed same-log
producer whose recorded output and current target bytes agree. Its
`--pending-confirmation` form is reserved for explicit Repair and migration:
it requires one structurally valid, unambiguous current same-log producer and
the current target, but permits absent or explicitly unconfirmed output
support so reproduction can establish confirmation later. It does not change
`pyrun-outputs.json`, add persisted pending state, or relax missing and
ambiguous producer checks. When a support record is already confirmed, the
producer's own current output and signature checks still apply, while recursive
lineage may remain pending reproduction. `update` applies
only explicit changes and rechecks the resulting boundary; changing a Git
repository target preserves and verifies its commit unless `--commit` replaces
it. Git repository inputs cannot become generated or use directory identity
options. Managed identity is available only for origin directories. `refresh`
preserves the target,
classification, and identity mode. `remove` requires prior removal of command
and evidence use and removes an empty registry. `rename` requires prior command
token edits, atomically updates same-entry evidence source tokens, and reports
producer commands whose support must be replaced by successful reruns. Each
mutation holds the shared log lock and the selected entry lock and leaves
generated validation state unchanged. A multi-file rename uses entry-keyed
recognized transaction residue until publication or complete rollback, and
cross-entry declaration disagreement remains a validation finding.

Entry-scoped `log evidence` and `log retention` actions read and validate the
complete current registry, build candidate state through the production
decoder, and atomically publish canonical state while holding the stable entry
lock. Their add, update, rename, remove, and list actions return only bounded
semantic results. Mutations support content-write-free `--dry-run`; exact add
or update results are unchanged, conflicting state fails, and an absent
removal is reported distinctly. Evidence actions require the agent-authored marker and
summary-reference change first and never edit Markdown. Retention actions
accept either one nonempty directory or one or more regular files and never
expose registry schemas through ordinary results. Every authoring action leaves
generated validation state unchanged.

`log evidence add` and `log evidence update` accept either the common
single-source arguments or `--definition PATH`, never both. A definition is a
regular non-symlink UTF-8 JSON file no larger than 8 MiB beneath
`/private/tmp`. Its object contains exactly `sources` and `transformation`;
the action and `--id` supply the remaining record fields through the unique
agent-authored presentation marker. The CLI passes those two values through
the production evidence, locator, transformation, and presentation contracts,
then uses the same candidate-publication path as common mode. It reads but
never modifies, retains, copies, or removes the definition. `--dry-run`
performs the complete source observation, evaluation, presentation comparison,
candidate build, and mutation preflight without writing the registry.

When the unique marker belongs to an artifact link or image embed, common mode
accepts one `--source` and no selection or conversion arguments. It infers the
closed artifact record, requires the marked target and source token to resolve
to the same canonical path, verifies the registered fingerprint, and publishes
through the ordinary evidence lifecycle without loading an artifact reader.

The explicit single-log Reorganize operations are:

```text
<skill>/scripts/log reorganize update-entry --path LOG --entry ENTRY
  [--date YYYY-MM-DD] [--slug SLUG] [--title TITLE] [--dry-run]
<skill>/scripts/log reorganize reorder --path LOG --entries ENTRY[,ENTRY...]
  [--dry-run]
<skill>/scripts/log reorganize relocate-log --path LOG --to DESTINATION
  [--dry-run]
<skill>/scripts/log reorganize transfer --path LOG
  --from-entry ENTRY --to-entry ENTRY
  (--all | [--evidence IDS] [--data NAMES] [--retention IDS])
  [--document-map SOURCE DESTINATION]... [--path-map SOURCE DESTINATION]...
  [--data-map SOURCE DESTINATION]... [--evidence-map SOURCE DESTINATION]...
  [--retention-map SOURCE DESTINATION]... [--dry-run]
<skill>/scripts/log reorganize remove-empty-entry --path LOG --entry ENTRY
  [--dry-run]
```

The agent completes every semantic choice, Markdown edit, selected support-file
move, and registry-record selection first. These commands verify that state and
then own only closed entry/log identity changes or coordinated authored-JSON
updates. They neither rewrite Markdown nor infer selections or destinations.
`reorder` receives every current entry ID once and applies the new sequential
IDs simultaneously. `relocate-log` moves the maintained summary/root pair only
within one filesystem. `remove-empty-entry` requires the summary item to be
absent and the remaining scaffold to be mechanically empty.

`transfer` permits its bounded decoder to delay current source document and
path checks only for explicitly selected records. It then applies every mapping
and validates the complete source and destination candidates through the
production registry, material, evidence-transformation, presentation, and
same-log consistency contracts before publication. Empty authored registries
are removed. `pyrun-outputs.json` is never relocated or rewritten to describe a
new execution. The `pyrun`-owned service may retire only exact source support
made stale by the selected transfer, and the result reports the destination
reruns needed to create new support.

All Reorganize mutations take the log lock before affected entry locks, publish
authored registry changes atomically, and leave generated validation artifacts
unchanged. A recognized interrupted-Reorganize marker beneath the operation
cache blocks later research mutation and validation publication until explicit
Repair; ordinary failures roll back and remove that marker.

Each authoring invocation emits exactly one
`research-log-authoring-result/1` object to standard output. Its stable fields
are `schema`, selected `task`, `status`, Boolean `changed`, diagnostic `code`,
and bounded `paths`; semantic list operations additionally return bounded
`records`. Explanatory diagnostics go to standard error. Changed, exact no-op,
content-write-free dry-run, and absent-removal results exit zero. Conflicts,
failed preconditions, and incomplete mutations exit 2 and still emit the
bounded failed result. Validation and discovery retain their own result schemas
and exit-status contracts.

Research operations coordinate through generated locks beneath
`<log>/.cache/research-log-operations/`. The stable `log.lock` supports shared
and exclusive nonblocking acquisition. Entry-scoped maintained mutations,
including `pyrun`, hold it shared before taking their stable-ID entry lock
exclusively; distinct entries can proceed concurrently, while contention on
the same entry fails without mutation. Log-wide mutations hold `log.lock`
exclusively before taking affected entry locks in sorted ID order. Initial log
creation instead uses a lock beneath the owning project's
`.cache/research-log-operations/`, keyed by the intended canonical log path.
Recognized Reorganize and entry-keyed authored-registry transaction residue
require explicit Repair and block applicable later operations.

Both publishing and dry-run Validate hold `log.lock` exclusively from before
their first research-owned read through evaluation, cache work, publication,
and result construction. Lock contention is an operational conflict and no
research-owned or generated bytes change. Validation retains its starting and
final research-owned snapshot checks because direct filesystem edits do not
participate in advisory CLI locks; a failed final check rolls back any bundle
whose installation has begun.

The CLI writes one bounded JSON result envelope to standard output when
evaluation or the unsupported-metadata preflight completes. A completed
published mechanical evaluation uses the validation CLI result schema listed
in `Current Versions` and contains:

- `schema`;
- `summary` is the resolved maintained-summary path;
- `status` is `complete_clear`, `complete_findings`, `incomplete`, or
  `unsupported_metadata`; and
- `published`, which states whether a new generated bundle was installed;
- `report`, which is the complete ready-to-present human summary for this
  invocation;
- the bounded `metrics`, `result_date`, `rules_version`, and scope aggregates;
  and
- `generated.human` and `generated.mechanical`, which name the installed
  generated reports.

The published CLI envelope does not duplicate the complete generated record on
standard output. `validation/results.json` owns those checks. An unpublished
dry-run or incomplete evaluation retains the complete validation-result record
in its result because no replacement bundle was installed. An
unsupported-metadata envelope contains
`code:"validation.unsupported_metadata"` and `observed.paths`, which lists
every detected unsupported path. It contains no partial mechanical record.

`complete_clear`, `complete_findings`, and `unsupported_metadata` exit zero
because the requested evaluation or preflight completed. `incomplete` exits 3
and publishes no per-log bundle; a writable run may retain completed
project-cache observations. Invalid inputs, observation failures outside the
mechanical outcome contract, and publication failures are operational errors:
they exit 2, write a precise message to standard error, and publish no result.
`--dry-run` returns the applicable mechanical envelope with
`published:false` and writes no validation result or cache path beyond the
canonical operation lock. When combined with
`--recompute`, it performs the complete cache-independent evaluation without
publishing either the result or the rebuilt cache.

A completed published evaluation owns exactly these active generated paths:

```text
<log>/validation/results.json
<log>/validation.md
<log>/.cache/research-log-validation.sqlite3
<log>/.cache/research-log-operations/log.lock
```

`pyrun` independently owns `<entry-root>/pyrun.json`. Standard validation
reads this file but never writes it. The file is excluded from artifact
inventory and report publication ownership.

The SQLite database may have `-journal`, `-wal`, and `-shm` companions. A
writable evaluation also owns the shared generated SQLite paths:

```text
<project>/.cache/research-log-fingerprints.sqlite3
<project>/.cache/research-log-fingerprints.sqlite3-journal
<project>/.cache/research-log-fingerprints.sqlite3-wal
<project>/.cache/research-log-fingerprints.sqlite3-shm
```

`validation/results.json` is authoritative and uses the mechanical-record
schema listed in `Current Versions`. Its exact top-level fields are `schema`,
`summary`, `rules_version`, `result_date`, `completion`, `checks`, and
`scopes`. Checks are unique and sorted by `identity`; each contains
`identity`, `scope`, `status`, `subject`, `dependencies`, and, only for
`fail` or `unavailable`, `failure`. A failure contains `code`, `subject`,
`observed`, `rule`, and an optional `dependency`. Scope aggregates contain
`scope`, aggregate `status`, total `checks`, and counts for every check status.
The record is canonical UTF-8 JSON with one trailing newline.

`<log>/.cache/research-log-validation.sqlite3` is disposable per-log
acceleration state using the schema and component versions listed in `Current
Versions`. It has independent `check_comparison` and `evidence_selections`
components.
`check_comparison` retains only passing dependency-bearing checks, with the
rules version, exact dependency projection, strict serialized check, and exact
SHA-256 identity of the authoritative `validation/results.json` from which
the baseline was built. `evidence_selections` retains strict serialized
successful `SelectionResult` values keyed by strong source content identity,
source profile, canonical locator identity, and locator-evaluator version.

Selections contain typed selected values, coordinates, identities, membership,
shape, and dependency projection. They contain no source payload, parsed table
or array, open handle, transformed presentation, or complete evidence check.
One serialized result is limited to 256 KiB and all retained results for one
log are limited to 16 MiB. An oversized selection remains a valid evaluation
result but is omitted from the cache. Each completed stable selection is
committed independently. Rows used in the current evaluation are assigned its
retention generation; unused rows are removed only after that evaluation
completes and its authoritative report publishes successfully.

The project-level SQLite fingerprint cache uses the schema listed in `Current
Versions` at
`<project>/.cache/research-log-fingerprints.sqlite3`. It stores current local
input observations independently of this per-log cache. File records contain
canonical absolute path, size, modification time, change time, algorithm, and
observed digest. Directory records contain the complete bounded metadata
identity, aggregate fingerprint, hydration state, and deterministic member
paths and kinds. Member files reuse the global file records. Repeated
declarations, directory commands, overlapping trees, and different logs share
one observation by canonical path.

An evaluated check counts as unchanged only when the current authoritative
mechanical-report bytes match the baseline report identity and the table
contains the same passing check under the current rules version and exact
dependency projection. This comparison happens after current evaluation and
does not skip check computation. A different rules version invalidates only
check comparison; it does not invalidate current project-level fingerprint
observations or otherwise eligible evidence selections.

Selection lookup happens after locator canonicalization and current strong
source-identity observation but before full source loading. A hit reconstructs
the exact typed `SelectionResult`, verifies the required optional reader is
available, and continues through current transformation and presentation
comparison. A hit performs no full source payload read, source parse, archive
open, or dataset materialization. Every used source is rechecked for stable
filesystem identity before evaluation returns. Source content, profile,
locator identity, or evaluator-version changes cause a miss.

Per-log cache absence, corruption, unsupported state, rejected rows, or I/O
failure causes bounded ordinary evaluation and never changes a conclusion. A
writable run rebuilds a corrupt cache. An unsupported future database or
component version is preserved and bypassed; compatible older components are
invalidated independently. A dry run opens each eligible cache read-only and
does not create, update, or garbage-collect state. `--recompute-validation`
bypasses and, during a dry run, does not open the per-log cache.
`--recompute-fingerprints` does the same for the project fingerprint cache.
Each non-bypassed cache remains independently eligible for reuse. A successful
writable run may repopulate each bypassed cache; combining the two flags or
using `--recompute` bypasses both. A dry run that bypasses both opens neither
cache and leaves generated state byte-identical.

`validation.md` is a deterministic nonauthoritative human document. It contains
one validated date, a compact Area and Result table, and bounded findings
grouped by entry and human issue type. Reproduction has no section in this
document; its independent human projection is `<log>/reproduction.md`. The
area vocabulary is `Clear`, `N issues`, `N artifact issues`, `N await
confirmation`, `Incomplete`, and an em dash for unevaluated areas.
Structure projects machine scope `conformance`, and Hygiene projects machine
scope `orphan`; neither display label changes the machine schema. Provenance
counts unique starting artifacts by their worst human result. Internal codes,
check identities, raw observed state, dependency mappings, passing totals, and
ordinary `not_applicable` checks remain only in `results.json`.

Each direct failed or unavailable condition enters a status-sensitive finding
signature using its code, resolved entry, normalized logical subject, violated
rule, and relevant observed state. Exact duplicate signatures collapse without
changing the authoritative checks. A direct prerequisite states how many
unique dependent `not_applicable` checks it prevents. Each entry and human
issue-type group displays at most ten deterministic target details, always
states its complete target count, and directs overflow to `log findings list`.
Human names and concise sentences come from one complete presentation catalog;
an emitted code without a catalog entry is an implementation error rather than
a fallback that exposes machine syntax. A clear report says `No mechanical
findings.`

The validation-owned targeted Provenance refresh accepts candidate
confirmation-only `pyrun.json` states from the reproduction publication
transaction. It rediscovers the current recorded commands, reconstructs the
exact output-support dependency projection, and replaces only direct
`provenance.output.unconfirmed` checks reached by the newly confirmed
executions plus summary-Provenance checks that depend on them. It then rebuilds
scope aggregates through the ordinary generated-record contract. Any command,
material, support, or dependency inconsistency aborts the refresh. This service
never evaluates Structure, Evidence, Hygiene, or unrelated Provenance checks,
never writes a file itself, and is not a general validation mode.

In the human Provenance artifact count, a
`provenance.output.unconfirmed` check projects as unavailable rather than as a
failed artifact. Multi-log summaries label that count `N unconfirmed`. A
downstream artifact whose `not_applicable` check depends transitively on an
actual failed Provenance prerequisite projects as a failed artifact, while its
authoritative machine check remains `not_applicable`. A failed Provenance
artifact takes precedence over an unconfirmed status for both an individual
artifact and the human row's aggregate status. Other `not_applicable` checks
remain only in machine-readable results and are omitted from human reports;
they are not abbreviated as N/A.
The batch CLI composes the multi-log table directly from the same scope
projection used by each human report. Agents do not parse generated reports or
recalculate these cells.

`log findings list` and `log findings show` are read-only bounded machine
access to the latest published `validation/results.json`. Both require an
explicit logical log path, read a regular non-symlink result through a bounded
UTF-8 decoder, expose its own result date without claiming currentness, and
never validate, publish, repair, or inspect research-owned files. `list`
returns at most 50 direct failed or unavailable finding-signature groups. Exact
`--entry` and `--subject` filters may be combined; there is no fuzzy matching,
pagination, or adjustable limit. Each returned group includes its code, entry,
logical subject, represented-check count, and one representative check ID.
`show` accepts one exact ID and returns that check's code, scope, status,
resolved entry, logical subject, dependencies, observed state, violated rule,
and result date without repair advice.

Finding queries distinguish absent published state
(`findings.result.missing`), unsupported schema
(`findings.result.schema_unsupported`), malformed or inconsistent state
(`findings.result.malformed`), duplicate identities (`findings.id.duplicate`),
unknown identities (`findings.id.unknown`), and identities that are not direct
findings (`findings.id.not_finding`). Expected query failures exit 2 and emit
no success object.

Validation acquires the canonical exclusive
`<log>/.cache/research-log-operations/log.lock` before opening the per-log
database and holds it through evaluation, authoritative publication,
comparison replacement, completed-run selection cleanup, and result
construction. Dry-run validation holds the same lock for its complete
read-only lifecycle. Publication rejects symlinks in generated destinations,
rechecks the unsupported-metadata boundary under the lock, and atomically
replaces each destination. An ordinary publication error restores every
replaced path to the prior completed bundle before releasing the lock. A cache
failure after successful publication leaves the authoritative bundle in place
and makes later reuse conservative. Process termination is subject to the
per-destination atomicity boundary; a later invocation must not interpret a
partial bundle as current. `validation.md` is composed from the authoritative
operation records under the same lock.

### Command-Provenance And Hygiene Diagnostics

The active command-input, producer, directory, retention, and Hygiene codes are
the closed set in `Approved Diagnostics` above. Existing invocation,
direction, producer, lineage, observation, and resource diagnostics remain
active only where that table and the surrounding contract retain their
conditions.

### Command-Provenance Resource And Safety Bounds

The command-derived provenance profile permits at most:

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
- 100,000 descendants in one fingerprinted or retained directory;
- 100,000 immediate candidates scanned in each identity-pattern wildcard
  parent;
- 512 bytes in one normalized path or source expression;
- 1,000,000 material-graph nodes and 4,000,000 edges per maintained log;
- 64 producer-lineage levels;
- 1 MiB of recorded command text per invocation; and
- the stricter source-reader and evidence-record limits already defined by
  this specification.

Readers and command parsers must be non-executing, path-safe, symlink-safe,
and bounded. Validation does not execute commands, import scripts, deserialize
unsafe formats, follow unrestricted external links, or enumerate outside the
declared scope. Crossing the material-graph node, edge, or producer-lineage
bound fails the affected graph evaluation; it never silently truncates
lineage or converts an unresolved tail into an orphan result.
Validation does not enumerate outside the target maintained log except through
exact locally declared input paths and the first-class entry `data` and
`images` material roots. Crossing a stable bound is `fail`; temporary material
access failure is `unavailable`.

## Future Command-Discovery Expansion If Warranted

The contract intentionally stops at bounded static loop expansion, direct
`pyrun` invocations, named inputs, runner role declarations, the closed
leading-or-trailing `input`/`output` option-name convention, exact file inputs,
and exact bounded directory inputs and outputs. A missing or ambiguous result
fails regardless of whether one relationship appears likely.

Additional automatic role words, internal-token matching,
glob grammars, range-to-filename expansion, dynamic output templates, selector
languages, per-command plugins, and evidence-record provenance hints remain
deferred until several concrete cases show that the current forms make natural
research authoring materially awkward. A proposed addition requires
retained-corpus evidence and explicit researcher approval. It must be closed,
independently checkable from recorded state, bounded, and simpler overall than
renaming the option or using a runner role declaration.

No future mechanism may select a merely plausible producer, suppress an orphan
without a retention record, invent missing lineage, override shell
direction, or inspect script internals as provenance authority.

## Conformance Examples

The first example composes presentation, evidence selection, transformation,
and recorded-command provenance. The locator and transformation examples that
follow isolate their respective subcontracts.

### Composed Statistic

An entry presents:

```markdown
The candidate success rate was `67.6%`<!-- eid:candidate-success-rate -->.
```

Its entry-local `evidence.json` contains:

```json
{
  "schema": "research-log-evidence/v3",
  "records": [{
    "id": "candidate-success-rate",
    "document": "entries/2026-08-27-e001-study/e001.md",
    "kind": "statistic",
    "sources": [{
      "source": "<results>",
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

The same entry's `data.json` registers `data/results.csv` as the generated
`results` file input, using its current SHA-256 fingerprint and
`"origin": false`.

The same experimental section records one command that names
`data/results.csv`:

````markdown
```bash
./pyrun scripts/run_study.py --input-dataset "<development-set>" --output-summary-csv data/results.csv
```
````

Mechanical validation resolves the local script without executing or
inspecting its internals. The role-bearing options establish the command graph.
It resolves `<development-set>` through the entry-root `data.json`, verifies
its fingerprint and `origin: true`, and does not traverse beyond that origin.
The entry-root `pyrun-outputs.json` must also contain a confirmed
`data/results.csv` record whose output bytes, script path and bytes, exact
parameters, and direct input fingerprints all match this current command and
filesystem state. The evidence check compares `67.6%`; the Provenance check
verifies the complete bounded chain. Neither decides whether success rate is
scientifically appropriate.

A runner declaration makes a non-natural relationship visible to both `pyrun`
and static validation. A retained `pyrun` output that needs confirmed support
therefore uses a natural output-bearing option, `--other-outputs`, or an
explicit capture option. Renaming only the Markdown command while leaving the
executable interface unchanged is not a valid repair.

### Other Presentation And Provenance Cases

- A summary statistic names one successful entry evidence record through its
  adjacent `ref`. A table reference also names one exact row and column. The
  summary reuses the target record's source and command-provenance projection
  and does not declare another producer.
- A direct, structured, or summary table uses the applicable closed table
  recipe. Every local source used by the table must independently resolve to
  exactly one producing invocation unless it reaches an explicit origin.
- A marked output block may select a retained command log. Use
  `./pyrun --capture-stdout-stderr data/run.log -- ...` so the log has both a
  graph relationship and confirmed output support; raw redirection or `tee`
  does not provide that support. The marked fence payload must still match the
  selected retained text exactly.
- A whole-artifact evidence presentation resolves its one source token and
  compares that canonical path with the normalized Markdown target before
  applying ordinary fingerprint and Provenance checks. A generated artifact
  still requires one mechanically proven command output and exact confirmed
  output support; an explicit origin stops the chain.
- A cross-log source is observed as a locally declared origin of the consuming log.
  Validation does not import the source log's command graph or validation
  result.
- Retained material with no mechanically discoverable producer fails
  `producer.missing`. Generated material with a discoverable
  producer but no confirmed output record fails `provenance.output.unrecorded`.
  There is no limitation declaration that converts either gap into a pass.

### Directory And Named-Input Case

Suppose an entry records:

````markdown
```bash
./pyrun --other-outputs output-dir -- \
  scripts/run_trials.py \
  --reference "<reference-grid>" \
  --cases 1:40 \
  --output-dir data/trials
```
````

`<reference-grid>` must resolve through exactly one entry-root `data.json`
input with an exact fingerprint and the applicable producer or origin
boundary.
The runner declaration establishes `data/trials` as a dedicated output
directory by observing its completed kind. Its complete collection is every
retained regular-file descendant observed beneath that directory. The
`--cases 1:40` selector is ordinary command input; validation does not need to
understand how it maps to filenames. Without an approved directory role, the
directory remains an unresolved material candidate.

### Locator Examples

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

### Locator And Transformation Failure Examples

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
| Locator JSON is malformed | `locator.syntax.invalid`; do not retry under another interpretation. |
| Wildcard selects more than the configured bound | `locator.selection.too_large`. |
| Summary `label` occurs outside the first column or is its row's only cell | `transformation.table.label_invalid`. |
| Boolean cell declares `style:"Yes/No"` | `transformation.boolean.invalid`; use `yes_no`. |
| Binary-float input is NaN or infinity | `transformation.nonfinite_unsupported`. |
| Binary-float input is not IEEE binary16, binary32, or binary64 | `transformation.type.mismatch`. |
| Numeric renderer declares `sign:"optional"` | `transformation.render.invalid`; use omission or `always`. |

## Compatibility And Evolution

The locator contract follows these evolution rules:

- any change that alters the parsing or meaning of an existing valid locator
  requires a new locator version;
- an additive source profile or structural property may join the registry
  only when it cannot change an existing locator's dispatch or result;
- changing typed equality, path behavior, expectation semantics, selection
  order, or failure classification requires a new version;
- resource-limit increases do not change locator meaning but must be recorded;
- unsupported future versions fail without fallback.

The transformation contract follows these evolution rules:

- any change that alters the parsing or meaning of an existing valid
  transformation requires a new transformation version;
- changing the value pipeline, rounding, rendering, unit attachment,
  input-consumption, canonical form, or table semantics requires a new
  version; and
- a future feature listed under `Future Expansion If Warranted` belongs in a
  later version unless it provably cannot change the result or validity of any
  existing recipe.

A change to evidence JSON schema dispatch, record or marker identity, field
ownership, summary-reference syntax or coordinates, cardinality, Markdown
parsing, exact comparison, command identity, runner-option or input-token syntax,
Provenance proof forms, origin-boundary semantics, output-support semantics,
graph semantics, or result scopes that alters an existing valid outcome
requires the applicable new evidence, command-discovery, or
mechanical-validation contract version.

## Current Implementation Boundary

Standard validation implements the locator and transformation,
evidence association and presentation, command discovery, end-to-end
Provenance, material graph, Hygiene, composed outcome, generated record, cache,
report, and unsupported-metadata preflight contracts in this document. It does
not parse unsupported generated validation metadata.

No downstream surface may define a competing evidence-record, locator,
transformation, presentation, command-Provenance, collection, output-support,
Hygiene, or mechanical-outcome contract. Self-contained runtime agent surfaces
may carry the bounded authoring and operational subset they need without
loading or linking to this specification, but they must remain compatible with
it.
Maintainer-facing implementation documentation may omit detail by pointing
here and must not contradict this specification.

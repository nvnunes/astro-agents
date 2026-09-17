# Research-Log Mechanical Validator Specification

## Status And Authority

Status: current. The evidence, locator, transformation, association, command,
Provenance, and Orphans rule contracts and the canonical validation model,
Reproduce admission, version-19 persistence, saved read model, report
rendering, public CLI, and Repair command contracts are implemented.

This document is the normative implementation contract for the code-only
research-log mechanical-validation CLI and its supporting tools. Their code,
tests, generated records, cache, diagnostics, and public operation must adhere
to this specification. It defines the evidence-record, association, Provenance,
Orphans, and generated-state contracts that validator code implements.

This specification does not define agent behavior or teach researchers how to
use the research-logging workflow. `skills/research-logging/` is the
self-documenting agent surface. Repair is its sole explicit repository-level
consumer of this specification and reads only a relevant section when
malformed or unsupported state prevents the owning CLI action from operating.
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
- Published validation: [Published Validation And Repair Batches](#published-validation-and-repair-batches).
- Inspection interface: [Retained Validation Snapshots](#retained-validation-snapshots).
- Adjacent command operations: [Isolated Command Verification](#isolated-command-verification).
- Extension and examples: [Future Command-Discovery Expansion If
  Warranted](#future-command-discovery-expansion-if-warranted), [Conformance
  Examples](#conformance-examples), and [Compatibility And
  Evolution](#compatibility-and-evolution).

## Current Versions

This table centralizes current versioned surfaces. The rest of the document
names a version only where mechanical dispatch, serialization, dependency
identity, cache compatibility, or evolution requires it.

| Surface | Version or transition disposition |
| --- | --- |
| Evidence records | `research-log-evidence/v5` |
| Locator language | 2; standalone locators use the `v2:` prefix |
| Transformation language | 2; standalone transformations use the `v2:` prefix |
| Input registry | `research-log-data/v6` |
| `pyrun` execution state | `research-log-pyrun/v7`; earlier schemas are unsupported; owned by the [reproduction specification](research-log-reproduction-spec.md#pyrunjson) |
| Legacy output records (validation read-only) | `research-log-pyrun-outputs/v1` |
| Retention registry | `research-log-retention/v1` |
| Directory observations | `research-log-directory-observation/1` |
| Directory fingerprints | `research-log-directory-fingerprint/1`, `research-log-identity-files-fingerprint/1`, and `research-log-identity-patterns-fingerprint/1` |
| Locator evaluator | `research-log-locator-evaluator/1` |
| Section classifier | `entry-section-labels/1` |
| Selection-cache serialization | `research-log-selection-result/1` |
| Mechanical rules | `research-log-mechanical/markdown-evidence-11` |
| Canonical validation snapshot | `research-log-validation-snapshot/3` |
| Authoring results | `research-log-authoring-result/1` |
| Command diagnostics | `research-log-command-diagnostic/1` |
| Isolated command verification | `research-log-command-verification-result/1`; lifecycle semantics are owned by the [reproduction specification](research-log-reproduction-spec.md#current-command-verification-boundary) |
| Validation response schemas | `research-log-validation-run/1`, `research-log-validation-root-run/1`, `research-log-validation-show/1`, `research-log-validation-finding-list/1`, `research-log-validation-batch-list/1`, `research-log-validation-blocked-list/1`, `research-log-validation-failed-list/1`, `research-log-validation-finding-detail/1`, and `research-log-validation-batch-detail/1` |
| Shared result store | `<log>/.cache/results.sqlite`; SQLite versions 19/20/21/22 preserve canonical validation snapshots and independent command diagnostics. Shared initialization creates version 19 without a reproduction domain; current reproduction publication installs version 22, while version-20/version-21 reproduction is unsupported and replacement-only. Versions 17/18 require replacement and are never validation input. This specification owns validation tables; the [reproduction specification](research-log-reproduction-spec.md#replacement-reproduction-model) owns its separate saved-run domain. |
| Former finding and result queries | Removed without aliases or compatibility status |
| Discovery results | `research-log-discovery-result/1` |
| Per-log validation cache | SQLite schema 2; `evidence_selections` component version 1 |
| Project fingerprint cache | SQLite schema 1 |

The specification includes `evidence.json`, presentation-marker, locator,
transformation, and command-discovery syntax because these are inputs to the
validator. The research-logging skill carries the bounded authoring and
operational rules agents need to produce compatible research logs; ordinary
research-agent work does not load this implementation specification.

Provenance lineage and structural execution-support validation is rooted in
evidence records. Commands outside that closure do not require structural
output-support or recursive lineage validation. Reproduce separately evaluates
whether retained executions remain current. Separately,
complete-graph output reconciliation reports every graph-declared output whose
artifact is absent as a Provenance finding and every output record absent from
the current graph as an orphan finding. Recorded `pyrun` command surfaces,
exact path and named-input connections, `pyrun` output support, and observed
retained material establish Provenance without an authored lineage graph.
Resolved script identity is part of structural output support; its raw-byte
fingerprint is informational current-source provenance. Current execution
records retain one nullable fingerprint for statically reachable project-local
effective code. Reproduce owns its currentness meaning. Validation reads that
state structurally but does not turn a mismatch or unavailable fingerprint into
a validation check.

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
  failures and unmatched-output orphan findings;
- orphan classification for unused retained material and unused input
  declarations;
- validation evaluation order, private rule areas, findings, batches, blocked
  and failed checks, resource bounds, and Reproduce-owned currentness;
- the canonical completed snapshot, shared research graph, and validation
  outcome meanings;
- canonical snapshot publication, cache, lock, and derived human-report
  contracts; and
- obsolete generated-state orphan findings and completed-validation
  boundaries.

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
records: command discovery and the completed entry presentation evaluation. A
summary reference reuses that completed entry presentation evaluation without
declaring another source or transformation. Neither surface duplicates the
complete contents of the command, output, or prose.

For a split entry, the summary reference's `entry` value is the exact authored
document stem, such as `e004a`. That reference identity remains distinct from
the stable physical entry ID, such as `e004`, which owns the shared entry
folder, repair context, and Reproduce admission ownership.

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

- source-internal paths, fields, filters, indexes, and explicit slices;
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
evidence-association, command-Provenance, shared-research-graph, Orphans, and
composed outcome subcontracts below own the remaining stages.

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
or declaration identity rule, participate in selection-cache identity. A remote
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

`blocked` belongs to the calling private rule check, not to locator
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
  lexical string field or an already typed field of that same numeric kind.
  Parsing converts strings only. An already typed integer or decimal is accepted
  unchanged when its kind matches; a different numeric kind or Boolean fails.

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

- `parse` converts a source string, or accepts an already typed source operand
  of that same numeric kind unchanged. A different numeric kind or Boolean
  fails. It affects only that condition's source operand and does not change
  the selected source value.
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
  `select` field expansion. An explicitly bounded text excerpt has one match.
- `items` counts final selected values after `select`, `property`, or text
  slicing. An explicitly bounded text excerpt has one item.
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

A text selector contains exactly `line:"N"` or `lines:"START:END"`, with an
optional `chars:"START:END"` only alongside line. Bounds are one-based,
positive, inclusive, and must exist in the source. Characters are Unicode
code points, not bytes. UTF-8 source line endings normalize to LF.
The selected result is exactly one string. Substring searches and inferred
occurrences are unsupported. Evidence output requires null transformation.

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
- closed Boolean rendering in short values and direct-table columns;
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

Non-table value expressions use only concrete input/item references.
Source-internal paths remain locator-owned.

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
When used in a direct-table column descriptor
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

Only `form:"table", mode:"direct"` is supported. A table has one source
selection, nonempty exact headings, and one column descriptor per heading.
The authored Markdown header/alignment rows define presented columns;
comment column clauses map source fields to those columns in order.
There are no generic structured or summary table transformations, joins,
compound cells, or arbitrary row-order declarations.

Native Boolean columns accept true_false, yes_no, and pass_fail styles.
Text parsing accepts exactly true, false, True, or False with parse:"boolean".
Comparison is case-insensitive only for declared Boolean cells; text,
headings, and source parsing remain exact.

#### Direct Table Grammar

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
record. Selected column and record order are unchanged. Locator select paths may
select a subset of columns and place them in presentation order.

A direct column descriptor is exactly one of:

- `{"form":"text"}`, which requires a string and passes it through exactly;
- `{"form":"boolean","style":"true_false"}`, optionally with
  `"parse":"boolean"`, where `style` and parsing have the closed styles true_false, yes_no, or pass_fail and exact Boolean text parsing;
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
unit. Direct mode cannot combine fields, align another input, insert labels,
enumerate exceptions, or use a multi-value cell form. Source selection may
filter rows and select/reorder columns, but not compute new values. A complete retained range, compound, or other display string can be
passed through one `text` column without assembly.

If the source does not already have the required rectangular membership and
order, the table is not direct. Retain a new presentation-ready artifact through a recorded script.
A summary table with independent evidence cells is ordinary Markdown, not
one table record. Every numeric or closed-Boolean data cell must carry its own
scalar or compound EID; a mixed table does not waive evidence completeness.
Otherwise retain a table-shaped artifact and use one direct-table record.

Maintenance note: Changes to direct-table syntax or behavior must be reflected
in
[Direct Evidence Tables](../skills/research-logging/references/record-evidence-definition-direct-tables.md)
and its public-CLI conformance tests.

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
- another unary presentation operation demonstrated across multiple logs.

Expansion must remain code-only, bounded, versioned, and unambiguous. It must
provide:

- representative retained examples from more than one research-log context;
- an explanation of why changing the presentation or retaining a
  purpose-built value is materially worse;
- one closed result grammar with every accepted spelling enumerated;
- complete current-schema conformance, failure, and resource-bound fixtures;
  any one-time conversion fixtures remain disposable; and
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
| Associated presented item | 1 MiB UTF-8 |
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
| `transformation.input.reference_invalid` | fail | A concrete item reference does not resolve in the required input. |
| `transformation.input.unused` | fail | A locator-selected item is not consumed by the recipe. |
| `transformation.input.reused` | fail | One selected item is referenced more than once. The transformation requires exact one-time consumption. |
| `transformation.table.direct_mismatch` | fail | A direct recipe does not have exactly one table or grouped-record input, or its selected columns and declared columns are not one-to-one. |
| `transformation.table.input_not_records` | fail | A direct table input lacks retained record grouping or uses a prohibited selection kind. |
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

### Evidence Files And Generated Validation Residue

The active association surfaces have these roles:

| Role | File | Association |
| --- | --- | --- |
| Entry presentation | Entry-root `evidence.json` | Entry-scoped stable ID shared with one hidden Markdown marker |
| Summary reference | Maintained summary Markdown | Hidden lookup by authored entry-document stem and evidence ID, plus a table coordinate when applicable |

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

The bounded generated-residue inventory recognizes superseded generated
validation artifacts and reports one `orphan.generated.residue` finding for
each path. It does not interpret the residue, block evaluation of unrelated
rules, or create a separate validation outcome. Retention records use their own
ID namespace and participate in orphan classification, not presentation
association.

The inventory recognizes generated residue only at these exact paths,
relative to the maintained-log root:

- `validation/results.json` or `validation/batches.json` from the former
  machine-state location;
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

When no active `.cache/results.sqlite` exists, the inventory also treats
`validation.md` as generated residue if its bounded prefix contains
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
  "schema": "research-log-evidence/v5",
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

Required keys are `id`, `document`, `kind`, `sources`, and `transformation`.
The optional `reproduction_tolerance` key has the exact shape
`{"absolute":"<canonical positive finite decimal>"}` and is allowed only on a
non-artifact record. Unknown keys fail. `kind` is `artifact`, `statistic`,
`table`, or `output`.
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
  "transformation": null,
  "artifact_fingerprint": {
    "algorithm": "sha256",
    "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

It has exactly one source, a null locator, and a null transformation. Null
locators are prohibited for every other record kind. The source resolves to
one registered file or one exact member of a registered directory; a bare
directory is invalid. For an image or link presentation, `artifact_fingerprint`
is required and is either the exact SHA-256 object shown above or `null`.
It is the evidence-owned accepted byte baseline for that one path-based
presentation; `null` is explicit incomplete baseline state and is unverified,
not a match. Inline `diff` artifacts must not carry the field and compare their
normalized text payload instead. The declaration identity selects how current
material is observed; it never supplies a historical presentation baseline.
Path-based artifact presentations do not open the artifact through a
format-specific reader. The authoring action obtains a stable current file
observation before publication and stores it as this baseline; update replaces
it only after the same association and stability checks. A fresh execution,
reproduction, promotion, cache rebuild, relocation, or repeated add must not
silently refresh it. An inline `diff` artifact uses only the bounded UTF-8
reader and exact comparison defined below.

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
<!-- eid:median-success-rate source=results select=/rate form=percentage -->
```

The comment starts with the literal prefix `<!-- eid:` and the record ID,
then a compact shell-quoted key=value definition, and ends with `-->`.
A bare EID is not a complete current definition. Case and marker aliases are
unsupported. Definitions may span lines; placement refers to the entire
comment adjacent to its owned presentation. The comment is non-rendered structure and is not part of
the evidence-bearing expression.

Marker placement depends on `kind`:

- A `statistic` marker immediately follows one inline code span on the same
  source line with no intervening characters. The code-span contents are the
  presented expression.
- A `table` marker occupies the immediately preceding source line. No blank,
  comment, label, or prose line may intervene before the first table row.
- An `output` marker occupies the immediately preceding source line before the
  opening `text` fence. No blank, comment, label, or prose line may intervene.
- A path-based `artifact` marker immediately follows one eligible local
  Markdown link or image embed on the same source line with no intervening
  characters. It binds to that immediately preceding Markdown node, including
  when one line contains several separately marked artifacts.
- An inline-text `artifact` marker occupies the immediately preceding source
  line before a fence whose info string is exactly `diff`. No blank, comment,
  label, or prose line may intervene. No other fence info string, attributes,
  or alternate spelling produces an inline artifact.

One marker binds exactly one presented item. One presented item has exactly one
marker. A marker ID must resolve to exactly one presentation record whose
`document` and `kind` agree with the observed item. Duplicate markers, nested
markers, a marker in a fence, a marker without an eligible item, and a
presentation record without a marker fail. EID-like literals inside fences
are not authored markers.

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

`entry` is the exact authored document stem in the current maintained log.
`eid` satisfies the evidence-ID grammar and names one presentation record
whose `document` has that stem. The pair `(authored document stem, evidence
ID)` is the summary lookup key; it is not canonical record identity. The
resolved record retains its stable identity `(maintained-log identity,
physical entry ID, evidence ID)`.

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
- entry tables, output blocks, local artifact links or image embeds, and inline
  artifact `diff` fences are eligible only beneath that experimental section's
  `Results:` label;
- summary statistics are eligible only in the maintained summary; and
- synthesis and prose entry sections contain no evidence-record targets.

The deterministic section classifier remains outside this specification but
its declared classifier version and classification result are association
dependencies. A marker cannot override an ineligible context.

Every eligible entry statistic, table, `text` output block, local artifact
link, local image embed, or `diff` fence must have one valid entry marker, and
every eligible summary statistic must have one valid summary reference. A
missing entry marker fails
`association.declaration_missing`; a missing summary reference fails
`summary.reference.missing`. Other unmarked prose is not promoted to evidence
by validation. Semantic Review may report an apparently evidential claim that
uses no supported presentation form.

External links, fragment-only links, and Markdown-document links are not
artifact evidence presentations. Summaries cannot present artifact evidence.

For a path-based artifact record, validation normalizes the marked Markdown
target relative to its document and independently resolves the source token
through `data.json`. Both must identify the same canonical artifact path before
fingerprint or Provenance evaluation. A different path fails
`association.artifact.source_mismatch` even when its bytes are identical.

For an inline-text artifact record, the source token must resolve to one
regular UTF-8 file. Validation reads no more than the associated-presentation
bound and compares its complete contents with the complete `diff` fence
payload. Both values normalize CRLF and CR to LF and remove exactly one
terminal LF for Markdown's structural fence separation. No other whitespace,
line, Unicode, diff syntax, or content normalization occurs. Different
normalized contents fail `association.artifact.content_mismatch` as Evidence;
invalid UTF-8 or a non-regular source fails
`association.artifact.inline_source_invalid` as Evidence. An unstable or
temporarily unreadable source is unavailable rather than a mismatch.

### Evidence Source And Transformation Cardinality

Non-artifact entry records consume source objects in their declared array
order. Each object contains one embedded locator and must return one successful
ordered typed selection. The transformation input slot is the zero-based
`sources` array position. Artifact records use the whole source and have no
selection or transformation input.

Cardinality is closed by presentation kind:

| Kind or table mode | Source objects | Additional requirement |
| --- | ---: | --- |
| `artifact` | 1 | A link or image resolves to the same path as the source, or an inline `diff` payload equals the complete UTF-8 source. |
| `statistic` | 1–8 | The transformation produces exactly one supported non-table form. |
| `output` | 1 | The bounded line/character locator selects the complete verbatim block payload; transformation is null. |
| `table` / `direct` | 1 | The selected table and recipe satisfy direct-table one-to-one rules. |

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

Inline artifact comparison is the closed exception that applies only the
structural line-ending and terminal-line-ending normalization stated above; it
does not use the locator or transformation language.

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

Shared output currentness does not change the referenced record's validation
projection. Summary provenance uses the recorded support relationship and adds
no currentness check or finding. Other finding, blocked, or failed provenance
targets retain their summary consequences.

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
6. canonical parsed statistic, table, output, or artifact presentation model,
   including the artifact presentation form and inline format when applicable;
7. ordered resolved-source identities;
8. locator and expectation projections;
9. transformation projection and accepted surface spellings, or the complete
   normalized inline artifact source and payload association; and
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
newly observed generated-residue path creates an orphan finding; its contents
do not enter evidence-association currentness.

The validator may use whole-file hashes to detect a need for parsing but must
persist and compare the narrower association projection for outcome reuse.

### Association Failures

Every failure records the evidence record identity when known, document, kind,
observed marker or presentation, and violated clause. Reserved active-validation
codes are:

| Code | Finding type or attempt effect | Condition |
| --- | --- | --- |
| `orphan.generated.residue` | orphan | Validation encountered one recognized superseded generated-validation artifact. The artifact is reported without interpreting its contents. |
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
| `association.artifact.content_mismatch` | evidence | An inline artifact payload differs from the complete normalized UTF-8 source. |
| `association.artifact.inline_source_invalid` | evidence | An inline artifact source is not one regular UTF-8 file. |
| `association.artifact.inline_source_unavailable` | failed check | An inline artifact source could not be read reliably. |
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
- 1 MiB of source Markdown for one marked table, output block, or inline
  artifact and 1 MiB in the inline artifact source file; and
- the stricter locator and transformation bounds already defined by this
  specification.

Crossing a stable authored bound is `fail`, not `unavailable`. Implementations
may stream files and indexes and must not require repository-wide discovery.

## Input Registry And Artifact Graph Contract

### Registry And Generated-State Ownership

This section defines the command-input, fingerprint, Provenance,
retention, and orphan contract.

The current schema and rules identifiers are listed in `Current Versions`.
`pyrun.json` is `pyrun`-owned execution support state, not an authored
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
The declaration identity in `data.json` selects the observation algorithm; it
is not a cache acceptance baseline. Cache keys include the canonical target and
complete identity selection (including selectors or Git commit), so an
incompatible identity-rule change cannot reuse an old observation. A cache may
accelerate current observation but can never refresh a retained execution
observation or an evidence artifact baseline.

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

`data.json` declares the current named resources used as material inputs by
recorded commands or evidence records owned by one entry root. It owns locator,
kind, origin/generated classification, directory selection, Git commit
selection, and optional reproduction comparison policy. It contains no accepted
current bytes or historical execution baseline. The observation service computes
current content under a declaration when a consumer needs it; `pyrun.json`
retains the observations made by a successful execution.

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
- A generated output is declared before its producing command when a later
  recorded command or evidence record consumes it. An output consumed by
  neither surface remains absent.
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
  "schema": "research-log-data/v6",
  "inputs": []
}
```

Both keys are required and unknown keys fail. `inputs` is non-empty. Strict
JSON uses the UTF-8, duplicate-key, finite-number, and trailing-content rules
of `evidence.json`. Array order has no meaning; canonicalization sorts by
`name`. One file is at most 8 MiB and contains at most 10,000 inputs.

Every direct item requires `name`, `kind`, `location`, `identity`, and the
Boolean `origin`:

```json
{
  "name": "development_catalog",
  "kind": "file",
  "location": "../../../../../inputs/development-catalog.csv",
  "identity": {"algorithm": "sha256"},
  "origin": true
}
```

An entry-local reference has exactly `from_entry` and
`name`. It resolves recursively to one direct generated declaration in the
named stable entry of the same log. It does not copy, shadow, rename, or change
the source declaration, cannot target an origin or another reference, and may
not form a cycle:

```json
{"from_entry": "e001", "name": "simulation-results"}
```

Generated resources may be declared before production; no absent-digest
sentinel exists. Direct declarations allow only `name`, `kind`, `location`,
`identity`, `origin`, and optional `comparison`. Unknown fields fail. The
closed identity forms are `{"algorithm":"sha256"}` for a file,
`{"algorithm":"directory-sha256-v1"}` for a full directory,
`{"algorithm":"identity-files-sha256-v1","files":[...]}` for explicit
selected files, `{"algorithm":"identity-patterns-sha256-v1","patterns":[...]}`
for pattern selection, and
`{"algorithm":"git-commit-sha1-v1","commit":"<40 lowercase hex>"}` for
a Git repository. `digest` is forbidden in every declaration. A Git commit is
an authored immutable-object selection, not a refreshable byte baseline.
Successful `pyrun` records observed current inputs and outputs only in
`pyrun.json`; it does not mutate declarations.

A generated file may additionally select the named evidence-scoped
reproduction comparison:

```json
"reproduction_comparison": {
  "contract": "research-log-evidence-scoped-comparison/1",
  "profile": "evidence"
}
```

No other comparison form is accepted. The optional declaration is invalid on
an origin, directory, or Git repository. It requires at least one applicable
non-artifact evidence record selecting the same canonical resource. Every such
record must have compatible bounded locator and transformation definitions;
an evidence record with `reproduction_tolerance` must select a numeric value.
Missing, ambiguous, inconsistent, or incompatible declarations are Conformance
failures before reproduction. The tolerance affects only retained-versus-
regenerated reproduction comparison and never presentation validation.

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
their selected commit as material identity, so one repository locator may
identify different commits under different names; the same pinned commit may
not be declared twice in one file.

Separate entries may declare the same material when each consumes it. Within
one maintained log, all file and directory declarations of one target must
agree on `kind`, complete `identity`, `origin`, and comparison declaration. Git
repository declarations agree
when their commit material identity agrees; locator paths may differ. Conflict
fails; validation does not choose one declaration. The conflicting
declarations are unavailable to dependent command and graph evaluation; other
declarations in the same registry and entries that do not declare the target
continue evaluation.

### Declaration Identity And Current Observations

Every direct declaration has exactly one closed identity:

- A local file uses `{"algorithm":"sha256"}`.
- A local directory uses
  `{"algorithm":"directory-sha256-v1"}`.
- A managed local directory uses
  `{"algorithm":"identity-files-sha256-v1","files":["<relative path>",...]}`.
- A pattern-managed local directory uses
  `{"algorithm":"identity-patterns-sha256-v1","patterns":["<relative selector>",...]}`.
- A pinned Git repository uses
  `{"algorithm":"git-commit-sha1-v1","commit":"<40 lowercase hex>"}`.

Every resource is locally accessible when a current consumer requires it.
Files and directories produce byte-derived current observations. A Git
repository identity selects the exact commit object and its tracked snapshot.
Size, modification time, and change time may determine whether a cached digest
must be recomputed, but they are never the identity being validated. If the
recomputed digest is unchanged, the resource is unchanged for Provenance.
Current observations are compared with retained execution observations for
provenance and reproduction, and with evidence-owned artifact baselines for
path-based presentation. Validation never rewrites either.

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
the declared artifact or selected Git commit. It is independent of storage location.
An origin may be inside or outside the entry, and an artifact inside the entry
may be either an origin or generated material.

An item with `origin: true` is a terminal identified input. Validation does not
claim how that artifact or commit snapshot came into existence. An item with
`origin: false` must trace to one unique earlier producer and then through that
producer's direct inputs. More than one earlier producer is ambiguous. An
origin boundary that hides a current `pyrun` producer not requiring
reproduction is invalid. An origin
does not connect an otherwise unreached artifact or suppress an orphan finding.

### Command Tokens And Roles

Both runners supply absolute paths for declared filesystem arguments, including
file and directory inputs and outputs. Ordinary relative outputs resolve
through output bindings against the entry; reproduction redirects those same
bindings to its workspace. Scalars and recorded recipe identities remain
unchanged. Existing declaration, fingerprint, and symlink checks still apply:
`<project>` and `<log>` placeholders alone declare no dependency. Scripts must
consume supplied paths without source-layout assumptions and pass them to
helpers and children. Additional files previously selected by paths embedded
in input contents need explicit declared parameters, including existing
directory-member tokens where appropriate. Stored paths may remain descriptive
metadata. Missing declarations or helper imports require script fixes; no
embedded-path resolver or import-discovery framework is introduced. Direct
Python execution gains no runner behavior.


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

A command with no runner options passes its Python program directly without a
separator. Any runner option requires one leading runner-option group followed
by `--` and the program. `--cid VALUE` is an optional runner override. A
nonnumeric valid value is the full effective CID. A canonical positive integer
`N` is numeric shorthand that resolves to the valid lexical Python filename
stem plus `-N`; for example, `--cid 2 -- scripts/foo.py` resolves to `foo-2`.
The shorthand rejects zero and leading-zero values and requires a `.py`
program whose stem satisfies the command-ID grammar. When `--cid` is absent,
the parser derives the effective CID from that same lexical `.py` filename
stem. A full explicit CID remains available for non-Python programs and invalid
stems.

The effective CID owns one command or bounded loop, is stable across all of
that owner's expansions, and is unique across the entry's split documents.
Repeated programs and basename collisions may use explicit numeric shorthand.
Multi-program owners and program replacement that must preserve a prior
identity require a full explicit CID. Static discovery and the live runner
resolve the same effective CID at their shared parsing boundary. Diagnostics
for a duplicate derived CID direct the author to add an explicit numeric or
full override. An unstable multi-program owner requires one full CID shared by
every command. Diagnostics do not invent suffixes.

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

Every declared input or output uses its matching named token. Direction comes
from an input- or output-bearing option, capture, or explicit runner role; a
token alone does not assign direction. A raw value matching an input is a
missing token, a raw proven input without an item is undeclared, and a raw
output path is `data.output.token_missing` in Conformance, once per authored
output argument rather than per expanded directory member. Distinct selectors
and repeated arguments with different targets remain separate. Each rule
records the command location, selector, and canonical output path and retains a
private passing check after token migration. On failure it attaches the exact
command repair key; only the resulting finding can participate in a batch.
Legacy path-authored
recipes remain decodable for diagnosis but are not admissible for reproduction.

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

`pyrun` accepts runner-visible role declarations before the runner-option `--`
separator. `--other-inputs <selectors>`, `--other-outputs <selectors>`, and
`--other-parameters <selectors>` each accept one comma-separated list of script
option names without leading hyphens or one-based positional selectors written
as `@N`. Each declaration may occur once. Lists reject empty or whitespace
items, duplicate selectors, selectors without a matching valued argument, and
selectors declared in conflicting roles. A selector applies to every occurrence
of its option. An explicit declaration overrides automatic role inference from
the option name. `--other-parameters` declares ordinary literal values: it
suppresses material inference for those values. Registered material tokens
require an input or output role and are rejected as ordinary values before
execution. Reserved project/log placeholders retain their normal expansion.

The same runner-option prefix accepts `--auto-reproduce=false` and the
flag-only `--exclusive`. Each may occur at most once. Omitting `--exclusive`
means non-exclusive scheduling; no value-bearing, negative, or child-argument
spelling is equivalent. These are reproduction policies outside the normalized
recipe and execution identity. `--exclusive` does not change ordinary direct
execution or express a CPU, GPU, device, or host-affinity requirement.

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

### `pyrun.json` Output Bindings

For every decoded `pyrun.json` execution, validation derives one closed output
binding projection from `recipe.parameters` and `recipe.outputs`. It does not
read Python source or add a persisted binding field. Every declared output must
occur exactly once either as a child-process parameter value or as a leading
runner-owned stream-capture target. Equals-delimited option values bind as the
value after the first `=` while preserving the option prefix. Captures bind
directly, remain file-only, and are followed by the persisted `--` separator.

Two or more parameter or capture occurrences that canonicalize to the same
declared output are ambiguous even when only one occurrence was assigned an
output role during command discovery. No occurrence is missing. Either state
is the execution-owned Conformance finding
`pyrun.output.binding_invalid`. The remainder of a structurally decoded
`pyrun.json` remains independently evaluable; one defective execution does not
make the complete file undecodable.

A single spelling that canonicalizes to the declared output but is not its
canonical identity, such as `./data/result.csv`, has an unambiguous mechanical
binding but is still `pyrun.output.binding_invalid` in Conformance. This applies
equally to child arguments and capture targets. `pyrun` may execute and record
that invocation, but the attached `pyrun.output.binding_invalid` finding
excludes that execution from Reproduce until the authored spelling is
canonical. Unrelated selected executions remain eligible.

The live runner, this validation rule, and reproduction use the same binding
projection implementation. Before launching a child, `pyrun` rejects a missing
or ambiguous projection that is knowable from the parsed invocation. A fresh
successful publication therefore derives its recipe, output set, and binding
from the same parse. Validation detects malformed retained execution state and
authored-state problems. Markdown-to-JSON recipe disagreement remains a
separate Provenance conclusion, and undeclared generated artifacts remain under
the orphan rules. A script may still accept but ignore a valid output argument;
static binding validation makes no claim about that runtime behavior.

For every current Markdown command whose structural recipe resolves to a
recorded CID and parameter execution ID, validation also requires exact automatic-reproduction
and exclusive-scheduling policy agreement. The exact authored
`--auto-reproduce=false` option must map to `auto_reproduce: false`; omission
must map to true. The authored `--exclusive` flag must map to `exclusive: true`;
omission must map to false. Stale policy syntax, an unsupported value, or a
Markdown/JSON policy mismatch is a Conformance failure. Both policies remain
outside execution identity.

`pyrun` also accepts repeatable `--env NAME=value` runner options before the
required `--` separator. It normalizes them by name into the persisted
execution signature and child environment. Duplicate names, malformed names,
and runner-managed names are invalid. Each run receives fresh temporary
directories for `MPLCONFIGDIR` and `XDG_CACHE_HOME`. Each command also receives
a fresh, unique scratch directory under `/private/tmp`, assigned through
`TMPDIR` after authored values.
Scripts use `tempfile` without a hardcoded temporary root; children inherit the
environment and must finish before the ordinary wrapper returns. Scratch is
removed after execution and is separate from retained outputs, caches,
diagnostics, and checkpoints. Its assigned path and runner-added environment
are outside recipe identity. Reproduction supervision, confinement, cleanup,
and recovery follow the reproduction specification's Execution Safety
contract. Scripts relying on relative output argument spelling require repair.
Script filenames receive no command-argument provenance classification.

The exact entry-local `data` and `images` directories are shared artifact-tree
roots, not material artifacts or collections. An unclassified argument that
resolves to either exact root creates no candidate. A role or `data.json`
declaration targeting either exact root fails `material.root.invalid` or
`data.declaration.invalid`, respectively. Descendant files and exclusively
owned descendant directories retain ordinary material behavior.

### `pyrun` Output-Support Records

Current execution state is entry-root `pyrun.json`, with one effective-CID
bucket per authored command or bounded loop and one record per expanded
parameter identity. The state stores the effective CID as an ordinary string
and does not distinguish authored from derived CIDs.
Every fence has exactly one owner, and every CID is stable and unique across an
entry's split documents. The
[reproduction specification](research-log-reproduction-spec.md#pyrunjson)
owns its schema, execution identity, reproduction requirement, and publication
lifecycle.
Mechanical validation and Repair read execution state directly without
execution or mutation. They associate each current invocation with its exact
`(CID, execution ID)` identity, compare the complete recipe and policies
independently, then use an output-to-execution-owner index for output and
directory-member resolution. An invocation or output that has no exact current
association fails as `provenance.output.execution_unassociated`; validation
does not fabricate a legacy parameter vector. There is no projection from
current execution state to a legacy output model.

Validation reports malformed, missing, or structurally unassociated execution
members without mutation. Reproduce separately reports stale, recipe-changed,
and policy-relevant currentness. The parameter-only ID excludes CID, script,
environment, roles, declarations, captures, policies, fingerprints, and
observations; structural association and Reproduce currentness are therefore
separate conclusions. A pending v7 record may retain only applicable
observation subsets while `requires_reproduction` is true. A record with that
flag false requires a script observation and exact input/output observation
keys as part of the closed execution-state schema; its effective-code
observation remains nullable because unsupported analysis is an explicit
Reproduce-owned state.

Validation accepts only strict `research-log-pyrun/v7` state. An earlier schema
fails with `pyrun.state.schema.unsupported`; validation does not infer missing
policy, write execution state, or provide a migration path.

Data and evidence readers likewise accept only `research-log-data/v6` and
`research-log-evidence/v5`. Their older schemas have no retained decoder or
migration command. The one-time disposable conversion and the separate
execution-state compatibility boundaries are defined by
[Current Contract Cutover](research-log-reproduction-spec.md#current-contract-cutover).

#### Legacy Output Records

Mechanical validation may read `pyrun-outputs.json` when no current
`pyrun.json` exists. If both exist, it reports `pyrun.state.conflict` rather
than choosing or merging them. This compatibility path reads existing records;
it never writes, migrates, or confirms them. Ordinary `pyrun` and Reproduce do
not use it to execute research commands. It is separate from both the removed
data/evidence conversion support. There is no current-state projection for
targeted refresh.

The legacy file is a mapping keyed by exact output path. Each output has a
copy of its invocation support:

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
the fingerprint used by the run. These retained observations use the closed
local fingerprint forms selected by `data.json` identity. `code` maps at most 256 unique canonical
Python source identities to exact `sha256` file fingerprints. A path beneath
the command's entry is entry-relative. Every other eligible path is relative
to the maintained log and begins `<log>/`. A logical path through a symlink
beneath the log remains in that form rather than expanding to the symlink's
physical target. Paths are normalized, bounded, and end in `.py`.

The directly executed `script.path` is not duplicated in `code`. Existing
legacy maps retain the eligible helper files recorded when they were created;
the read-only compatibility path does not rewrite or relabel them. They remain
whole-file observations only inside this legacy format. Repeated imports and
aliases are deduplicated by resolved file identity while retaining one
canonical logical path. Every output from one invocation receives the same
complete `code` mapping. An empty mapping records that the legacy execution has
no retained helper dependency. Current v7 state does not contain this map.

Validation requires every logical code path to resolve to an existing regular
file and rejects two keys that resolve to the same file. A missing or non-file
target, or a duplicate resolved identity, is
`provenance.output.code_invalid`. Legacy validation compares current code fingerprints
with the recorded mapping when support currentness is relevant; a difference is a
`code` field in `provenance.output.signature_mismatch`. Code observations use
the shared fingerprint service and one resolved file observation is reused
across output records and logical aliases. When currentness is evaluated,
execution-linked stability checks re-observe the same files before the shared
evaluation completes.

A projected output record associates with a reconstructed invocation only when its
output identity, script path, ordered parameters, and direct input names
match. The reproduction requirement and output, script, input, and code fingerprints are
currentness rather than association fields. Raw script fingerprint differences
remain informational; output, input, and legacy code-map differences can enter
the legacy signature mismatch. Associated records for one
invocation must agree on their complete `code` mappings. Structurally valid
associated support adds one `code` input edge from each recorded file to the
invocation when that invocation enters the evidence-rooted graph. Thus an
associated record requiring reproduction or a record with stale fingerprints still
connects its helpers for orphan classification without changing validation checks.
Malformed, unavailable, inconsistent, or unmatched support adds no code edge
and suppresses no helper orphan.

#### Effective-Code Analysis

Before launch, `pyrun` parses the direct Python script and follows statically
reachable project-local behavior through ordinary imports, referenced
functions, methods, values, and resolvable child Python entrypoints. The
analysis may cross the entire current Git project and stops at that boundary;
external package implementation does not participate. It never imports or
executes project code and writes no graph, manifest, cache, or source copy.
A method absent from a project-local class hierarchy ends at the project
boundary when every base path is statically resolved and at least one reaches
an external base; the local call and its arguments remain in the normalized
syntax.

The fingerprint payload is deterministic project-relative normalized syntax.
Comments, whitespace, formatting, source positions, unrelated modules, and
unreachable definitions do not participate. Reachable bodies, defaults,
decorators, import-time statements, values, aliases, callbacks, resolved
methods including methods selected directly from a statically known
constructor, cycles, classic packages, and namespace packages do. A direct
`__file__` Python child invocation is the statically known current entrypoint.
The closed fingerprint algorithm is `python-effective-code-sha256-v1`.

Dynamic imports or generated code, runtime import-path mutation including an
explicit `PYTHONPATH`, wildcard imports, unresolved project-local attribute or
method dispatch whose complete base hierarchy cannot be proved to terminate
externally, and
unresolved child Python entrypoints make the whole analysis unsupported.
Unsupported analysis returns no partial fingerprint and does not fall back to
hashing a containing module. It retains at most 64 deterministically ordered
actionable locations. The analyzer reads at most 256 source files and 1 MiB per
source. Syntax, source identity/stability, project boundary, and resource-limit
failures are operational errors rather than unsupported language results.

Command sync succeeds when analysis is unsupported or fails operationally and
publishes bounded structured warnings naming the script, project-relative
location, line, construct or error code, and consequence. Ordinary `pyrun`
emits no warning for unsupported analysis, executes normally, and records
`effective_code: null`; an operational analysis failure prevents ordinary
publication. Reproduction analyzes current
source independently: a missing saved fingerprint or an unavailable current
fingerprint selects runnable work with the distinct
`effective_code_unavailable` diagnosis. Unfingerprintable code is selected on
each incremental plan because currentness cannot be established. The
[reproduction specification](research-log-reproduction-spec.md#selection-and-precedence)
owns that selection lifecycle.

For supported code, `pyrun` records one pre-launch fingerprint and recomputes
it after successful child execution. A mismatch or operational failure prevents
output/state publication. A failed run retains the prior whole execution; a
successful stable run atomically publishes the new raw script, effective-code,
input, and output observations. The raw script SHA-256 is retained as exact
source information but is not code-currentness authority.

`pyrun` resolves the command from its working entry and `data.json`. Successful
execution, stable inputs and code, and complete output observations are required
before publication. Replacement applies to whole executions and their complete
output sets, as defined by
[Atomic Publication And Replacement](research-log-reproduction-spec.md#atomic-publication-and-replacement).
Failed execution, capture, observation, or publication writes no record.

Ordinary output parameters use the existing mechanical input/output role
rules. Retained process streams use one of these forms:

```bash
./pyrun --capture-stdout "<stdout-log>" -- \
  scripts/run_study.py \
  --parameter value

./pyrun --capture-stderr "<stderr-log>" -- \
  scripts/run_study.py \
  --parameter value

./pyrun --capture-stdout-stderr "<run-log>" -- \
  scripts/run_study.py \
  --parameter value
```

`--capture-stdout` and `--capture-stderr` may be combined with distinct
targets. `--capture-stdout-stderr` is mutually exclusive with both. The
runner options and `--` may stay together on the `./pyrun` line; put the script
on the following line. A command without runner options may put the Python
script immediately after `./pyrun` and omit `--`. Line wrapping does not change
parsing. Captured bytes are mirrored to the corresponding
terminal stream. The shared stream pump drains each child pipe after an
individual destination fails. Failure to write or durably flush a declared
capture stops and reaps the child, prevents success publication, and leaves
any already-written output file in place. A closed terminal mirror disables
only that mirror, emits a warning when another terminal stream remains
available, and does not block a successful required capture or publication.
Pump joining and process termination are bounded. Raw shell redirection and
`tee` are outside the recorded-command grammar.

An existing record may contain `requires_reproduction: true`. Such a record
marks a current declared invocation that still requires successful execution.
It may carry only the still-applicable historical observation subset; absent
observations are unavailable history, not successful execution evidence. The
record still participates in validation's structural association, ownership,
lineage, and orphan rules. Its currentness belongs only to Reproduce. The next
successful `pyrun` execution replaces it with complete current observations and
`requires_reproduction: false`. Historical workflows with no record participate
in structural graph and orphan evaluation, but a reached generated output with
no structurally associated support still fails Provenance.

### Producer And Lineage Semantics

Provenance means that the validator can identify the artifact behind every
presented evidence item; classify that artifact as a declared origin or identify
its unique producer; associate each reached output with retained execution
support for a command signature still present in the entry; and follow every
generated producer input backward to origins. Current recipe and artifact
signatures are Reproduce currentness, not validation Provenance. Failure of a
structural link fails the starting artifact's Provenance.

Validation reports the complete bounded set of independently established
failures reachable from each starting artifact. A failure stops only the graph
edge that cannot be followed safely. Missing or ambiguous producers and
directory conflicts end their affected edge because no unique producer may be
selected; a cycle ends its repeated edge; other independent inputs, evidence
roots, and entries continue. Output-support failure does not hide the declared
inputs of a uniquely identified producer. Validation records the support
failure and continues through those inputs. Whole-log evaluation stops only
when malformed or unavailable log-wide state prevents safe graph construction.

Evidence and direct presentations begin graph traversal. The validator reuses
the already constructed shared research graph; it does not build a second
lineage model from output records. For each reached generated artifact:

- exactly one earlier command producer is required;
- its exact associated execution must structurally own the output;
- the output support, script, parameters, direct input names, and recorded code
  must satisfy their closed structural contracts;
- any reproduction requirement or unequal current signature is retained only
  as a Reproduce-owned currentness conclusion; and
- every direct input with `origin: false` recursively satisfies these rules,
  while `origin: true` stops that branch.

No earlier producer requires `origin: true`; one earlier producer requires
`origin: false`; several earlier producers fail as ambiguous; and a later
producer never supplies an earlier consumer. A selected producer with no
material inputs terminates successfully at its current artifact-producer
relationship. There is no command-level root, command type, filename-derived
root, or `provenance.root.missing` check.

Validation finds current command signatures through bounded static expansion of
the entry and all its subentry Markdown files. `pyrun` does not parse Markdown
or attempt to identify the command that called it. The enclosing entry of the
working directory identifies record ownership; subentries intentionally share that
entry-level command and output-support surface.

The resulting claim is bounded: the retained evidence artifact is connected to
declared origin artifacts by the mechanically visible command graph, and every
reached generated output has structurally associated retained support. It does
not claim that the output is current for the present script, inputs, parameters,
or output bytes, and it does not establish causation, complete dependency
capture, scientific validity, reproducibility, or the truth of undeclared
runtime state. Reproduction owns currentness and is not performed here.

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
- An origin directory is valid only when no structurally associated `pyrun`
  producer identifies its root or any member as generated. Its
  boundary reaches a consumed member
  through the explicit membership edge, not a path-prefix rule.
- A consumed generated directory must have one exclusive earlier
  `output-directory` at its root or an enclosing root, with every consumed
  member recorded among that producer's outputs. Competing directory or member
  producers fail exclusivity.
- One exclusive `pyrun` output-directory and its projected directory-level
  output-support record with the same script, parameters, and material input
  identities form one atomic artifact. The record may still require reproduction,
  and its output fingerprint may be stale; both are Reproduce-owned currentness
  conclusions and create no validation check. Every regular-file
  descendant observed by the record belongs to the artifact and its recursive
  fingerprint. Reaching the root or one exact member connects the complete
  bundle for ownership and Orphans without claiming that sibling members were
  consumed or presented.
- Declare every output directory in `data.json` and use its named token,
  including output-only bundles outside evidence closure.
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

### Evidence-rooted Orphans

The orphan universe remains bounded regular files under each entry root,
including first-class `data` and `images`, and excluding entry Markdown,
`evidence.json`, `data.json`, `retention.json`, `pyrun`,
`pyrun.json`, legacy `pyrun-outputs.json`, their recognized recovery backups,
validator output, research-log temporary paths, and runtime-cache descendants.

A `<project>/...` output outside an entry participates in Provenance and may be
registered as a generated input, but its location alone does not add it to the
entry Orphans or Retention universe. Validation does not scan project-wide
outputs for orphans or retention.

Connectivity starts only at evidence sources and direct presentations and
traces backward through unique producers and declared inputs. A command outside
this closure connects none of its scripts, inputs, outputs, or directory
members. Its atomic output directory remains one unreached artifact rather than
one artifact per descendant. An origin boundary terminates a reached branch but
never connects an unreached artifact or suppresses an orphan finding.

Each eligible standalone file or atomic generated output directory is
connected, declared-retained, or orphaned. An exact bundle-member edge remains
member-specific in the evidence and command graph, but it connects the complete
bundle membership for ownership and orphan classification.
The validation snapshot in `.cache/results.sqlite` records authoritative
artifact-level orphan findings. A named command input, named command output, or evidence reference counts
as registry use. Output declaration use does not connect an unreached artifact
to evidence. An unused data item produces one `orphan.input.unused` finding; unused
declarations are reported separately and do not inflate artifact counts.

Complete-graph output reconciliation produces one Provenance condition and one
Orphans condition. A current graph output whose file is absent is
`provenance.output.missing`: it breaks Provenance and is not an orphan finding.
A projected output-support record whose output key is absent from the complete
current graph is an unmatched output. If the file also exists, it is reported
only as `orphan.output.unmatched`, not again as an orphan. An unmatched
directory-output record suppresses descendant orphan findings and produces one
finding at its root. An existing file outside the current graph with no output
record is an ordinary orphan.

| Current graph output | Current file | Output record | Result |
| --- | --- | --- | --- |
| yes | no | either | Provenance finding: missing output |
| no | either | yes | Orphans: unmatched output |
| no | yes | no | Orphans: orphan output |

An output present in the complete graph but outside the evidence-rooted closure
is an orphan unless retained. An atomic output directory produces one root
orphan rather than descendant findings. Its record is not unmatched because
the graph still identifies its current producer.

`validation.md` reports one Orphans finding count that combines orphan
artifacts, unmatched outputs, and unused input declarations. Their distinct
machine-readable findings and repair context remain in `.cache/results.sqlite`.
Internal orphan-target metadata may compact context for maximal all-orphan
directories below, but never equal to, the owning entry root. Starting with each child
directory, collapse the highest directory whose every eligible file is
orphaned; otherwise recurse in normalized lexical order. Root-level files
remain individual findings. Mixed directories retain individual files or
smaller target groups. Atomic output-directory collapse occurs before this
ordinary context compaction and always uses the declared output root. No
artifact appears twice, and every orphan artifact remains its own finding.

The internal target-group identity is `orphan-group:` plus lowercase SHA-256
of canonical JSON for `[maintained-log identity, entry material owner,
normalized entry-relative directory]`. The opaque identity is not a public
validation entity or batch key and creates no graph edge, retention, or
collection. The mechanically proven collapsed directory coordinate separately
supplies the canonical material repair key described in [Published Validation
And Repair Batches](#published-validation-and-repair-batches), which joins its
member findings into one repair batch.

### Provenance Truth Table

| Data item and token | Earlier producers | Origin | Producer support | Result |
| --- | --- | --- | --- | --- |
| Missing item or raw input | any | any | any | Fail undeclared or missing-token validation before lineage. |
| Declared and used | 0 | yes | n/a | Terminal origin after current fingerprint validation. |
| Declared and used | 0 | no | n/a | Fail `lineage.missing`. |
| Declared and used | 1 | no | missing | Fail Provenance and continue through the unique producer's declared inputs. |
| Declared and used | 1 | no | requires reproduction or unequal | Record a Reproduce-owned currentness condition and continue through the unique producer's declared inputs; create no validation check, finding, blocker, or batch. |
| Declared and used | 1 | no | structurally associated support | Trace to the unique producer's inputs; retain any currentness conclusion only for Reproduce. |
| Declared and used | 1 | yes | structurally associated producer | Fail `data.origin.invalid`; reproduction currentness does not change the ownership conflict. |
| Declared and used | more than 1 | either | n/a | Fail `lineage.ambiguous`. |
| Declared but unused | any | either | n/a | Report `orphan.input.unused`; create no graph edge. |
| Reached producer | n/a | n/a | any support state, no inputs | Record any support failure and terminate at the artifact-producer relationship. |
| Reached producer | n/a | n/a | unresolved candidate | Fail `material.candidate.unresolved`. |
| Reached producer | n/a | n/a | any support state, one or more inputs | Record any support failure and follow every declared input under the rows above. |

### Directory Truth Table

| Use | Producer state | Boundary | Result |
| --- | --- | --- | --- |
| Whole `input-directory` | no root/member producer | origin | Consume all fingerprinted members through the aggregate boundary. |
| Exact member | no root/member producer | origin | Consume only that member; siblings stay disconnected. |
| Whole directory or subdirectory | one exclusive earlier `output-directory` at that root or an enclosing root | absent | Require every consumed member in the producer's recorded outputs; trace those members to that producer and use its owning-root support record. |
| Exact member | one exact earlier `output-directory` | absent | Trace only that member and connect the atomic root for orphan classification; do not claim sibling consumption. |
| Workflow outside evidence closure | exact exclusive `output-directory` with matching directory support | absent | Declare the output-only directory in `data.json`, use its named token, and treat it as one atomic artifact. |
| Any directory | competing directory or member producers | either | Fail `directory.producer.conflict`; a sole enclosing owner is not competition. |
| Generated directory | no covering earlier directory producer or missing owned member | absent | Fail `directory.producer.conflict` for a consumed directory; do not infer ownership from filesystem containment alone. |
| Origin directory | structurally associated root/member producer | present | Fail `directory.origin.conflict`. |
| Any directory | selected membership/content differs from a retained execution observation | either | Retain a Reproduce-owned currentness conclusion; a declaration alone has no accepted digest. |
| Workflow outside evidence closure | atomic output directory | absent | Report one root-level orphan unless the complete bundle is retained. |
| Workflow outside evidence closure | other directory | any | Members remain orphan-eligible unless retained. |

### Diagnostics

For `material.candidate.unresolved`, retain the unresolved argument selectors
and values, command location, and successfully resolved output declarations as
typed ambiguity observations and command-owned diagnostic context. Rejected
commands remain outside established producer edges. A validation finding may
reference one only through explicit `IssueContext`, such as an exact output
material, owning directory, command location, or candidate node; ambiguity
never becomes a successful relationship or an implicit batch key.

An authoring operation retains its primary error code and prints bounded
diagnostic detail. Recorded-command discovery, declaration, retirement, and
rejected-producer diagnostics belong to the independent command-diagnostic
domain and are retrieved through `log command show`. They are not validation
snapshots, finding or chain views, or generic exports. A cache failure preserves
the original authoring error without dumping the complete payload;
`--dry-run` writes no command diagnostic.

| Code | Finding type or attempt effect | Condition |
| --- | --- | --- |
| `data.file.location_invalid` | conformance | `data.json` is outside one entry root or a parent/log-level surface exists. |
| `data.declaration.invalid` | conformance | A data file, item, field, identity, boundary, or bound violates the closed contract. |
| `data.name.duplicate` | conformance | One entry repeats a name. |
| `data.target.duplicate` | conformance | One entry repeats a canonical target through any alias. |
| `data.declaration.conflict` | conformance | Entries disagree on one target's kind, identity, or boundary. |
| `data.input.undeclared` | provenance | A proven input has no item, including an unknown token. |
| `data.input.token_missing` | conformance | A proven input uses a raw location instead of its item token. |
| `data.git.projection_missing` | conformance | A repository-consuming command omits its locator or commit projection. |
| `material.candidate.unresolved` | conformance | A path-like or dynamic material candidate has no proven role. |
| `material.root.invalid` | conformance | A command role targets the exact shared entry `data` or `images` artifact root. |
| `data.origin.invalid` | provenance | An origin boundary hides a structurally associated `pyrun` producer. |
| `data.target.missing` | provenance | A local input or selected member is absent. |
| `data.fingerprint.unobserved` | provenance | Generated material has no retained successful execution observation where one is required. |
| `data.fingerprint.mismatch` | provenance | Current material differs from the retained observation of an affected execution. |
| `directory.membership.invalid` | provenance | Membership is unsafe, aliased, unsupported, or over-bound. |
| `directory.producer.conflict` | provenance | A generated directory lacks one exclusive earlier producer covering its root and consumed members. |
| `directory.origin.conflict` | provenance | An origin directory root or member has a structurally associated `pyrun` producer. |
| `pyrun.outputs.invalid` | provenance | A legacy `pyrun-outputs.json` file or record violates its closed schema. A malformed shared file that cannot be associated reliably with individual bindings produces one entry-owned source finding. |
| `pyrun.outputs.unavailable` | failed check | Current output-support state cannot be read reliably. |
| `pyrun.output.identity_invalid` | provenance | A `pyrun` output cannot map to one permitted entry-relative or `<project>/...` record key. |
| `pyrun.output.binding_invalid` | conformance | One decoded execution has a missing, ambiguous, noncanonical, or otherwise invalid output binding. |
| `provenance.output.unrecorded` | provenance | A reached generated output has no output support record. |
| `provenance.output.execution_unassociated` | provenance | A current execution-state output cannot be associated with the current producer invocation. |
| `provenance.output.reproduction_required` | Reproduce currentness, not a finding | A reached generated output still requires reproduction. |
| `provenance.output.signature_mismatch` | Reproduce currentness, not a finding | Current output, script, parameters, direct inputs, or recorded code differ from the current record. |
| `provenance.output.code_invalid` | provenance | A recorded code path is unavailable, is not a regular file, or duplicates another resolved code identity. |
| `provenance.output.signature_unsupported` | provenance | A reached producer input cannot be represented in the exact record signature. |
| `provenance.output.missing` | provenance | The current graph declares an output whose artifact is absent. |
| `provenance.observation.unavailable` | failed check | Execution-linked bytes changed during one localized check or could not be re-observed. |
| `retention.file.location_invalid` | conformance | `retention.json` is outside one entry root. |
| `retention.declaration.invalid` | conformance | A retention file or record violates shape, path, overlap, eligibility, or redundancy. |
| `retention.target.missing` | conformance | A retention target is absent. |
| `orphan.material.unused` | orphan | One retained artifact lies outside the evidence closure and retention. |
| `orphan.input.unused` | orphan | One data item has no named command input, named command output, or evidence use. |
| `orphan.output.unmatched` | orphan | An output support record has no output in the complete current graph. |

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
   to recorded execution or legacy output support, then follow mechanically
   proven upstream inputs, explicit origins, and required directory membership
   within that closure;
6. collect execution-currentness conclusions for Reproduce without projecting
   them into validation checks;
7. reconcile the complete graph with current files and output-support records,
   reporting absent graph-declared outputs as Provenance findings and records
   absent from the graph as orphan findings; and
8. compose evidence and Provenance outcomes, then classify remaining material
   as connected, declared-retained, or orphaned for orphan classification.

A malformed owned entry or entry-local command surface produces an entry-owned
Conformance finding and does not prevent unaffected entries from continuing
through evidence, provenance, and orphan evaluation. Validation stops the
whole log only when the maintained summary or another log-wide structure
cannot be read or interpreted safely.

A failed prerequisite is not restated as several speculative mismatches. A
validation finding or localized validator failure leaves its dependent checks
`blocked`, with root finding or failed-check IDs retained once. Independent
checks continue.

One confirmed defect at one natural rule subject produces one canonical finding
check and therefore one finding. In particular, repeated evidence-record consumption of
the same provenance defect does not create record-specific copies.
Private consumer checks depend on the canonical check, while the shared
research graph retains every affected evidence record and downstream
relationship. Different rules at one subject and the same rule at different
subjects remain distinct. This is direct evaluator ownership, not
presentation-time deduplication.

Output-support rules are owned by the exact producer/output declaration. A
file declaration owns its file. A directory declaration owns its directory
root, so one unsatisfied support rule produces one finding even when validation
reaches it through several member files. Collection membership, evidence use,
and downstream consumption remain graph relationships available to Repair and
Reproduce; they do not create member-specific copies of the finding.

A malformed shared output-support file is the prerequisite exception. When it
cannot be decoded well enough to associate failures with exact bindings, it
produces one entry-owned source finding. Dependent producer/output checks are
`blocked` against that finding; validation does not copy the same source
diagnostic onto each binding.

### Canonical Attempt, Finding, Batch, And Snapshot Model

Validation has one internal model with three one-way layers:

1. Evaluation mechanics produce private `RuleCheck` values and one
   `ValidationAttempt`. A check records its rule area, outcome, subject,
   dependencies, optional diagnostic, and typed issue context. Passing checks
   remain private.
2. One conversion turns `finding` checks into `Finding` values, groups them into
   `Batch` values, projects blocked and failed checks into their separate saved
   rows, and creates a `ValidationSnapshot` after successful operation
   completion. A finding's existence means an authored defect; it has no
   status.
3. Persistence, reports, queries, Repair, and Reproduce consume the completed
   snapshot. They must not reinterpret private checks or reconstruct repair
   relationships from diagnostic prose.

`RuleCheck.area` and `Finding.type` use the closed values `conformance`,
`evidence`, `provenance`, and `orphan`. Evaluation target scope is independent:
it is either the full maintained log or one stable entry. An existing check ID
becomes the finding ID for that unsatisfied rule; there is no second finding-ID
namespace.

Every finding or failed check has one diagnostic containing the exact code,
subject, rule, and bounded observed state. A finding check becomes exactly one
finding. Its typed `IssueContext` records the exact entry owner when known,
source locations, strong repair keys, relevant graph nodes, and a private
admission owner. These values are captured where the evaluator has the typed
record, command, execution, material, or registry object. A later projection
must not derive them by searching IDs, paths, observed JSON, or prose.

A finding or failed check leaves dependent applicable checks blocked. Every
blocked row traces transitively to at least one root finding or failed check.
`caused_by` is reserved for confirmed finding-to-finding relationships.

One completed snapshot has outcome `clear`, `findings`, or `failed`, exact
target and source identity, rules version, timing, pass count, canonical
findings, batches, blocked rows, failed rows, and bounded repair and report
context. `clear` means zero findings and zero failed checks. `findings` means at
least one finding and zero failed checks. `failed` means at least one localized
validator failure. Blocked checks do not select the snapshot outcome.

### Shared Research Graph

The evaluator builds one neutral `ResearchGraph` under the log lock from the
already loaded research objects and filesystem observations. Validation and
Reproduce consume this graph; neither owns a second reconstruction.

Graph nodes have composite typed identities for physical entries and documents,
evidence records and presentations, data and retention records, recorded
commands, persisted executions, canonical materials, finite collections,
scripts, and observed code. Established edges represent declaration,
presentation, command-to-execution binding, production, consumption, origin,
retention, membership, script use, code use, and execution dependency. Zero or
multiple producer candidates, rejected command candidates, and unresolved
bindings are typed ambiguity observations, never successful edges.

The graph builder owns canonicalization, bounded inventory, candidate indexing,
and established topology only. Validation owns rule conclusions and Orphans
classification. Reproduce owns currentness, target selection, policy
boundaries, admission, and scheduling. A graph capacity bound aborts the whole
validation operation and preserves the prior snapshot; partial graph state is
not published as a validation finding.

### Evaluation Outcomes

| Condition | Finding type or attempt effect |
| --- | --- |
| Malformed, missing, oversized, unsupported, or ambiguous authored structure | Confirmed `conformance` finding; affected dependents are blocked. |
| Missing/conflicting evidence declaration or presentation mismatch | Confirmed `evidence` finding. |
| Missing, ambiguous, or conflicting producer, lineage, execution, origin, input, output, or collection relationship | Confirmed `provenance` finding. |
| An output requires reproduction or its recorded signature is no longer current | Reproduce-owned currentness conclusion. No validation check, finding, blocker, or repair batch is created. |
| Unused material, unused input declaration, unmatched output record, or obsolete generated-validation artifact | Confirmed `orphan` finding. |
| Localized transient access, reader/library, cache/database, permission/device, or I/O failure | One failed check; affected dependents are blocked and the completed failed snapshot is saved. |
| Capacity exhaustion, publication failure, or source mutation across the operation boundary | Whole validation operation fails; no snapshot replaces the latest saved snapshot. |
| Scientific validity, interpretation, claim support, or summary meaning | Semantic Review; no mechanical finding. |
| Ability to rerun and reproduce a workflow | Reproduction; no standard-validation finding. |

Every finding names the stable code, exact subject, bounded observed state,
violated rule, explicit entry when known, failed prerequisites, and mechanically
known repair context. Finding reporting does not generate proposed edits or
repair scaffolds.

Deterministic missing/type/format conditions use precise failure codes.
`association.resource.too_large`, `provenance.resource.too_large`, and other
stable contract bounds are Conformance findings. Localized operational variants of
`source_changed`, `locator.source.changed`, `locator.reader.unavailable`,
`association.document_unavailable`,
`association.artifact.inline_source_unavailable`, and
`provenance.observation.unavailable` are failed checks. A declared source absent
at initial observation is a finding; a source change across the controller's
operation boundary fails the operation without publication.

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
9. recorded output, script, parameter, input, and code-support structure; and
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

The same evaluation records output, script, parameter, input, and code
currentness for Reproduce. Those conclusions do not alter validation
applicability or check outcomes.

### Public Management And Validation Operations

The public entrypoint is the extensionless `scripts/log` resolved from the
active research-logging skill package. Direct path-qualified invocation is
canonical. `pyrun` remains the separate recorded execution wrapper.

Maintained-log discovery is a separate read-only operation:

```text
<skill>/scripts/log discover --root PROJECT
```

It performs bounded discovery beneath one regular non-symlink project root and
returns the sorted maintained-summary paths. Discovery is not validation and
does not read or interpret candidate Markdown.

The validation grammar is:

```text
<skill>/scripts/log validate run (--path LOG [--entry ENTRY] | --root PROJECT)
  [--recompute] [--recompute-validation]
  [--recompute-fingerprints] [--dry-run] [--format text|json]

<skill>/scripts/log validate show (--path LOG | --root PROJECT)
  [--format text|json]

<skill>/scripts/log validate list findings --path LOG [--entry ENTRY]
  [--type conformance|evidence|provenance|orphan]
  [--limit N] [--cursor CURSOR] [--format text|json]

<skill>/scripts/log validate list batches --path LOG [--entry ENTRY]
  [--limit N] [--cursor CURSOR] [--format text|json]

<skill>/scripts/log validate list blocked --path LOG [--entry ENTRY]
  [--limit N] [--cursor CURSOR] [--format text|json]

<skill>/scripts/log validate list failed --path LOG [--entry ENTRY]
  [--limit N] [--cursor CURSOR] [--format text|json]

<skill>/scripts/log validate detail finding --path LOG [--entry ENTRY]
  --id FINDING_ID [--section repair_keys|nodes|relationships|ambiguities]
  [--limit N] [--cursor CURSOR] [--format text|json]

<skill>/scripts/log validate detail batch --path LOG [--entry ENTRY]
  --id BATCH_ID
  [--section findings|repair_keys|nodes|relationships|ambiguities]
  [--limit N] [--cursor CURSOR] [--format text|json]

<skill>/scripts/log validate render --path LOG
```

`--path` names the logical `LOG` base whose `LOG.md` summary and `LOG/`
root are both present. `run --root` validates every bounded discovery result
with per-log failure isolation. `show --root` reads each discovered log's latest
completed full-log snapshot and never validates. An omitted `--path` never
means all logs. `--entry` is available only on `run --path`, `list`, and
`detail`; it selects one stable physical entry.

Root-scoped text tables render the `Log` column as each logical log's final
directory name. Text tables render `Saved` as the compact UTC calendar date
`Mon D`. JSON rows retain the canonical full logical-log path and exact saved
timestamp; single-log text output also retains the full path.

`run` is the only validation operation that observes research sources.
`show`, `list`, `detail`, and `render` are read-only with respect to
research sources. `show` accepts no diagnostic selector. `list findings`
accepts only the optional public `--type` category selector. Batches have no
single type. There are no public `log results`, top-level `log findings`, or
compatibility aliases.

`--recompute-validation` bypasses validation selection reuse,
`--recompute-fingerprints` bypasses fingerprint reuse, and `--recompute`
requests both without changing rules or scope. Dry-run evaluates the same
target without saving a snapshot or report. Text is the concise default;
structured callers request `--format json`. Validation timestamps are observed
by the evaluator; `validate run` accepts no user-supplied date.

This grammar is the only supported validation command contract.
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
requires exactly one `## Entries` section and consistent IDs, dates, canonical
document links, entry directories, and entry documents within that inventory.
Malformed or ambiguous rows, duplicate IDs or targets, mismatched physical
state, and occupied new targets fail. Summary row order, H1 wording, and
validation/reproduction navigation wording or placement are not allocation
preconditions. The command allocates one above the highest reliable physical ID
without filling gaps; creates the minimal canonical entry document and a
relative symlink to the active package's verified `pyrun`; and appends only the
new summary item. The summary commits last. Ordinary publication failures roll
back, while recognizable interruption residue fails closed for explicit
Repair. Neither operation repairs or changes unrelated summary prose,
interpretation, follow-ups, optional support material, or generated validation
state.

Authoring actions may omit `--path LOG` when the working directory has exactly
one maintained ancestor log. No match or multiple matches fails with an
informative context error; use the explicit logical log base to disambiguate.

The Record and Repair authoring contracts are:

### Command Synchronization

```text
log command sync [--path LOG] --entry ENTRY --cid CID
  [--add-origin NAME=PATH]...
  [--add-origin-directory NAME=PATH]...
  [--add-origin-git NAME=COMMIT:PATH]...
  [--add-generated NAME=PATH]...
  [--add-generated-directory NAME=PATH]...
  [--add-from-entry NAME=ENTRY]...
  [--change-target NAME=TARGET]...
  [--delete-execution EXECUTION_ID]...
  [--dry-run]
```

The selector takes the full effective CID stored in normalized state. A
recorded `pyrun` invocation may omit `--cid` and derive the CID from its Python
program stem, or use numeric shorthand to derive `PROGRAM_STEM-N`. A full CID
in Markdown remains the explicit override.

The add forms are idempotent ensure operations:

- `--add-origin` and `--add-generated` declare regular files;
- the `-directory` variants explicitly declare directories, including a
  generated directory that does not exist yet;
- `--add-origin-git NAME=COMMIT:PATH` declares a Git origin and revision as one
  target; and
- `--add-from-entry NAME=ENTRY` declares a same-name reference to a generated
  artifact in another entry.

`--change-target` changes a path or `COMMIT:PATH` only when the declaration is
local to the selected command. Shared target changes route to `log data
update`. Kind, boundary, directory identity, `reproduction_comparison`, rename,
deletion, and cross-entry source replacement also belong to `log data`.

`--delete-execution` removes a named stale execution. If its Markdown
invocation remains, sync recreates it as pending with
`requires_reproduction: true`. A current valid execution cannot be deleted
through this option.

### Evidence Comparison And Synchronization

```text
log evidence compare [--path LOG] --entry ENTRY --id ID

log evidence sync [--path LOG] --entry ENTRY --id ID
  [--add-origin NAME=PATH]...
  [--add-origin-directory NAME=PATH]...
  [--add-from-entry NAME=ENTRY]...
  [--change-target NAME=PATH]...
  [--dry-run]

log evidence compare [--path LOG] --entry ENTRY --source NAME

log evidence sync [--path LOG] --entry ENTRY --source NAME
```

The ID-scoped form reads the complete definition from the Markdown `eid`
comment. Compare shows only that EID and its exact before and after presented
Markdown. Sync validates and normalizes the definition into `evidence.json`,
evaluates it, and replaces the adjacent placeholder or prior presentation.

ID-scoped compare and sync validate only the selected evidence item's markers,
definition, presentation, and surrounding Markdown context. Its marker must be
unique across the entry's owned documents. Invalid unrelated evidence markers
or definitions do not block the operation; whole-document evidence validation
belongs to `log validate`.

Summary refresh validates and updates only references forwarding the selected
evidence. Malformed references to unrelated evidence remain untouched.

The ID-scoped add forms have the same ensure behavior as command sync and must
be consumed by the candidate evidence definition. Evidence may add a file or
directory origin or a same-name cross-entry reference. It cannot create a
generated declaration; an unknown generated source must direct the agent to
author and synchronize its producer command. `--change-target` is limited to a
declaration used only by that evidence record.

The source-scoped form (including --dry-run on sync) selects one direct generated declaration in its owning
entry, follows same-log references, and finds every evidence record that uses
the artifact. Compare returns each EID with only its exact before and after
presentation. Sync reevaluates the same set from current state and atomically
updates:

- every related Markdown presentation;
- any summary value that forwards one of those presentations; and
- every applicable path-based `artifact_fingerprint`.

One invalid or unstable related record rejects the source-scoped sync without
partial publication. When a presentation is unchanged, sync leaves its
Markdown bytes alone and still refreshes the applicable fingerprint. Compare
creates no stored preview or comparison ID, and sync does not require a prior
compare result.

Every successful path-based artifact sync accepts the currently observed
artifact bytes and refreshes `artifact_fingerprint`. There is no separate
baseline flag and no caller-supplied digest.

#### Evidence Markdown Forms

The `eid` comment is the durable definition. It names the evidence ID, sources,
selection, bounded presentation rules, and optional
`reproduction_tolerance`. Derived expectations remain normalized tool-owned
state.

Supported forms are:

- a scalar or short value in an adjacent inline-code span;
- artifact evidence;
- text output from one UTF-8 source using `line=N`, `lines=N:M`, or
  `line=N chars=A:B`, with one-based inclusive positions and Unicode code
  points;
- a closed compound scalar—range, tuple, interval, or plus/minus—from ordered
  sources; and
- a direct Markdown table backed by one table-shaped artifact.

A new scalar starts with an empty code span. A new direct table has its authored
header and alignment row but no body. Its Markdown headings determine display
labels and order; the comment maps each heading position to one source field
and bounded transformation and may select rows. A new text-output record starts
with an empty `text` fence, and sync replaces only the fence body.

A summary table is ordinary Markdown whose evidence-bearing cells have separate
scalar or compound EIDs. Every numeric or closed-Boolean data cell must be
independently marked; partial marking does not replace a table declaration.
A joined or derived table requires a recorded script
that emits a retained table-shaped artifact, followed by direct-table evidence.
Neither pattern adds a summary, join, formula, or advanced-table feature to the
CLI.

The current schema is `research-log-evidence/v5`. It contains only
the supported forms above. Reject v4; do not add a compatibility reader,
converter, definition-file path, or generic advanced definition form.

### Graph-Level Data

```text
log data update [--path LOG] --entry ENTRY NAME
  [--target TARGET]
  [--boundary origin|generated]
  [--kind file|directory]
  [--identity IDENTITY]...
  [--reproduction-comparison exact|evidence]
  [--acknowledge-shared]
  [--dry-run]

log data rename [--path LOG] --entry ENTRY OLD NEW [--dry-run]
log data delete [--path LOG] --entry ENTRY NAME [--dry-run]
log data list [--path LOG] --entry ENTRY
```

`IDENTITY` is `byte-complete`, `file:PATH`, or `pattern:GLOB`. Supplying
identity values replaces the directory identity policy. `TARGET` is a plain
path or `COMMIT:PATH` for a Git origin. This single target form covers Git
revision and repository-path changes.

Data v6 persists the optional policy as `reproduction_comparison`. `exact` clears the
optional field and restores exact artifact comparison; `evidence` delegates
reproduction comparison to evidence records, whose comments may define
`reproduction_tolerance`.

`data update` preserves omitted properties. A declaration with several
consumers requires `--acknowledge-shared`; the first rejection lists those
consumers. The acknowledgment accepts the wider scope but does not bypass
validation.

For rename, the agent edits every Markdown use first. `data rename` fails with
remaining old-name uses or missing replacements, then updates the declaration
and normalized references only when the Markdown migration is complete. For
delete, the agent removes uses and synchronizes their owners first. `data
delete` fails with every remaining use and never deletes material from disk.
Existing material left disconnected is reported for a subsequent retention or
filesystem decision.

Data rename validates Markdown evidence definitions only for records using the
renamed declaration. Unrelated invalid evidence does not block the rename.

`data list` exposes maintained semantic declaration properties needed for
Record and Repair: name, direct or cross-entry form, target or source entry,
kind, boundary, directory identity, Git revision, and
`reproduction_comparison`. It need not expose derived serialization details or
historical digests.

### Retention

```text
log retention add [--path LOG] --entry ENTRY --id ID
  --target TARGET [--target TARGET]... [--reason TEXT] [--dry-run]

log retention update [--path LOG] --entry ENTRY --id ID
  [--add-target TARGET]... [--remove-target TARGET]...
  [--reason TEXT | --clear-reason]
  [--dry-run]

log retention rename [--path LOG] --entry ENTRY OLD NEW [--dry-run]
log retention delete [--path LOG] --entry ENTRY --id ID [--dry-run]
log retention list [--path LOG] --entry ENTRY
```

Retention remains separate from data. Add is an idempotent ensure. Update is
additive and preserves omitted coverage and reason. Retention rejects targets
currently connected to command or evidence state and overlap with another
retention decision. Removing coverage or a retention record never deletes its
targets; existing material left disconnected is reported for the agent's next
action.

### Command And Evidence Lifecycles

```text
log command rename [--path LOG] --entry ENTRY OLD NEW [--dry-run]
log command delete [--path LOG] --entry ENTRY --cid CID [--dry-run]
log command list [--path LOG] --entry ENTRY

log evidence rename [--path LOG] --entry ENTRY OLD NEW [--dry-run]
log evidence delete [--path LOG] --entry ENTRY --id ID [--dry-run]
log evidence list [--path LOG] --entry ENTRY
```

For rename, the agent edits the Markdown CID or EID first. The lifecycle action
verifies that the old identity is gone and the new identity is present before
updating normalized state and references.

Evidence rename and delete validate only markers and summary references for
their selected IDs. Unrelated invalid markers, definitions, and references do
not block either action. Registry files must still satisfy their data contracts
before publication; focused edits do not repair unrelated records.

For evidence deletion, the agent removes the presentation and marker first.
The delete action removes only the evidence record and reports newly unused
data for a separate `data delete` decision.

For command deletion, the agent removes the command block first. The delete
action fails while downstream consumers use its outputs, removes its execution
records and command-exclusive generated declarations together, and reports
output material left disconnected. It never deletes output files.

The list actions expose the maintained semantic state needed to choose a normal
Record or Repair action. Preserve existing `command verify` and `command show`
as operational utilities.

#### Compact Evidence Definition Language

Comments use shell-quoted key=value tokens separated by whitespace. A semicolon
starts a new source or table-column clause; it does not introduce an expression.
Each source starts with source=NAME or source=NAME/member. The ID and complete
definition are durable Markdown input, not a separate file or YAML/JSON comment.
A source may declare path, repeated select and identity pointers, repeated where
conditions, and bounded parse/render/scale/magnitude/sign. Pointers use JSON
Pointer escaping, numeric indexes, /* expansion, and /[START:END] half-open
slices. where=POINTER:eq:TYPE:VALUE or :in:TYPE:VALUE,VALUE supports string,
integer, decimal, boolean, or null; comma-containing in-set strings use percent
encoding. Numeric CSV predicates explicitly parse decimal/integer text.

Global form defaults to scalar and supports scalar, percentage, range, tuple,
interval, and plus_minus. One-source render=boolean:STYLE creates short Boolean
evidence. Global unit applies to a compound value. Each source contributes its
selected operands in observed order; rendering fields may be uniform or one per
operand. Numeric render is integer, grouped_integer, fixed:N, scientific:N, or
significant:N. scale is finite decimal, magnitude is true, and sign is always.
The closed percentage form accepts fixed:N and consumes a retained proportion.
reproduction_tolerance=ABSOLUTE_DECIMAL is optional and strictly positive.

A direct table source uses path/identity/where plus one column=POINTER clause
per Markdown header column, in that order. Columns replace source select fields.
Column recipes support text, numeric rendering with optional parse/scale/
magnitude/sign/unit, percentage:N, and boolean:STYLE with optional parse=boolean.
Source and column shape/cardinality must agree. No derived columns or joins exist.
Text output uses only source and line=N, lines=N:M, or line=N chars=A:B.
A whole artifact uses only source and no locator/transform fields.

Sync derives locator expectations and artifact fingerprints; no caller-controlled
expectation or fingerprint field is accepted. Validation compares the authored
definition with maintained normalized state before evaluating it and reports
evidence.definition.unsynchronized when they differ. Definition comparison
normalizes implied root paths and default percentage precision, but never hides
semantic changes. Empty value spans, table bodies, and text fences are valid sync
placeholders, not completed validation evidence.

All assertions and deltas are preflighted against valid current registries.
Coupled publication uses existing recovery guards. Failures and dry-run change
no owned file. Source observations are rechecked before acceptance. Shared data
and graph-level lifecycle actions lock the log before affected entries; a source
refresh waits a bounded ten seconds for overlapping operations before reporting
contention. Operations leave generated validation state unchanged and never
implicitly execute research. Direct JSON editing is reserved for explicitly
authorized malformed state the owning decoder cannot handle.

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
IDs simultaneously, including incoming `from_entry` declaration references.
Entry update and reorder inspect the data and evidence declarations needed for
that coordinated mapping but do not hash unrelated registered material.
`relocate-log` moves the maintained summary/root pair only within one filesystem
and inspects only data declarations whose relative locators may need rewriting.
These identity operations preserve retained `pyrun.json` recipes and observations
byte-for-byte. `remove-empty-entry` requires the summary item to be absent and
the remaining scaffold to be mechanically empty.

Evidence and retention structural decoders own their complete declaration
grammar separately from current document and target context. `transfer` uses
those shared decoders and may defer current source context only for its exact
selected record IDs. Unselected records pass their old context before mapping;
the complete mapped source and destination candidates pass ordinary strict
context before publication.

Transfer observes only selected data material and sources reached by moved
evidence. It reuses an affected resource observation within the operation,
compares non-artifact selections with their destination presentation, and
compares path-based artifacts with the preserved evidence-owned exact-file
baseline. Selected generated data must match its exact recorded source execution
output observation. Same-log consistency compares declarations rather than
current hashes, so an unrelated external byte change is left for explicit
validation. A locator change resolving to the same selected content remains
content-equivalent for execution currentness but does not assert that a
historical execution used the new locator.

Empty authored registries are removed. Legacy `pyrun-outputs.json` is never
relocated or rewritten to describe a new execution. The `pyrun`-owned service
may retire only exact source support made stale by the selected transfer, and
the result reports the destination reruns needed to create new support. It never
rewrites a retained observation as if it ran under a destination entry, name, or
locator.

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
bounded failed response. Validation and discovery retain their own response schemas
and exit-status contracts.

Research operations coordinate through generated locks beneath
`<log>/.cache/research-log-operations/`. The stable `log.lock` supports shared
and exclusive nonblocking acquisition. Entry-scoped maintained mutations hold
it shared before taking their stable-ID entry lock exclusively. Command sync,
evidence sync, and ordinary `pyrun` use short snapshot and publication
transactions: command preparation, hashing, evidence extraction, and child
execution hold no entry/log OS locks. Their short entry transactions allow up
to ten seconds for acquisition; a remaining conflict fails without publication.
Log-wide mutations hold `log.lock`
exclusively before taking affected entry locks in sorted ID order. Initial log
creation instead uses a lock beneath the owning project's
`.cache/research-log-operations/`, keyed by the intended canonical log path.
Recognized Reorganize and entry-keyed authored-registry transaction residue
require explicit Repair and block applicable later operations.

Sync checks the selected Markdown definition and relevant declarations against
its preparation state before publication. A relevant concurrent change fails
with `authoring.state.changed` and directs the caller to review it and rerun
sync. Consistent redundant publication is accepted. Unrelated declaration,
evidence, command, and prose changes are preserved by merging into freshly read
registries and rebinding selected Markdown regions, not by installing a stale
whole-file candidate. Evidence publication additionally holds the brief
log-local `summary.lock` while refreshing selected forwarded values; it never
holds an exclusive whole-log lock during extraction.

Evidence compare/sync refuse sources reserved by an ordinary output writer.
Data identity/location changes and deletion refuse boundaries in use by an
ordinary execution, including directory descendants; comparison-policy-only
changes remain possible. See the ordinary
[artifact reservation contract](research-log-reproduction-spec.md#ordinary-artifact-reservations).

An acquired operation lock publishes bounded JSON owner metadata beside the
lock as `<lock>.owner.json`. It identifies the operation, scope, process,
request and source fingerprints, and start time needed to report one precise
`operation.lock.conflict`. Metadata publication and removal are atomic with the
owner lifecycle. A stale or malformed metadata file never owns a lock and is
replaced by the next successful owner. Callers report the observed owner once;
they do not poll, retry, or inspect process tables.

Single-path durable file publication has one shared low-level owner beneath the
validation and command modules. It owns sibling temporary creation, file flush
and sync, existing-mode preservation, atomic install, parent-directory sync,
and temporary cleanup. Create-only publication fails on an occupied target and
removes a newly linked target if its directory sync fails. Operation modules
retain path validation, locks, domain diagnostics, multi-file ordering,
snapshots, rollback, and recovery residue; the shared primitive is not a
transaction framework. Reorganize and promotion retain their own rename
sequences because their displaced paths and rollback order are operation state.

Both publishing and dry-run Validate hold `log.lock` exclusively from before
their first research-owned read through evaluation, cache work, publication,
and response construction. Lock contention is an operational conflict and no
research-owned or generated bytes change. Validation retains its starting and
final research-owned snapshot checks because direct filesystem edits do not
participate in advisory CLI locks; a failed final check rolls back any bundle
whose installation has begun.

### Public Payload Contracts

Text is the concise default for every validation action. Explicit
`--format json` returns one action-specific bounded envelope and no additional
stdout text.

`run` returns the evaluated log and target, outcome `clear|findings|failed`,
whether the completed snapshot was saved, the saved time when applicable, all
four finding-type counts, batch count, blocked count, failed count, and the next
useful validation command. A dry run returns the same evaluated facts with
`saved:false`. It never exposes private `RuleCheck` values. Run envelopes have
a 16 KiB UTF-8 budget. A root-run envelope that cannot fit all per-log rows
fails with `validation.response.too_large` rather than hiding a log.
Single-action JSON error messages are limited to 2 KiB of UTF-8.

`show` returns rows containing log, saved time, outcome, fixed
Conformance/Evidence/Provenance/Orphans counts, and Batches. A single-log row
also contains blocked and failed counts. Root rows omit those diagnostic counts;
the root envelope instead contains their aggregates, contributing-log count,
and partial flag. Root text prints both aggregates after the table. Missing
replacement state is `Not validated` with unavailable counts, never zeroes. A
per-log store error is an `Error` row and does not suppress sibling rows in root
view.

Finding-list rows contain finding ID, type, code, owning entry when known,
concise subject, and batch ID. Batch-list rows contain batch ID, finding count,
represented types, repair entries, context entries, focus finding, and exact
rationale. Blocked rows contain the check ID, area, owning entry when known,
subject, and root finding or failed-check IDs. Failed rows contain the check ID,
area, owning entry when known, code, rule, operation class, subject, and bounded
reason.
Finding detail contains the complete canonical finding, its containing
batch, and direct saved repair context. Its finding header includes the saved
issue title and explanation, exact rule and subject, labelled source locations,
and the complete bounded diagnostic. Comparison-style diagnostics distinguish
actual from expected state and state the exact reason in plain language.
Snapshot publication rejects a finding when that complete header cannot fit
the finding-detail response budget; readers never truncate or strand a saved
diagnostic. Batch detail repeats complete membership
counts on every page and pages homogeneous sections in the fixed order findings,
repair keys, nodes, relationships, and ambiguities.

JSON schemas are validation-specific `run`, `root-run`, `show`, `finding-list`,
`batch-list`, `blocked-list`, `failed-list`, `finding-detail`, and `batch-detail`
envelopes. `render` has no
JSON projection; success is silent and failure uses the normal operational
error channel.
They do not expose internal snapshot IDs, database keys, or a generic
result/entity/export schema. Every cursor binds the selected snapshot
generation, log, action, filters, section, and last stable key.

A completed `run` exits 0 for `clear` or `findings` and 3 for `failed`, including
a nonpublishing dry run. A nondry completed run saves its snapshot. Operational
or contract failure outside a completed evaluation exits
2 and publishes nothing. Root run exits 2 if any log has an operational error,
otherwise 3 if any log has a failed snapshot, otherwise 0. Saved-state `Not validated`
is a normal `show` row and exits 0; a malformed, busy, or unsupported store
makes that row `Error` and the root show exit 2.

A completed full-log validation owns the latest full validation slot and
`<log>/validation.md`. Entry validation owns only its stable-entry snapshot.
The active generated and acceleration paths are:

```text
<log>/.cache/results.sqlite
<log>/validation.md
<log>/.cache/research-log-validation.sqlite3
<log>/.cache/research-log-operations/log.lock
<project>/.cache/research-log-fingerprints.sqlite3
```

SQLite journal, WAL, and shared-memory companions belong to their owning
database. `pyrun` independently owns `<entry-root>/pyrun.json`; standard
validation reads but never writes it. The shared result store is the sole
disposable machine authority for queryable validation and reproduction state,
with each domain retaining its own schema and lifecycle.
`<log>/.cache/research-log-validation.sqlite3` is disposable per-log
acceleration state using the schema and component versions listed in `Current
Versions`. It retains strict serialized successful `SelectionResult` values
keyed by strong source content identity, source profile, canonical locator
identity, and locator-evaluator version.

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

`validation.md` is a deterministic, source-controlled, nonauthoritative human
projection of the latest completed full-log snapshot. Beneath `# Validation`,
it contains only the same two-column field/value table as single-log text
`log validate show`: Log, Saved, Outcome, Conformance, Evidence, Provenance,
Orphans, Batches, Blocked, Failed, in that order. Counts include explicit zeroes;
unavailable summary values are not represented as zero. Saved uses the compact
UTC date `Mon D`; JSON retains exact timestamps and canonical paths.
There are no inventories, batch rationales, diagnostics, navigation commands,
or reproduction sections. Finding and batch detail remain on `list` and `detail`.

A localized validator failure enters the saved Failed snapshot and its separate
failed-check inventory. A whole-operation failure never replaces the report.
Saved finding names and explanations still come from the complete presentation
catalog and remain available through detail; an emitted code without a catalog
entry is an implementation error, not a machine-syntax fallback. Report rendering
and recovery never reevaluate research sources or change the saved snapshot.

Reproduction does not request, publish, or invoke validation after it
completes. Its result and report remain independent from the existing validation
snapshot; only an explicit later validation evaluates changed execution state.
Promotion likewise updates its owned outputs, execution state, and reproduction
result without an Evidence, Provenance, targeted-refresh, or full-validation
operation.

Output reproduction requirements and signature-currentness mismatches belong
to Reproduce and never appear as validation findings or repair batches.
`show` counts findings by the four fixed types. Single-log text uses the fixed
field/value summary, including Batches, Blocked and Failed rows. The cross-log
table reports batches in their own column, omits blocked and failed columns,
and reports both aggregates after the table. A batch counts once regardless of how many types or
entries it spans. Missing saved validation is explicit rather than rendered as
zero. Agents use the generated projection; they do not parse reports or
recalculate counts.

#### Published Validation And Repair Batches

A completed validation publishes one canonical `ValidationSnapshot`. The
snapshot is the sole persisted and public validation-domain input to reports,
query views, and Repair. Reproduce admission privately consumes the fresh
in-memory snapshot together with the live `ResearchGraph` from the same
evaluation; it reads no saved validation projection. Passing checks, database
keys, and the complete live evaluation topology are private implementation
state. Canonical blocked projections retain only the affected validation check
and root finding or failed-check IDs. The bounded saved repair subgraph
described below is part of snapshot detail. Generated mechanical records,
human finding groups, command chains,
unresolved groups, admission effects, and generic stored results are former
models removed by the replacement, not hidden validation entities.

Every finding belongs to exactly one primary batch. Batch construction first
forms the connected-component closure of only these mechanically proven
relationships:

- identical `failed-prerequisite:<check-id>` ownership;
- a file-level `source:<logical-location>` defect for parse, encoding, schema,
  or access-contract repair owned by that file;
- one authored `record:<entry>:<registry-kind>:<record-id>`;
- one recorded `command:<entry>:<invocation-id>`;
- one persisted `execution:<entry>:<cid>:<execution-id>`;
- one canonical `material:<material-id>` whose repair owns the defect; or
- one exact `ownership:<material-id>:<candidate-set-digest>` conflict.

An explicit `caused_by` finding relationship also joins its two findings.
Source locations and ambiguous candidates are repair context, not implicit
grouping keys. Every component already containing multiple findings remains
unchanged. The batch builder then consolidates only residual singleton Orphans
findings when two or more have the same exact entry and orphan diagnostic code.
Entryless Orphans findings use log scope plus exact code. Different entries and
different orphan codes remain separate, and the fallback never absorbs a
singleton into an existing multi-finding component. Matching type, code family,
entry, filename, words, or graph proximity does not otherwise join findings. A
finding left without either a proven relationship or an eligible orphan peer is
a singleton batch. Batches may cross finding types and have no batch type or
arbitrary membership cap.

An output-support finding retains its exact declared output material key and
also carries the exact producing command key. When that command has a complete
recipe match in current `pyrun.json` state, the finding additionally carries
that persisted execution key and node. Legacy `pyrun-outputs.json` support has
no execution identity and therefore stops at the command key. Distinct output
findings can consequently share one command or execution batch without losing
their material subjects. An orphan-member finding retains its exact material
key; when the orphan-collapse algorithm proves that every inventoried member of
a directory is orphaned, each member also carries that canonical directory
material key. The orphan-singleton fallback adds no repair key and does not
change finding context; directory grouping remains visible inside batch detail.
Unrelated commands, outputs, and non-orphan paths acquire no shared key merely
because they belong to the same entry.

For each final batch, `finding_ids` are sorted and complete;
`repair_keys` are the exact strong keys; the orphan-singleton fallback is named
only in `rationale` and is never fabricated as a repair key. `repair_entries`
own editable anchors;
`context_entries` are related read-only dependencies; and `rationale` names the
exact shared keys and causal edges that joined members or the exact entry/code
scope of an orphan-singleton fallback. `focus_finding_id` is
the first stable root not caused by another member. `batch_id` is `batch-` plus
the digest of sorted finding IDs and normalized strong keys; display order and
incidental context do not affect it.

Snapshot repair context is one bounded saved subgraph. Graph nodes,
relationships, and ambiguities are stored once per snapshot. Findings retain
only their direct repair-key and context-node anchors; batches retain finding
membership, their direct repair keys, and repair/context entry roles. A batch
detail request derives only that selected batch's repair neighborhood from the
saved graph. It includes direct inputs, outputs, scripts, code, collections,
bindings, producers, consumers, declarations, retention, evidence use, and
ambiguity candidates for included commands, executions, and materials. The
derivation does not transitively traverse unrelated graph branches, and no
expanded finding or batch neighborhood is persisted. Every saved relationship
remains marked established or ambiguous.

Snapshot publication is atomic and replacement-only. Existing saved validation
is disposable cache state and is never migrated into the new model. A successful
replacement run derives a fresh snapshot from current sources. A whole-operation
failure preserves the latest completed snapshot and report.

#### Finding And Batch Inspection

`log validate list findings` and `log validate list batches` are bounded
inventories of the selected completed snapshot. Finding inventory may be
filtered only by exact finding `--type`; batch inventory has no type selector.
Rows use stable canonical finding and batch identities and include the minimal
context needed to choose a detail request.

`log validate detail finding` returns one complete canonical finding, its
containing batch, saved issue title and explanation, exact unsatisfied rule,
complete bounded diagnostic, source locations, causes, repair keys, and direct
saved graph context. The text view labels those fields rather than emitting an
unexplained JSON object. A comparison-style diagnostic records and displays its
plain-language reason plus actual and expected state. `log validate detail batch`
returns the batch's complete membership counts, repair and context entries,
focus finding, grouping rationale, and paged findings, keys, graph nodes,
established relationships, and ambiguity observations. Neither command proposes
an edit or infers a new relationship.

Without `--entry`, list and detail select the latest completed full-log
snapshot. With `--entry`, they select a newer completed inspection of that
stable entry when present and otherwise select that entry from the latest
completed full-log snapshot. A cross-entry batch remains complete and
distinguishes repair-owned entries from dependency-only context. Unknown,
superseded, replacement-required, malformed, unsupported, and busy state uses
the validation-specific read errors defined by the public payload contract.

Entry validation runs under the same log lock, evaluates the selected physical
entry and recursively reached producer context, and publishes at most that
entry's completed snapshot. It never changes the full-log snapshot or report.
Its immediate run projection is not a whole-log clearance claim. A source
change across the operation boundary fails the operation and publishes nothing.

Validation acquires the canonical exclusive
`<log>/.cache/research-log-operations/log.lock` before opening the per-log
database and holds it through evaluation, authoritative publication,
comparison replacement, completed-run selection cleanup, and run-response
construction. Dry-run validation holds the same lock for its complete
read-only lifecycle. Publication rejects symlinks in generated destinations,
rechecks the generated-residue inventory under the lock, and first commits the
canonical snapshot in one atomic database transaction. A snapshot transaction
failure leaves the prior snapshot visible. The report is then composed from
the committed snapshot and replaced atomically as a derived second stage. A
report replacement failure preserves the newly committed queryable snapshot,
leaves prior report bytes in place, and records a stale materialization marker
for render-only recovery. A cache failure after successful publication leaves
the authoritative snapshot in place and makes later reuse conservative.
Process termination is subject to these per-stage atomicity boundaries; a
later invocation must not interpret a stale report as authoritative.

### Retained Validation Snapshots

Completed validation is retained as disposable current-observation cache state in
the shared result store. The validation domain keeps at most one latest completed
full-log snapshot and one latest completed snapshot for each stable entry. It
does not retain validation history. Passing checks and private admission
decisions are never persisted as validation state.

The persisted validation projection consists only of snapshot identity and
outcome counts, atomic findings, strong repair keys, bounded
repair-context nodes and relationships, canonical batches and membership,
canonical blocked checks with root blocker IDs, canonical failed checks with
bounded diagnostics,
repair-versus-context entry roles, and report-materialization state. It has no
mechanical record, scope aggregate, human group, command chain, unresolved group,
admission effect, generic stored entity, or validation export model.

One successful non-dry-run evaluation publishes a freshly derived snapshot from
the current research sources. Existing validation state from the superseded
schema is not read, converted, or migrated. Before the first successful
replacement run, saved-state presentation reports `Not validated` with
`replacement_required`; finding and batch inspection reports
`validation.replacement_required` and gives the exact `log validate run`
command. Whole-operation failures and dry-run evaluations do not replace the
schema or a previously completed snapshot. A completed failed snapshot does.

A completed full-log publication atomically replaces the full slot and clears
entry inspection slots from the prior full cycle. A completed entry publication
replaces only that stable entry's slot and never creates or repopulates a
full-log snapshot. Command diagnostics and reproduction-domain records are
independent and survive validation replacement. Full-log and entry publication
both preserve source identity, rules version, evaluation timestamps, outcome
counts, and generation-bound cursor protection.

The replacement transaction creates the current validation tables, inserts the
fresh complete snapshot, removes superseded validation-owned
projections, updates shared schema metadata, and commits under the existing
result-store lock. Reproduction-domain tables and rows remain logically
unchanged. Any failure rolls back to the exact pre-transaction database. Insert
nodes and findings before edges and batch membership so foreign keys validate
throughout candidate publication.

Validation readers use indexed bounded projections for:

- the latest full-log or stable-entry snapshot summary;
- finding inventory, optionally filtered by finding type;
- batch inventory;
- blocked-check inventory;
- failed-check inventory;
- finding detail with its containing batch and direct repair context; and
- batch detail with complete membership counts and paged findings, repair keys,
  nodes, established relationships, and ambiguity observations.

Readers do not observe research sources, run validation, repair a store, or
infer batches and repair relationships from diagnostic prose. Summary, list,
and finding-detail views use normalized indexed rows. Batch detail loads the
shared saved graph and deterministically derives only the requested batch's
bounded neighborhood from its direct anchors. A malformed or busy store and an
unsupported replacement schema produce validation-specific read errors.
Missing and superseded snapshots are reported explicitly and never redirected.

Publication uses SQLite foreign keys, rollback journaling, full synchronous
commits, and the existing short transaction boundary. Writers reject symlinked
store paths and unsafe companions. One snapshot replacement either becomes
fully visible or leaves the prior state visible. Report composition occurs from
committed snapshot rows while the log-operation lock is still held. A report
replacement failure keeps the committed snapshot queryable, preserves prior
report bytes, records a stale materialization marker, and is recoverable through
`log validate render` without reevaluation.

Ordinary validation views and run responses retain the 16 KiB UTF-8 response budget and page sizes
from 1 through 100. Cursors bind the selected snapshot generation, action,
filters, section, and last stable key. Snapshot replacement invalidates list
cursors. Detail continuation remains valid only while its selected slot remains
present. Pagination never truncates a field or splits one batch into false
sub-batches; complete membership counts remain in every batch-detail header.
Saved finding diagnostics and presentation context are decoded as canonical
objects and fail closed as malformed storage when their shape or required
nonempty presentation fields are corrupt.

Snapshot publication permits up to 5,000,000 normalized rows while retaining
the independent 128 MiB canonical-snapshot byte bound. After direct-anchor
storage replaced expanded batch neighborhoods, the largest measured maintained
snapshot required 57,168 rows and about 10.5 MiB of canonical JSON. This leaves
more than 87-fold row-capacity headroom rather than tuning the limit to current
data.

The shared store accepts only its current schema. Validation cache deletion may
reset its local generation, but no age-based eviction, per-result deletion,
history archive, validation migration, compatibility reader, or generic export
command exists.

### Command-Provenance And Orphans Diagnostics

The active command-input, producer, directory, retention, and Orphans codes are
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
- 1,000,000 `ResearchGraph` nodes and 4,000,000 edges per maintained log;
- 64 producer-lineage levels;
- 1 MiB of recorded command text per invocation; and
- the stricter source-reader and evidence-record limits already defined by
  this specification.

Readers and command parsers must be non-executing, path-safe, symlink-safe,
and bounded. Validation does not execute commands, import scripts, deserialize
unsafe formats, follow unrestricted external links, or enumerate outside the
declared target. Crossing a `ResearchGraph` node or edge bound, or the stable
producer-lineage bound, fails the validation operation without publication; it
never silently truncates lineage or converts an unresolved tail into an orphan
conclusion.
Validation does not enumerate outside the target maintained log except through
exact locally declared input paths and the first-class entry `data` and
`images` material roots. A localized transient material-access failure creates
one failed check and blocks only its dependents.

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
The candidate success rate was `67.6%`<!-- eid:candidate-success-rate source=results select=/success_rate where=/case:eq:string:candidate form=percentage -->.
```

Its entry-local `evidence.json` contains:

```json
{
  "schema": "research-log-evidence/v5",
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
`results` file input, using its declared SHA-256 identity rule and
`"origin": false`.

The same experimental section records one command that names
`data/results.csv`:

````markdown
```bash
./pyrun scripts/run_study.py \
  --input-dataset "<development-set>" \
  --output-summary-csv "<results>"
```
````

Mechanical validation resolves the local script without executing or
inspecting its internals. The role-bearing options establish the command graph.
It resolves `<development-set>` through the entry-root `data.json`, verifies
its fingerprint and `origin: true`, and does not traverse beyond that origin.
The entry-root `pyrun.json` must contain a retained execution owning
`data/results.csv` through an exact structural association. Reproduce separately
decides whether its output, script, parameters, code, and direct inputs are
current. The evidence check compares `67.6%`; the Provenance check verifies the
complete bounded structural chain. Neither decides whether success rate is
scientifically appropriate.

A runner declaration makes a non-natural relationship visible to both `pyrun`
and static validation. A retained `pyrun` output that needs structural execution support
therefore uses a natural output-bearing option, `--other-outputs`, or an
explicit capture option. Renaming only the Markdown command while leaving the
executable interface unchanged is not a valid repair.

### Other Presentation And Provenance Cases

- A summary statistic names one successful entry evidence record through its
  adjacent `ref`. A table reference also names one exact row and column. The
  summary reuses the target record's source and command-provenance projection
  and does not declare another producer.
- A direct table uses the closed direct-table
  recipe. Every local source used by the table must independently resolve to
  exactly one producing invocation unless it reaches an explicit origin.
- A marked output block may select a retained command log. Declare the generated
  log and use `./pyrun --capture-stdout-stderr "<run-log>" --` followed by the
  Python program so it has both a graph relationship and retained execution
  support; raw redirection or `tee` does not provide that support. The marked
  fence payload must still match the selected retained text exactly.
- A whole-artifact evidence presentation resolves its one source token and
  compares that canonical path with the normalized Markdown target before
  comparing the current file's SHA-256 with its evidence-owned artifact
  baseline. A generated artifact
  still requires one mechanically proven command output and structurally
  associated output support; an explicit origin stops the chain. Reproduce
  evaluates its current signature separately.
- A cross-log source is observed as a locally declared origin of the consuming log.
  Validation does not import the source log's command graph or validation
  result.
- Retained material with no mechanically discoverable producer fails
  `producer.missing`. Generated material with a discoverable
  producer but no structurally associated output record fails
  `provenance.output.unrecorded`.
  There is no limitation declaration that converts either gap into a pass.

### Directory And Named-Input Case

Suppose an entry records:

````markdown
```bash
./pyrun --other-outputs output-dir -- \
  scripts/run_trials.py \
  --reference "<reference-grid>" \
  --cases 1:40 \
  --output-dir "<trials>"
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
data/run.log :: v2:{"text":{"line":"2"}}
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
graph semantics, evaluated target scope, or finding type that alters an existing valid outcome
requires the applicable new evidence, command-discovery, or
mechanical-validation contract version.

## Current Implementation Boundary

Standard validation now evaluates native `RuleCheck` values, constructs the
neutral shared research graph directly from loaded domain observations, and
derives canonical attempts, findings, batches, and completed snapshots without
a parallel mechanical-record projection. Unsupported generated-validation
artifacts are ordinary orphan findings.

Canonical snapshot persistence, validation report rendering, and the bounded
saved read model, public CLI, and command-verification cutover are implemented.
Old saved validation is recognized only as replacement-required state and is
replaced from current sources rather than migrated.

No downstream surface may define a competing evidence-record, locator,
transformation, presentation, command-Provenance, collection, output-support,
Orphans, or mechanical-outcome contract. Self-contained runtime agent surfaces
may carry the bounded authoring and operational subset they need without
loading or linking to this specification, but they must remain compatible with
it.
Maintainer-facing implementation documentation may omit detail by pointing
here and must not contradict this specification.

## Isolated Command Verification

The exact adjacent command grammar is:

```text
log command verify --path LOG --entry ENTRY --cid CID --execution-id ID [--execution-timeout-seconds SECONDS] [--format text|json]
log command show --path LOG [--id DIAGNOSTIC_ID] [--format text|json]
```

`log command verify` is a
reproduction-oriented command operation, not validation. It resolves one
current invocation, compares isolated regenerated outputs to retained
baselines, and does not publish or alter validator state. Entry/full validation
remains the only clearance path. The former `repair-check` spelling is removed
at the command cutover and is not retained as an alias.

Verify's closed `research-log-command-verification-result/1` contract, timeout
range, statuses, and exit behavior are owned by the reproduction specification.
Text is the default presentation; `--format json` emits that one machine result.

Failed command discovery, declaration, retirement, and rejected-producer checks
may replace the latest command-owned diagnostic without changing validation or
reproduction state. `log command show` exits 0 and reads the latest diagnostic,
or the exact retained diagnostic selected by `--id`, without observing research
sources. Text is the default presentation. `--format json` emits one closed
`research-log-command-diagnostic/1` object with exactly `schema`, `summary`,
nullable `entry`, `operation`, `code`, `diagnostic_id`, `generation`, `stored_at`,
and `records`. Publication and reads permit at most 1,000 record objects and 1
MiB of UTF-8 JSON. Indexed fields, record order and kinds, and the complete
payload are validated again on read. Missing diagnostics fail with
`command.diagnostic.missing`, unsupported stores with
`command.diagnostic.schema.unsupported`, shared-store contention with
`command.diagnostic.busy`, and malformed or over-bound retained data with
`command.diagnostic.malformed`; each exits 2. A later diagnostic replaces the
prior diagnostic, so a superseded ID is missing rather than historical state.

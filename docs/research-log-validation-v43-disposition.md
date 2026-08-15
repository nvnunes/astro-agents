# Research-Log Validation v43 Disposition

This document is the source of truth for migrating generated v43 validation
records to the local-publication model. The migration must account for every
listed file and field before it writes or removes a canonical record. Unknown
files or fields block migration for the affected log.

Disposition terms:

- `preserve`: retain durable evidence without changing its meaning or date.
- `transform`: move reusable information to the named owner.
- `recompute`: omit disposable acceleration data and rebuild it when needed.
- `remove`: delete data whose owner or lifecycle no longer exists.

## Generated Files

| v43 file | Disposition | Phase 4 owner or action | Compatibility proof and removal gate |
| --- | --- | --- | --- |
| `validation.md` | preserve and transform | `validation.md` remains the durable report; add the local snapshot identity and merge the remediation detail | Preserve all completed rows, findings, counts, dates, scope, mode, and rules provenance. Replace only generated provenance/status/remediation structure. |
| `validation-failures.md` | transform, then remove | `validation.md` `## Remediation` section | Parsed failure identities, checks, findings, count, and order must equal the report's failed rows and the migrated remediation section before deletion. |
| `validation-state.json` schema 9 | preserve temporarily | Disposable compatibility cache until the same log publishes its Phase 5 native state | Phase 4 may read and copy exact schema-9 content. Do not discard hashes, inspections, dependencies, resolutions, graph slices, or results before the Phase 5 replacement passes its migration checks. |
| `validation-index.json` schema 6 | preserve temporarily | Disposable per-log graph slice until the same log publishes its Phase 5 native slice | The slice must decode, identify its owning summary, and remain source-current. Phase 4 may exclude it from a repository view but must not delete it before the Phase 5 replacement passes. |
| `validation-decisions.json` | create | Durable semantic-judgment store, schema 1 | Extract only judgments whose v43 rule version and complete decision-input fingerprint are provable. Historical outcomes that lack proof remain only in `validation.md`. |
| `.research-log-validation-index/manifest.json` | remove | No replacement | Remove after the aggregate-consumer audit and after on-demand repository audit/view tests pass. |
| `.research-log-validation-index/incoming.json` | remove | No replacement | Remove with `manifest.json`; neither file may remain a currentness or publication dependency. |
| repository `.research-log-validation.lock` | remove | Per-log lock in the validation directory | Remove after every command stops acquiring the repository lock and same-log/different-log concurrency tests pass. |
| repository staging, transaction, incoming, or recovery artifacts | remove | No replacement | Remove after repository recovery-before-read and transaction code are unreachable and tests reject their reintroduction. |
| per-log temporary replacement files and lock file | remove when inactive | Filesystem publication mechanism only | Exclude from research discovery, orphan inventory, and local snapshot identity. The operating system releases a lock after process exit; abandoned temporary files are never canonical inputs. |

## `validation.md`

| v43 field or section | Disposition | Phase 4 owner or transformation |
| --- | --- | --- |
| H1 and `Log` | preserve | Keep in `validation.md`. |
| `Requested scope` | preserve | Keep in `validation.md`. |
| `Report-update date` | preserve | Keep the original date during migration. |
| `Validation mode` | preserve | Keep in `validation.md`. |
| `Validation-rules version` | preserve | Keep the full v43 value. |
| cross-log review status | transform | Record the frozen contributing/excluded slice view and whether coverage was complete for the dated scan. |
| local research identity (absent in v43 report) | transform | Reconstruct `local-research-snapshot-v1` from schema-9 `input_files` content identities and directory memberships after excluding other maintained-log roots. This is the same source-identity projection used by native scans; it does not use the composite v43 input fingerprint, repository aggregate, foreign slices, generated files, or a fresh artifact hash. |
| `Status Summary` | transform | Rebuild from the preserved detailed rows. Link failures to the in-report remediation anchor. |
| `Counts` | preserve | Values must continue to equal the detailed rows. |
| `Summary` rows and cells | preserve | Retain statistic, supporting entry and section, provenance result, and original result date. |
| `Entries` headings, rows, and cells | preserve | Retain target, kind, Integrity, Provenance, Reproducibility, notes, and original dates or failures. |
| failure/remediation detail (absent in v43 report) | transform | Append one stable `## Remediation` section built from `validation-failures.md`; preserve scope, target, check, finding, order, and anchors. |

## `validation-state.json` Schema 9

Phase 4 reads this schema strictly and retains the complete file as a temporary
Phase 5 migration source. The durable destinations below describe which values
may also be extracted during Phase 4; they do not authorize pruning the schema-9
cache early.

| v43 field path | Disposition | Destination or recomputation rule |
| --- | --- | --- |
| `schema_version` | preserve temporarily | Required to decode the Phase 5 migration source; remove with the schema-9 file. |
| `validation_rules_version` | preserve and transform | Keep in the compatibility cache and copy to every extracted judgment. |
| `input_fingerprint` | preserve temporarily | Keep only as the schema-9 composite cache key. It does not become the local snapshot identity because it includes repository facts. |
| `input_files.*` | preserve temporarily | Phase 5 cache-reuse input. Recompute when the schema-9 cache is missing or unusable. |
| `input_files.*.{size,mtime_ns,ctime_ns,sha256,members}` | preserve temporarily | Preserve exact identity evidence for Phase 5 reuse. |
| `input_files.*.{missing,error}` | preserve temporarily | Preserve unavailable-input evidence; do not reinterpret it as a valid identity. |
| `mechanical_checks.*` and all nested fields | preserve temporarily | Phase 5 inspection-reuse input. Its current v43 value is an opaque allowlisted mapping owned by the v43 adapter. |
| `directory_memberships.*.{members,sha256}` | preserve temporarily | Phase 5 membership-cache input. |
| `directory_memberships.*.error` | preserve temporarily | Preserve unavailable-membership evidence. |
| `files.*` | preserve temporarily | Phase 5 material-hash and inspection reuse input. |
| `files.*.{size,mtime_ns,ctime_ns,sha256,members}` | preserve temporarily | Preserve exact identity evidence for Phase 5 reuse. |
| `files.*.{missing,error}` | preserve temporarily | Preserve unavailable-material evidence. |
| `completed_checks[]` | transform and preserve temporarily | Extract provable semantic outcomes into `validation-decisions.json`; retain the complete list for Phase 5 migration. |
| `completed_checks[].{entry,target,check}` | transform | Durable judgment subject identity. |
| `completed_checks[].result` | transform | Durable judgment result and report result. A date is the original result date; `FAIL` uses the completed report date because v43 retained no separate failure date. |
| `completed_checks[].dependencies[]` | preserve temporarily | Phase 5 typed-dependency migration input. |
| `completed_checks[].dependencies[].{path,role,identity}` and nested identity fields | preserve temporarily | Phase 5 typed-dependency migration input and proof material. |
| `completed_checks[].dependency_signature` | transform | Conservative schema-1 decision-input fingerprint proof together with subject and full v43 rules version. |
| `completed_checks[].graph_slice.{identity,nodes,edges,roots}` and all nested node/root fields | preserve temporarily | Phase 5 graph-contract migration input; disposable after the native replacement. |
| `completed_checks[].resolution.{entry,section,lines}` | transform and preserve temporarily | Copy Summary support basis into the schema-1 judgment; line location is refreshable in Phase 5. |
| `completed_checks[].resolution.producer_invocation` | transform and preserve temporarily | Copy the v43 producer basis into the schema-1 judgment and retain for Phase 5 identity resolution. |
| `completed_checks[].resolution.producer_bindings[].{material,invocation}` | transform and preserve temporarily | Copy selected producer bindings into the schema-1 judgment and retain for Phase 5 identity resolution. |
| `completed_checks[].findings[]` | preserve and transform | Keep genuine failure reasoning in the report remediation section and schema-1 judgment. Never classify it as obsolete. |
| `orphan_dispositions[]` | transform and preserve temporarily | Extract provable item judgments into `validation-decisions.json`; retain the complete list for Phase 5 migration. |
| `orphan_dispositions[].inventory_version` | preserve temporarily | Phase 5 fingerprint-adapter input. |
| `orphan_dispositions[].entry` | transform | Durable judgment subject identity. |
| `orphan_dispositions[].items[].identity` | transform | Durable orphan-candidate subject identity. |
| `orphan_dispositions[].items[].decision` | transform | Durable result. |
| `orphan_dispositions[].items[].basis` | transform | Durable producer/retention basis when available. |
| `orphan_dispositions[].items[].fingerprint` | transform | Conservative schema-1 decision-input fingerprint proof with full v43 rules version. |
| `orphan_dispositions[].dependencies[].{path,role}` | preserve temporarily | Phase 5 typed-dependency migration input. |
| `result.date` | preserve and transform | Completed report date and fallback date for v43 failures/orphan judgments that retained no separate date. |
| `result.{mode,requested_scope,scope}` | preserve | Durable report provenance; keep cached copy until Phase 5 replaces state. |
| `result.{summary_rows,summary_failed,entry_rows,entry_failed,entries,failed_entries,failure_rows}` | preserve | Durable report counts; cached copy remains disposable. |
| `result.failures[].{scope,target,checks}` | preserve and transform | Keep in the report remediation projection; cached copy remains disposable. |
| `report.{size,sha256}` | recompute | Disposable cache-to-report identity. It must never determine whether the report itself is readable or historically valid. |
| `graph_identity` | preserve temporarily | Phase 5 graph migration input and disposable cache consistency key. |

## `validation-index.json` Schema 6

| v43 field path | Disposition | Destination or recomputation rule |
| --- | --- | --- |
| `schema_version` | preserve temporarily | Required by the Phase 5 adapter; remove with the schema-6 file. |
| `validation_rules_version` | preserve temporarily | Used to decide graph compatibility. |
| `summary` | preserve temporarily | Identifies the owning log. |
| `namespace` | preserve temporarily | Validated derivative of `summary`. |
| `graph_identity` | preserve temporarily | Disposable slice self-identity and Phase 5 migration input. |
| `material_owners.*.{namespace,kind}` | preserve temporarily | On-demand repository-view acceleration data. Recompute from the owning log if absent or stale. |
| `source_inputs.*.{size,sha256}` | preserve temporarily | Verify source currentness once when accepting a foreign slice. |
| `edge_sources.*[]` | preserve temporarily | Map each cross-log edge to its consuming-source identities. |
| `source_identity` | preserve temporarily | Self-validates `source_inputs` and `edge_sources`. |
| `graph.validation_rules_version` | preserve temporarily | Must agree with the slice rules version. |
| `graph.nodes[].{namespace,kind,identity,origins}` and all nested origin/input fields | preserve temporarily | On-demand graph view and Phase 5 graph-contract migration input. |
| `graph.edges[].{source,target,kind,origins,identity}` and all nested node/origin/input fields | preserve temporarily | On-demand graph view and Phase 5 graph-contract migration input. |
| `graph.roots[].{node,policy}` and nested node fields | preserve temporarily | On-demand graph view and Phase 5 graph-contract migration input. |
| `graph.identity` | preserve temporarily | Must agree with `graph_identity`. |

## `validation-decisions.json` Schema 1

Schema 1 is a strict Phase 4 format, not a copy of schema-9 state.

| Native field path | Source and rule |
| --- | --- |
| `schema_version` | Constant `1`. |
| `validation_rules_version` | Exact full v43 rules version shared by the stored judgments. |
| `local_snapshot_identity` | Current report's local snapshot identity. |
| `judgments[]` | Deterministically ordered, content-addressed reusable judgments reachable from the current report. |
| `judgments[].identity` | SHA-256 over every other judgment field. |
| `judgments[].provenance` | `native-reviewed` only when complete reasoning was explicitly recorded; otherwise `legacy-attested`. |
| `judgments[].kind` | `completed-check` or `orphan-disposition`. |
| `judgments[].subject` | Exact stable subject fields for the judgment kind. |
| `judgments[].decision_input_fingerprint` | Proven v43 dependency signature or orphan fingerprint. Omit the judgment when unavailable. |
| `judgments[].validation_rules_version` | Exact full v43 rules version. |
| `judgments[].result` | Preserved completed-check result or orphan disposition. |
| `judgments[].decision_date` | Original result date when retained; otherwise the completed v43 report date with `date_provenance: report-date-fallback`. |
| `judgments[].date_provenance` | `recorded` or `report-date-fallback`. |
| `judgments[].basis` | Preserved support, producer, binding, retention, or failure basis when retained. |
| `judgments[].rationale` | Exact recorded findings or notes when retained; omit when unavailable. |
| `judgments[].rationale_provenance` | `recorded` or `unavailable-in-v43`; never infer rationale from the result. |

## Repository Aggregate Files

Every field in `.research-log-validation-index/manifest.json` and
`incoming.json` has disposition `remove`: `schema_version`,
`validation_rules_version`, `logs[]` and its `summary`, `namespace`,
`graph_identity`, and `source_identity` fields, `incoming_identity`, `incoming`
edge maps and nested graph-edge fields, `sources` and nested source snapshots,
and both file `identity` fields. Their only legitimate diagnostic projection is
an ephemeral repository audit assembled from per-log slices.

## Migration Completion Audit

A log completes Phase 4 migration only when:

- every discovered generated file and decoded field matches this table;
- `validation.md` retains all completed rows, findings, dates, and failures and
  contains its local snapshot identity and remediation section;
- `validation-decisions.json` contains only judgments with provable v43 rules
  and decision-input fingerprints and records missing rationale honestly;
- the schema-9 state and schema-6 slice remain byte-identical until that log's
  Phase 5 native replacements publish; and
- research-owned files remain byte-identical.

Repository-level Phase 4 completion additionally requires that the aggregate,
repository lock, transaction, staging, and recovery surfaces are absent and
that no serializer emits a legacy-only or unclassified field.

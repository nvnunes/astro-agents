# Research-Logging CLI Simplification Coverage

This record traces the completed 28-finding simplification program to its
implementation commits and maintained verification. It is a verification
index, not a replacement for the normative mechanical-validator and
reproduction specifications.

The integration baseline is commit `d63f900`. Phase 8 adds durable publication
ownership in `ffac117` and the workflow test and correction in `0f6481f`.

## Integrated Current-Format Workflow

`test_research_log_integrated_workflow.py` drives the public dispatcher and the
bounded subprocess launcher through one data/v5, evidence/v4, and pyrun/v6
fixture. Its checkpoints are intentionally exact:

| Checkpoint | Required outcome and retained state |
| --- | --- |
| Scaffold and author | `log init`, `log add`, `log data add-origin`, and `log command sync --add-generated` exit 0; the pending execution has no observations and requires reproduction. |
| Execute and present | Real `pyrun` exits 0, records script/input/output observations, and clears `requires_reproduction`; `log evidence add` captures the linked output baseline. |
| Discover and validate | Discovery returns the one summary; entry and full `log validate run` operations return `outcome:clear`; full validation saves the canonical SQLite snapshot and `validation.md` without changing Markdown, evidence, or linked bytes. |
| Change policy | Changing only `auto_reproduce` preserves execution identity and observations while changing the policy field. |
| Change recipe | Synchronization first requires explicit retirement; the accepted replacement has a new identity and empty observations until a successful real execution. |
| Add downstream work | A second generated result is synchronized, executed, presented, and validated clear. |
| Change a declaration | `log data update` changes the selected origin without editing pyrun observations. A fresh downstream run records its local input. Full validation returns `outcome:findings` with one Orphans finding and zero Provenance findings; upstream currentness remains exclusively Reproduce-owned. Authored Markdown, both evidence baselines, and both linked results remain byte-identical. |
| Prepare reproduction | `log reproduce --dry-run --include-all` returns plan/11, rejects the changed direct input, and skips its dependent; it does not substitute the new bytes for the recorded baseline. |
| Inspect and render | `log validate list findings --path LOG --type orphan` exposes the validation finding; `log validate list blocked --path LOG` and `log validate list failed --path LOG` expose validation coverage and validator failures when present; `log validate run --root PROJECT` selects the same log; `log validate render --path LOG` uses the saved snapshot without reevaluation. |

The same production state machine is covered at its expensive process and
failure boundaries by
`test_current_execution_first_identity_stop_resume_and_compare`,
`test_current_supervisor_routes_stop_resume_and_publication_retry`,
`test_current_publication_failures_resume_at_the_exact_durable_stage`, and
`test_racing_resumes_install_one_supervisor_for_one_fixed_plan`. Together they
prove durable execution, stop/resume of one fixed plan, inspection-ready result
publication, and recovery from execution or publication failure without
replanning or corrupting prior state.

## Finding Traceability

The test names below are representative retained proof. The complete suites
remain authoritative for adjacent failure and bound checks.

| Finding | Implementing commit(s) | Representative retained proof |
| --- | --- | --- |
| Validation 1 — persisted check comparison does not skip computation | `250c9d5` | `test_research_log_validation_cache.py`; `test_recompute_bypasses_cache_and_publishes_rebuilt_cache` |
| Validation 2 — post-reproduction refresh is a parallel evaluator | `cb9bd0f` | `test_generated_declaration_has_no_refresh_operation`; `test_generated_registration_rejects_retired_refresh_option` |
| Validation 3 — batch defects survive changing identities | `cb9bd0f` | `test_research_log_findings.py`; `test_validation_snapshot_storage_conformance.py` |
| Validation 4 — parallel result representations | `45d0f5`, `ff67241` | `test_research_log_result_store.py`; `test_validation_snapshot_storage_conformance.py` |
| Validation 5 — generic evaluation obscures lifecycle | `cb9bd0f` | `test_research_log_validation_controller.py`; `test_research_log_validation_cli.py` |
| Validation 6 — current execution converted to transitional output state | `58e995c`, `c8a746b` | `test_research_log_pyrun_state.py`; `test_current_execution_requires_exact_command_association` |
| Reproduction 1 — incident recovery remains in runtime | `19d368f` | `test_current_dead_owner_recovery_stops_without_restarting_work`; absence checks below |
| Reproduction 2 — historical job formats keep multiple lifecycles | `19d368f` | `test_every_run_management_surface_refuses_historical_json_unchanged`; current SQLite job tests |
| Reproduction 3 — planning repeats full validation | `13ca9d6` | `test_launch_captures_validation_context_for_rerender`; `test_dry_run_keeps_validation_snapshot_state_absent` |
| Reproduction 4 — scheduler waiting rewrites coordinator state | `4ae32e5` | `test_reproduction_job_storage.py` permit/waiter tests; scheduler tests |
| Reproduction 5 — operational JSON couples whole history | `4ae32e5` | `test_one_checkpoint_update_does_not_read_or_write_siblings`; `test_one_comparison_update_writes_no_sibling_or_plan_rows` |
| Reproduction 6 — execution replans across repairs | `13ca9d6` | `test_fixed_plan_has_no_mutable_run_members`; `test_racing_resumes_install_one_supervisor_for_one_fixed_plan` |
| Reproduction 7 — one repair inherits job lifecycle | `0e59c09` | `test_log_command_verify.py`; `test_stale_current_recipe_is_not_executed` |
| Repair 1 — command correction describes the edit twice | `f7d9d78` | `test_log_command_sync.py`; explicit retirement and combined sync cases |
| Repair 2 — registry edits rebuild whole-log context | `f7d9d78` | `test_entry_locks_serialize_locally_without_cross_entry_rewrites`; command-sync rollback cases |
| Repair 3 — data update overlaps fingerprint refresh | `58e995c` | `test_data_registry_remains_declarative_when_file_bytes_change`; retired-refresh tests |
| Repair 4 — evidence authoring reevaluates sources | `1f1b7b7` | `test_common_authoring_prepares_each_evidence_input_once`; publication identity recheck |
| Reorganize 1 — small edits trigger whole-log material verification | `b60f88b`, `d63f900` | `test_update_entry_does_not_observe_unrelated_changed_bytes`; `test_identity_dry_runs_validate_the_declarations_they_would_rewrite` |
| Reorganize 2 — transfer duplicates registry decoders | `daa9c0f` | `test_transfer_rejects_malformed_selected_evidence_declaration`; shared decoder tests |
| Reorganize 3 — transfer supports two provenance generations | `19d368f` | `test_transfer_reuses_selected_generated_output_observation`; absence checks below |
| Pyrun 1 — runtime and Markdown reconstruct recipes separately | `272c2c4`, `c8a746b` | `test_log_command_sync.py`; `test_auto_reproduce_is_policy_outside_recipe_parameters` |
| Pyrun 2 — separate stream-capture engines | `c89b420` | `test_stream_capture.py`; `test_capture_options_mirror_and_record_stream_outputs` |
| Pyrun 3 — execution repeatedly validates the full registry | `c8a746b` | `test_research_log_pyrun_state.py`; integrated workflow state transitions |
| Pyrun 4 — helper discovery uses runtime instrumentation | `95018fd` | `test_records_static_helpers_with_dynamic_warning`; `test_changed_static_helper_publishes_no_support` |
| Other 1 — scaffolding eagerly creates reproduction results | `7cbc299` | `test_init_dry_run_then_creates_only_canonical_empty_log` |
| Other 2 — entry add requires unrelated summary canonicality | `7cbc299` | `test_log_scaffold.py` summary-preservation and rollback cases |
| Other 3 — discovery reads Markdown before filesystem shape | `7cbc299` | `test_research_log_validation_cli.py` discovery shape cases |
| Other 4 — durable publication has several owners | `ffac117`, `0f6481f` | `test_file_publication.py`; integrated workflow; authored-registry and validation-snapshot rollback suites |

## Approved Declaration/Observation Matrix

| Scenario | Maintained verification |
| --- | --- |
| External input bytes change | Integrated workflow; `test_data_registry_remains_declarative_when_file_bytes_change`; reproduction plan reports `direct_input_changed`. |
| Generated input changes before a downstream fresh run | Integrated workflow; `test_entry_exposes_stale_producer_after_evidence_passes`; recursive chain tests. |
| Generated output is manually modified | `test_changed_generated_evidence_fails_provenance_and_blocks_evidence`; `test_changed_retained_baseline_fails_even_when_regenerated_bytes_match`. |
| Selected value/table is unchanged while unrelated cells change | `test_exact_selected_value_matches_despite_whole_artifact_change`; `test_evidence_scoped_match_still_rejects_a_replaced_retained_baseline`. |
| External image/download is replaced at the same path | `test_artifact_association_uses_path_not_equal_bytes`; `test_cross_log_evidence_reads_only_the_external_artifact`; presented-evidence behavior cases. |
| Generated image/download is replaced | `test_generated_artifact_uses_evidence_and_enters_provenance`; `test_changed_generated_evidence_fails_provenance_and_blocks_evidence`. |
| Evidence-only origin has no execution | `test_artifact_evidence_may_use_an_explicit_origin_boundary`; `test_evidence_only_input_is_not_reported_as_unused`. |
| Directory membership changes | `test_directory_hash_is_deterministic_and_tracks_every_change`; selected-file and pattern cache tests; publication reobservation test. |
| Git declaration or checkout changes | `test_git_repository_identity_is_the_exact_commit_not_live_state`; `test_git_repository_actions_keep_locator_and_commit_coupled`; pyrun Git projection test. |
| Child/capture/stability failure | `test_success_records_exact_output_support_and_failed_run_does_not`; `test_input_change_during_execution_publishes_no_record`; stream-capture failure tests. |
| Old reproduction after input/code change | Integrated dry-run rejection; `test_changed_script_blocks_only_its_dependants`; retained-baseline comparison tests. |

## Follow-Up Fixture Contracts

| Contract | Maintained verification |
| --- | --- |
| Fresh run records changed origin bytes without editing data/v5 | Integrated workflow and `test_fingerprint_drift_blocks_execution_without_rewriting`. |
| Fresh downstream recording does not clear upstream failure | Integrated workflow and recursive provenance tests. |
| Evidence-scoped comparison remains subordinate to intact retained baselines | `test_evidence_scoped_match_still_rejects_a_replaced_retained_baseline`. |
| Explicit evidence update captures bytes; add/rename preserve baselines | `test_whole_artifact_round_trip_and_path_association`; `test_rename_preserves_path_artifact_baselines`. |
| Null/malformed artifact baselines fail closed | presented-evidence behavior cases; `test_strict_file_decodes_presentations_only`. |
| Directory-member baseline hashes only the linked member | `test_artifact_accepts_one_exact_registered_directory_member`; `test_changed_directory_member_hashes_only_that_member`. |
| Cache clearing cannot change durable evidence acceptance | `test_invalid_cache_recomputes_without_changing_the_outcome`; evidence dependency tests. |
| Full/selected/pattern directory and Git identities remain distinct | `test_research_log_data.py`; `test_research_log_fingerprint_cache.py`; reproduction publication reobservation. |
| Failed child, capture, helper, or publication creates no success record | `test_pyrun.py`; `test_stream_capture.py`; current reproduction publication-retry tests. |
| Read-only planning and validation do not mutate generated state unexpectedly | Integrated workflow; `test_dry_run_keeps_validation_snapshot_state_absent`; read-only cache tests. |

## One-Time Migration Evidence

Declaration/evidence conversion is not a runtime feature. The downstream
`girmos-aosims` commit `21e1fff77762fff026044434ab145e02fc1250bb` and the
Phase 2 execution record are the preservation proof: 50 data/v5 files, 50
evidence/v4 files, 2,769 declarations, and 3,127 evidence records were audited;
the disposable converter, historical inputs, and conversion tests were removed.
The same record preserves the reviewed artifact-baseline transfer and acceptance
inventory.

CID conversion is likewise a completed plan event. The downstream
`girmos-aosims` commit `72d61743042edbb1c7511c1a01705f769600a6ce` and the
Phase 5 execution record preserve its preview, exact rewrite inventory,
state/observation preservation, and validation evidence. Current tests
construct CID-scoped pyrun/v6 state directly; they do not reconstruct the
converter.

## Stale-Surface Gate

Final conformance must distinguish intentional rejection and immutable
historical-byte tests from active consumers. Runtime, help, guidance, and
maintained builders must expose no:

- `data refresh` action or targeted-refresh adapter;
- registry-digest acceptance path;
- pre-v6 job reader or compatibility execution path;
- legacy transfer-provenance decoder;
- declaration/evidence migration command, converter, or old-schema decoder.

The Phase 8 gate uses focused `rg` searches over scripts, skill guidance, docs,
and maintained builders, followed by the complete tool and agent-surface checks.
Any positive match must be classified as current implementation, explicit
unsupported-schema rejection, immutable historical preservation data, or a
stale surface that blocks completion.

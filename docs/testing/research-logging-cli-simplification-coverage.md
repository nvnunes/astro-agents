# Research-Logging Authoring Coverage

This index maps the current Record and Repair CLI to maintained public tests.
It supplements [the complete tool gate](research-logging.md), not the normative
[mechanical-validator contract](../research-log-mechanical-validator-spec.md)
or [reproduction contract](../research-log-reproduction-spec.md).

All named test modules live in `skills/research-logging/tests/`. Public
authoring tests use the dispatcher; execution tests use the bounded real
`pyrun` launcher. Literal Markdown and independent retained-byte comparisons
supply their expected results. Engine-only fixtures are not authoring proof.

## Command Sync

`test_log_command_sync.py` covers the following settled actions and contracts:

| Surface or contract | Named public proof |
| --- | --- |
| Implicit program CID; full override; explicit numbered invocation | `test_implicit_cid_selects_the_python_program_owner`; `test_full_override_selects_a_cid_different_from_the_program_stem`; `test_numeric_cid_distinguishes_multiple_invocations_of_one_program` |
| Batch selection, rename/delete, one dry-run/apply pair, invalid-member refusal | `test_batch_rename_and_delete_are_one_preview_and_apply`; `test_one_invalid_selected_command_prevents_every_registry_write`; `test_conflicting_renames_fail_before_publication` |
| Producer deletion with selected, renamed, or retargeted consumers; live-use refusal | `test_selected_consumer_edit_and_producer_delete_share_one_sync`; `test_renamed_consumer_edit_and_producer_delete_share_one_sync`; `test_retargeted_replacement_and_old_producer_delete_share_one_sync` |
| Selected target ownership and unchanged-registry publication | `test_multiple_selected_commands_share_one_declaration_and_target_change`; `test_rename_only_does_not_rewrite_unchanged_data_registry` |
| Generated file and missing directory bootstrap; real execution | `test_success_creates_pending_no_output_member_without_observing`; `test_generated_directory_bootstraps_and_runs_through_pyrun` |
| Origin file, origin directory, pinned Git, same-name cross-entry additions | `test_explicit_file_directory_git_and_cross_entry_add_forms` |
| Consistent assertions, omission, redundant ensure, conflicting assertion | `test_declaration_ensure_is_additive_idempotent_and_conflict_safe` |
| Local target update, identity preservation, shared ownership routing | `test_target_change_preserves_identity_and_rejects_shared_consumers`; `test_shared_target_change_lists_command_evidence_and_entry_consumers` |
| Policy change without observation loss | `test_combined_declarations_and_policy_update` |
| Per-CID stale-execution acknowledgement; current execution preserved | `test_one_cid_acknowledges_all_stale_parameter_executions`; `test_rename_with_changed_parameters_needs_one_aggregate_acknowledgement`; `test_redundant_stale_acknowledgement_is_accepted` |
| Missing name, missing producer, wrong kind, malformed registry diagnostics | `test_missing_name_shows_file_and_directory_declaration_forms`; `test_missing_generated_producer_names_the_bootstrap_action`; `test_wrong_add_kind_fails_without_writes`; `test_malformed_registry_requires_direct_repair_without_writes` |
| Dry run and coupled publication failure | `test_dry_run_has_two_diffs_and_zero_writes`; `test_second_file_failure_rolls_back_both_registries` |
| Scoped parse failures, validation follow-up, and competing producers | `test_unrelated_parse_failure_is_not_reported_or_blocking`; `test_validation_still_reports_unsupported_command_fence`; `test_rejected_competing_output_blocks_selected_sync` |
| New origins still reject recorded producers | `test_live_file_and_directory_origins_still_reject_recorded_producers` |

`test_log_command_sync.py::test_late_unselected_consumer_blocks_producer_delete`
is a bounded internal publication-recheck test. It inserts an unselected
Markdown consumer after preparation and asserts that deletion fails without
publishing either registry; it complements the public dispatcher proofs above.
`test_redundant_input_on_outputless_command_skips_producer_index` instruments
the internal producer-index boundary for a no-op assertion in both dry-run and
apply; the public additive/no-op outcomes are covered above.

## Evidence Compare And Sync

`test_log_evidence_sync.py` provides literal comment, presentation, and
normalized-state checks:

| Surface or contract | Named public proof |
| --- | --- |
| ID-scoped compare, placeholder fill and surrounding-byte preservation | `test_scalar_placeholder_compare_then_sync_preserves_neighbors` |
| Batch selectors, rename/delete, one dry-run/apply pair, invalid-member refusal | `test_replaces_six_records_with_one_in_one_public_change_set`; `test_rename_reevaluates_new_definition_and_updates_summary`; `test_invalid_member_prevents_batch_publication`; `test_conflicting_batch_selection_rejects_without_writes` |
| Unselected Markdown-only consumer blocks a target change | `test_target_change_rejects_unselected_markdown_only_consumer` |
| Evidence deletion preserves the data declaration without an advisory unused-data row | `test_delete_only_is_idempotent_without_unused_declaration_scan` |
| Additive changes to one definition aspect | `test_changed_aspect_needs_only_the_changed_comment_and_call` |
| Origins and directory members, unused/conflicting assertions | `test_directory_origin_reads_only_explicit_member`; `test_conflicting_and_unused_additions_are_no_write` |
| Source-scoped compare/sync across entries and summary references; same-name add-from-entry | `test_source_scope_updates_related_entries_and_forwarded_summary` |
| Closed compound forms and per-operand formats | `test_closed_compounds_have_exact_literal_spellings`; `test_compound_source_can_format_selected_fields_differently` |
| Selection, filtering, typed identity literals, numeric rendering and existing accepted spelling | `test_range_uses_observed_rows_and_preserves_existing_spelling`; `test_bounded_numeric_rendering_is_declared_not_inferred`; `test_observed_identity_literals_round_trip_through_full_validation`; `test_numeric_comment_filters_accept_matching_native_types_only` |
| Independent Boolean summary-table cell | `test_short_boolean_value_in_an_ordinary_summary_table_cell`; `test_log_record_workflow.py::test_mixed_independent_table_keeps_unmarked_values_unaccepted` |
| Direct table subset/reordering, exact headers and alignment; column transforms | `test_direct_table_preserves_header_alignment_and_formats_subset_order`; `test_table_filters_boolean_and_percentage_columns` |
| Inclusive lines/Unicode characters and literal comment text | `test_text_slices_use_inclusive_unicode_positions_and_preserve_fences`; `test_text_excerpt_treats_literal_eid_comments_as_source_text` |
| Inline whole diff, linked artifact baseline and unchanged Markdown fingerprint refresh | `test_inline_whole_diff_artifact_is_filled_and_refreshed`; `test_artifact_bytes_refresh_fingerprint_without_touching_markdown`; `test_source_scope_refreshes_all_member_artifact_fingerprints` |
| Invalid/unsupported definition, old schema, changed comment; no-write refusal | `test_invalid_definitions_fail_before_publishing`; `test_v4_registry_and_removed_sync_options_are_rejected_without_writes`; `test_validation_rejects_an_unsynchronized_comment_edit` |
| Dry run; related failure; changed observation; late publication failure; process overlap | `test_dry_run_changes_nothing`; `test_invalid_related_record_prevents_every_publication`; `test_changed_source_observation_prevents_every_publication`; `test_late_publication_failure_restores_all_owned_files`; `test_overlapping_source_syncs_serialize_under_bounded_launches` |

`test_research_log_validation_evidence.py::test_publication_recheck_rejects_replaced_file_with_same_bytes`
also reaches public sync and its real filesystem publication boundary.
`test_redundant_origin_and_target_assertions_skip_log_material_scan`,
`test_redundant_cross_entry_reference_skips_origin_scan`, and
`test_delete_without_data_edit_skips_unused_data_scan` instrument internal
scan boundaries for no-op and cross-entry-only operations in dry-run and apply.

## Data, Retention And Record Lifecycles

`test_log_graph_lifecycle.py` owns these public graph-level round trips:

| Surface or contract | Named public proof |
| --- | --- |
| Data update: target, kind, boundary and Markdown-first order | `test_kind_and_boundary_changes_have_a_workable_markdown_first_order` |
| Directory identity and Git commit:path update | `test_directory_identity_and_git_target_round_trip` |
| Reproduction comparison and evidence tolerance; omitted-property preservation | `test_data_and_evidence_round_trip_preserves_omitted_properties` |
| Shared update and explicit acknowledgment, including idempotent redundant update | `test_shared_data_update_lists_consumers_and_requires_acknowledgment` |
| Data rename/delete/list, cross-entry references and exact replacement validation | `test_cross_entry_data_rename_preserves_presentation_and_rolls_back_late_failure`; `test_data_rename_requires_every_parameter_replacement`; `test_data_and_evidence_round_trip_preserves_omitted_properties` |
| Retention add/update/rename/delete/list, additive targets/reason and connected refusal | `test_retention_additive_round_trip_and_connected_refusal` |
| Retention versus both consumer syncs; explicit coverage removal first | `test_retention_transfers_require_explicit_removal_before_either_sync` |
| Differently named cross-entry physical aliases block destructive ownership changes | `test_cross_entry_alias_blocks_physical_ownership_changes` |
| Command sync rename/delete, observations preserved and failure no-write; list query | `test_command_rename_delete_and_late_failure` |
| Evidence sync rename/delete, baseline preservation and remaining-use refusal; list query | `test_data_and_evidence_round_trip_preserves_omitted_properties`; `test_artifact_evidence_rename_preserves_accepted_baseline` |
| Semantic listing preserves declared numeric values without JSON editing | `test_semantic_evidence_list_preserves_numeric_declaration_values` |

Command `verify` and `show` retain `test_log_command_verify.py` and the
command diagnostic/query checks in `test_log.py`. Reorganization retains
`test_log_reorganize.py`; it seeds current normalized fixture declarations
without pretending that disconnected setup is consumer-owned authoring.

## Integrated Record And Reproduction

`test_log_record_workflow.py::test_directory_execution_and_cross_entry_evidence_refresh`
creates a Markdown command, bootstraps a missing generated directory, runs real
`pyrun`, authors owning and referencing evidence, reruns the producer, compares
once and syncs once by source, then validates clear. It covers a script-produced
derived table, column selection/reordering, text output, linked-artifact
fingerprints, ordinary independent table cells, summary forwarding, retention
and a second idempotent sync. Evidence refresh preserves execution observations.

`test_research_log_integrated_workflow.py::test_current_authoring_execution_and_validation_workflow`
also proves policy-only changes, explicit stale-execution deletion, a downstream
command, advanced data target changes and read-only reproduction preparation.
Maintained reproduction suites prove exact and evidence-scoped comparison,
intact retained baselines, tolerance, job lifecycle and failure publication;
authoring does not replace those contracts.

## Stale Surface And Guidance

`test_log_authoring_surface.py` checks the exact public action sets and rejects
removed parameters, runtime mutation/reader paths, advanced table routes,
definition-file guidance and active old-format workflow builders.
`test_log_data.py` checks unknown creation actions and malformed registry
no-write behavior. `test_research_log_validation_transformation.py` checks
removed table modes fail closed. Unsupported-schema/rejection fixtures are
intentional negative coverage, not installed compatibility.

`test_record_surface.py`, `test_reorganize_surface.py` and the deterministic
`scripts/validate_agent_surface.py` harness cover current skill routing,
provenance examples, Markdown-first ownership and package references.
The complete gate includes compile, Ruff, mypy, complexity, all research-tool
tests and diff-check; no focused test or passing count substitutes for it.

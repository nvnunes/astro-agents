# Research-Logging Validation

Use this reference for changes to research-logging tools, contracts, or
command/input-registry guidance affecting `pyrun`, as selected by
[Testing](../testing.md). Commands run from the project root in
the [repository environment](agent-surface.md#environment-and-deterministic-checks).
Focused checks belong to the affected change; the complete gate blocks
completion of any research-tool change. Unrelated research data problems do
not add work to these implementation gates.

The completed CLI simplification program has a durable
[finding and scenario coverage index](research-logging-cli-simplification-coverage.md).
The replacement validation model has a durable
[contract-to-test coverage index](research-log-validation-model-coverage.md).

## Focused Contract And Validator Checks

When changing `skills/research-logging/scripts/pyrun` or research-log
command/input-registry guidance that affects `pyrun`, also run:

```bash
./.conda/bin/python -m unittest skills/research-logging/tests/test_pyrun.py
```

When changing the bounded legacy `pyrun-outputs.json` migration reader, also
run its focused contract tests:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_research_log_pyrun_outputs.py'
```

When changing the current `pyrun.json` contract or lifecycle, also run:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_research_log_output_bindings.py \
  skills/research-logging/tests/test_research_log_pyrun_state.py \
  skills/research-logging/tests/test_log_command_sync.py \
  skills/research-logging/tests/test_pyrun.py
```

When changing research-log section classification, evidence presentation, or
validation behavior, also run:

```bash
./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_research_log_validation*.py'
```

For changes spanning authoring, execution, validation, or reproduction
preparation, run the current-format workflow test while iterating:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_research_log_integrated_workflow.py
```

For output-code currentness or shared-research-graph material classification,
use these focused tests while iterating before running that complete validator
set:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_research_log_validation_engine.py \
  skills/research-logging/tests/test_research_log_validation_material_graph.py
```

## Subprocess Fixture Launches

Tests that execute a temporary `pyrun` must use `run_pyrun_process` from
`research_log_cli_test_support`. The helper invokes the launcher through
`/bin/sh` and sets a bounded timeout. Do not pass a temporary shebang script
directly to `subprocess.run` or `subprocess.Popen` from sandboxed tests: on
managed macOS hosts, interpreter dispatch can wait before the script body
starts. Any new subprocess-backed test helper must also set a timeout.

Executable-bit and shebang-dispatch coverage for `./pyrun` belongs in a
separately identified, unsandboxed platform smoke test. The complete tool suite
tests the launcher's bootstrap behavior through explicit shell dispatch.

## Complete Tool Gate

For any research-logging tool change, run the complete tool gate rather than
linting only the main validator:

The complete unittest suite includes process-lifecycle tests that enumerate
the host process table to track detached descendants. When running this gate
from a sandboxed agent session, request elevated execution for the complete
unittest command before its first attempt. Do not run it in the process-
observation sandbox and retry after the expected permission failure.

```bash
./.conda/bin/python -m py_compile skills/research-logging/scripts/log \
  skills/research-logging/scripts/pyrun \
  skills/research-logging/scripts/pyrun_code_dependencies.py \
  skills/research-logging/scripts/stream_capture.py \
  skills/research-logging/scripts/research_log_data.py \
  skills/research-logging/scripts/research_log_paths.py \
  skills/research-logging/scripts/log_commands/*.py \
  skills/research-logging/scripts/validation/*.py
./.conda/bin/ruff check skills/research-logging/scripts \
  skills/research-logging/tests
./.conda/bin/mypy
./.conda/bin/python scripts/check_research_logging_complexity.py
./.conda/bin/python -m unittest discover \
  -s skills/research-logging/tests -p 'test_*.py'
```

The pinned local Conda environment is the quality gate. Ambient Python, Ruff,
or mypy installations may be used for diagnosis, but not as completion
evidence for a research-logging tool change.

## Reproduction Checks

For reproduction planning, comparison, and staging development, run:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_log_reproduction_admission.py \
  skills/research-logging/tests/test_log_reproduction_planning.py \
  skills/research-logging/tests/test_reproduction_job_storage.py \
  skills/research-logging/tests/test_log_reproduction_scheduler.py \
  skills/research-logging/tests/test_log_reproduction_execution_selection.py \
  skills/research-logging/tests/test_log_reproduction_comparison.py \
  skills/research-logging/tests/test_log_reproduction_results.py \
  skills/research-logging/tests/test_log_reproduction_jobs.py \
  skills/research-logging/tests/test_log_reproduction_empty_recovery.py \
  skills/research-logging/tests/test_log_reproduction_promotion.py \
  skills/research-logging/tests/test_log_reproduction_publication.py \
  skills/research-logging/tests/test_log_reproduction_queries.py
```

For consolidated result storage, render recovery, or scaffold changes, also run
the focused store tests:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_research_log_result_store.py \
  skills/research-logging/tests/test_log_reproduction_empty_recovery.py \
  skills/research-logging/tests/test_log_report_render.py \
  skills/research-logging/tests/test_log_scaffold.py
```

This focused command supplements rather than replaces the complete tool gate.

For changes to the canonical validation model, publication lifecycle, saved
queries, or public validation projections, run:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_research_log_validation_domain.py \
  skills/research-logging/tests/test_research_log_validation_batches.py \
  skills/research-logging/tests/test_research_log_validation_research_graph.py \
  skills/research-logging/tests/test_validation_snapshot_storage.py \
  skills/research-logging/tests/test_validation_snapshot_storage_conformance.py \
  skills/research-logging/tests/test_validation_read_model.py \
  skills/research-logging/tests/test_validation_saved_queries.py \
  skills/research-logging/tests/test_research_log_report_context.py \
  skills/research-logging/tests/test_log_report_render.py \
  skills/research-logging/tests/test_research_log_validation_cli.py
```

This is the focused validation model, publication, read-model, and projection
gate. It also supplements rather than replaces the complete tool gate.

The reproduction suite uses fixed-plan fixtures. Its completion coverage must
exercise finding-owned admission over the fresh validation snapshot and shared
research graph, exact command/execution/output binding, cross-type batch
independence, blocker dependency propagation, locked fresh preparation, strict
accepted plan insertion and SQLite
reconstruction, run-store and scheduler transactions, stop and interruption
recovery, dependency failure and independent progress, publication-only retry,
source-change rejection and new-run preparation, comparison-baseline
invariance, reservation/promotion overlap, and validation-snapshot invariance.
It must not restore continuation, whole-snapshot certification, JSON job
decoding, or accepted-attempt lineage coverage.

For reproduction execution and process-lifecycle development, run the focused
controlled-fixture suite outside any enclosing process-observation sandbox:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_log_reproduction_execution.py
```

On macOS, explicitly exercise the production Seatbelt profile as a separate
host-confinement smoke test:

```bash
REPRODUCTION_SANDBOX_TEST=1 \
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_log_reproduction_execution.py \
  skills/research-logging/tests/test_log_command_verify.py
```

These tests use only generated projects and synthetic workers. The enabled
host tests must prove both network denial and retained-boundary write denial
for durable reproduction and isolated command verification. They never execute a
maintained research recipe.

The complexity check is a ratchet over explicitly recorded complexity debt. It
allows refactoring to reduce findings, but rejects a new complex function, a
higher complexity score, or growth in the total advisory finding count.

### Research-Log Mechanical Validation Boundary

The normative behavior exercised by this gate is owned by
[`docs/research-log-mechanical-validator-spec.md`](../research-log-mechanical-validator-spec.md).
Keep field grammars, schema versions, lifecycle rules, limits, and diagnostic
semantics there rather than restating them in this testing guide. When a
contract changes, update the specification and its tests; update this section
only when the gate or its coverage boundaries change.

The complete research-logging tool gate must cover:

- public CLI discovery, dispatch, authoring, reorganization, and validation
  operations defined by [Public Management And Validation
  Operations](../research-log-mechanical-validator-spec.md#public-management-and-validation-operations);
- evidence sources, locators, transformations, presentations, and summary
  associations defined by [Evidence-Record Role And
  Scope](../research-log-mechanical-validator-spec.md#evidence-record-role-and-scope)
  and [Evidence File And Presentation
  Association](../research-log-mechanical-validator-spec.md#evidence-file-and-presentation-association);
- input, retention, command, output-support, provenance, lineage, and Orphans
  behavior defined by [Input Registry And Artifact Graph
  Contract](../research-log-mechanical-validator-spec.md#input-registry-and-artifact-graph-contract);
- evaluation, publication, generated-state, locking, dry-run, failure, and
  currentness behavior defined by [Mechanical Validation Evaluation And
  Outcomes](../research-log-mechanical-validator-spec.md#mechanical-validation-evaluation-and-outcomes);
- cache, observation, and materialization behavior under the specification's
  [Resource And Safety Bounds](../research-log-mechanical-validator-spec.md#resource-and-safety-bounds)
  and [Dependency Projection And
  Currentness](../research-log-mechanical-validator-spec.md#dependency-projection-and-currentness);
- focused Record, Replace, Update Summary, Repair, Reorganize, and Validate
  behavior cases in
  [`skills/research-logging/tests/presented-evidence-cases.md`](../../skills/research-logging/tests/presented-evidence-cases.md); and
- researcher-directed semantic Review routing, lens, composition, authority,
  and neighboring-workflow cases in
  [`skills/research-logging/tests/semantic-review-cases.md`](../../skills/research-logging/tests/semantic-review-cases.md).

Run the complete research-logging tool gate after any validator change. Use the
focused controller, engine, evidence, command, locator, transformation,
provenance, shared-graph material-classification, and publication tests during
iteration.

Wall time is diagnostic rather than an objective gate. Require bounded
complexity, no avoidable repeated reads or hashes, correct cache reuse, and no
asymptotic regression.

## Command-Verification Coverage

Current command-verification tests must prove its isolated workspace is the only
artifact written, selected current inputs and retained baselines are stable,
and no reproduction run, publication, promotion, or metadata update occurs.
Do not retain execution-target reproduction or `--verify-repair` fixtures.

Run the focused command and command-diagnostic gate with:

```bash
PYTHONPATH=skills/research-logging/scripts:skills/research-logging/tests \
  ./.conda/bin/python -m unittest \
  skills/research-logging/tests/test_log_command_verify.py \
  skills/research-logging/tests/test_command_diagnostic_storage.py \
  skills/research-logging/tests/test_log_command_sync.py \
  skills/research-logging/tests/test_rejected_producer_diagnostics.py
```

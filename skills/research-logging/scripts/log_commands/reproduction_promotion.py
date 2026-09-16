"""Explicit copy-based promotion of one complete staged execution output set."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, cast

from research_log_data import Fingerprint
from research_log_paths import REPRODUCTION_REPORT
from validation.operation_state import operation_directory, operation_lock
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    ObservedExecution,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    load_pyrun_state,
    validated_pyrun_serialization,
)

from .context import (
    LogContext,
    resolve_entry,
    resolve_project_root,
)
from .model import ActionError
from .reproduction_domain import CommandOutcome
from .reproduction_execution import _fingerprint
from .reproduction_job_control import (
    JobStoreError,
    recognize_run_directory,
)
from .reproduction_jobs import _find_run, load_accepted_plan
from .reproduction_paths import iter_canonical_run_roots
from .reproduction_run import ArtifactResult
from .reproduction_work import CommandWork
from .reproduction_work_job import open_work_job
from .reproduction_work_plan import ReproductionPlan
from .storage import atomic_write_text, atomic_write_texts, entry_lock

MAX_ACTIVE_RUNS = 100_000


@dataclass(frozen=True)
class PromotionResult:
    """One successfully promoted whole execution output set."""

    run_id: str
    entry: str
    cid: str
    execution_id: str
    outputs: tuple[str, ...]

    def as_dict(self) -> Mapping[str, object]:
        return {
            "entry": self.entry,
            "cid": self.cid,
            "execution_id": self.execution_id,
            "outputs": list(self.outputs),
            "run_id": self.run_id,
            "status": "promoted",
        }


@dataclass(frozen=True)
class _PromotedOutput:
    artifact: str
    kind: str
    staged: Path
    destination: Path
    baseline: Fingerprint
    fingerprint: Fingerprint


@dataclass(frozen=True)
class _InstalledOutput:
    destination: Path
    displaced: Path
    replacement: Path


@dataclass(frozen=True)
class _StagingBundle:
    work: CommandWork
    artifacts: tuple[ArtifactResult, ...]
    workspace_path: str


@dataclass(frozen=True)
class _PromotionResolution:
    """Accepted run, entry, and staging inputs for one promotion resolution."""

    project: Path
    entry_root: Path
    run_root: Path
    plan: ReproductionPlan
    entry_id: str
    cid: str
    execution_id: str
    bundle: _StagingBundle


def promote_execution(
    log: LogContext, *, run_id: str, cid: str, execution_id: str
) -> PromotionResult:
    """Promote one complete current staging bundle without changing its source."""

    run_root = _find_run(log, run_id)
    plan = load_accepted_plan(run_root)
    bundle = _load_staging_bundle(run_root, run_id, cid, execution_id)
    entry_id = bundle.work.identity.entry
    entry = resolve_entry(log, entry_id)
    project = resolve_project_root(log.root)
    with entry_lock(entry):
        resolution = _PromotionResolution(
            project,
            entry.root,
            run_root,
            plan,
            entry_id,
            cid,
            execution_id,
            bundle,
        )
        outputs = _resolve_outputs(resolution)
        marker = _begin_promotion(log, run_id, execution_id, outputs)
        try:
            _publish_promotion(log, resolution, outputs)
        finally:
            _finish_promotion(log, marker)
    return PromotionResult(
        run_id,
        entry_id,
        cid,
        execution_id,
        tuple(item.artifact for item in outputs),
    )


def _load_staging_bundle(
    run_root: Path, run_id: str, cid: str, execution_id: str
) -> _StagingBundle:
    """Resolve native complete production without requiring equal comparisons."""
    with open_work_job(run_root) as job:
        if job.accepted.run_id != run_id:
            raise ActionError(
                "reproduction.promotion.execution_missing", "run identity changed"
            )
        matches = [
            work
            for work in job.accepted.plan.commands
            if work.identity.cid == cid and work.identity.execution_id == execution_id
        ]
        if len(matches) != 1:
            raise ActionError(
                "reproduction.promotion.execution_missing",
                f"expected one staged execution, found {len(matches)}",
            )
        work = matches[0]
        result = job.load_command_result(work.identity)
        if result is None or result.outcome is not CommandOutcome.SUCCEEDED:
            raise ActionError(
                "reproduction.promotion.incomplete", "staged execution is incomplete"
            )
        artifacts = tuple(
            job.load_artifact_result(artifact.identity)
            for artifact in job.accepted.plan.artifacts
            if artifact.producer == work.identity
        )
        if any(artifact is None for artifact in artifacts) or (
            {
                artifact.identity.artifact
                for artifact in artifacts
                if artifact is not None
            }
            != {name for name, _ in work.execution.recipe.outputs}
        ):
            raise ActionError(
                "reproduction.promotion.incomplete",
                "complete output comparisons are missing",
            )
        return _StagingBundle(
            work,
            tuple(artifact for artifact in artifacts if artifact is not None),
            job.accepted.workspace_path,
        )


def _resolve_outputs(context: _PromotionResolution) -> tuple[_PromotedOutput, ...]:
    work = context.bundle.work
    state = load_pyrun_state(
        context.entry_root / "pyrun.json",
        entry_root=context.entry_root,
        project_root=context.project,
    )
    execution = state.execution(context.cid, context.execution_id)
    if execution is None or (
        execution.recipe.as_dict() != work.execution.recipe.as_dict()
        or execution.observed.as_dict() != work.execution.observed.as_dict()
    ):
        raise ActionError(
            "reproduction.promotion.execution_changed",
            "current execution no longer matches accepted promotion baseline",
        )
    records = {
        artifact.identity.artifact: artifact for artifact in context.bundle.artifacts
    }
    if set(records) != {name for name, _ in execution.recipe.outputs}:
        raise ActionError(
            "reproduction.promotion.output_set_changed",
            "staged output inventory changed",
        )
    return tuple(
        _resolve_promoted_output(context, artifact, kind, records[artifact])
        for artifact, kind in execution.recipe.outputs
    )


def _resolve_promoted_output(
    context: _PromotionResolution, artifact: str, kind: str, record: ArtifactResult
) -> _PromotedOutput:
    work = context.bundle.work
    destination = output_target_path(
        artifact, entry_root=context.entry_root, project_root=context.project
    )
    baseline = dict(work.execution.observed.outputs).get(artifact)
    if (
        baseline is None
        or not destination.exists()
        or destination.is_symlink()
        or _fingerprint(destination, kind) != baseline
    ):
        raise ActionError(
            "reproduction.promotion.baseline_changed",
            f"promotion destination no longer matches accepted baseline: {artifact}",
        )
    if record.regenerated_path is None or record.regenerated is None:
        raise ActionError(
            "reproduction.promotion.incomplete",
            f"staged output is incomplete: {artifact}",
        )
    bundle_root = _safe_run_path(context.run_root, context.bundle.workspace_path)
    try:
        expected = destination.relative_to(context.project)
        staged_relative = Path(record.regenerated_path).relative_to(bundle_root)
    except ValueError as error:
        raise ActionError("reproduction.promotion.staging_invalid", artifact) from error
    if staged_relative != expected:
        raise ActionError(
            "reproduction.promotion.staging_invalid", f"foreign output path: {artifact}"
        )
    staged = _safe_run_path(bundle_root, staged_relative.as_posix())
    fingerprint = record.regenerated
    if (
        staged.is_symlink()
        or not staged.exists()
        or _fingerprint(staged, kind) != fingerprint
    ):
        raise ActionError(
            "reproduction.promotion.staged_changed",
            f"staged output changed or disappeared: {artifact}",
        )
    return _PromotedOutput(artifact, kind, staged, destination, baseline, fingerprint)


def _begin_promotion(
    log: LogContext,
    run_id: str,
    execution_id: str,
    outputs: Sequence[_PromotedOutput],
) -> Path:
    token = secrets.token_hex(12)
    project = resolve_project_root(log.root)
    marker = operation_directory(project) / f"promotion-{token}.json"
    with operation_lock(project, "reproduction-promotion-index.lock"):
        _require_no_active_input_overlap(log, outputs)
        with operation_lock(log.root, "reproduction-publication.lock"):
            atomic_write_text(
                marker,
                _canonical(
                    {
                        "execution_id": execution_id,
                        "outputs": [
                            item.destination.resolve().as_posix() for item in outputs
                        ],
                        "run_id": run_id,
                        "started_at": _utc_now(),
                    }
                ),
            )
    return marker


def _finish_promotion(log: LogContext, marker: Path) -> None:
    with operation_lock(
        resolve_project_root(log.root), "reproduction-promotion-index.lock"
    ):
        with operation_lock(log.root, "reproduction-publication.lock"):
            marker.unlink(missing_ok=True)


def _require_no_active_input_overlap(
    log: LogContext, outputs: Sequence[_PromotedOutput]
) -> None:
    promoted = {item.destination.resolve() for item in outputs}
    project = resolve_project_root(log.root)
    try:
        run_roots = iter_canonical_run_roots(project, max_entries=MAX_ACTIVE_RUNS)
    except OSError as error:
        raise ActionError("reproduction.promotion.state_invalid", str(error)) from error
    for run_root in run_roots:
        if recognize_run_directory(run_root, project) != "current":
            continue
        try:
            with open_work_job(run_root) as job:
                status = job.load_run_control()
        except JobStoreError as error:
            raise ActionError(error.code, str(error)) from error
        if status.status is not None:
            continue
        plan = load_accepted_plan(run_root)
        materials = plan.materials
        inputs = {
            Path(cast(str, item["identity"])).resolve()
            for item in materials
            if item.get("role") == "boundary" and isinstance(item.get("identity"), str)
        }
        overlap = _overlapping_paths(promoted, inputs)
        if overlap:
            raise ActionError(
                "reproduction.promotion.conflict",
                f"active reproduction reads a promoted output: {min(overlap)}",
            )


def _overlapping_paths(left: set[Path], right: set[Path]) -> set[Path]:
    """Return paths that overlap directly or through a directory boundary."""

    return {
        source
        for source in left
        for target in right
        if source == target or source in target.parents or target in source.parents
    }


def _publish_promotion(
    log: LogContext,
    resolution: _PromotionResolution,
    outputs: Sequence[_PromotedOutput],
) -> None:
    project = resolve_project_root(log.root)
    text_candidates, prior_text = _metadata_candidates(log, resolution, outputs)
    installed: tuple[_InstalledOutput, ...] = ()
    try:
        installed = _install_outputs(project, outputs)
        atomic_write_texts(text_candidates)
        from research_log_result_store import result_generation

        expected_generation = result_generation(log.root, "reproduction")
        updates = _report_candidates(log, outputs)
        with operation_lock(log.root, "reproduction-publication.lock"):
            from research_log_result_store import (
                invalidate_report_materialization,
                record_report_materialization,
                result_generation,
                results_lock,
            )

            with results_lock(log.root):
                if result_generation(log.root, "reproduction") != expected_generation:
                    raise ActionError(
                        "results.report.write_failed",
                        "reproduction result changed before promotion report "
                        "replacement",
                    )
                invalidate_report_materialization(log.root, "reproduction")
                atomic_write_texts(updates)
                report = updates[log.root / REPRODUCTION_REPORT]
                record_report_materialization(
                    log.root,
                    "reproduction",
                    report.encode(),
                    expected_generation=expected_generation,
                )
    except BaseException:
        rollback_errors = _rollback_outputs(installed)
        try:
            atomic_write_texts(prior_text)
        except BaseException as error:
            rollback_errors.append(str(error))
        if rollback_errors:
            raise ActionError(
                "reproduction.promotion.rollback_failed",
                "; ".join(rollback_errors),
            )
        raise
    _discard_displaced(installed)


def _metadata_candidates(
    log: LogContext,
    resolution: _PromotionResolution,
    outputs: Sequence[_PromotedOutput],
) -> tuple[dict[Path, str], dict[Path, str]]:
    project = resolve_project_root(log.root)
    entry = resolve_entry(log, resolution.entry_id)
    state = load_pyrun_state(
        entry.root / "pyrun.json", entry_root=entry.root, project_root=project
    )
    execution = state.execution(resolution.cid, resolution.execution_id)
    if execution is None:
        raise ActionError(
            "reproduction.promotion.execution_changed",
            "execution is no longer current",
        )
    accepted = resolution.bundle.work
    if (
        execution.recipe.as_dict() != accepted.execution.recipe.as_dict()
        or execution.observed.as_dict() != accepted.execution.observed.as_dict()
    ):
        raise ActionError(
            "reproduction.promotion.execution_changed",
            "current execution no longer matches accepted promotion baseline",
        )
    recorded = dict(accepted.execution.observed.outputs)
    for output in outputs:
        if (
            not output.destination.exists()
            or output.destination.is_symlink()
            or recorded.get(output.artifact) != output.baseline
            or _fingerprint(output.destination, output.kind) != output.baseline
            or output.staged.is_symlink()
            or not output.staged.exists()
            or _fingerprint(output.staged, output.kind) != output.fingerprint
        ):
            raise ActionError(
                "reproduction.promotion.baseline_changed",
                f"promotion destination changed before transaction: {output.artifact}",
            )
    fingerprints = {item.artifact: item.fingerprint for item in outputs}
    candidate_execution = PyrunExecution(
        False,
        execution.auto_reproduce,
        execution.last_run_at,
        execution.runner,
        execution.environment_profile,
        execution.execution_contract,
        execution.recipe,
        ObservedExecution(
            execution.observed.script,
            execution.observed.inputs,
            execution.observed.code,
            tuple(
                (name, fingerprints[name]) for name, _kind in execution.recipe.outputs
            ),
        ),
        execution.exclusive,
    )
    command = state.commands.get(resolution.cid)
    if command is None:
        raise ActionError(
            "reproduction.promotion.execution_changed",
            "command is no longer current",
        )
    executions = dict(command.executions)
    executions[resolution.execution_id] = candidate_execution
    commands = dict(state.commands)
    commands[resolution.cid] = PyrunCommand(executions)
    candidate_state = PyrunFile(state.path, state.entry_root, commands)
    updates = {
        state.path: validated_pyrun_serialization(candidate_state, project_root=project)
    }
    prior = {path: path.read_text(encoding="utf-8") for path in updates}
    return updates, prior


def _report_candidates(
    log: LogContext, outputs: Sequence[_PromotedOutput]
) -> Mapping[Path, str]:
    """Render immutable saved facts; promotion does not rewrite research outcomes."""
    from .reproduction_inspection import load_inspection
    from .reproduction_saved_report import compose_saved_report

    return {log.root / REPRODUCTION_REPORT: compose_saved_report(load_inspection(log))}


def _install_outputs(
    project: Path, outputs: Sequence[_PromotedOutput]
) -> tuple[_InstalledOutput, ...]:
    tmp = project / "tmp"
    tmp.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="promotion-", dir=tmp))
    installed: list[_InstalledOutput] = []
    try:
        for index, item in enumerate(outputs):
            destination = item.destination
            if (
                destination.is_symlink()
                or not destination.exists()
                or item.kind == "file"
                and not destination.is_file()
                or item.kind == "directory"
                and not destination.is_dir()
                or _fingerprint(destination, item.kind) != item.baseline
                or item.staged.is_symlink()
                or not item.staged.exists()
                or _fingerprint(item.staged, item.kind) != item.fingerprint
            ):
                raise ActionError(
                    "reproduction.promotion.destination_changed", str(destination)
                )
            replacement = (
                destination.parent
                / f".{destination.name}.promotion-{secrets.token_hex(8)}"
            )
            _copy_path(item.staged, replacement, item.kind)
            if _fingerprint(replacement, item.kind) != item.fingerprint:
                raise ActionError(
                    "reproduction.promotion.copy_changed", str(item.staged)
                )
            displaced = root / f"displaced-{index}"
            os.replace(destination, displaced)
            installed.append(_InstalledOutput(destination, displaced, replacement))
            os.replace(replacement, destination)
    except BaseException:
        rollback_errors = _rollback_outputs(tuple(installed))
        if rollback_errors:
            # Keep the private displaced tree intact: it is the only durable
            # copy of an original that could not be restored.
            raise ActionError(
                "reproduction.promotion.rollback_failed",
                "; ".join(rollback_errors),
            )
        shutil.rmtree(root, ignore_errors=True)
        raise
    return tuple(installed)


def _rollback_outputs(installed: Sequence[_InstalledOutput]) -> list[str]:
    errors: list[str] = []
    for item in reversed(installed):
        try:
            _remove_path(item.destination)
            os.replace(item.displaced, item.destination)
            _remove_path(item.replacement)
        except BaseException as error:
            errors.append(f"{item.destination}: {error}")
    return errors


def _discard_displaced(installed: Sequence[_InstalledOutput]) -> None:
    roots = {item.displaced.parent for item in installed}
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)


def _copy_path(source: Path, destination: Path, kind: str) -> None:
    if kind == "file":
        shutil.copy2(source, destination)
    elif kind == "directory":
        shutil.copytree(source, destination, symlinks=False)
    else:
        raise ActionError("reproduction.promotion.kind_invalid", kind)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _safe_run_path(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise ActionError(
            "reproduction.promotion.staging_invalid", "staged path is invalid"
        )
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ActionError(
                "reproduction.promotion.staging_invalid",
                "staged path traverses a symlink",
            )
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ActionError(
            "reproduction.promotion.staging_invalid", "staged path escapes its bundle"
        ) from error
    return candidate


def _required_string(value: Mapping[str, object], name: str) -> str:
    selected = value.get(name)
    if not isinstance(selected, str) or not selected:
        raise ActionError("reproduction.promotion.staging_invalid", f"missing {name}")
    return selected


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )

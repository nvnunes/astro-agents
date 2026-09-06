"""Explicit copy-based promotion of one complete staged execution output set."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, cast

from research_log_data import DataFile, Fingerprint, load_data_file, parse_fingerprint
from validation.human_projection import load_report_context
from validation.operation_state import operation_directory, operation_lock
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    load_pyrun_state,
    validated_pyrun_serialization,
)
from validation.report import compose_validation_report
from validation.targeted_refresh import (
    TargetedRefreshError,
    refresh_promoted_provenance,
)

from .context import (
    LogContext,
    parse_entry_directory_name,
    resolve_entry,
    resolve_project_root,
)
from .model import ActionError
from .reproduction_comparison import STAGING_SCHEMA
from .reproduction_execution import _fingerprint
from .reproduction_jobs import _find_run, _load_run, _plan_from_record
from .reproduction_paths import resolve_project_tmp
from .reproduction_planner import (
    project_reproduction_state,
    verify_reproduction_snapshot,
)
from .reproduction_publication import _load_validation, _require_admissible_validation
from .reproduction_results import (
    compose_reproduction_report,
    load_reproduction_results,
    project_current_results,
    reconcile_run_folders,
)
from .storage import atomic_write_text, atomic_write_texts, entry_lock

MAX_STAGING_BYTES = 64 << 20
MAX_ACTIVE_RUNS = 100_000


@dataclass(frozen=True)
class PromotionResult:
    """One successfully promoted whole execution output set."""

    run_id: str
    entry: str
    execution_id: str
    outputs: tuple[str, ...]

    def as_dict(self) -> Mapping[str, object]:
        return {
            "entry": self.entry,
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
    fingerprint: Fingerprint


@dataclass(frozen=True)
class _InstalledOutput:
    destination: Path
    displaced: Path
    replacement: Path


def promote_execution(
    log: LogContext, *, run_id: str, execution_id: str
) -> PromotionResult:
    """Promote one complete current staging bundle without changing its source."""

    run_root = _find_run(log, run_id)
    run_record = _load_run(run_root / "run.json")
    plan = _plan_from_record(run_record)
    bundle = _load_bundle(run_root, run_id, execution_id)
    entry_id = _required_string(bundle, "entry")
    entry = resolve_entry(log, entry_id)
    project = resolve_project_root(log.root)
    outputs = _resolve_outputs(project, entry.root, run_root, execution_id, bundle)
    verify_reproduction_snapshot(log, plan)
    with entry_lock(entry):
        verify_reproduction_snapshot(log, plan)
        marker = _begin_promotion(log, run_id, execution_id, outputs)
        try:
            _publish_promotion(log, entry_id, execution_id, outputs)
        finally:
            _finish_promotion(log, marker)
    return PromotionResult(
        run_id,
        entry_id,
        execution_id,
        tuple(item.artifact for item in outputs),
    )


def _load_bundle(
    run_root: Path, run_id: str, execution_id: str
) -> Mapping[str, object]:
    path = run_root / "staging.json"
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_STAGING_BYTES
    ):
        raise ActionError("reproduction.promotion.staging_missing", str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ActionError(
            "reproduction.promotion.staging_invalid", str(error)
        ) from error
    if not isinstance(value, Mapping) or set(value) != {
        "executions",
        "run_id",
        "schema",
        "target",
    }:
        raise ActionError("reproduction.promotion.staging_invalid", str(path))
    executions = value.get("executions")
    if (
        value.get("schema") != STAGING_SCHEMA
        or value.get("run_id") != run_id
        or not isinstance(executions, list)
    ):
        raise ActionError("reproduction.promotion.staging_invalid", str(path))
    matches = [
        item
        for item in executions
        if isinstance(item, Mapping) and item.get("execution_id") == execution_id
    ]
    if len(matches) != 1:
        raise ActionError(
            "reproduction.promotion.execution_missing",
            f"expected one staged execution, found {len(matches)}",
        )
    bundle = cast(Mapping[str, object], matches[0])
    if (
        set(bundle)
        != {
            "bytes",
            "complete",
            "diagnostics",
            "entry",
            "execution_id",
            "outputs",
            "path",
        }
        or bundle.get("complete") is not True
    ):
        raise ActionError(
            "reproduction.promotion.incomplete", "staged execution is incomplete"
        )
    return bundle


def _resolve_outputs(
    project: Path,
    entry_root: Path,
    run_root: Path,
    execution_id: str,
    bundle: Mapping[str, object],
) -> tuple[_PromotedOutput, ...]:
    state = load_pyrun_state(
        entry_root / "pyrun.json", entry_root=entry_root, project_root=project
    )
    execution = state.executions.get(execution_id)
    if execution is None:
        raise ActionError(
            "reproduction.promotion.execution_changed", "execution is no longer current"
        )
    raw_outputs = bundle.get("outputs")
    bundle_path = bundle.get("path")
    if not isinstance(raw_outputs, list) or not isinstance(bundle_path, str):
        raise ActionError(
            "reproduction.promotion.staging_invalid", "invalid staged output list"
        )
    records: dict[str, Mapping[str, object]] = {}
    for value in raw_outputs:
        if not isinstance(value, Mapping) or set(value) != {
            "artifact",
            "available",
            "expected",
            "kind",
            "outcome",
            "reason",
            "regenerated",
            "staged",
        }:
            raise ActionError(
                "reproduction.promotion.staging_invalid", "invalid staged output"
            )
        artifact = value.get("artifact")
        if not isinstance(artifact, str) or artifact in records:
            raise ActionError(
                "reproduction.promotion.staging_invalid", "duplicate staged output"
            )
        records[artifact] = value
    expected = dict(execution.recipe.outputs)
    if set(records) != set(expected):
        raise ActionError(
            "reproduction.promotion.output_set_changed",
            "staged outputs do not equal the current execution output set",
        )
    bundle_root = _safe_run_path(run_root, bundle_path)
    results: list[_PromotedOutput] = []
    for artifact, kind in execution.recipe.outputs:
        record = records[artifact]
        staged_path = record.get("staged")
        if (
            record.get("available") is not True
            or record.get("kind") != kind
            or not isinstance(staged_path, str)
            or record.get("regenerated") is None
        ):
            raise ActionError(
                "reproduction.promotion.incomplete",
                f"staged output is incomplete: {artifact}",
            )
        staged = _safe_run_path(bundle_root, staged_path)
        destination = output_target_path(
            artifact, entry_root=entry_root, project_root=project
        )
        fingerprint = parse_fingerprint(record["regenerated"], f"promotion:{artifact}")
        if (
            staged.is_symlink()
            or not staged.exists()
            or _fingerprint(staged, kind) != fingerprint
        ):
            raise ActionError(
                "reproduction.promotion.staged_changed",
                f"staged output changed or disappeared: {artifact}",
            )
        results.append(
            _PromotedOutput(artifact, kind, staged, destination, fingerprint)
        )
    return tuple(results)


def _begin_promotion(
    log: LogContext,
    run_id: str,
    execution_id: str,
    outputs: Sequence[_PromotedOutput],
) -> Path:
    token = secrets.token_hex(12)
    project = resolve_project_root(log.root)
    marker = operation_directory(project) / f"promotion-{token}.json"
    with operation_lock(log.root, "reproduction-publication.lock"):
        with operation_lock(project, "reproduction-promotion-index.lock"):
            _require_no_active_input_overlap(log, outputs)
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
    with operation_lock(log.root, "reproduction-publication.lock"):
        with operation_lock(
            resolve_project_root(log.root), "reproduction-promotion-index.lock"
        ):
            marker.unlink(missing_ok=True)


def _require_no_active_input_overlap(
    log: LogContext, outputs: Sequence[_PromotedOutput]
) -> None:
    promoted = {item.destination.resolve() for item in outputs}
    project = resolve_project_root(log.root)
    try:
        tmp = resolve_project_tmp(project)
    except OSError as error:
        raise ActionError("reproduction.promotion.state_invalid", str(error)) from error
    for index, run_root in enumerate(sorted(tmp.iterdir(), key=lambda path: path.name)):
        if index >= MAX_ACTIVE_RUNS:
            raise ActionError(
                "reproduction.promotion.resource_limit",
                "active run scan limit exceeded",
            )
        path = run_root / "run.json"
        if run_root.is_symlink() or not path.is_file() or path.is_symlink():
            continue
        record = _load_run(path)
        if cast(Mapping[str, object], record["state"])["status"] is not None:
            continue
        plan = _plan_from_record(record)
        materials = cast(
            Sequence[Mapping[str, object]], plan.source_snapshot["materials"]
        )
        inputs = {
            Path(cast(str, item["identity"])).resolve()
            for item in materials
            if item.get("role") == "boundary" and isinstance(item.get("identity"), str)
        }
        overlap = promoted & inputs
        if overlap:
            raise ActionError(
                "reproduction.promotion.conflict",
                f"active reproduction reads a promoted output: {min(overlap)}",
            )


def _publish_promotion(
    log: LogContext,
    entry_id: str,
    execution_id: str,
    outputs: Sequence[_PromotedOutput],
) -> None:
    project = resolve_project_root(log.root)
    text_candidates, prior_text = _metadata_candidates(
        log, entry_id, execution_id, outputs
    )
    installed: tuple[_InstalledOutput, ...] = ()
    try:
        installed = _install_outputs(project, outputs)
        atomic_write_texts(text_candidates)
        with operation_lock(log.root, "reproduction-publication.lock"):
            updates = _report_candidates(log, outputs)
            atomic_write_texts(updates)
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
    entry_id: str,
    execution_id: str,
    outputs: Sequence[_PromotedOutput],
) -> tuple[dict[Path, str], dict[Path, str]]:
    project = resolve_project_root(log.root)
    entry = resolve_entry(log, entry_id)
    state = load_pyrun_state(
        entry.root / "pyrun.json", entry_root=entry.root, project_root=project
    )
    execution = state.executions[execution_id]
    fingerprints = {item.artifact: item.fingerprint for item in outputs}
    candidate_execution = PyrunExecution(
        True,
        execution.slow,
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
    )
    executions = dict(state.executions)
    executions[execution_id] = candidate_execution
    candidate_state = PyrunFile(state.path, state.entry_root, executions)
    updates = {
        state.path: validated_pyrun_serialization(candidate_state, project_root=project)
    }
    destinations = {item.destination.resolve(): item.fingerprint for item in outputs}
    for other in _entry_roots(log):
        data = load_data_file(other / "data.json", entry_root=other)
        changed = [
            item
            for item in data.inputs
            if Path(item.canonical_target).resolve() in destinations
        ]
        if not changed:
            continue
        if other != entry.root:
            if any(
                item.fingerprint != destinations[Path(item.canonical_target).resolve()]
                for item in changed
            ):
                raise ActionError(
                    "reproduction.promotion.cross_entry_dependency",
                    "promotion would require mutation outside the producing entry",
                )
            continue
        inputs = tuple(
            replace(
                item,
                fingerprint=destinations.get(
                    Path(item.canonical_target).resolve(), item.fingerprint
                ),
            )
            for item in data.inputs
        )
        candidate_data = DataFile(data.path, data.entry_root, inputs)
        updates[data.path] = candidate_data.canonical_json() + "\n"
    prior = {path: path.read_text(encoding="utf-8") for path in updates}
    return updates, prior


def _report_candidates(
    log: LogContext, outputs: Sequence[_PromotedOutput]
) -> Mapping[Path, str]:
    validation = _load_validation(log)
    _require_admissible_validation(log, validation)
    try:
        validation = refresh_promoted_provenance(
            log.summary,
            validation,
            [item.destination for item in outputs],
            result_date=_utc_now()[:10],
        )
    except TargetedRefreshError as error:
        raise ActionError(
            "reproduction.validation.refresh_failed", str(error)
        ) from error
    project = resolve_project_root(log.root)
    result_path = log.root / "reproduction" / "results.json"
    results = reconcile_run_folders(
        load_reproduction_results(result_path), project_root=project
    )
    projected, currentness = project_current_results(
        results, project_reproduction_state(log)
    )
    context = load_report_context(log.summary)
    return {
        result_path: results.serialized(),
        log.root / "reproduction.md": compose_reproduction_report(
            projected,
            context=context,
            currentness=currentness,
            folder_links_from=log.root,
        ),
        log.root / "validation" / "results.json": validation.canonical_json() + "\n",
        log.root / "validation.md": compose_validation_report(
            validation, context=context
        ),
    }


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
            try:
                os.replace(replacement, destination)
            except BaseException:
                os.replace(displaced, destination)
                raise
            installed.append(_InstalledOutput(destination, displaced, replacement))
    except BaseException:
        _rollback_outputs(tuple(installed))
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


def _entry_roots(log: LogContext) -> tuple[Path, ...]:
    entries = log.root / "entries"
    return tuple(
        sorted(
            (
                path.resolve()
                for path in entries.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and parse_entry_directory_name(path.name) is not None
            ),
            key=lambda path: path.name,
        )
    )


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

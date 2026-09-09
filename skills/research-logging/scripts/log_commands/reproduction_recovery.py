"""Bounded recovery of the fixed Phase 12 reproduction publication state."""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, cast

from validation.operation_state import operation_lock, require_mutation_ready
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PyrunFile,
    load_pyrun_state,
    validated_pyrun_serialization,
)

from .context import LogContext, resolve_entry
from .model import ActionError
from .reproduction_comparison import (
    LEGACY_STAGING_SCHEMA,
    ArtifactComparison,
    ExecutionComparison,
    _profile,
)
from .reproduction_contract import (
    ReproductionPlan,
    canonical_execution_source_digest,
    canonical_record_digest,
    source_snapshot,
    successful_checkpoint_state,
)
from .reproduction_execution import _fingerprint
from .reproduction_jobs import _finish_complete, _load_run, _plan_from_record
from .reproduction_planner import verify_reproduction_runtime_snapshot
from .reproduction_publication import (
    CompletedPublication,
    _artifact_results,
    publish_completed_reproduction,
)
from .storage import atomic_write_text
from .validation_adapter import evaluate_validation

RECOVERY_SCHEMA = "research-log-reproduction-phase12-recovery-audit/1"
PHASE12_MANIFEST_SHA256 = (
    "51299ae1a8f7b396f04d1963445e75eda24869881342aee2219691138ba9ade0"
)
MAX_MANIFEST_BYTES = 64 << 20


def recover_phase12_publication(
    project: Path,
    manifest_path: Path,
    *,
    apply: bool,
) -> Mapping[str, object]:
    """Verify and optionally publish the fixed Phase 12 recovery manifest."""

    root = project.resolve()
    manifest = _load_manifest(manifest_path)
    runs = cast(Sequence[Mapping[str, object]], manifest["runs"])
    logs = tuple(_run_log(root, run) for run in runs)
    changed = 0
    already_confirmed = 0
    published: list[str] = []
    with ExitStack() as locks:
        for log in sorted(logs, key=lambda item: item.root.as_posix()):
            require_mutation_ready(log.root)
            locks.enter_context(operation_lock(log.root, "log.lock"))
        verified = [
            _verify_run(root, log, run) for log, run in zip(logs, runs, strict=True)
        ]
        changed = sum(item[0] for item in verified)
        already_confirmed = sum(item[1] for item in verified)
        if apply:
            for log, run, (_changed, _confirmed, candidates) in zip(
                logs, runs, verified, strict=True
            ):
                _apply_confirmations(root, log, candidates)
                _recover_publication(root, log, run, candidates)
                published.append(cast(str, run["run_id"]))
    validations: list[Mapping[str, object]] = []
    if apply:
        for log in logs:
            try:
                result = evaluate_validation(log.summary)
                validations.append(
                    {
                        "status": result.get("status"),
                        "summary": log.summary.relative_to(root).as_posix(),
                    }
                )
            except Exception as error:
                validations.append(
                    {
                        "error": str(error),
                        "status": "failed",
                        "summary": log.summary.relative_to(root).as_posix(),
                    }
                )
    return {
        "already_confirmed": already_confirmed,
        "apply": apply,
        "confirmations_to_change": changed,
        "manifest_sha256": PHASE12_MANIFEST_SHA256,
        "published_runs": published,
        "runs": len(runs),
        "validations": validations,
    }


def _load_manifest(path: Path) -> Mapping[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ActionError("reproduction.recovery.manifest_invalid", str(path))
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ActionError(
            "reproduction.recovery.manifest_invalid", "manifest too large"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PHASE12_MANIFEST_SHA256:
        raise ActionError(
            "reproduction.recovery.manifest_changed",
            f"expected {PHASE12_MANIFEST_SHA256}, observed {digest}",
        )
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ActionError(
            "reproduction.recovery.manifest_invalid", str(error)
        ) from error
    fields = {"created_at", "derivation", "project", "runs", "schema", "totals"}
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value.get("schema") != RECOVERY_SCHEMA
        or value.get("project") != "<project>"
        or not isinstance(value.get("runs"), list)
    ):
        raise ActionError("reproduction.recovery.manifest_invalid", str(path))
    return cast(Mapping[str, object], value)


def _run_log(project: Path, run: Mapping[str, object]) -> LogContext:
    summary = _project_path(project, run.get("summary"), placeholder=False)
    if summary.suffix != ".md" or not summary.is_file():
        raise ActionError("reproduction.recovery.source_changed", str(summary))
    return LogContext(summary, summary.with_suffix(""))


def _verify_run(
    project: Path, log: LogContext, run: Mapping[str, object]
) -> tuple[int, int, tuple[Mapping[str, object], ...]]:
    required = {
        "authority_files",
        "candidates",
        "counts",
        "failure",
        "label",
        "run_folder",
        "run_id",
        "run_json_sha256",
        "staging_json_sha256",
        "summary",
        "target",
        "timestamps",
    }
    if set(run) != required or not isinstance(run.get("candidates"), list):
        raise ActionError("reproduction.recovery.manifest_invalid", "invalid run")
    run_root = _project_path(project, run.get("run_folder"), placeholder=True)
    run_path = run_root / "run.json"
    record = _load_run(run_path)
    already_recovered = _verify_run_record(log, run, run_path, record)
    _require_digest(run_root / "staging.json", run.get("staging_json_sha256"))
    candidates = tuple(cast(Sequence[Mapping[str, object]], run["candidates"]))
    by_path = _verify_candidates(project, log, candidates)
    _verify_authority_files(project, run, by_path)
    if not already_recovered:
        _verify_recoverable_publication(project, log, run, run_root, candidates)
    changed = sum(not _candidate_is_confirmed(project, item) for item in candidates)
    return changed, len(candidates) - changed, candidates


def _verify_run_record(
    log: LogContext,
    run: Mapping[str, object],
    run_path: Path,
    record: Mapping[str, object],
) -> bool:
    original_run = _digest(run_path) == run.get("run_json_sha256")
    state = cast(Mapping[str, object], record["state"])
    already_recovered = (
        state.get("status") == "complete"
        and state.get("operational_failure") is None
        and _published_run_present(log, cast(str, run["run_id"]))
    )
    if not original_run and not already_recovered:
        raise ActionError("reproduction.recovery.source_changed", str(run_path))
    if (
        record.get("run_id") != run.get("run_id")
        or record.get("summary") != run.get("summary")
        or record.get("target") != run.get("target")
    ):
        raise ActionError("reproduction.recovery.source_changed", str(run_path))
    if original_run and state.get("operational_failure") != run.get("failure"):
        raise ActionError("reproduction.recovery.source_changed", str(run_path))
    return already_recovered


def _verify_candidates(
    project: Path,
    log: LogContext,
    candidates: Sequence[Mapping[str, object]],
) -> Mapping[str, Sequence[Mapping[str, object]]]:
    by_path: dict[str, list[Mapping[str, object]]] = {}
    for candidate in candidates:
        path = candidate.get("pyrun_path")
        if not isinstance(path, str):
            raise ActionError("reproduction.recovery.manifest_invalid", "pyrun path")
        by_path.setdefault(path, []).append(candidate)
        _verify_candidate_files(project, log, candidate)
    return by_path


def _verify_authority_files(
    project: Path,
    run: Mapping[str, object],
    candidates: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    authority = cast(Sequence[Mapping[str, object]], run.get("authority_files"))
    authority_by_path = {
        cast(str, item.get("path")): item
        for item in authority
        if isinstance(item, Mapping)
    }
    if len(authority_by_path) != len(authority):
        raise ActionError("reproduction.recovery.manifest_invalid", "authority files")
    for path, item in authority_by_path.items():
        target = _project_path(project, path, placeholder=False)
        if target.name == "pyrun.json":
            _verify_normalized_pyrun(
                project,
                target,
                candidates.get(path, []),
                cast(str, item.get("sha256")),
            )
        else:
            _require_digest(target, item.get("sha256"))
    missing_authority = set(candidates) - set(authority_by_path)
    if missing_authority:
        raise ActionError(
            "reproduction.recovery.manifest_invalid",
            f"candidate authority is absent: {min(missing_authority)}",
        )


def _verify_recoverable_publication(
    project: Path,
    log: LogContext,
    run: Mapping[str, object],
    run_root: Path,
    candidates: Sequence[Mapping[str, object]],
) -> None:
    record = _load_run(run_root / "run.json")
    plan = _runtime_plan(project, log, _plan_from_record(record))
    comparisons = _legacy_comparisons(project, log, run_root, candidates)
    skips = _dependency_skips(plan, comparisons)
    accepted = cast(Mapping[str, str | None], record["timestamps"])["accepted_at"]
    _artifact_results(
        CompletedPublication(
            plan,
            comparisons,
            cast(str, run["run_id"]),
            cast(str, accepted),
            _utc_now(),
            run_root,
            skips,
        )
    )


def _verify_candidate_files(
    project: Path, log: LogContext, candidate: Mapping[str, object]
) -> None:
    checkpoint = _project_path(
        project, candidate.get("checkpoint_path"), placeholder=True
    )
    _require_digest(checkpoint, candidate.get("checkpoint_sha256"))
    value = _load_json_file(checkpoint)
    if (
        not successful_checkpoint_state(value.get("state"))
        or value.get("execution_id") != candidate.get("execution_id")
        or value.get("completed_at") != candidate.get("checkpoint_completed_at")
        or not isinstance(value.get("outputs"), list)
    ):
        raise ActionError("reproduction.recovery.source_changed", str(checkpoint))
    checkpoint_outputs = {
        cast(str, item.get("artifact")): item.get("fingerprint")
        for item in cast(Sequence[Mapping[str, object]], value["outputs"])
    }
    outputs = candidate.get("outputs")
    if not isinstance(outputs, list):
        raise ActionError("reproduction.recovery.manifest_invalid", "outputs")
    entry = resolve_entry(log, cast(str, candidate.get("entry")))
    for output in cast(Sequence[Mapping[str, object]], outputs):
        artifact = output.get("artifact")
        kind = output.get("kind")
        expected = output.get("expected")
        regenerated = output.get("regenerated")
        if (
            not isinstance(artifact, str)
            or kind not in {"file", "directory"}
            or expected != regenerated
            or checkpoint_outputs.get(artifact) != regenerated
        ):
            raise ActionError("reproduction.recovery.manifest_invalid", "output")
        retained = output_target_path(
            artifact, entry_root=entry.root, project_root=project
        )
        if _fingerprint(retained, cast(str, kind)).as_dict() != expected:
            raise ActionError("reproduction.recovery.source_changed", str(retained))


def _verify_normalized_pyrun(
    project: Path,
    path: Path,
    candidates: Sequence[Mapping[str, object]],
    expected_digest: str,
) -> None:
    raw = _load_json_file(path)
    executions = raw.get("executions")
    if not isinstance(executions, dict):
        raise ActionError("reproduction.recovery.source_changed", str(path))
    for candidate in candidates:
        identity = candidate.get("execution_id")
        current = executions.get(identity)
        if not isinstance(identity, str) or not isinstance(current, dict):
            raise ActionError("reproduction.recovery.source_changed", str(path))
        current_digest = canonical_record_digest(current)
        allowed = {
            candidate.get("pre_repair_record_digest"),
            candidate.get("confirmed_record_digest"),
        }
        if current_digest not in allowed:
            raise ActionError("reproduction.recovery.source_changed", identity)
        current["confirmed"] = candidate.get("current_confirmed")
    normalized = _pretty_json(raw).encode("utf-8")
    if hashlib.sha256(normalized).hexdigest() != expected_digest:
        raise ActionError("reproduction.recovery.source_changed", str(path))
    entry_root = path.parent
    load_pyrun_state(path, entry_root=entry_root, project_root=project)


def _candidate_is_confirmed(project: Path, candidate: Mapping[str, object]) -> bool:
    path = _project_path(project, candidate.get("pyrun_path"), placeholder=False)
    value = _load_json_file(path)
    current = cast(Mapping[str, object], value["executions"])[
        cast(str, candidate["execution_id"])
    ]
    return canonical_record_digest(
        cast(Mapping[str, object], current)
    ) == candidate.get("confirmed_record_digest")


def _apply_confirmations(
    project: Path,
    log: LogContext,
    candidates: Sequence[Mapping[str, object]],
) -> None:
    grouped: dict[Path, list[Mapping[str, object]]] = {}
    for candidate in candidates:
        path = _project_path(project, candidate.get("pyrun_path"), placeholder=False)
        grouped.setdefault(path, []).append(candidate)
    for path, selected in sorted(grouped.items(), key=lambda item: item[0].as_posix()):
        entry = resolve_entry(log, cast(str, selected[0]["entry"]))
        state = load_pyrun_state(path, entry_root=entry.root, project_root=project)
        executions = dict(state.executions)
        changed = False
        for candidate in selected:
            identity = cast(str, candidate["execution_id"])
            execution = executions[identity]
            expected = (
                candidate["confirmed_record_digest"]
                if execution.confirmed
                else candidate["pre_repair_record_digest"]
            )
            if canonical_record_digest(execution.as_dict()) != expected:
                raise ActionError("reproduction.recovery.source_changed", identity)
            if not execution.confirmed:
                executions[identity] = replace(execution, confirmed=True)
                changed = True
        if changed:
            candidate_state = PyrunFile(path, entry.root, executions)
            atomic_write_text(
                path,
                validated_pyrun_serialization(candidate_state, project_root=project),
            )


def _recover_publication(
    project: Path,
    log: LogContext,
    manifest_run: Mapping[str, object],
    candidates: Sequence[Mapping[str, object]],
) -> None:
    run_root = _project_path(project, manifest_run.get("run_folder"), placeholder=True)
    record = _load_run(run_root / "run.json")
    if cast(Mapping[str, object], record["state"]).get(
        "status"
    ) == "complete" and _published_run_present(log, cast(str, manifest_run["run_id"])):
        return
    plan = _runtime_plan(project, log, _plan_from_record(record))
    comparisons = _legacy_comparisons(project, log, run_root, candidates)
    skips = _dependency_skips(plan, comparisons)
    finished = _utc_now()
    accepted = cast(Mapping[str, str | None], record["timestamps"])["accepted_at"]
    published = publish_completed_reproduction(
        log,
        CompletedPublication(
            plan,
            comparisons,
            cast(str, manifest_run["run_id"]),
            cast(str, accepted),
            finished,
            run_root,
            skips,
        ),
    )
    result = next(
        item for item in published.results.runs if item.run_id == manifest_run["run_id"]
    )
    _finish_complete(
        log,
        run_root,
        cast(str, manifest_run["run_id"]),
        result.artifact_outcomes,
        finished,
    )


def _runtime_plan(
    project: Path, log: LogContext, plan: ReproductionPlan
) -> ReproductionPlan:
    executions = []
    loaded: dict[str, PyrunFile] = {}
    for item in cast(
        Sequence[Mapping[str, object]], plan.source_snapshot["executions"]
    ):
        entry_id = cast(str, item["entry"])
        identity = cast(str, item["execution_id"])
        if entry_id not in loaded:
            entry = resolve_entry(log, entry_id)
            loaded[entry_id] = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=project,
            )
        execution = loaded[entry_id].executions.get(identity)
        if execution is None:
            raise ActionError("reproduction.recovery.source_changed", identity)
        executions.append(
            {
                "digest": canonical_execution_source_digest(execution.as_dict()),
                "entry": entry_id,
                "execution_id": identity,
            }
        )
    authority = tuple(
        item
        for item in cast(
            Sequence[Mapping[str, object]], plan.source_snapshot["authority_files"]
        )
        if Path(cast(str, item["path"])).name != "pyrun.json"
    )
    snapshot = source_snapshot(
        authority_files=authority,
        executions=executions,
        materials=cast(
            Sequence[Mapping[str, object]], plan.source_snapshot["materials"]
        ),
    )
    upgraded = replace(plan, source_snapshot=snapshot)
    verify_reproduction_runtime_snapshot(log, upgraded)
    return upgraded


def _legacy_comparisons(
    project: Path,
    log: LogContext,
    run_root: Path,
    candidates: Sequence[Mapping[str, object]],
) -> tuple[ExecutionComparison, ...]:
    staging = _load_json_file(run_root / "staging.json")
    if staging.get("schema") != LEGACY_STAGING_SCHEMA or not isinstance(
        staging.get("executions"), list
    ):
        raise ActionError("reproduction.recovery.source_changed", "staging schema")
    results = [
        _legacy_execution(project, log, run_root, item)
        for item in cast(Sequence[Mapping[str, object]], staging["executions"])
    ]
    for candidate in candidates:
        artifacts = tuple(
            ArtifactComparison(
                cast(str, item["artifact"]),
                "matched",
                None,
                cast(str, item["comparison_profile"]),
                cast(Mapping[str, object], item["expected"]),
                cast(Mapping[str, object], item["regenerated"]),
            )
            for item in cast(Sequence[Mapping[str, object]], candidate["outputs"])
        )
        results.append(
            ExecutionComparison(
                cast(str, candidate["entry"]),
                cast(str, candidate["execution_id"]),
                artifacts,
                None,
                True,
            )
        )
    identities = [(item.entry, item.execution_id) for item in results]
    if len(identities) != len(set(identities)):
        raise ActionError("reproduction.recovery.source_changed", "duplicate execution")
    return tuple(sorted(results, key=lambda item: (item.entry, item.execution_id)))


def _legacy_execution(
    project: Path, log: LogContext, run_root: Path, value: Mapping[str, object]
) -> ExecutionComparison:
    entry_id = cast(str, value.get("entry"))
    entry = resolve_entry(log, entry_id)
    bundle = _safe_run_path(run_root, cast(str, value.get("path")))
    complete = value.get("complete") is True
    artifacts = []
    for item in cast(Sequence[Mapping[str, object]], value.get("outputs")):
        artifact = cast(str, item.get("artifact"))
        kind = cast(str, item.get("kind"))
        expected = item.get("expected")
        regenerated = item.get("regenerated")
        retained = output_target_path(
            artifact, entry_root=entry.root, project_root=project
        )
        if expected is not None and _fingerprint(retained, kind).as_dict() != expected:
            raise ActionError("reproduction.recovery.source_changed", str(retained))
        profile = None
        staged_value = item.get("staged")
        if regenerated is not None:
            if not isinstance(staged_value, str):
                raise ActionError("reproduction.recovery.source_changed", artifact)
            staged = _safe_run_path(bundle, staged_value)
            if _fingerprint(staged, kind).as_dict() != regenerated:
                raise ActionError("reproduction.recovery.source_changed", str(staged))
            if complete:
                profile = _profile(retained, staged)
        artifacts.append(
            ArtifactComparison(
                artifact,
                cast(str, item.get("outcome")),
                cast(str | None, item.get("reason")),
                profile,
                cast(Mapping[str, object] | None, expected),
                cast(Mapping[str, object] | None, regenerated),
            )
        )
    return ExecutionComparison(
        entry_id,
        cast(str, value.get("execution_id")),
        tuple(artifacts),
        cast(str, value.get("path")),
        complete,
    )


def _dependency_skips(
    plan: ReproductionPlan, comparisons: Sequence[ExecutionComparison]
) -> tuple[Mapping[str, object], ...]:
    failed = {
        f"{item.entry}:{item.execution_id}" for item in comparisons if not item.complete
    }
    skips: list[Mapping[str, object]] = []
    for execution in sorted(plan.executions, key=lambda item: cast(int, item["order"])):
        reference = f"{execution['entry']}:{execution['execution_id']}"
        dependencies = set(cast(Sequence[str], execution["depends_on"]))
        blocked = sorted(dependencies & failed)
        if blocked:
            failed.add(reference)
            skips.append(
                {
                    "depends_on": blocked,
                    "entry": execution["entry"],
                    "execution_id": execution["execution_id"],
                    "reason": "dependency_failed",
                }
            )
    return tuple(skips)


def _published_run_present(log: LogContext, run_id: str) -> bool:
    path = log.root / "reproduction" / "results.json"
    if not path.is_file() or path.is_symlink():
        return False
    value = _load_json_file(path)
    runs = value.get("runs")
    return isinstance(runs, list) and any(
        isinstance(item, Mapping) and item.get("run_id") == run_id for item in runs
    )


def _project_path(project: Path, value: object, *, placeholder: bool) -> Path:
    if not isinstance(value, str):
        raise ActionError("reproduction.recovery.manifest_invalid", "invalid path")
    raw = value.removeprefix("<project>/") if placeholder else value
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ActionError("reproduction.recovery.manifest_invalid", value)
    path = project.joinpath(*pure.parts)
    permitted_root = project
    if placeholder:
        if not pure.parts or pure.parts[0] != "tmp":
            raise ActionError("reproduction.recovery.manifest_invalid", value)
        permitted_root = (project / "tmp").resolve()
    try:
        path.resolve().relative_to(permitted_root)
    except ValueError as error:
        raise ActionError("reproduction.recovery.manifest_invalid", value) from error
    return path


def _safe_run_path(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ActionError("reproduction.recovery.source_changed", value)
    path = root.joinpath(*pure.parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ActionError("reproduction.recovery.source_changed", value) from error
    if path.is_symlink() or not path.exists():
        raise ActionError("reproduction.recovery.source_changed", value)
    return path


def _load_json_file(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ActionError("reproduction.recovery.source_changed", str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ActionError("reproduction.recovery.source_changed", str(path)) from error
    if not isinstance(value, dict):
        raise ActionError("reproduction.recovery.source_changed", str(path))
    return cast(dict[str, object], value)


def _require_digest(path: Path, expected: object) -> None:
    if not isinstance(expected, str) or _digest(path) != expected:
        raise ActionError("reproduction.recovery.source_changed", str(path))


def _digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the explicit preview-or-apply maintenance entrypoint."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args(arguments)
    result = recover_phase12_publication(
        options.project,
        options.manifest,
        apply=options.apply,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

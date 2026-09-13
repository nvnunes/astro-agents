"""Isolated current-source checking for one recorded repair."""

from __future__ import annotations

import hashlib
import secrets
import signal
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from research_log_data import (
    DataContractError,
    Fingerprint,
    load_data_file,
    observe_fingerprint,
    resolve_input_token,
)
from validation.evidence import (
    EvidenceContractError,
    index_entry_documents,
    load_evidence_file,
)
from validation.evidence_comparison import (
    EvidenceComparisonDefinition,
    evidence_comparison_definitions,
)
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PYRUN_EXECUTION_RE,
    PyrunStateError,
    execution_id,
    load_pyrun_state,
    recipe_from_invocation,
)

from .context import (
    EntryContext,
    LogContext,
    parse_entry_document_name,
    resolve_entry,
    resolve_project_root,
)
from .current_invocations import entry_invocations
from .model import ActionError
from .reproduction_comparison import (
    ExecutionComparison,
    compare_execution_artifacts,
)
from .reproduction_contract import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    MAX_EXECUTION_TIMEOUT_SECONDS,
    AcceptedInvocation,
)
from .reproduction_execution import (
    execute_isolated_invocation,
    preflight_isolated_invocation,
)
from .reproduction_paths import resolve_project_tmp
from .storage import log_lock, reproduction_log_reservation


@dataclass(frozen=True)
class RepairCheckRequest:
    entry: str
    cid: str
    execution_id: str
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RepairCheckResult:
    summary: str
    entry: str
    cid: str
    execution_id: str
    status: str
    exit_status: int
    workspace: str | None
    execution: dict[str, object]
    inputs: tuple[dict[str, object], ...]
    outputs: tuple[dict[str, object], ...]
    diagnostics: dict[str, str]
    limitations: tuple[str, ...] = ("selected_repair_only",)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "research-log-repair-check-result/1",
            "summary": self.summary,
            "entry": self.entry,
            "cid": self.cid,
            "execution_id": self.execution_id,
            "status": self.status,
            "exit_status": self.exit_status,
            "published": False,
            "workspace": self.workspace,
            "execution": self.execution,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "diagnostics": self.diagnostics,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class _RepairAuthority:
    entry: EntryContext
    project: Path
    invocation: AcceptedInvocation
    definitions: tuple[EvidenceComparisonDefinition, ...]
    snapshot: tuple[tuple[str, str], ...]
    inputs: dict[str, Fingerprint]
    sources: tuple[dict[str, object], ...]


def run_repair_check(log: LogContext, request: RepairCheckRequest) -> RepairCheckResult:
    """Return an unavailable result for runtime prerequisites, never publish state."""

    if not 1 <= request.execution_timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS:
        raise ActionError(
            "repair_check.timeout.invalid",
            "--execution-timeout-seconds must be between 1 and 604800",
        )
    if PYRUN_EXECUTION_RE.fullmatch(request.execution_id) is None:
        raise ActionError(
            "repair_check.execution.invalid",
            "--execution-id requires a full pyrun-exec/v2 ID",
        )
    try:
        return _run_repair_check(log, request)
    except (
        DataContractError,
        EvidenceContractError,
        PyrunStateError,
    ) as error:
        return _unavailable_result(
            log,
            request,
            ActionError("repair_check.authority.unavailable", str(error)),
        )
    except ActionError as error:
        if _runtime_unavailable(error):
            return _unavailable_result(log, request, error)
        raise


def _run_repair_check(
    log: LogContext, request: RepairCheckRequest
) -> RepairCheckResult:
    """Execute and compare one selected current invocation without job state."""

    if not 1 <= request.execution_timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS:
        raise ActionError(
            "repair_check.timeout.invalid",
            "--execution-timeout-seconds must be between 1 and 604800",
        )
    if PYRUN_EXECUTION_RE.fullmatch(request.execution_id) is None:
        raise ActionError(
            "repair_check.execution.invalid",
            "--execution-id requires a full pyrun-exec/v2 ID",
        )
    with reproduction_log_reservation(log):
        with log_lock(log):
            authority = _load_authority(log, request)
        root = (
            resolve_project_tmp(authority.project)
            / "repair-check"
            / date.today().isoformat()
        )
        name = f"repair-check-{log.root.name}-{request.entry}-{secrets.token_hex(8)}"
        workspace = root / name
        # All selector, input, binding, path, and confinement failures happen
        # before this retained workspace exists.
        preflight_isolated_invocation(
            authority.entry,
            authority.invocation,
            workspace,
            input_observations=authority.inputs,
        )
        workspace.mkdir(parents=True, mode=0o700)
        cancelled = threading.Event()
        received: list[int] = []
        prior = {
            kind: signal.getsignal(kind) for kind in (signal.SIGINT, signal.SIGTERM)
        }

        def stop(kind: int, _frame: object) -> None:
            received.append(kind)
            cancelled.set()

        try:
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGTERM, stop)
            with log_lock(log):
                try:
                    _require_authority_unchanged(log, request, authority)
                except (
                    DataContractError,
                    EvidenceContractError,
                    PyrunStateError,
                ) as error:
                    return _unavailable_result(
                        log,
                        request,
                        ActionError("repair_check.authority.unavailable", str(error)),
                        workspace=workspace,
                        diagnostics=_workspace_diagnostics(workspace),
                        received=received,
                    )
            isolated = execute_isolated_invocation(
                authority.entry,
                authority.invocation,
                workspace,
                execution_timeout_seconds=request.execution_timeout_seconds,
                stop_requested=cancelled.is_set,
                input_observations=authority.inputs,
            )
            with log_lock(log):
                try:
                    _require_authority_unchanged(log, request, authority)
                    comparison = compare_execution_artifacts(
                        authority.invocation,
                        isolated.attempt,
                        isolated.output_paths,
                        entry_root=authority.entry.root,
                        project_root=authority.project,
                        definitions=authority.definitions,
                    )
                except (
                    ActionError,
                    DataContractError,
                    EvidenceContractError,
                    PyrunStateError,
                ) as error:
                    if not isinstance(error, ActionError) or _runtime_unavailable(
                        error
                    ):
                        return _unavailable_result(
                            log,
                            request,
                            _authority_error(error),
                            workspace=workspace,
                            diagnostics=_diagnostics(
                                isolated.attempt.stdout, isolated.attempt.stderr
                            ),
                            received=received,
                        )
                    raise
            return _result(
                log,
                authority,
                isolated.attempt,
                comparison,
                isolated.output_paths,
                workspace,
                received,
            )
        except ActionError as error:
            if _runtime_unavailable(error):
                return _unavailable_result(
                    log,
                    request,
                    error,
                    workspace=workspace,
                    diagnostics=_workspace_diagnostics(workspace),
                    received=received,
                )
            raise
        finally:
            for kind, handler in prior.items():
                signal.signal(kind, handler)


def _load_authority(log: LogContext, request: RepairCheckRequest) -> _RepairAuthority:
    """Load one complete current authority while the caller owns the log lock."""
    entry = resolve_entry(log, request.entry)
    project = resolve_project_root(log.root)
    try:
        data = load_data_file(entry.root / "data.json", entry_root=entry.root)
    except (DataContractError, OSError, ValueError) as error:
        # A current repair check has no authority to normalize a malformed or
        # unsafe declaration.  Surface it as an unobservable runtime input so
        # the selected child is never launched.
        raise ActionError("repair_check.input.unavailable", str(error)) from error
    state = load_pyrun_state(
        entry.root / "pyrun.json", entry_root=entry.root, project_root=project
    )
    execution = state.execution(request.cid, request.execution_id)
    if execution is None:
        raise ActionError(
            "repair_check.execution.unknown",
            f"unknown execution in {entry.id}: {request.execution_id}",
        )
    candidates = [
        item
        for item in entry_invocations(entry, project_root=project)
        if item.cid == request.cid
        and execution_id(
            recipe_from_invocation(item, entry_root=entry.root, project_root=project)
        )
        == request.execution_id
    ]
    if len(candidates) != 1:
        raise ActionError(
            "repair_check.execution.unresolved",
            "current command selection is absent or ambiguous",
        )
    if (
        recipe_from_invocation(
            candidates[0], entry_root=entry.root, project_root=project
        )
        != execution.recipe
    ):
        raise ActionError(
            "repair_check.recipe.changed",
            "current Markdown recipe differs from recorded execution",
        )
    evidence_path = entry.root / "evidence.json"
    evidence = (
        load_evidence_file(
            evidence_path, entry_root=entry.root, log_root=entry.log.root
        )
        if evidence_path.exists()
        else None
    )
    definitions = tuple(
        definition
        for definition in evidence_comparison_definitions(data, evidence)
        if definition.resource.canonical_target
        in {
            str(
                output_target_path(
                    artifact, entry_root=entry.root, project_root=project
                ).resolve()
            )
            for artifact, _kind in execution.recipe.outputs
        }
    )
    invocation = AcceptedInvocation(
        entry.id, request.cid, request.execution_id, execution, data, {}
    )
    inputs, sources, snapshot = _snapshot(entry, project, invocation, definitions)
    return _RepairAuthority(
        entry, project, invocation, tuple(definitions), snapshot, inputs, sources
    )


def _require_authority_unchanged(
    log: LogContext, request: RepairCheckRequest, expected: _RepairAuthority
) -> None:
    """Reload rather than trust a prior object graph at each stability boundary."""
    try:
        current = _load_authority(log, request)
    except ActionError as error:
        if error.code.startswith(
            (
                "repair_check.baseline.",
                "entry.identity.",
                "repair_check.execution.unresolved",
            )
        ):
            raise ActionError(
                "repair_check.source.changed",
                "selected repair sources changed during execution",
            ) from error
        raise
    if current.snapshot != expected.snapshot:
        raise ActionError(
            "repair_check.source.changed",
            "selected repair sources changed during execution",
        )


def _snapshot(
    entry: EntryContext,
    project: Path,
    invocation: AcceptedInvocation,
    definitions: tuple[EvidenceComparisonDefinition, ...],
) -> tuple[
    dict[str, Fingerprint], tuple[dict[str, object], ...], tuple[tuple[str, str], ...]
]:
    """Freeze current authority, retained baselines, and evidence-only sources."""
    from validation.pyrun_outputs import code_target_path
    from validation.pyrun_state import script_target_path

    roots = [
        entry.log.summary,
        entry.root / "data.json",
        entry.root / "pyrun.json",
        entry.root / "evidence.json",
    ]
    roots.extend(_summary_documents(entry))
    roots.append(
        script_target_path(
            invocation.execution.recipe.script,
            entry_root=entry.root,
            project_root=project,
        )
    )
    roots.extend(
        code_target_path(name, entry_root=entry.root)
        for name, _ in invocation.execution.observed.code
    )
    resources = (
        []
        if invocation.data is None
        else [
            item
            for item in invocation.data.inputs
            if item.name in invocation.execution.recipe.inputs
        ]
    )
    if invocation.data is not None:
        targets = {
            resolved.resource.canonical_target
            for definition in definitions
            for record in definition.records
            for source in record.sources
            for resolved in (resolve_input_token(source.source, invocation.data),)
        }
        resources.extend(
            item
            for item in invocation.data.inputs
            if item.canonical_target in targets
            or item.name in dict(invocation.execution.recipe.outputs)
        )
    result: list[tuple[str, str]] = []
    for path in roots:
        if path.is_symlink() or not path.is_file():
            raise ActionError("repair_check.source.unavailable", str(path))
        try:
            result.append(
                (str(path.resolve()), hashlib.sha256(path.read_bytes()).hexdigest())
            )
        except (OSError, UnicodeError) as error:
            raise ActionError("repair_check.source.unavailable", str(error)) from error
    _snapshot_retained_baselines(entry, project, invocation, result)
    inputs: dict[str, Fingerprint] = {}
    for resource in resources:
        try:
            fingerprint = observe_fingerprint(resource).fingerprint
            result.append((f"input:{resource.name}", str(fingerprint)))
            if resource.name in invocation.execution.recipe.inputs:
                inputs[resource.name] = fingerprint
        except (DataContractError, OSError, ValueError) as error:
            raise ActionError("repair_check.input.unavailable", str(error)) from error
    sources = tuple(
        _source_report(name, recorded, path)
        for name, recorded, path in _source_records(entry, project, invocation)
    )
    return inputs, sources, tuple(sorted(result))


def _summary_documents(entry: EntryContext) -> tuple[Path, ...]:
    """Return only regular entry documents explicitly owned by the summary."""

    try:
        summary = entry.log.summary.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ActionError("association.document_unavailable", str(error)) from error
    paths = []
    for target in index_entry_documents(summary):
        path = entry.log.summary.parent / target
        identity = parse_entry_document_name(path.name)
        if identity is None or identity.id != entry.id:
            continue
        if path.parent != entry.root or path.is_symlink() or not path.is_file():
            raise ActionError("entry.identity.unresolved", str(path))
        paths.append(path)
    if not paths:
        raise ActionError("entry.identity.unresolved", entry.id)
    return tuple(sorted(set(paths)))


def _snapshot_retained_baselines(
    entry: EntryContext,
    project: Path,
    invocation: AcceptedInvocation,
    result: list[tuple[str, str]],
) -> None:
    """Freeze every declared output by artifact path, not data-resource name."""

    from validation.pyrun_outputs import output_target_path

    from .reproduction_execution import observe_output_fingerprint

    recorded = dict(invocation.execution.observed.outputs)
    for artifact, kind in invocation.execution.recipe.outputs:
        path = output_target_path(artifact, entry_root=entry.root, project_root=project)
        try:
            current = observe_output_fingerprint(path, kind)
        except (OSError, ValueError) as error:
            raise ActionError(
                "repair_check.baseline.unavailable", str(error)
            ) from error
        if recorded.get(artifact) != current:
            raise ActionError(
                "repair_check.baseline.changed",
                f"retained baseline changed: {artifact}",
            )
        result.append((f"baseline:{artifact}", str(current)))


def _source_records(
    entry: EntryContext, project: Path, invocation: AcceptedInvocation
) -> tuple[tuple[str, Fingerprint, Path], ...]:
    from validation.pyrun_outputs import code_target_path
    from validation.pyrun_state import script_target_path

    script = invocation.execution.observed.script
    if script is None:
        raise ActionError(
            "repair_check.baseline.missing",
            "selected execution has no retained script observation",
        )
    return (
        (
            "script",
            script,
            script_target_path(
                invocation.execution.recipe.script,
                entry_root=entry.root,
                project_root=project,
            ),
        ),
        *(
            (name, fingerprint, code_target_path(name, entry_root=entry.root))
            for name, fingerprint in invocation.execution.observed.code
        ),
    )


def _source_report(name: str, recorded: Fingerprint, path: Path) -> dict[str, object]:
    current = _file_fingerprint(path)
    return {
        "name": name,
        "recorded": recorded.as_dict(),
        "current": current.as_dict(),
        "differs_from_recorded": current != recorded,
    }


def _file_fingerprint(path: Path) -> Fingerprint:
    if path.is_symlink() or not path.is_file():
        raise ActionError("repair_check.source.unavailable", str(path))
    try:
        return Fingerprint(
            "sha256", digest=hashlib.sha256(path.read_bytes()).hexdigest()
        )
    except (OSError, UnicodeError) as error:
        raise ActionError("repair_check.source.unavailable", str(error)) from error


def _result(  # noqa: PLR0913
    log: LogContext,
    authority: _RepairAuthority,
    attempt: object,
    comparison: ExecutionComparison,
    output_paths: Mapping[str, Path],
    workspace: Path,
    received: list[int] | tuple[int, ...] = (),
) -> RepairCheckResult:
    from .reproduction_execution import ExecutionAttempt

    assert isinstance(attempt, ExecutionAttempt)
    invocation = authority.invocation
    if attempt.failure_code == "worker_cleanup_incomplete":
        status, exit_status = "worker_cleanup_incomplete", 2
    elif attempt.stopped or received:
        status, exit_status = (
            "cancelled",
            (143 if received and received[0] == signal.SIGTERM else 130),
        )
    elif attempt.failure_code is not None:
        status, exit_status = "execution_failed", 3
    elif any(item.outcome == "comparison_failed" for item in comparison.artifacts):
        status, exit_status = "comparison_unavailable", 3
    elif comparison.matched:
        status, exit_status = "matched", 0
    else:
        status, exit_status = "different", 1
    return RepairCheckResult(
        log.summary.as_posix(),
        invocation.entry,
        invocation.cid,
        invocation.execution_id,
        status,
        exit_status,
        str(workspace),
        {
            "returncode": attempt.returncode,
            "state": attempt.checkpoint.state,
            "failure": attempt.failure_code,
            "policy": {
                "auto_reproduce": getattr(invocation.execution, "auto_reproduce", None),
                "environment_profile": getattr(
                    invocation.execution, "environment_profile", None
                ),
                "execution_contract": getattr(
                    invocation.execution, "execution_contract", None
                ),
                "exclusive": getattr(invocation.execution, "exclusive", None),
                "runner": getattr(invocation.execution, "runner", None),
                "requires_reproduction": getattr(
                    invocation.execution, "requires_reproduction", None
                ),
            },
            "sources": list(authority.sources),
        },
        tuple(
            {
                "name": name,
                "recorded": recorded.as_dict(),
                "current": authority.inputs[name].as_dict(),
                "differs_from_recorded": authority.inputs[name] != recorded,
                "historical_difference": authority.inputs[name] != recorded,
            }
            for name, recorded in invocation.execution.observed.inputs
        ),
        tuple(
            {
                **item.as_dict(),
                "kind": dict(invocation.execution.recipe.outputs)[item.artifact],
                "path": str(output_paths[item.artifact]),
            }
            for item in comparison.artifacts
        ),
        _diagnostics(attempt.stdout, attempt.stderr),
    )


def _runtime_unavailable(error: ActionError) -> bool:
    """Classify non-selector runtime prerequisites and stability observations."""

    return error.code.startswith(
        (
            "repair_check.source.",
            "repair_check.input.",
            "repair_check.baseline.",
            "repair_check.source.changed",
            "reproduction.environment.",
            "reproduction.input.",
            "reproduction.script.",
            "association.document_",
        )
    )


def _authority_error(
    error: ActionError | DataContractError | EvidenceContractError | PyrunStateError,
) -> ActionError:
    """Normalize current-authority decoder failures for structured results."""

    return (
        error
        if isinstance(error, ActionError)
        else ActionError("repair_check.authority.unavailable", str(error))
    )


def _unavailable_result(  # noqa: PLR0913
    log: LogContext,
    request: RepairCheckRequest,
    error: ActionError,
    *,
    workspace: Path | None = None,
    diagnostics: dict[str, str] | None = None,
    received: list[int] | tuple[int, ...] = (),
) -> RepairCheckResult:
    """Represent an unobservable repair prerequisite as a non-publishing result."""

    comparison = error.code.startswith(
        ("repair_check.baseline.", "reproduction.comparison.")
    )
    status = "comparison_unavailable" if comparison else "unavailable"
    if received:
        status = "cancelled"
    return RepairCheckResult(
        log.summary.as_posix(),
        request.entry,
        request.cid,
        request.execution_id,
        status,
        143 if received and received[0] == signal.SIGTERM else 130 if received else 3,
        None if workspace is None else str(workspace),
        {"failure": {"code": error.code, "message": str(error)}},
        (),
        (),
        diagnostics or {"stdout": "", "stderr": ""},
    )


def _diagnostics(stdout: str, stderr: str) -> dict[str, str]:
    """Return retained stream text alongside their exact workspace paths."""

    def read(path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    return {
        "stdout": read(stdout),
        "stdout_path": stdout,
        "stderr": read(stderr),
        "stderr_path": stderr,
    }


def _workspace_diagnostics(workspace: Path) -> dict[str, str]:
    """Return any retained diagnostics when launch or control flow failed early."""

    streams = sorted(workspace.glob("diagnostics/**/*.log"))
    values = {path.name.removesuffix(".log"): path for path in streams}
    return _diagnostics(
        str(values.get("stdout", workspace / "diagnostics" / "stdout.log")),
        str(values.get("stderr", workspace / "diagnostics" / "stderr.log")),
    )

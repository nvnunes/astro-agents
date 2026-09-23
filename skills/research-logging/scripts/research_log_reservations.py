"""Short transactions and artifact reservations for ordinary research execution.

Reservations are generated process-owned state, not held filesystem locks.
An abandoned reservation requires explicit cleanup; it never expires into
permission to overwrite an artifact. Workers register before executing code.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterator

from validation.file_publication import atomic_replace_text
from validation.filesystem import BoundedFileReadError, bounded_file_bytes
from validation.operation_state import operation_directory, operation_lock

SCHEMA = "research-log-artifact-reservation/1"
MAX_RESERVATIONS = 1000
MAX_RESERVATION_BYTES = 64 * 1024
MAX_CANDIDATE_BYTES = 256 * 1024


class ArtifactReservationError(OSError):
    """Artifact access or explicit cleanup conflicts with a reservation."""

    code = "artifact.reservation.conflict"


class WorkerStillActiveError(ArtifactReservationError):
    """A zero-exit worker still has live process-group descendants."""


class MissingArtifactReservationError(ArtifactReservationError):
    """An exact reservation UUID is absent rather than malformed."""


@dataclass(frozen=True)
class ArtifactReservation:
    """One ordinary invocation's canonical artifact paths and process owners."""

    identity: str
    entry: str
    cid: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    parent_pid: int
    worker_pid: int | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the bounded generated-state record."""

        return {"schema": SCHEMA, **asdict(self)}


def _path(root: Path, identity: str) -> Path:
    if len(identity) != 32 or any(char not in "0123456789abcdef" for char in identity):
        raise ArtifactReservationError("invalid artifact reservation identity")
    return operation_directory(root) / f"ordinary-execution-{identity}.json"


def recovery_candidate_path(root: Path, identity: str) -> Path:
    """Return the generated completion candidate for one exact reservation."""

    return _path(root, identity).with_name(f"ordinary-completion-{identity}.json")


def recovery_reservation(root: Path, entry: Path, identity: str) -> ArtifactReservation:
    """Read one exact entry-owned reservation without changing generated state."""

    path = _path(root, identity)
    if not path.exists() and not path.is_symlink():
        raise MissingArtifactReservationError(
            f"reservation {identity} was not found; use the exact UUID printed "
            "by the failed pyrun invocation in its entry root"
        )
    record = _decode(path)
    if record.entry != entry.resolve().as_posix():
        raise ArtifactReservationError(
            f"reservation {identity} does not belong to entry {entry}"
        )
    return record


def require_recovery_worker_finished(record: ArtifactReservation) -> None:
    """Require the launcher and registered worker group to have exited."""

    if _alive(record.parent_pid) or (
        record.worker_pid is not None and _alive(record.worker_pid, group=True)
    ):
        raise ArtifactReservationError(
            f"recovery requires the launcher and worker to finish: "
            f"{record.entry}/{record.cid}; owner PID {record.parent_pid}, "
            f"worker {record.worker_pid}"
        )


def _decode(path: Path) -> ArtifactReservation:
    try:
        raw = json.loads(bounded_file_bytes(path, maximum_bytes=MAX_RESERVATION_BYTES))
    except (ValueError, OSError, BoundedFileReadError) as error:
        raise ArtifactReservationError(
            f"reservation requires Repair: {path}: {error}"
        ) from error
    expected = {
        "schema",
        "identity",
        "entry",
        "cid",
        "reads",
        "writes",
        "parent_pid",
        "worker_pid",
    }
    if not isinstance(raw, dict) or set(raw) != expected or raw["schema"] != SCHEMA:
        raise ArtifactReservationError(f"reservation requires Repair: {path}")
    for field in ("identity", "entry", "cid"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise ArtifactReservationError(
                f"reservation requires Repair: {path}: {field}"
            )
    if not Path(raw["entry"]).is_absolute():
        raise ArtifactReservationError(f"reservation requires Repair: {path}: entry")
    for field in ("reads", "writes"):
        if not isinstance(raw[field], list) or not all(
            isinstance(value, str) and Path(value).is_absolute() for value in raw[field]
        ):
            raise ArtifactReservationError(
                f"reservation requires Repair: {path}: {field}"
            )
    _require_owners(raw, path)
    reservation = ArtifactReservation(
        raw["identity"],
        raw["entry"],
        raw["cid"],
        tuple(raw["reads"]),
        tuple(raw["writes"]),
        raw["parent_pid"],
        raw["worker_pid"],
    )
    if _path(path.parents[2], reservation.identity).name != path.name:
        raise ArtifactReservationError(f"reservation identity mismatch: {path}")
    return reservation


def _require_owners(raw: dict, path: Path) -> None:
    for field in ("parent_pid", "worker_pid"):
        value = raw[field]
        if field == "worker_pid" and value is None:
            continue
        if type(value) is not int or value <= 0:
            raise ArtifactReservationError(
                f"reservation requires Repair: {path}: {field}"
            )


def _records(root: Path) -> tuple[ArtifactReservation, ...]:
    paths = sorted(operation_directory(root).glob("ordinary-execution-*.json"))
    if len(paths) > MAX_RESERVATIONS:
        raise ArtifactReservationError(
            "too many artifact reservations; clear abandoned runs"
        )
    return tuple(_decode(path) for path in paths)


def _publish(root: Path, reservation: ArtifactReservation) -> None:
    text = json.dumps(reservation.as_dict(), sort_keys=True) + "\n"
    if len(text.encode()) > MAX_RESERVATION_BYTES:
        raise ArtifactReservationError("artifact reservation crossed its byte bound")
    atomic_replace_text(_path(root, reservation.identity), text)


def _overlap(first: str, second: str) -> bool:
    left, right = Path(first), Path(second)
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _require_access(
    records: tuple[ArtifactReservation, ...],
    reads: tuple[str, ...],
    writes: tuple[str, ...],
    exclude: str | None,
) -> None:
    for record in records:
        if record.identity == exclude:
            continue
        conflicts = {
            path
            for path in reads
            if any(_overlap(path, prior) for prior in record.writes)
        }
        conflicts.update(
            path
            for path in writes
            if any(_overlap(path, prior) for prior in (*record.reads, *record.writes))
        )
        conflicts.update(
            path for path in writes if Path(record.entry).is_relative_to(Path(path))
        )
        if conflicts:
            raise ArtifactReservationError(
                f"{record.entry}/{record.cid}: artifacts in use: {sorted(conflicts)}; "
                f"owner PID {record.parent_pid}, worker {record.worker_pid}; "
                "wait for the run or use log command release for an abandoned run"
            )


@contextmanager
def artifact_transaction(
    root: Path,
    *,
    reads: tuple[Path, ...] = (),
    writes: tuple[Path, ...] = (),
    exclude: str | None = None,
) -> Iterator[None]:
    """Briefly guard reservation discovery and a state-sensitive publication."""

    read_paths = tuple(path.resolve().as_posix() for path in reads)
    write_paths = tuple(path.resolve().as_posix() for path in writes)
    with operation_lock(root, "artifact-reservations.lock", timeout_seconds=10):
        _require_access(_records(root), read_paths, write_paths, exclude)
        yield


def require_artifact_access(
    root: Path, *, reads: tuple[Path, ...] = (), writes: tuple[Path, ...] = ()
) -> None:
    """Read-only conflict check; publication must check again under its guard."""

    _require_access(
        _records(root),
        tuple(path.resolve().as_posix() for path in reads),
        tuple(path.resolve().as_posix() for path in writes),
        None,
    )


def _alive(pid: int, *, group: bool = False) -> bool:
    try:
        (os.killpg if group else os.kill)(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def require_worker_finished(root: Path, identity: str) -> None:
    """Refuse publication while any registered worker-group consumer survives."""

    reservation = _decode(_path(root, identity))
    if reservation.worker_pid is not None and _alive(
        reservation.worker_pid, group=True
    ):
        raise WorkerStillActiveError(
            "worker descendants remain active; reservation retained"
        )


@contextmanager
def reserve_execution(
    root: Path, entry: Path, cid: str, reads: tuple[Path, ...], writes: tuple[Path, ...]
) -> Iterator[ArtifactReservation]:
    """Reserve one invocation, releasing only after its worker group finishes."""

    reservation = ArtifactReservation(
        uuid.uuid4().hex,
        entry.resolve().as_posix(),
        cid,
        tuple(sorted({path.resolve().as_posix() for path in reads})),
        tuple(sorted({path.resolve().as_posix() for path in writes})),
        os.getpid(),
    )
    with artifact_transaction(root, reads=reads, writes=writes):
        _publish(root, reservation)
    try:
        yield reservation
    finally:
        with operation_lock(root, "artifact-reservations.lock", timeout_seconds=10):
            path = _path(root, reservation.identity)
            if path.exists():
                current = _decode(path)
                if not recovery_candidate_path(
                    root, reservation.identity
                ).exists() and (
                    current.worker_pid is None
                    or not _alive(current.worker_pid, group=True)
                ):
                    path.unlink()


def register_worker(root: Path, identity: str) -> None:
    """Register the worker before it can execute or write retained outputs."""

    with operation_lock(root, "artifact-reservations.lock", timeout_seconds=10):
        path = _path(root, identity)
        if not path.exists():
            raise ArtifactReservationError(
                "reservation was released before worker launch"
            )
        current = _decode(path)
        if current.worker_pid is not None:
            raise ArtifactReservationError("reservation already has a worker")
        _publish(root, replace(current, worker_pid=os.getpid()))


def release_abandoned(root: Path, entry: Path, cid: str, *, dry_run: bool) -> int:
    """Clear only this entry/CID's reservations, refusing every live owner."""

    if dry_run:
        return len(_abandoned(root, entry, cid))
    with operation_lock(root, "artifact-reservations.lock", timeout_seconds=10):
        selected = _abandoned(root, entry, cid)
        for record in selected:
            recovery_candidate_path(root, record.identity).unlink(missing_ok=True)
            _path(root, record.identity).unlink()
        return len(selected)


def finish_recovery(root: Path, entry: Path, identity: str) -> None:
    """Remove only a finished entry-owned candidate and reservation."""

    with operation_lock(root, "artifact-reservations.lock", timeout_seconds=10):
        record = recovery_reservation(root, entry, identity)
        require_recovery_worker_finished(record)
        _path(root, identity).unlink()
        recovery_candidate_path(root, identity).unlink()


def finish_orphan_candidate(root: Path, identity: str) -> None:
    """Finish cleanup after publication removed the reservation first."""

    with operation_lock(root, "artifact-reservations.lock", timeout_seconds=10):
        reservation_path = _path(root, identity)
        if reservation_path.exists() or reservation_path.is_symlink():
            raise ArtifactReservationError(
                "reservation reappeared during recovery cleanup"
            )
        recovery_candidate_path(root, identity).unlink()


def _abandoned(root: Path, entry: Path, cid: str) -> tuple[ArtifactReservation, ...]:
    selected = tuple(
        record
        for record in _records(root)
        if record.entry == entry.resolve().as_posix() and record.cid == cid
    )
    for record in selected:
        if _alive(record.parent_pid) or (
            record.worker_pid is not None and _alive(record.worker_pid, group=True)
        ):
            raise ArtifactReservationError(
                f"cannot release live execution: {entry}/{cid}"
            )
    return selected

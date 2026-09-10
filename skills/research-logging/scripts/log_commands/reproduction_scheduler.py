"""Project-wide ordinary and exclusive reproduction scheduling permits."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence, cast

import psutil
from validation.operation_state import (
    OperationLockError,
    operation_directory,
    operation_lock,
)

from .model import ActionError
from .storage import atomic_write_text

SCHEDULER_SCHEMA = "research-log-reproduction-scheduler/1"
POLL_SECONDS = 0.1
MAX_WAITERS = 10_000
MAX_PERMITS = 4_096
MAX_SCHEDULER_BYTES = 64 * 1024 * 1024
MAX_RUN_PATH_ANCESTORS = 8
_THREAD_LOCK = threading.Lock()
RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
EXECUTION_ID_RE = re.compile(r"pyrun-exec/v1:[0-9a-f]{64}\Z")
ENTRY_ID_RE = re.compile(r"e[0-9]{3}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


@dataclass(frozen=True)
class SchedulingPermit:
    """One granted coordinator permit."""

    project_root: Path
    permit_id: str


@dataclass(frozen=True)
class _SchedulingRequest:
    project_root: Path
    run_root: Path
    run_id: str
    planned: Mapping[str, object]
    supervisor_pid: int


def acquire_scheduling_permit(
    project_root: Path,
    run_root: Path,
    run_id: str,
    planned: Mapping[str, object],
    *,
    stop_requested: Callable[[], bool],
) -> SchedulingPermit | None:
    """Wait for and return one conflict-free project scheduling permit."""

    exclusive = planned.get("exclusive") is True
    request = _SchedulingRequest(project_root, run_root, run_id, planned, os.getpid())
    waiter_ticket: int | None = None
    while not stop_requested():
        with _coordinator_lock(project_root):
            state = _load(project_root)
            unresolved = _remove_dead(project_root, state)
            if unresolved:
                _write(project_root, state)
                owner = unresolved[0]
                raise ActionError(
                    "reproduction.scheduler.reconciliation_required",
                    "dead supervisor owns an unreconciled scheduling permit for "
                    f"{owner['run_id']}:{owner['entry']}:{owner['execution_id']}; "
                    "inspect that run with `log reproduce status` before retrying",
                )
            if exclusive and waiter_ticket is None:
                if len(cast(list[object], state["waiters"])) >= MAX_WAITERS:
                    raise ActionError(
                        "reproduction.scheduler.resource_limit",
                        "exclusive waiter bound was reached",
                    )
                waiter_ticket = cast(int, state["next_ticket"])
                state["next_ticket"] = waiter_ticket + 1
                cast(list[object], state["waiters"]).append(
                    _waiter(request, waiter_ticket)
                )
                _write(project_root, state)
            active = cast(list[Mapping[str, object]], state["active"])
            waiters = cast(list[Mapping[str, object]], state["waiters"])
            admissible = False
            if exclusive:
                first = min(
                    waiters,
                    key=lambda item: (
                        cast(int, item["ticket"]),
                        cast(str, item["run_id"]),
                        cast(int, item["plan_order"]),
                    ),
                    default=None,
                )
                admissible = (
                    not active
                    and first is not None
                    and first["ticket"] == waiter_ticket
                    and first["run_id"] == run_id
                )
            else:
                admissible = not waiters and not any(
                    _claims_conflict(item, planned, run_root, project_root)
                    for item in active
                )
            if admissible:
                if len(active) >= MAX_PERMITS:
                    raise ActionError(
                        "reproduction.scheduler.resource_limit",
                        "active permit bound was reached",
                    )
                permit_id = f"permit-{secrets.token_hex(12)}"
                if exclusive:
                    state["waiters"] = [
                        item for item in waiters if item["ticket"] != waiter_ticket
                    ]
                active.append(
                    _permit(
                        permit_id,
                        "exclusive" if exclusive else "ordinary",
                        request,
                    )
                )
                _write(project_root, state)
                return SchedulingPermit(project_root, permit_id)
            _write(project_root, state)
        time.sleep(POLL_SECONDS)
    if waiter_ticket is not None:
        cancel_scheduling_waiters(project_root, run_id)
    return None


def release_scheduling_permit(permit: SchedulingPermit) -> None:
    """Release one permit after its terminal checkpoint is durable."""

    with _coordinator_lock(permit.project_root):
        state = _load(permit.project_root)
        state["active"] = [
            item
            for item in cast(list[Mapping[str, object]], state["active"])
            if item["permit_id"] != permit.permit_id
        ]
        _write(permit.project_root, state)


def cancel_scheduling_waiters(project_root: Path, run_id: str) -> None:
    """Remove every not-yet-granted exclusive request for one stopped run."""

    with _coordinator_lock(project_root):
        state = _load(project_root)
        state["waiters"] = [
            item
            for item in cast(list[Mapping[str, object]], state["waiters"])
            if item["run_id"] != run_id
        ]
        _write(project_root, state)


def release_run_scheduling(project_root: Path, run_id: str) -> None:
    """Remove proved-inactive waiters and permits for one recovered run."""

    with _coordinator_lock(project_root):
        state = _load(project_root)
        state["waiters"] = [
            item
            for item in cast(list[Mapping[str, object]], state["waiters"])
            if item["run_id"] != run_id
        ]
        state["active"] = [
            item
            for item in cast(list[Mapping[str, object]], state["active"])
            if item["run_id"] != run_id
        ]
        _write(project_root, state)


def _empty() -> dict[str, object]:
    return {"schema": SCHEDULER_SCHEMA, "next_ticket": 0, "waiters": [], "active": []}


@contextmanager
def _coordinator_lock(project_root: Path) -> Iterator[None]:
    """Serialize threads locally and wait briefly for the cross-process mutex."""

    with _THREAD_LOCK:
        while True:
            manager = operation_lock(project_root, "reproduction-scheduler.lock")
            try:
                manager.__enter__()
            except OperationLockError:
                time.sleep(POLL_SECONDS)
                continue
            try:
                yield
            finally:
                manager.__exit__(None, None, None)
            return


def _load(project_root: Path) -> dict[str, object]:
    path = operation_directory(project_root) / "reproduction-scheduler.json"
    if not path.exists():
        return _empty()
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("scheduler record must be a regular file")
        raw = path.read_bytes()
        if len(raw) > MAX_SCHEDULER_BYTES:
            raise OSError("scheduler record crossed its byte bound")
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ActionError("reproduction.scheduler.invalid", str(error)) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "next_ticket", "waiters", "active"}
        or value.get("schema") != SCHEDULER_SCHEMA
        or not isinstance(value.get("next_ticket"), int)
        or isinstance(value.get("next_ticket"), bool)
        or cast(int, value.get("next_ticket")) < 0
        or not isinstance(value.get("waiters"), list)
        or not isinstance(value.get("active"), list)
    ):
        raise ActionError("reproduction.scheduler.invalid", "invalid scheduler record")
    waiters = cast(list[object], value["waiters"])
    active = cast(list[object], value["active"])
    if (
        not waiters
        and not active
        or len(waiters) > MAX_WAITERS
        or len(active) > MAX_PERMITS
        or any(not _valid_waiter(item) for item in waiters)
        or any(not _valid_permit(item) for item in active)
    ):
        raise ActionError("reproduction.scheduler.invalid", "invalid scheduler item")
    typed_waiters = cast(list[Mapping[str, object]], waiters)
    typed_active = cast(list[Mapping[str, object]], active)
    if typed_waiters != sorted(
        typed_waiters,
        key=lambda item: (item["ticket"], item["run_id"], item["plan_order"]),
    ) or typed_active != sorted(
        typed_active,
        key=lambda item: (
            item["run_id"],
            item["plan_order"],
            item["entry"],
            item["execution_id"],
        ),
    ):
        raise ActionError(
            "reproduction.scheduler.invalid", "scheduler order is invalid"
        )
    tickets = [cast(int, item["ticket"]) for item in typed_waiters]
    permit_ids = [cast(str, item["permit_id"]) for item in typed_active]
    if (
        len(tickets) != len(set(tickets))
        or any(ticket >= cast(int, value["next_ticket"]) for ticket in tickets)
        or len(permit_ids) != len(set(permit_ids))
        or sum(item["kind"] == "exclusive" for item in typed_active) > 0
        and len(typed_active) != 1
        or text != _canonical(value)
    ):
        raise ActionError(
            "reproduction.scheduler.invalid", "scheduler record is invalid"
        )
    return cast(dict[str, object], value)


def _write(project_root: Path, state: Mapping[str, object]) -> None:
    directory = operation_directory(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reproduction-scheduler.json"
    if not state["waiters"] and not state["active"]:
        path.unlink(missing_ok=True)
        return
    normalized = dict(state)
    normalized["waiters"] = sorted(
        cast(Sequence[Mapping[str, object]], state["waiters"]),
        key=lambda item: (item["ticket"], item["run_id"], item["plan_order"]),
    )
    normalized["active"] = sorted(
        cast(Sequence[Mapping[str, object]], state["active"]),
        key=lambda item: (
            item["run_id"],
            item["plan_order"],
            item["entry"],
            item["execution_id"],
        ),
    )
    encoded = _canonical(normalized)
    if len(encoded.encode("utf-8")) > MAX_SCHEDULER_BYTES:
        raise ActionError(
            "reproduction.scheduler.resource_limit",
            "scheduler record crossed its byte bound",
        )
    atomic_write_text(path, encoded)


def _remove_dead(
    project_root: Path, state: dict[str, object]
) -> tuple[Mapping[str, object], ...]:
    """Remove dead waiters and provably inactive permits.

    A dead permit remains fail-closed unless its canonical owner record is
    terminal, has no active execution, and records no surviving worker. The
    returned permits require explicit owner-run recovery.
    """

    state["waiters"] = [
        item
        for item in cast(list[Mapping[str, object]], state["waiters"])
        if _supervisor_alive(item)
    ]
    retained: list[Mapping[str, object]] = []
    unresolved: list[Mapping[str, object]] = []
    for item in cast(list[Mapping[str, object]], state["active"]):
        if _supervisor_alive(item):
            retained.append(item)
        elif _terminal_permit_owner_is_inactive(project_root, item):
            continue
        else:
            retained.append(item)
            unresolved.append(item)
    state["active"] = retained
    return tuple(unresolved)


def _supervisor_alive(item: Mapping[str, object]) -> bool:
    pid = item.get("supervisor_pid")
    assert isinstance(pid, int) and not isinstance(pid, bool) and pid > 1
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        raise ActionError(
            "reproduction.scheduler.inspection_unavailable", str(error)
        ) from error
    return True


def _terminal_permit_owner_is_inactive(
    project_root: Path, item: Mapping[str, object]
) -> bool:
    """Prove that a dead permit belongs to one fully inactive terminal run."""

    record = _permit_owner_record(project_root, item)
    if record is None:
        return False
    state = cast(Mapping[str, object], record["state"])
    workers = cast(Sequence[Mapping[str, object]], record["workers"])
    active = state.get(
        "active_executions",
        state.get("current_execution"),
    )
    return (
        record.get("run_id") == item.get("run_id")
        and state.get("status") in {"complete", "stopped", "failed"}
        and state.get("phase") is None
        and active in (None, [])
        and not any(worker.get("state") == "running" for worker in workers)
        and not _recorded_worker_alive(cast(str, record["run_id"]), workers)
    )


def _recorded_worker_alive(
    run_id: str, workers: Sequence[Mapping[str, object]]
) -> bool:
    for worker in workers:
        try:
            process = psutil.Process(cast(int, worker["pid"]))
            marker = process.environ().get("RESEARCH_LOG_REPRODUCTION_RUN_ID")
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            return True
        except (OSError, psutil.Error) as error:
            raise ActionError(
                "reproduction.scheduler.inspection_unavailable", str(error)
            ) from error
        if marker == run_id or (
            isinstance(marker, str) and marker.startswith(f"{run_id}:")
        ):
            return True
    return False


def _permit_owner_record(
    project_root: Path, item: Mapping[str, object]
) -> Mapping[str, object] | None:
    run_path = item.get("run_path")
    if not isinstance(run_path, str):
        return None
    try:
        from .reproduction_jobs import _load_run
        from .reproduction_paths import canonical_run_root

        owner_root = next(
            candidate
            for candidate in tuple(Path(run_path).parents)[:MAX_RUN_PATH_ANCESTORS]
            if (candidate / "run.json").is_file()
        )
        owner_root = canonical_run_root(
            owner_root, project_root, require_exists=True
        )
        return _load_run(owner_root / "run.json")
    except (ActionError, OSError, StopIteration):
        return None


def _waiter(
    request: _SchedulingRequest,
    ticket: int,
) -> dict[str, object]:
    planned = request.planned
    return {
        "ticket": ticket,
        "run_id": request.run_id,
        "entry": planned["entry"],
        "execution_id": planned["execution_id"],
        "plan_order": planned["order"],
        "supervisor_pid": request.supervisor_pid,
        "registered_at": _utc_now(),
    }


def _permit(
    permit_id: str,
    kind: str,
    request: _SchedulingRequest,
) -> dict[str, object]:
    planned = request.planned
    return {
        "permit_id": permit_id,
        "kind": kind,
        "run_id": request.run_id,
        "entry": planned["entry"],
        "execution_id": planned["execution_id"],
        "plan_order": planned["order"],
        "supervisor_pid": request.supervisor_pid,
        "read_paths": _resolve_claims(
            planned.get("read_paths", []), request.run_root, request.project_root
        ),
        "write_paths": _resolve_claims(
            planned.get("write_paths", []), request.run_root, request.project_root
        ),
        "run_path": _resolve_claim(
            cast(
                str,
                planned.get(
                    "run_path",
                    f"<run>/executions/{planned['entry']}/{planned['order']}",
                ),
            ),
            request.run_root,
            request.project_root,
        ),
        "writable_paths": _resolve_claims(
            planned.get("writable_paths", []), request.run_root, request.project_root
        ),
        "granted_at": _utc_now(),
    }


def _claims_conflict(
    active: Mapping[str, object],
    planned: Mapping[str, object],
    run_root: Path,
    project_root: Path,
) -> bool:
    if active.get("kind") == "exclusive" or planned.get("exclusive") is True:
        return True
    reads = _resolve_claims(planned.get("read_paths", []), run_root, project_root)
    writes = _resolve_claims(planned.get("write_paths", []), run_root, project_root)
    writable = _resolve_claims(
        planned.get("writable_paths", []), run_root, project_root
    )
    active_reads = cast(Sequence[str], active["read_paths"])
    active_writes = cast(Sequence[str], active["write_paths"])
    active_writable = cast(Sequence[str], active["writable_paths"])
    run_path = _resolve_claim(cast(str, planned["run_path"]), run_root, project_root)
    active_run_path = cast(str, active["run_path"])
    mutable = (*writes, *writable, run_path)
    active_mutable = (*active_writes, *active_writable, active_run_path)
    if _sets_overlap(mutable, active_reads) or _sets_overlap(active_mutable, reads):
        return True
    return _sets_overlap(mutable, active_mutable)


def _valid_waiter(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "ticket",
        "run_id",
        "entry",
        "execution_id",
        "plan_order",
        "supervisor_pid",
        "registered_at",
    }:
        return False
    return (
        _nonnegative_int(value["ticket"])
        and _valid_identity(value)
        and _positive_int(value["plan_order"])
        and _positive_pid(value["supervisor_pid"])
        and _timestamp(value["registered_at"])
    )


def _valid_permit(value: object) -> bool:
    fields = {
        "permit_id",
        "kind",
        "run_id",
        "entry",
        "execution_id",
        "plan_order",
        "supervisor_pid",
        "read_paths",
        "write_paths",
        "run_path",
        "writable_paths",
        "granted_at",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return False
    permit_id = value["permit_id"]
    return (
        isinstance(permit_id, str)
        and re.fullmatch(r"permit-[0-9a-f]{24}", permit_id) is not None
        and value["kind"] in {"ordinary", "exclusive"}
        and _valid_identity(value)
        and _positive_int(value["plan_order"])
        and _positive_pid(value["supervisor_pid"])
        and all(
            _valid_paths(value[name])
            for name in ("read_paths", "write_paths", "writable_paths")
        )
        and _valid_path(value["run_path"])
        and _timestamp(value["granted_at"])
    )


def _valid_identity(value: Mapping[str, object]) -> bool:
    return (
        isinstance(value.get("run_id"), str)
        and RUN_ID_RE.fullmatch(cast(str, value["run_id"])) is not None
        and isinstance(value.get("entry"), str)
        and ENTRY_ID_RE.fullmatch(cast(str, value["entry"])) is not None
        and isinstance(value.get("execution_id"), str)
        and EXECUTION_ID_RE.fullmatch(cast(str, value["execution_id"])) is not None
    )


def _valid_paths(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(_valid_path(item) for item in value)
        and value == sorted(set(cast(list[str], value)))
    )


def _valid_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and Path(value).is_absolute()
        and str(Path(value).resolve()) == value
    )


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_pid(value: object) -> bool:
    return _positive_int(value) and cast(int, value) > 1


def _timestamp(value: object) -> bool:
    return isinstance(value, str) and TIMESTAMP_RE.fullmatch(value) is not None


def _canonical(value: Mapping[str, object]) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    )


def _sets_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    return any(_path_overlap(a, b) for a in left for b in right)


def _path_overlap(left: str, right: str) -> bool:
    a = Path(left)
    b = Path(right)
    return a == b or a in b.parents or b in a.parents


def _resolve_claims(value: object, run_root: Path, project_root: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ActionError("reproduction.scheduler.invalid_claim", "invalid path claims")
    return sorted({_resolve_claim(item, run_root, project_root) for item in value})


def _resolve_claim(value: str, run_root: Path, project_root: Path) -> str:
    if value == "<run>":
        return str(run_root.resolve())
    if value.startswith("<run>/"):
        return str((run_root / value.removeprefix("<run>/")).resolve())
    if value.startswith("<project>/"):
        return str((project_root / value.removeprefix("<project>/")).resolve())
    return str(Path(value).resolve())


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

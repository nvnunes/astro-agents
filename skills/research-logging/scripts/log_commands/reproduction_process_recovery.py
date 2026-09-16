"""Shared physical recovery rules, independent of accepted job/result formats.

Run-marked process termination, exhaustive survivor observations, PID probes and
confined scratch deletion retain the original operational safety contract.
Durable job owners decide how these observations affect lifecycle state.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import psutil

from .model import ActionError
from .reproduction_execution import _utc_now
from .reproduction_job_control import ExecutionIdentity, RecoveryWorkerObservation
from .reproduction_job_control import WorkerRecord as StoredWorkerRecord


def _remove_current_scratch(path: Path) -> None:
    temporary = Path("/private/tmp")
    if (
        not path.is_absolute()
        or path.parent != temporary
        or not path.name.startswith("reproduction-scratch-")
    ):
        raise ActionError(
            "reproduction.scratch.invalid", "stored scratch path is not owned"
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(temporary, flags)
    except OSError as error:
        raise ActionError(
            "reproduction.scratch.invalid", "scratch parent is unavailable"
        ) from error
    try:
        try:
            observed = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(observed.st_mode)
            or not shutil.rmtree.avoids_symlink_attacks
        ):
            raise ActionError(
                "reproduction.scratch.invalid", "stored scratch path is not owned"
            )
        try:
            cast(Any, shutil.rmtree)(path.name, dir_fd=directory_fd)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ActionError(
                "reproduction.scratch.invalid", "stored scratch path changed"
            ) from error
    finally:
        os.close(directory_fd)


def _recovery_worker_observations(
    survivors: Sequence[Mapping[str, object]],
    *,
    observed_at: str,
) -> tuple[RecoveryWorkerObservation, ...]:
    """Convert one exhaustive process scan to durable worker observations."""

    observed: list[RecoveryWorkerObservation] = []
    for survivor in survivors:
        entry = survivor.get("entry")
        cid = survivor.get("cid")
        execution_id = survivor.get("execution_id")
        identity = (
            ExecutionIdentity(entry, cid, execution_id)
            if isinstance(entry, str)
            and isinstance(cid, str)
            and isinstance(execution_id, str)
            else None
        )
        registered_at = survivor.get("registered_at")
        last_observed_at = survivor.get("last_observed_at")
        observed.append(
            RecoveryWorkerObservation(
                identity,
                StoredWorkerRecord(
                    cast(str, survivor["worker_id"]),
                    cast(str | None, survivor.get("parent_worker_id")),
                    cast(int, survivor["pid"]),
                    "running",
                    registered_at if isinstance(registered_at, str) else observed_at,
                    last_observed_at
                    if isinstance(last_observed_at, str)
                    else observed_at,
                ),
            )
        )
    return tuple(observed)


def _terminate_marked_workers(run_id: str) -> list[Mapping[str, object]]:
    found: dict[int, tuple[psutil.Process, str | None, str | None, str | None]] = {}
    try:
        for process in psutil.process_iter(["pid"]):
            try:
                marker = process.environ().get("RESEARCH_LOG_REPRODUCTION_RUN_ID")
                if marker == run_id or (
                    isinstance(marker, str) and marker.startswith(f"{run_id}:")
                ):
                    entry, cid, execution = _marker_identity(run_id, marker)
                    found[process.pid] = (process, entry, cid, execution)
            except psutil.Error:
                continue
    except (OSError, psutil.Error) as error:
        raise ActionError(
            "reproduction.worker.inspection_unavailable", str(error)
        ) from error
    processes = [item[0] for item in found.values()]
    for process in processes:
        try:
            process.kill()
        except psutil.Error:
            pass
    _, live = psutil.wait_procs(processes, timeout=10.0)
    now = _utc_now()
    return [
        {
            "entry": found[process.pid][1],
            "cid": found[process.pid][2],
            "worker_id": f"worker-{process.pid}",
            "parent_worker_id": None,
            "pid": process.pid,
            "execution_id": found[process.pid][3],
            "state": "running",
            "registered_at": now,
            "last_observed_at": now,
        }
        for process in sorted(live, key=lambda item: item.pid)
    ]


def _marker_identity(
    run_id: str, marker: object
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(marker, str) or not marker.startswith(f"{run_id}:"):
        return None, None, None
    parts = marker.removeprefix(f"{run_id}:").split(":")
    if (
        len(parts) == 3
        and re.fullmatch(r"e[0-9]{3}", parts[0]) is not None
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", parts[1]) is not None
    ):
        digest = parts[2]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is not None:
            return parts[0], parts[1], f"pyrun-exec/v2:{digest}"
    return None, None, None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

"""Publish native job completion and recover its compact report without execution.

Accepted work and native observations are the only fact owners. A minimal job
journal freezes completion time before the shared write and records external
acknowledgments. Exact saved-run retry recognizes a lost commit acknowledgment
without replaying latest indexes or changing the immutable facts.
"""

from __future__ import annotations

from pathlib import Path

from research_log_result_store import results_lock
from validation.operation_state import operation_lock

from .context import LogContext
from .reproduction_execution import _utc_now
from .reproduction_job_control import JobStoreInvariantError
from .reproduction_saved_report import materialize_saved_report_locked
from .reproduction_saved_storage import publish_saved_run
from .reproduction_work_job import open_work_job


def publish_work_job(log: LogContext, run_root: Path) -> int:
    """Publish one completed native job, or recover only its unfinished writes.

    The caller owns run scope locks and any promotion coordination. Lock order
    is native job, log publication, then results. No current source/graph scan,
    recipe execution or planning occurs here. Failures preserve the frozen job
    and any committed saved facts; retry uses the identical completion time.
    A fully acknowledged job returns its report generation without mutations.
    """

    with open_work_job(run_root) as job:
        if job.accepted.plan.summary != str(log.summary):
            raise JobStoreInvariantError("publication log differs from accepted job")
        state = job.load_run_control()
        publication = job.load_publication()
        if state.status == "complete":
            if publication is None or publication.report_generation is None:
                raise JobStoreInvariantError(
                    "complete job has no report acknowledgment"
                )
            return publication.report_generation
        run = job.prepare_publication(finished_at=_utc_now())
        with operation_lock(log.root, "reproduction-publication.lock"):
            with results_lock(log.root):
                generation = publish_saved_run(log.root, run)
                job._record_result_commit(generation, updated_at=_utc_now())
                materialize_saved_report_locked(log)
                job._finish_publication(generation, updated_at=_utc_now())
    return generation

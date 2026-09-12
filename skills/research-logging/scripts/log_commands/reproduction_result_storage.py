"""Normalized SQLite ownership for published reproduction observations."""
# ruff: noqa: E501, F841

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from research_log_data import parse_fingerprint
from research_log_paths import RESULTS_STORE
from research_log_result_store import (
    ResultStoreError,
    result_snapshot,
    result_transaction,
)

if TYPE_CHECKING:
    from .reproduction_results import (
        ArtifactResult,
        CommandResult,
        ReproductionResults,
        RunResult,
    )


class ReproductionStorageError(RuntimeError):
    """A precise failure reading or publishing normalized reproduction rows."""

    def __init__(self, message: str, *, code: str = "reproduction.results.invalid"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommandProjectionRequest:
    run_id: str | None
    bucket: str | None = None
    entry: str | None = None
    reason: str | None = None
    execution_id: str | None = None
    limit: int = 50


@dataclass(frozen=True)
class ReproductionPublicationRequest:
    """The closed replacement set for one terminal reproduction publication."""

    summary: str
    run: RunResult
    artifacts: tuple[ArtifactResult, ...]
    commands: tuple[CommandResult, ...]
    selected_execution_keys: tuple[tuple[str, str], ...]
    selected_pre_execution_artifact_keys: tuple[tuple[str, str], ...]


def _root(path: Path) -> Path:
    if path.as_posix().endswith(RESULTS_STORE):
        return path.parent.parent
    raise ReproductionStorageError(f"result store path is not canonical: {path}")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _validate_comparison_contracts(row: sqlite3.Row) -> None:
    profile = row["comparison_profile"]
    if profile is None:
        if any(
            row[name] is not None
            for name in (
                "comparison_contract",
                "expected_json",
                "regenerated_json",
                "evidence_contract",
                "evidence_definition",
            )
        ):
            raise ReproductionStorageError("reproduction comparison contracts are invalid")
        return
    if row["comparison_contract"] != "research-log-reproduction-comparison/1":
        raise ReproductionStorageError("reproduction comparison contract is invalid")
    evidence_definition = row["evidence_definition"]
    if (evidence_definition is None and row["evidence_contract"] is not None) or (
        evidence_definition is not None
        and row["evidence_contract"] != "research-log-evidence-comparison-result/1"
    ):
        raise ReproductionStorageError("reproduction evidence contract is invalid")


def publish_reproduction_results(
    path: Path, request: ReproductionPublicationRequest
) -> int:
    """Commit a terminal run and replace only its declared current identities."""
    artifact_rows = request.artifacts
    command_rows = request.commands
    selected_execution_keys = request.selected_execution_keys
    selected_pre_execution_artifact_keys = request.selected_pre_execution_artifact_keys
    _validate_selected_identities(
        request.run,
        artifact_rows,
        command_rows,
        selected_execution_keys,
        selected_pre_execution_artifact_keys,
    )
    try:
        with result_transaction(_root(path)) as db:
            metadata = db.execute(
                "SELECT summary FROM reproduction_metadata WHERE singleton=1"
            ).fetchone()
            if metadata is not None and metadata["summary"] != request.summary:
                raise ReproductionStorageError(
                    "reproduction summary identity is invalid"
                )
            _validate_publication_rows(request.artifacts, request.commands)
            run = request.run
            target = dict(run.target)
            db.execute("DELETE FROM reproduction_runs WHERE run_id=?", (run.run_id,))
            db.execute(
                "INSERT INTO reproduction_runs VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    target["kind"],
                    target.get("entry"),
                    target.get("execution_id"),
                    int(run.include_all),
                    run.status,
                    run.accepted_at,
                    run.finished_at,
                    run.folder.path,
                    *(
                        run.artifact_outcomes[name]
                        for name in (
                            "matched",
                            "changed",
                            "failed",
                            "comparison_failed",
                            "skipped",
                        )
                    ),
                    *(
                        None
                        if run.command_outcomes is None
                        else run.command_outcomes[name]
                        for name in (
                            "not_automatic",
                            "reproduction_not_needed",
                            "unchanged_failed",
                            "unchanged_blocked",
                            "succeeded",
                            "failed",
                            "blocked",
                            "total",
                        )
                    ),
                ),
            )
            for order, record in enumerate(run.command_records or ()):
                db.execute(
                    "INSERT INTO reproduction_run_commands VALUES (?,?,?,?,?,?,?,?,?,?)",  # noqa: E501
                    (
                        run.run_id,
                        record["entry"],
                        record["execution_id"],
                        order,
                        record["bucket"],
                        record["reason"],
                        record.get("terminal_disposition"),
                        record.get("source_digest"),
                        _json(record.get("recipe")),
                        _json(record),
                    ),
                )
            for position, execution in enumerate(run.executions):
                db.execute(
                    "INSERT INTO reproduction_run_executions VALUES (?,?,?,?,?,?,?)",
                    (
                        run.run_id,
                        execution["entry"],
                        execution["execution_id"],
                        position,
                        execution.get("started_at"),
                        execution.get("finished_at"),
                        execution.get("elapsed_seconds"),
                    ),
                )
            for entry, execution_id in selected_execution_keys:
                db.execute(
                    "DELETE FROM reproduction_artifact_results "
                    "WHERE entry=? AND execution_id=?",
                    (entry, execution_id),
                )
            for command in command_rows:
                db.execute(
                    "INSERT INTO reproduction_execution_results VALUES (?,?,?,?,?,?) ON CONFLICT(entry,execution_id) DO UPDATE SET disposition=excluded.disposition,source_digest=excluded.source_digest,recorded_at=excluded.recorded_at,producing_run_id=excluded.producing_run_id",  # noqa: E501
                    (
                        command.entry,
                        command.execution_id,
                        command.disposition,
                        command.source_digest,
                        command.recorded_at,
                        command.run_id,
                    ),
                )
            for artifact in artifact_rows:
                comparison = artifact.comparison
                db.execute(
                    "DELETE FROM reproduction_comparison_evidence WHERE entry=? AND artifact=?",  # noqa: E501
                    (artifact.entry, artifact.artifact),
                )
                db.execute(
                    "INSERT INTO reproduction_artifact_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(entry,artifact) DO UPDATE SET execution_id=excluded.execution_id,outcome=excluded.outcome,reason=excluded.reason,recorded_at=excluded.recorded_at,producing_run_id=excluded.producing_run_id,comparison_contract=excluded.comparison_contract,comparison_profile=excluded.comparison_profile,expected_json=excluded.expected_json,regenerated_json=excluded.regenerated_json,evidence_contract=excluded.evidence_contract,evidence_definition=excluded.evidence_definition",  # noqa: E501
                    (
                        artifact.entry,
                        artifact.artifact,
                        artifact.execution_id,
                        artifact.outcome,
                        artifact.reason,
                        artifact.recorded_at,
                        artifact.run_id,
                        None
                        if comparison is None
                        else "research-log-reproduction-comparison/1",
                        None if comparison is None else comparison.profile,
                        None
                        if comparison is None or comparison.expected is None
                        else _json(comparison.expected.as_dict()),
                        None
                        if comparison is None or comparison.regenerated is None
                        else _json(comparison.regenerated.as_dict()),
                        None
                        if comparison is None or comparison.evidence_definition is None
                        else "research-log-evidence-comparison-result/1",
                        None if comparison is None else comparison.evidence_definition,
                    ),
                )
                if comparison is not None:
                    for position, evidence in enumerate(comparison.evidence):
                        db.execute(
                            "INSERT INTO reproduction_comparison_evidence VALUES (?,?,?,?,?,?,?,?)",  # noqa: E501
                            (
                                artifact.entry,
                                artifact.artifact,
                                position,
                                evidence.get("record_id"),
                                _json(evidence.get("retained")),
                                _json(evidence.get("regenerated")),
                                _json(evidence.get("tolerance")),
                                int(bool(evidence.get("matched"))),
                            ),
                        )
            db.execute(
                "INSERT INTO reproduction_metadata VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET summary=excluded.summary,updated_at=excluded.updated_at",  # noqa: E501
                (request.summary, run.finished_at or run.accepted_at),
            )
            prior = db.execute(
                "SELECT generation FROM store_state WHERE domain='reproduction'"
            ).fetchone()
            generation = 1 if prior is None else prior[0] + 1
            db.execute(
                "INSERT INTO store_state VALUES ('reproduction',?,?) ON CONFLICT(domain) DO UPDATE SET generation=excluded.generation,summary=excluded.summary",  # noqa: E501
                (generation, request.summary),
            )
            db.execute("DELETE FROM report_materializations WHERE kind='reproduction'")
            _validate_explicit_export_bound(db, path)
            return generation
    except ResultStoreError as error:
        raise ReproductionStorageError(str(error), code=error.code) from error


def _validate_selected_identities(
    run: RunResult,
    artifacts: tuple[ArtifactResult, ...],
    commands: tuple[CommandResult, ...],
    selected_execution_keys: tuple[tuple[str, str], ...],
    selected_pre_execution_artifact_keys: tuple[tuple[str, str], ...],
) -> None:
    """Require a closed selected projection before any result-store mutation."""
    execution_keys = {(item.entry, item.execution_id) for item in commands}
    pre_execution_keys = {
        (item.entry, item.artifact) for item in artifacts if item.execution_id is None
    }
    artifact_keys = {(item.entry, item.artifact) for item in artifacts}
    if (
        len(execution_keys) != len(commands)
        or len(pre_execution_keys) != len(selected_pre_execution_artifact_keys)
        or len(artifact_keys) != len(artifacts)
        or len(set(selected_execution_keys)) != len(selected_execution_keys)
        or len(set(selected_pre_execution_artifact_keys))
        != len(selected_pre_execution_artifact_keys)
        or set(selected_execution_keys) != execution_keys
        or set(selected_pre_execution_artifact_keys) != pre_execution_keys
        or any(item.run_id != run.run_id for item in artifacts)
        or any(item.run_id != run.run_id for item in commands)
        or any(
            item.execution_id is not None
            and (item.entry, item.execution_id) not in execution_keys
            for item in artifacts
        )
    ):
        raise ReproductionStorageError("selected reproduction identities are invalid")


def _validate_publication_rows(
    artifacts: tuple[ArtifactResult, ...],
    commands: tuple[CommandResult, ...],
) -> None:
    """Reject candidate row counts before opening a write transaction."""
    from .reproduction_results import (
        MAX_ARTIFACT_RESULTS,
        MAX_COMMAND_RESULTS,
    )

    if len(artifacts) > MAX_ARTIFACT_RESULTS or len(commands) > MAX_COMMAND_RESULTS:
        raise ReproductionStorageError("reproduction publication exceeds row bound")


def _validate_explicit_export_bound(db: sqlite3.Connection, path: Path) -> None:
    """Measure the staged, normalized export before its transaction commits."""
    from .reproduction_results import (
        MAX_ARTIFACT_RESULTS,
        MAX_COMMAND_RESULTS,
        MAX_RESULT_BYTES,
        MAX_RUN_RESULTS,
    )

    if (
        db.execute("SELECT count(*) FROM reproduction_artifact_results").fetchone()[0]
        > MAX_ARTIFACT_RESULTS
        or db.execute("SELECT count(*) FROM reproduction_execution_results").fetchone()[0]
        > MAX_COMMAND_RESULTS
        or db.execute("SELECT count(*) FROM reproduction_runs").fetchone()[0]
        > MAX_RUN_RESULTS
    ):
        raise ReproductionStorageError("reproduction publication exceeds cumulative row bound")
    result = _load_reproduction_projection(path, connection=db)
    if _explicit_export_bytes(result) > MAX_RESULT_BYTES:
        raise ReproductionStorageError("reproduction publication exceeds cumulative byte bound")


def export_reproduction_results(path: Path) -> ReproductionResults:
    """Assemble the complete retained result only for an explicit export.

    Ordinary result views deliberately use the narrow query helpers below.  In
    particular, no report, planner, or inspection action should call this
    function: it reads every current artifact and every retained run.
    """
    result = _load_reproduction_projection(path)
    # The explicit export is the only whole-result read and retains the
    # public 64 MiB contract rather than silently returning an oversized map.
    if _explicit_export_bytes(result) > 64 * 1024 * 1024:
        raise ReproductionStorageError("reproduction export exceeds 64 MiB")
    return result


def _export_value(result: ReproductionResults) -> dict[str, object]:
    """Build the explicit export envelope; ordinary readers never call this."""

    return {
        "artifacts": [item.as_dict() for item in result.artifacts],
        "commands": [item.as_dict() for item in result.commands],
        "runs": [item.as_dict() for item in result.runs],
        "schema": "research-log-reproduction-result/10",
        "summary": result.summary,
        "updated_at": result.updated_at,
    }


def _explicit_export_bytes(result: ReproductionResults) -> int:
    """Return the exact UTF-8 byte size of the explicit export envelope."""

    return len(_json(_export_value(result)).encode())


def load_reproduction_report_projection(
    path: Path, *, project_root: Path | None = None
) -> ReproductionResults:
    """Return the typed rows required by the bounded human report compositor."""
    return _load_reproduction_projection(path, project_root=project_root)


def _load_reproduction_projection(
    path: Path,
    *,
    project_root: Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> ReproductionResults:
    from .reproduction_results import (
        ArtifactResult,
        CommandResult,
        ComparisonRecord,
        ReproductionResults,
        RunFolder,
        RunResult,
    )

    try:
        with (
            result_snapshot(_root(path))
            if connection is None
            else nullcontext(connection)
        ) as db:
            metadata = db.execute(
                "SELECT summary,updated_at FROM reproduction_metadata WHERE singleton=1"  # noqa: E501
            ).fetchone()
            if metadata is None:
                raise ReproductionStorageError("reproduction result is absent")
            artifacts = []
            for row in db.execute(
                "SELECT * FROM reproduction_artifact_results ORDER BY entry,artifact"
            ):
                evidence = []
                for member in db.execute(
                    "SELECT * FROM reproduction_comparison_evidence WHERE entry=? AND artifact=? ORDER BY position",  # noqa: E501
                    (row["entry"], row["artifact"]),
                ):
                    evidence.append(
                        {
                            "matched": bool(member["matched"]),
                            "record_id": member["record_id"],
                            "regenerated": json.loads(member["regenerated_json"]),
                            "retained": json.loads(member["retained_json"]),
                            "tolerance": json.loads(member["tolerance_json"]),
                        }
                    )
                _validate_comparison_contracts(row)
                if row["comparison_profile"] is None and evidence:
                    raise ReproductionStorageError("orphan comparison evidence")
                comparison = (
                    None
                    if row["comparison_profile"] is None
                    else ComparisonRecord(
                        row["comparison_profile"],
                        None
                        if row["expected_json"] is None
                        else parse_fingerprint(
                            json.loads(row["expected_json"]), "expected"
                        ),
                        None
                        if row["regenerated_json"] is None
                        else parse_fingerprint(
                            json.loads(row["regenerated_json"]), "regenerated"
                        ),
                        row["evidence_definition"],
                        tuple(evidence),
                    )
                )
                artifacts.append(
                    ArtifactResult(
                        row["entry"],
                        row["artifact"],
                        row["execution_id"],
                        row["outcome"],
                        row["reason"],
                        row["recorded_at"],
                        row["producing_run_id"],
                        comparison,
                    )
                )
            commands = tuple(
                CommandResult(*row)
                for row in db.execute(
                    "SELECT entry,execution_id,disposition,source_digest,recorded_at,producing_run_id FROM reproduction_execution_results ORDER BY entry,execution_id"  # noqa: E501
                )
            )
            runs = []
            for row in db.execute(
                "SELECT * FROM reproduction_runs ORDER BY finished_at DESC,accepted_at DESC,run_id"  # noqa: E501
            ):
                target = {"kind": row["target_kind"], "entry": row["target_entry"]}
                if row["target_execution_id"] is not None:
                    target["execution_id"] = row["target_execution_id"]
                executions = tuple(
                    {
                        "entry": item["entry"],
                        "execution_id": item["execution_id"],
                        "started_at": item["started_at"],
                        "finished_at": item["finished_at"],
                        "elapsed_seconds": item["elapsed_seconds"],
                    }
                    for item in db.execute(
                        "SELECT entry,execution_id,started_at,finished_at,elapsed_seconds "
                        "FROM reproduction_run_executions WHERE run_id=? ORDER BY position",
                        (row["run_id"],),
                    )
                )
                records = tuple(
                    _validated_command_detail(item)
                    for item in db.execute(
                        "SELECT entry,execution_id,bucket,reason,terminal_disposition,source_digest,recipe_json,detail_json FROM reproduction_run_commands WHERE run_id=? ORDER BY plan_order",  # noqa: E501
                        (row["run_id"],),
                    )
                )
                runs.append(
                    RunResult(
                        row["run_id"],
                        target,
                        bool(row["include_all"]),
                        row["status"],
                        row["accepted_at"],
                        row["finished_at"],
                        {
                            "matched": row["artifact_matched"],
                            "changed": row["artifact_changed"],
                            "failed": row["artifact_failed"],
                            "comparison_failed": row["artifact_comparison_failed"],
                            "skipped": row["artifact_skipped"],
                        },
                        RunFolder(
                            row["folder_path"],
                            "unknown"
                            if project_root is None
                            else _folder_availability(project_root, row["folder_path"]),
                        ),
                        executions,
                        None
                        if row["command_total"] is None
                        else {
                            "not_automatic": row["command_not_automatic"],
                            "reproduction_not_needed": row[
                                "command_reproduction_not_needed"
                            ],
                            "unchanged_failed": row["command_unchanged_failed"],
                            "unchanged_blocked": row["command_unchanged_blocked"],
                            "succeeded": row["command_succeeded"],
                            "failed": row["command_failed"],
                            "blocked": row["command_blocked"],
                            "total": row["command_total"],
                        },
                        records or None,
                    )
                )
    except ResultStoreError as error:
        if error.code == "results.schema.unsupported":
            from .reproduction_results import ReproductionResultSchemaError

            raise ReproductionResultSchemaError(str(error)) from error
        raise ReproductionStorageError(str(error), code=error.code) from error
    except (sqlite3.Error, ValueError, json.JSONDecodeError) as error:
        raise ReproductionStorageError(str(error)) from error
    return ReproductionResults(
        metadata["summary"],
        metadata["updated_at"],
        tuple(artifacts),
        tuple(runs),
        commands,
    )


def _folder_availability(project_root: Path, folder_path: str) -> str:
    """Project run-folder availability from the retained path at read time."""
    target = project_root / folder_path
    try:
        return "available" if target.is_dir() and not target.is_symlink() else "unknown"
    except OSError:
        return "unknown"


def reproduction_artifact_projection(
    path: Path,
    *,
    entry: str | None = None,
    outcome: str | None = None,
    artifact: str | None = None,
    limit: int = 50,
) -> tuple[str, int, tuple[ArtifactResult, ...]]:
    """Return one filtered, bounded artifact projection from normalized rows."""
    from .reproduction_results import ArtifactResult

    clauses: list[str] = []
    bindings: list[object] = []
    for column, value in (
        ("entry", entry),
        ("outcome", outcome),
        ("artifact", artifact),
    ):
        if value is not None:
            clauses.append(f"{column}=?")
            bindings.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        with result_snapshot(_root(path)) as db:
            metadata = db.execute(
                "SELECT summary FROM reproduction_metadata WHERE singleton=1"
            ).fetchone()
            if metadata is None:
                raise ReproductionStorageError("reproduction result is absent")
            matched = db.execute(
                "SELECT count(*) FROM reproduction_artifact_results" + where, bindings
            ).fetchone()[0]
            rows = db.execute(
                "SELECT entry,artifact,execution_id,outcome,reason,recorded_at,"
                "producing_run_id,comparison_contract,comparison_profile,expected_json,regenerated_json,"
                "evidence_contract,evidence_definition FROM reproduction_artifact_results"
                + where
                + " ORDER BY entry,artifact LIMIT ?",
                [*bindings, limit],
            ).fetchall()
            values: list[ArtifactResult] = []
            for row in rows:
                evidence = tuple(
                    {
                        "matched": bool(item["matched"]),
                        "record_id": item["record_id"],
                        "regenerated": json.loads(item["regenerated_json"]),
                        "retained": json.loads(item["retained_json"]),
                        "tolerance": json.loads(item["tolerance_json"]),
                    }
                    for item in db.execute(
                        "SELECT record_id, retained_json, regenerated_json, "
                        "tolerance_json, matched "
                        "FROM reproduction_comparison_evidence "
                        "WHERE entry=? AND artifact=? ORDER BY position",
                        (row["entry"], row["artifact"]),
                    )
                )
                from .reproduction_results import ComparisonRecord

                _validate_comparison_contracts(row)
                if row["comparison_profile"] is None and evidence:
                    raise ReproductionStorageError("orphan comparison evidence")
                comparison = (
                    None
                    if row["comparison_profile"] is None
                    else ComparisonRecord(
                        row["comparison_profile"],
                        None
                        if row["expected_json"] is None
                        else parse_fingerprint(
                            json.loads(row["expected_json"]), "expected"
                        ),
                        None
                        if row["regenerated_json"] is None
                        else parse_fingerprint(
                            json.loads(row["regenerated_json"]), "regenerated"
                        ),
                        row["evidence_definition"],
                        evidence,
                    )
                )
                values.append(
                    ArtifactResult(
                        row["entry"],
                        row["artifact"],
                        row["execution_id"],
                        row["outcome"],
                        row["reason"],
                        row["recorded_at"],
                        row["producing_run_id"],
                        comparison,
                    )
                )
    except (ResultStoreError, sqlite3.Error, ValueError, json.JSONDecodeError) as error:
        raise ReproductionStorageError(str(error)) from error
    return metadata["summary"], matched, tuple(values)


def reproduction_summary_projection(path: Path) -> dict[str, object]:
    """Return scalar summary counts without decoding artifact or command details."""
    try:
        with result_snapshot(_root(path)) as db:
            metadata = db.execute(
                "SELECT summary,updated_at FROM reproduction_metadata WHERE singleton=1"
            ).fetchone()
            if metadata is None:
                raise ReproductionStorageError("reproduction result is absent")
            latest = db.execute(
                "SELECT * FROM reproduction_runs WHERE status='complete' "
                "ORDER BY finished_at DESC, accepted_at DESC, run_id DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return {
                    "summary": metadata["summary"],
                    "updated_at": metadata["updated_at"],
                    "run": None,
                }
            counts = db.execute(
                "SELECT "
                "sum(outcome='matched'),sum(outcome='changed'),"
                "sum(outcome='skipped' AND reason='dependency_failed'),"
                "sum(outcome='skipped' AND reason!='dependency_failed'),"
                "sum(outcome='failed' AND execution_id IS NULL),"
                "sum(outcome='failed' AND execution_id IS NOT NULL),"
                "sum(outcome='comparison_failed'),"
                "sum(outcome NOT IN ('matched','changed','failed','comparison_failed','skipped')) "
                "FROM reproduction_artifact_results"
            ).fetchone()
            if counts[7]:
                raise ReproductionStorageError("reproduction artifact outcome is invalid")
            artifact = {
                "matched": counts[0] or 0,
                "not_matched": counts[1] or 0,
                "command_blocked": (counts[2] or 0) + (counts[4] or 0),
                "command_skipped": counts[3] or 0,
                "command_failed": counts[5] or 0,
                "comparison_failed": counts[6] or 0,
            }
            artifact["total"] = sum(artifact.values())
            return {
                "summary": metadata["summary"],
                "updated_at": metadata["updated_at"],
                "run": True,
                "run_id": latest["run_id"],
                "artifact": artifact,
                "command": {
                    "not_automatic": latest["command_not_automatic"],
                    "reproduction_not_needed": latest[
                        "command_reproduction_not_needed"
                    ],
                    "unchanged_failed": latest["command_unchanged_failed"],
                    "unchanged_blocked": latest["command_unchanged_blocked"],
                    "succeeded": latest["command_succeeded"],
                    "failed": latest["command_failed"],
                    "blocked": latest["command_blocked"],
                    "total": latest["command_total"],
                },
            }
    except (ResultStoreError, sqlite3.Error) as error:
        raise ReproductionStorageError(str(error)) from error


def reproduction_command_projection(
    path: Path, request: CommandProjectionRequest, *, project_root: Path | None = None
) -> tuple[str, RunResult, int, tuple[dict[str, object], ...]]:
    """Select one run and only its requested bounded command-detail rows."""
    from .reproduction_results import RunFolder, RunResult

    try:
        with result_snapshot(_root(path)) as db:
            metadata = db.execute(
                "SELECT summary FROM reproduction_metadata WHERE singleton=1"
            ).fetchone()
            if metadata is None:
                raise ReproductionStorageError("reproduction result is absent")
            run_where = (
                "run_id=?" if request.run_id is not None else "status='complete'"
            )
            run_bindings = (request.run_id,) if request.run_id is not None else ()
            row = db.execute(
                "SELECT * FROM reproduction_runs WHERE "
                + run_where
                + " ORDER BY finished_at DESC, accepted_at DESC, run_id DESC LIMIT 1",
                run_bindings,
            ).fetchone()
            if row is None or row["status"] != "complete":
                raise ReproductionStorageError("completed reproduction run is absent")
            target: dict[str, object] = {
                "kind": row["target_kind"],
                "entry": row["target_entry"],
            }
            if row["target_execution_id"] is not None:
                target["execution_id"] = row["target_execution_id"]
            availability = (
                "unknown"
                if project_root is None
                else _folder_availability(project_root, row["folder_path"])
            )
            run = RunResult(
                row["run_id"],
                target,
                bool(row["include_all"]),
                row["status"],
                row["accepted_at"],
                row["finished_at"],
                {
                    "matched": row["artifact_matched"],
                    "changed": row["artifact_changed"],
                    "failed": row["artifact_failed"],
                    "comparison_failed": row["artifact_comparison_failed"],
                    "skipped": row["artifact_skipped"],
                },
                RunFolder(row["folder_path"], availability),
                (),
                None
                if row["command_total"] is None
                else {
                    "not_automatic": row["command_not_automatic"],
                    "reproduction_not_needed": row["command_reproduction_not_needed"],
                    "unchanged_failed": row["command_unchanged_failed"],
                    "unchanged_blocked": row["command_unchanged_blocked"],
                    "succeeded": row["command_succeeded"],
                    "failed": row["command_failed"],
                    "blocked": row["command_blocked"],
                    "total": row["command_total"],
                },
            )
            clauses = ["run_id=?"]
            bindings: list[object] = [row["run_id"]]
            for column, value in (
                ("bucket", request.bucket),
                ("entry", request.entry),
                ("reason", request.reason),
                ("execution_id", request.execution_id),
            ):
                if value is not None:
                    clauses.append(f"{column}=?")
                    bindings.append(value)
            where = " WHERE " + " AND ".join(clauses)
            matched = db.execute(
                "SELECT count(*) FROM reproduction_run_commands" + where, bindings
            ).fetchone()[0]
            records = tuple(
                _validated_command_detail(item)
                for item in db.execute(
                    "SELECT entry,execution_id,bucket,reason,terminal_disposition,source_digest,recipe_json,detail_json FROM reproduction_run_commands"
                    + where
                    + " ORDER BY plan_order LIMIT ?",
                    [*bindings, request.limit],
                )
            )
    except (ResultStoreError, sqlite3.Error, ValueError, json.JSONDecodeError) as error:
        raise ReproductionStorageError(str(error)) from error
    return metadata["summary"], run, matched, records


def _validated_command_detail(row: sqlite3.Row) -> dict[str, object]:
    from .reproduction_results import _decode_command_record

    detail = _decode_command_record(json.loads(row["detail_json"]), 0)
    recipe = json.loads(row["recipe_json"])
    if (
        detail["entry"] != row["entry"]
        or detail["execution_id"] != row["execution_id"]
        or detail["bucket"] != row["bucket"]
        or detail["reason"] != row["reason"]
        or detail.get("terminal_disposition") != row["terminal_disposition"]
        or detail.get("source_digest") != row["source_digest"]
        or detail["recipe"] != recipe
    ):
        raise ReproductionStorageError("stored command detail does not match scalars")
    return dict(detail)


def load_current_execution_results(
    path: Path,
) -> dict[tuple[str, str], dict[str, object]]:
    """Return only planner reuse columns, keyed by compound execution identity."""
    try:
        with result_snapshot(_root(path)) as db:
            rows = db.execute(
                "SELECT entry,execution_id,disposition,source_digest,recorded_at,producing_run_id FROM reproduction_execution_results ORDER BY entry,execution_id"  # noqa: E501
            ).fetchall()
    except (ResultStoreError, sqlite3.Error) as error:
        raise ReproductionStorageError(str(error)) from error
    return {
        (row["entry"], row["execution_id"]): {
            "entry": row["entry"],
            "execution_id": row["execution_id"],
            "disposition": row["disposition"],
            "source_digest": row["source_digest"],
            "recorded_at": row["recorded_at"],
            "run_id": row["producing_run_id"],
        }
        for row in rows
    }


def initialize_empty_reproduction_results(
    path: Path, *, summary: str, updated_at: str
) -> int:
    """Publish an explicitly authorized cold whole-log recovery projection."""
    try:
        with result_transaction(_root(path)) as db:
            db.execute(
                "INSERT INTO reproduction_metadata VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET summary=excluded.summary,updated_at=excluded.updated_at",  # noqa: E501
                (summary, updated_at),
            )
            prior = db.execute(
                "SELECT generation FROM store_state WHERE domain='reproduction'"
            ).fetchone()
            generation = 1 if prior is None else prior[0] + 1
            db.execute(
                "INSERT INTO store_state VALUES ('reproduction',?,?) ON CONFLICT(domain) DO UPDATE SET generation=excluded.generation,summary=excluded.summary",  # noqa: E501
                (generation, summary),
            )
            db.execute("DELETE FROM report_materializations WHERE kind='reproduction'")
            return generation
    except ResultStoreError as error:
        raise ReproductionStorageError(str(error), code=error.code) from error

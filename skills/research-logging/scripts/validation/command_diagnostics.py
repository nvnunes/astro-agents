"""Independent bounded storage for recorded-command diagnostics."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence, cast

from research_log_result_store import (
    UNCHANGED_DOMAIN_STORE_VERSIONS,
    ResultStoreError,
    result_snapshot,
    result_transaction,
    results_lock,
)
from result_export_encoder import ExportTooLarge, measure_json_value

COMMAND_DIAGNOSTIC_SCHEMA = "research-log-command-diagnostic/1"
MAX_DIAGNOSTIC_RECORDS = 1_000
MAX_DIAGNOSTIC_BYTES = 1024 * 1024


@dataclass
class RejectedProducerIndex:
    """Index discovery diagnostics by exact outputs and directory ancestors."""

    commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[str, set[str]] = field(default_factory=dict)
    directories: dict[str, set[str]] = field(default_factory=dict)

    def add(self, observed: object) -> None:
        """Retain an available rejected-command diagnostic from discovery."""

        if not isinstance(observed, Mapping):
            return
        command = observed.get("rejected_command")
        if not isinstance(command, dict):
            return
        identity = str(command["identity"])
        self.commands[identity] = command
        for output in command["declared_outputs"]:
            index = self.directories if output["kind"] == "directory" else self.outputs
            index.setdefault(output["path"], set()).add(identity)

    def related(self, material: str) -> tuple[dict[str, Any], ...]:
        """Return commands declaring this exact output or an owning directory."""

        path = PurePosixPath(material)
        identities = set(self.outputs.get(str(path), ()))
        for parent in (path, *path.parents):
            identities.update(self.directories.get(str(parent), ()))
        return tuple(self.commands[identity] for identity in sorted(identities))


def rejected_producer_message(commands: tuple[dict[str, Any], ...]) -> str:
    """Render a bounded explanation; retained text views expose omitted detail."""

    lines = ["Recorded commands declare this output but were excluded:"]
    for command in commands[:5]:
        lines.append(
            f"  {command['document']}, fence {command['fence']}, "
            f"command {command['ordinal']}: {command['script']}"
        )
        lines.append(f"  {command['code']}")
        for argument in command["arguments"][:8]:
            lines.append(f"    {argument['selector']}: {argument['value']}")
        if len(command["arguments"]) > 8:
            lines.append("    Additional arguments omitted from this summary.")
    if len(commands) > 5:
        lines.append(f"  {len(commands) - 5} additional matching commands omitted.")
    lines.append(
        "These arguments have no declared input/output role. Declare their actual "
        "roles using --other-inputs or --other-outputs."
    )
    return "\n".join(line[:240] + ("…" if len(line) > 240 else "") for line in lines)


def publish_command_diagnostic(  # noqa: PLR0913 -- closed command record contract
    log_root: Path,
    summary: str,
    code: str,
    records: object,
    *,
    operation: str = "authoring",
    entry: str | None = None,
) -> str:
    """Replace the latest command diagnostic without changing validation state."""

    if not isinstance(records, (list, tuple)) or any(
        not isinstance(item, Mapping) for item in records
    ):
        raise ValueError("command diagnostic records must be objects")
    if len(records) > MAX_DIAGNOSTIC_RECORDS:
        raise ValueError("command diagnostic exceeds its record bound")
    payload = {
        "code": code,
        "entry": entry,
        "operation": operation,
        "records": [dict(cast(Mapping[str, object], item)) for item in records],
        "schema": COMMAND_DIAGNOSTIC_SCHEMA,
        "summary": summary,
    }
    try:
        measure_json_value(payload, MAX_DIAGNOSTIC_BYTES)
    except ExportTooLarge as error:
        raise ValueError("command diagnostic exceeds its byte bound") from error
    diagnostic_id = str(uuid.uuid4())
    stored_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with results_lock(log_root):
        with result_transaction(log_root) as db:
            if (
                int(db.execute("PRAGMA user_version").fetchone()[0])
                not in UNCHANGED_DOMAIN_STORE_VERSIONS
            ):
                raise ValueError(
                    "command diagnostics require replacement validation storage"
                )
            prior = db.execute(
                "SELECT generation FROM store_state WHERE domain='command'"
            ).fetchone()
            generation = 1 if prior is None else int(prior[0]) + 1
            db.execute("DELETE FROM command_diagnostics")
            db.execute(
                "INSERT INTO command_diagnostics VALUES (1,?,?,?,?,?,?,?,?)",
                (
                    diagnostic_id,
                    generation,
                    operation,
                    code,
                    entry,
                    summary,
                    stored_at,
                    _json(
                        {
                            key: value
                            for key, value in payload.items()
                            if key != "records"
                        }
                    ),
                ),
            )
            db.executemany(
                "INSERT INTO command_diagnostic_records VALUES (?,?,?,?)",
                [
                    (
                        1,
                        position,
                        str(record.get("kind", "command")),
                        _json(record),
                    )
                    for position, record in enumerate(
                        cast(Sequence[Mapping[str, object]], records)
                    )
                ],
            )
            db.execute(
                "INSERT INTO store_state VALUES ('command',?,?) "
                "ON CONFLICT(domain) DO UPDATE SET "
                "generation=excluded.generation,summary=excluded.summary",
                (generation, summary),
            )
    return diagnostic_id


def load_command_diagnostic(
    log_root: Path,
    *,
    diagnostic_id: str | None = None,
) -> dict[str, object]:
    """Load the latest or exact retained command diagnostic."""

    try:
        with result_snapshot(log_root) as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version not in UNCHANGED_DOMAIN_STORE_VERSIONS:
                raise ResultStoreError(
                    "command.diagnostic.schema.unsupported",
                    f"store version {version} is unsupported",
                )
            current = db.execute(
                "SELECT * FROM command_diagnostics ORDER BY diagnostic_pk LIMIT 2"
            ).fetchall()
            if not current:
                raise ResultStoreError(
                    "command.diagnostic.missing", "command diagnostic is unavailable"
                )
            if len(current) != 1:
                raise _malformed("command diagnostic storage is not singleton")
            row = current[0]
            if diagnostic_id is not None and row["diagnostic_id"] != diagnostic_id:
                raise ResultStoreError(
                    "command.diagnostic.missing", "command diagnostic is unavailable"
                )
            payload = _json_object(row["payload_json"])
            _validate_payload(payload, row)
            records = _load_records(db, row)
            diagnostic_id_value = _nonempty_string(
                row["diagnostic_id"], "diagnostic identity"
            )
            stored_at = _nonempty_string(row["stored_at"], "stored time")
            generation = int(row["generation"])
            if generation < 1:
                raise _malformed("stored diagnostic generation is invalid")
            state = db.execute(
                "SELECT generation,summary FROM store_state WHERE domain='command'"
            ).fetchone()
            if (
                state is None
                or int(state["generation"]) != generation
                or state["summary"] != payload["summary"]
            ):
                raise _malformed("command diagnostic state marker disagrees")
            value = {
                **payload,
                "diagnostic_id": diagnostic_id_value,
                "generation": generation,
                "records": records,
                "stored_at": stored_at,
            }
            try:
                measure_json_value(value, MAX_DIAGNOSTIC_BYTES)
            except ExportTooLarge as error:
                raise _malformed(
                    "stored command diagnostic exceeds its byte bound"
                ) from error
            return value
    except ResultStoreError as error:
        raise _command_diagnostic_error(error) from error
    except (sqlite3.DatabaseError, TypeError, UnicodeError, ValueError) as error:
        raise _malformed("command diagnostic is malformed") from error


def _validate_payload(payload: Mapping[str, object], row: sqlite3.Row) -> None:
    required = {"code", "entry", "operation", "schema", "summary"}
    if set(payload) != required:
        raise _malformed("stored diagnostic payload fields are invalid")
    expected: dict[str, object] = {
        "code": _nonempty_string(row["code"], "diagnostic code"),
        "entry": (
            None
            if row["entry"] is None
            else _nonempty_string(row["entry"], "diagnostic entry")
        ),
        "operation": _nonempty_string(row["operation"], "diagnostic operation"),
        "schema": COMMAND_DIAGNOSTIC_SCHEMA,
        "summary": _nonempty_string(row["summary"], "diagnostic summary"),
    }
    if dict(payload) != expected:
        raise _malformed("stored diagnostic payload disagrees with indexed fields")


def _command_diagnostic_error(error: ResultStoreError) -> ResultStoreError:
    code = {
        "results.store.missing": "command.diagnostic.missing",
        "results.schema.unsupported": "command.diagnostic.schema.unsupported",
        "results.store.malformed": "command.diagnostic.malformed",
        "results.store.busy": "command.diagnostic.busy",
    }.get(error.code, error.code)
    return ResultStoreError(code, str(error))


def _load_records(db: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    raw_bytes = len(
        _nonempty_string(row["payload_json"], "diagnostic payload").encode()
    )
    rows = db.execute(
        "SELECT position,kind,record_json FROM command_diagnostic_records "
        "WHERE diagnostic_pk=? ORDER BY position LIMIT ?",
        (row["diagnostic_pk"], MAX_DIAGNOSTIC_RECORDS + 1),
    )
    for expected_position, item in enumerate(rows):
        if expected_position >= MAX_DIAGNOSTIC_RECORDS:
            raise _malformed("stored command diagnostic exceeds its record bound")
        if int(item["position"]) != expected_position:
            raise _malformed("stored diagnostic record positions are invalid")
        record_json = _nonempty_string(item["record_json"], "diagnostic record")
        raw_bytes += len(record_json.encode("utf-8"))
        if raw_bytes > MAX_DIAGNOSTIC_BYTES:
            raise _malformed("stored command diagnostic exceeds its byte bound")
        record = _json_object(record_json)
        stored_kind = _nonempty_string(item["kind"], "diagnostic record kind")
        record_kind = record.get("kind", "command")
        if (
            not isinstance(record_kind, str)
            or not record_kind
            or record_kind != stored_kind
        ):
            raise _malformed("stored diagnostic record kind disagrees with its index")
        records.append(record)
    return records


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise ResultStoreError(
            "command.diagnostic.malformed", "stored diagnostic JSON is invalid"
        )
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ResultStoreError(
            "command.diagnostic.malformed", "stored diagnostic JSON is invalid"
        ) from error
    if not isinstance(parsed, dict):
        raise ResultStoreError(
            "command.diagnostic.malformed", "stored diagnostic JSON is invalid"
        )
    return cast(dict[str, object], parsed)


def _nonempty_string(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value:
        raise _malformed(f"stored {subject} is invalid")
    return value


def _malformed(message: str) -> ResultStoreError:
    return ResultStoreError("command.diagnostic.malformed", message)

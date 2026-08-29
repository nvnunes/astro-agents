"""Explicit, target-local v1-to-v2 evidence upgrade tooling.

The tooling inventories one clean legacy log, validates a fully authored
candidate in an isolated copy, and can publish that candidate transactionally.
It does not invent v2 locators, transformations, markers, or repair choices.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, NoReturn, Sequence, cast

from .locator_v1_upgrade import LocatorV1UpgradeError, parse_source_expressions
from .records import validation_lock

ENTRY_HEADER = ("entry", "section", "kind", "evidence", "sources", "transformation")
SUMMARY_HEADER = ("statistic", "entry", "section", "transformation")
MAX_UPGRADE_FILES = 100_000
MAX_UPGRADE_BYTES = 8 * 1024 * 1024 * 1024
TRANSACTION_DIRECTORY = Path("validation/.cache/upgrade-transactions")
TRANSACTION_MANIFEST = "transaction.json"


class EvidenceUpgradeError(RuntimeError):
    """Raised when preflight, candidate validation, or publication fails."""

    def __init__(self, code: str, subject: str, observed: object):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed


@dataclass(frozen=True)
class LegacyEvidenceRow:
    """One exact v1 CSV row retained for upgrade traceability."""

    identity: str
    file: str
    line: int
    record_kind: str
    disposition: str


@dataclass(frozen=True)
class UpgradeInventory:
    """Complete mutually exclusive v1 row inventory for one target log."""

    summary: Path
    snapshot_identity: str
    files: tuple[str, ...]
    rows: tuple[LegacyEvidenceRow, ...]

    @property
    def counts(self) -> dict[str, int]:
        """Count rows by deterministic preflight disposition."""

        values: dict[str, int] = {}
        for row in self.rows:
            values[row.disposition] = values.get(row.disposition, 0) + 1
        return dict(sorted(values.items()))


@dataclass(frozen=True)
class StagedUpgrade:
    """Fully authored candidate bound to an exact pre-upgrade snapshot."""

    summary: Path
    snapshot: Mapping[str, str]
    replacements: Mapping[str, bytes]
    removals: tuple[str, ...]


def inventory_upgrade(summary: Path) -> UpgradeInventory:
    """Inventory one clean legacy log without creating v2 declarations."""

    summary = summary.resolve()
    _require_no_pending_transaction(summary)
    if not summary.is_file():
        _error("upgrade.target.invalid", str(summary), {"exists": False})
    log_root = summary.with_suffix("")
    if not log_root.is_dir() or log_root.is_symlink() or summary.is_symlink():
        _error("upgrade.target.invalid", str(summary), {"log_root": str(log_root)})
    markdown = [summary, *sorted(log_root.rglob("*.md"))]
    csv_files = sorted(log_root.rglob("evidence.csv"))
    v2_files = sorted(log_root.rglob("evidence.json"))
    has_v2_surface = bool(v2_files) or any(
        "<!-- eid:" in path.read_text(encoding="utf-8")
        or "<!-- ref " in path.read_text(encoding="utf-8")
        for path in markdown
    )
    if not csv_files or has_v2_surface:
        _error(
            "upgrade.target.invalid",
            str(summary),
            {
                "evidence_csv": len(csv_files),
                "evidence_json": len(v2_files),
                "v2_surface": has_v2_surface,
            },
        )
    boundary = summary.parent
    rows: list[LegacyEvidenceRow] = []
    for path in csv_files:
        rows.extend(
            _inventory_file(path, boundary, summary_file=path.parent == log_root)
        )
    snapshot = _snapshot(summary)
    return UpgradeInventory(
        summary=summary,
        snapshot_identity=_snapshot_identity(snapshot),
        files=tuple(path.relative_to(boundary).as_posix() for path in csv_files),
        rows=tuple(rows),
    )


def _inventory_file(
    path: Path, boundary: Path, *, summary_file: bool
) -> tuple[LegacyEvidenceRow, ...]:
    relative = path.relative_to(boundary).as_posix()
    header = SUMMARY_HEADER if summary_file else ENTRY_HEADER
    identities: set[tuple[str, ...]] = set()
    rows: list[LegacyEvidenceRow] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != header:
                _error(
                    "upgrade.preflight.failed",
                    relative,
                    {"header": reader.fieldnames, "expected": header},
                )
            for line, row in enumerate(reader, 2):
                decoded, identity = _legacy_row(
                    relative, line, row, header, summary_file=summary_file
                )
                if identity in identities:
                    _error(
                        "upgrade.preflight.failed",
                        f"{relative}:{line}",
                        {"reason": "duplicate_identity", "identity": identity},
                    )
                identities.add(identity)
                rows.append(decoded)
    except LocatorV1UpgradeError as exc:
        _error(
            "upgrade.preflight.failed",
            relative,
            {"code": exc.code, "subject": exc.subject, "observed": exc.observed},
        )
    except (OSError, UnicodeError, csv.Error) as exc:
        _error("upgrade.preflight.failed", relative, {"error": str(exc)})
    if not rows:
        _error("upgrade.preflight.failed", relative, {"reason": "empty_file"})
    return tuple(rows)


def _legacy_row(
    relative: str,
    line: int,
    row: Mapping[str | None, str | None],
    header: Sequence[str],
    *,
    summary_file: bool,
) -> tuple[LegacyEvidenceRow, tuple[str, ...]]:
    subject = f"{relative}:{line}"
    if None in row or any(value is None for value in row.values()):
        _error("upgrade.preflight.failed", subject, {"row": row})
    typed = cast(Mapping[str, str], row)
    required = set(header) - {"transformation"}
    if any(not typed[field] for field in required):
        _error(
            "upgrade.preflight.failed",
            subject,
            {"reason": "empty_required_field"},
        )
    _validate_v1_transformation(typed.get("transformation", ""), subject)
    identity: tuple[str, ...]
    if summary_file:
        identity = (typed["statistic"], typed["entry"], typed["section"])
        kind = "summary"
        disposition = "summary_mapping_required"
    else:
        identity, kind, disposition = _entry_row(typed, subject)
    return (
        LegacyEvidenceRow(
            identity=subject,
            file=relative,
            line=line,
            record_kind=kind,
            disposition=disposition,
        ),
        identity,
    )


def _entry_row(
    row: Mapping[str, str], subject: str
) -> tuple[tuple[str, ...], str, str]:
    kind = row["kind"]
    if kind not in {"statistic", "table", "output"}:
        _error("upgrade.preflight.failed", subject, {"kind": kind})
    sources = parse_source_expressions(row["sources"])
    if kind != "table" and len(sources) != 1:
        _error(
            "upgrade.preflight.failed",
            subject,
            {"sources": len(sources), "kind": kind},
        )
    identity = (row["entry"], row["section"], kind, row["evidence"])
    disposition = (
        "transformation_authorship_required"
        if row.get("transformation")
        else "mechanical_candidate"
    )
    return identity, kind, disposition


def _validate_v1_transformation(value: str, subject: str) -> None:
    version = re.match(r"v([0-9]+):", value)
    if version is not None and version.group(1) != "1":
        _error(
            "upgrade.preflight.failed",
            subject,
            {
                "code": "transformation.version.unsupported",
                "version": version.group(1),
            },
        )


def stage_upgrade(
    summary: Path,
    *,
    replacements: Mapping[str, bytes],
    removals: Sequence[str],
    validate_candidate: Callable[[Path], None],
) -> StagedUpgrade:
    """Validate a complete authored candidate in an isolated target copy."""

    inventory_upgrade(summary)
    summary = summary.resolve()
    snapshot = _snapshot(summary)
    replacements = {
        _target_path(path): bytes(payload) for path, payload in replacements.items()
    }
    removals = tuple(sorted({_target_path(path) for path in removals}))
    if set(replacements) & set(removals):
        _error(
            "upgrade.candidate.invalid",
            str(summary),
            {"conflicts": sorted(set(replacements) & set(removals))},
        )
    legacy_files = {
        path.relative_to(summary.parent).as_posix()
        for path in summary.with_suffix("").rglob("evidence.csv")
    }
    if not legacy_files <= set(removals):
        _error(
            "upgrade.candidate.invalid",
            str(summary),
            {"legacy_files_not_removed": sorted(legacy_files - set(removals))},
        )
    with tempfile.TemporaryDirectory(prefix="research-log-upgrade-") as directory:
        candidate_parent = Path(directory)
        candidate_summary = candidate_parent / summary.name
        shutil.copy2(summary, candidate_summary)
        shutil.copytree(
            summary.with_suffix(""),
            candidate_parent / summary.with_suffix("").name,
            symlinks=True,
            ignore=shutil.ignore_patterns("validation"),
        )
        _apply_candidate(candidate_parent, replacements, removals)
        try:
            validate_candidate(candidate_summary)
        except Exception as exc:
            _error(
                "upgrade.candidate.invalid",
                str(summary),
                {"error": str(exc)},
            )
    return StagedUpgrade(
        summary=summary,
        snapshot=snapshot,
        replacements=replacements,
        removals=removals,
    )


def publish_upgrade(
    staged: StagedUpgrade,
    *,
    verify_published: Callable[[Path], None],
) -> None:
    """Publish one validated candidate under the shared per-log lock.

    The function byte-exactly restores every touched path if publication or
    immediate verification fails. It is not called by standard validation.
    """

    summary = staged.summary.resolve()
    boundary = summary.parent
    touched = sorted(set(staged.replacements) | set(staged.removals))
    with validation_lock(summary.with_suffix("")):
        _require_no_pending_transaction(summary)
        if _snapshot(summary) != dict(staged.snapshot):
            _error(
                "upgrade.snapshot.changed",
                str(summary),
                {"snapshot": "changed"},
            )
        transaction = _prepare_transaction(summary, touched)
        try:
            _apply_candidate(boundary, staged.replacements, staged.removals)
            verify_published(summary)
        except Exception as exc:
            try:
                _restore_transaction(summary, transaction)
            except Exception as rollback_exc:
                _error(
                    "upgrade.recovery.required",
                    str(summary),
                    {"publish": str(exc), "rollback": str(rollback_exc)},
                )
            _error(
                "upgrade.publish.failed",
                str(summary),
                {"error": str(exc)},
            )
        _remove_transaction(transaction)


def recover_upgrade(summary: Path) -> bool:
    """Restore one interrupted publication to its exact legacy state.

    Return ``True`` when a pending transaction was recovered and ``False``
    when no recovery was required. Recovery always rolls back; it never infers
    whether a partially published candidate was complete.
    """

    summary = summary.resolve()
    with validation_lock(summary.with_suffix("")):
        transactions = _pending_transactions(summary)
        if not transactions:
            return False
        if len(transactions) != 1:
            _error(
                "upgrade.recovery.required",
                str(summary),
                {"transactions": [path.name for path in transactions]},
            )
        transaction = transactions[0]
        try:
            _restore_transaction(summary, transaction)
        except Exception as exc:
            _error(
                "upgrade.recovery.required",
                str(summary),
                {"transaction": transaction.name, "error": str(exc)},
            )
        return True


def _snapshot(summary: Path) -> dict[str, str]:
    boundary = summary.parent
    validation_root = summary.with_suffix("") / "validation"
    paths = [summary, *sorted(summary.with_suffix("").rglob("*"))]
    regular = [
        path
        for path in paths
        if path.is_file()
        and not path.is_relative_to(validation_root)
        and not path.is_symlink()
    ]
    size = sum(path.stat().st_size for path in regular)
    if len(regular) > MAX_UPGRADE_FILES or size > MAX_UPGRADE_BYTES:
        _error(
            "upgrade.preflight.failed",
            str(summary),
            {"files": len(regular), "bytes": size},
        )
    return {
        path.relative_to(boundary).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in regular
    }


def _snapshot_identity(snapshot: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, identity in sorted(snapshot.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(identity.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _target_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        _error("upgrade.candidate.invalid", str(value), {"path": value})
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _error("upgrade.candidate.invalid", value, {"path": value})
    return path.as_posix()


def _apply_candidate(
    boundary: Path,
    replacements: Mapping[str, bytes],
    removals: Sequence[str],
) -> None:
    boundary = boundary.resolve()
    for relative, payload in sorted(replacements.items()):
        target = boundary.joinpath(*PurePosixPath(relative).parts)
        _reject_target_symlinks(boundary, target, relative)
        if target.exists() and not target.is_file():
            raise EvidenceUpgradeError(
                "upgrade.publish.failed", relative, {"reason": "not_regular_file"}
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    for relative in sorted(removals):
        target = boundary.joinpath(*PurePosixPath(relative).parts)
        _reject_target_symlinks(boundary, target, relative)
        if target.exists() and not target.is_file():
            raise EvidenceUpgradeError(
                "upgrade.publish.failed", relative, {"reason": "not_regular_file"}
            )
        target.unlink(missing_ok=True)


def _prepare_transaction(summary: Path, touched: Sequence[str]) -> Path:
    transaction_root = summary.with_suffix("") / TRANSACTION_DIRECTORY
    transaction = transaction_root / uuid.uuid4().hex
    backups = transaction / "backups"
    backups.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    for number, relative in enumerate(touched):
        target = summary.parent.joinpath(*PurePosixPath(relative).parts)
        _reject_target_symlinks(summary.parent.resolve(), target, relative)
        if target.exists() and not target.is_file():
            _error(
                "upgrade.publish.failed",
                relative,
                {"reason": "not_regular_file"},
            )
        if target.is_file():
            payload = target.read_bytes()
            backup_name = f"{number:06d}.bin"
            _atomic_write(backups / backup_name, payload)
            records.append(
                {
                    "backup": backup_name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "target": relative,
                }
            )
        else:
            records.append({"backup": None, "sha256": None, "target": relative})
    manifest = {
        "records": records,
        "schema": "research-log-evidence-upgrade-transaction/1",
        "summary": summary.name,
    }
    _atomic_write(
        transaction / TRANSACTION_MANIFEST,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )
    return transaction


def _restore_transaction(summary: Path, transaction: Path) -> None:
    manifest = _read_transaction(summary, transaction)
    replacements: dict[str, bytes] = {}
    removals: list[str] = []
    for record in manifest:
        relative = str(record["target"])
        backup = record["backup"]
        if backup is None:
            removals.append(relative)
            continue
        payload = (transaction / "backups" / str(backup)).read_bytes()
        if hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise EvidenceUpgradeError(
                "upgrade.recovery.required",
                relative,
                {"reason": "backup_identity_mismatch"},
            )
        replacements[relative] = payload
    _apply_candidate(summary.parent, replacements, removals)
    _remove_transaction(transaction)


def _read_transaction(
    summary: Path, transaction: Path
) -> tuple[Mapping[str, object], ...]:
    manifest_path = transaction / TRANSACTION_MANIFEST
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) != {"records", "schema", "summary"}
        or value["schema"] != "research-log-evidence-upgrade-transaction/1"
        or value["summary"] != summary.name
        or not isinstance(value["records"], list)
    ):
        raise EvidenceUpgradeError(
            "upgrade.recovery.required",
            str(manifest_path),
            {"reason": "manifest_invalid"},
        )
    records: list[Mapping[str, object]] = []
    targets: set[str] = set()
    for record in value["records"]:
        if not isinstance(record, dict) or set(record) != {
            "backup",
            "sha256",
            "target",
        }:
            raise EvidenceUpgradeError(
                "upgrade.recovery.required",
                str(manifest_path),
                {"reason": "record_invalid"},
            )
        target = _target_path(record["target"])
        backup = record["backup"]
        identity = record["sha256"]
        if target in targets or not (
            (backup is None and identity is None)
            or (
                isinstance(backup, str)
                and re.fullmatch(r"[0-9]{6}\.bin", backup)
                and isinstance(identity, str)
                and re.fullmatch(r"[0-9a-f]{64}", identity)
            )
        ):
            raise EvidenceUpgradeError(
                "upgrade.recovery.required",
                str(manifest_path),
                {"reason": "record_invalid"},
            )
        targets.add(target)
        records.append({"backup": backup, "sha256": identity, "target": target})
    return tuple(records)


def _pending_transactions(summary: Path) -> tuple[Path, ...]:
    root = summary.with_suffix("") / TRANSACTION_DIRECTORY
    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        _error(
            "upgrade.recovery.required",
            str(summary),
            {"transaction_root": str(root)},
        )
    return tuple(sorted(path for path in root.iterdir() if path.is_dir()))


def _require_no_pending_transaction(summary: Path) -> None:
    transactions = _pending_transactions(summary)
    if transactions:
        _error(
            "upgrade.recovery.required",
            str(summary),
            {"transactions": [path.name for path in transactions]},
        )


def _remove_transaction(transaction: Path) -> None:
    shutil.rmtree(transaction)
    root = transaction.parent
    if root.exists() and not any(root.iterdir()):
        root.rmdir()


def _reject_target_symlinks(boundary: Path, target: Path, relative: str) -> None:
    boundary = boundary.resolve()
    try:
        relative_parent = target.parent.relative_to(boundary)
    except ValueError as exc:
        raise EvidenceUpgradeError(
            "upgrade.publish.failed", relative, {"reason": "outside_boundary"}
        ) from exc
    current = boundary
    for part in relative_parent.parts:
        current /= part
        if current.is_symlink():
            raise EvidenceUpgradeError(
                "upgrade.publish.failed", relative, {"reason": "symlink"}
            )
    if target.is_symlink():
        raise EvidenceUpgradeError(
            "upgrade.publish.failed", relative, {"reason": "symlink"}
        )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _error(code: str, subject: str, observed: object) -> NoReturn:
    raise EvidenceUpgradeError(code, subject, observed)

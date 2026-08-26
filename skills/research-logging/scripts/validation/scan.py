"""Typed scan-lifecycle result metrics."""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    cast,
)

from .activity import (
    ValidationActivityLog,
    log_checkpoint,
    log_operation,
    log_phase,
)
from .contracts import (
    FileChangedError,
    LifecycleRecordContractError,
    ScanRecord,
    ValidationMetrics,
    ValidationToolError,
    decode_scan_record,
)
from .discovery import (
    ENTRY_ID_RE,
    HEADING_RE,
    LINK_RE,
    bibtex_keys,
    data_index,
    parse_markdown,
    resolve_evidence_source,
    resolve_reference,
    summary_evidence_record,
)
from .graph import (
    DependencyGraph,
)
from .graph_adapter import build_dependency_graph
from .graph_queries import orphan_locations
from .identities import (
    entry_validation_identity,
    summary_validation_identity,
)
from .incremental import (
    IncrementalOperations,
    compare_prior_record,
)
from .inventory import (
    MaterialInventoryPolicy,
    OwnedInventory,
    OwnedMaterial,
    content_identity,
    directory_membership_identity,
    display_path,
    file_identity,
    infer_project_root,
    logical_display_path,
    owned_entry_folders,
    owned_inventory,
)
from .observations import (
    CHANGED_DURING_OBSERVATION,
    CONTENT_CHANGED,
    CONTENT_UNCHANGED,
    NEW,
    ObservationSession,
)
from .records import LOCK_FILENAME
from .validation_notes import (
    blanket_retention_error,
    normalized_retention_scope,
    orphan_retention_notes,
)


def validated_jobs(jobs: object) -> int:
    """Return a valid positive scan worker count."""

    if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
        raise ValidationToolError("validation scan jobs must be a positive integer")
    return jobs


def stable_file_read(
    path: Path,
    reader: Callable[[], Any],
    recorded_identity: IdentityFunction = file_identity,
) -> Tuple[Any, Dict[str, Any]]:
    """Read one file while binding the operation to a stable identity."""

    before = file_identity(path)
    value = reader()
    identity = recorded_identity(path)
    after = file_identity(path)
    if before != after:
        raise FileChangedError(f"file changed during validation read: {path}")
    return value, identity


def optional_stable_file_read(
    path: Path, reader: Callable[[], Any]
) -> Tuple[Any, Dict[Path, Dict[str, Any]]]:
    """Read an optional record and return its observed identity when present."""

    if not path.is_file():
        return reader(), {}
    value, identity = stable_file_read(path, reader)
    return value, {path.resolve(): identity}


def discover_entries(
    summary_path: Path,
    log_root: Path,
    markdown_parser: Callable[[Path], Dict[str, Any]] = parse_markdown,
) -> Dict[str, Any]:
    """Discover maintained entry links and reconcile them with entry files."""

    (parsed, lines), source_identity = stable_file_read(
        summary_path,
        lambda: (
            markdown_parser(summary_path),
            summary_path.read_text(encoding="utf-8").splitlines(),
        ),
        summary_validation_identity,
    )
    listed: list[Dict[str, Any]] = []
    seen = set()
    in_entries = False
    for number, line in enumerate(lines, 1):
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            if heading.group(2).strip() == "Entries":
                in_entries = True
                continue
            if in_entries:
                break
        if not in_entries:
            continue
        for match in LINK_RE.finditer(line):
            resolved = resolve_reference(match.group("target"), summary_path)
            raw_path = resolved.get("path")
            if (
                not raw_path
                or not raw_path.endswith(".md")
                or "/entries/" not in raw_path
            ):
                continue
            path = Path(raw_path)
            entry_id = path.stem
            if not ENTRY_ID_RE.match(entry_id) or raw_path in seen:
                continue
            seen.add(raw_path)
            listed.append(
                {
                    "id": entry_id,
                    "title": match.group("label"),
                    "path": raw_path,
                    "line": number,
                    "exists": path.is_file(),
                }
            )
    discovered = sorted(
        path.resolve().as_posix() for path in (log_root / "entries").glob("**/*.md")
    )
    listed_paths = [entry["path"] for entry in listed]
    return {
        "listed": listed,
        "discovered": discovered,
        "unlisted": sorted(set(discovered) - set(listed_paths)),
        "missing": [entry["path"] for entry in listed if not entry["exists"]],
        "summary": parsed,
        "source_identity": source_identity,
    }


def candidate_references(
    parsed: Mapping[str, Any], source: Path, project_root: Path
) -> list[Dict[str, Any]]:
    """Return presented file candidates from experimental Results links."""

    candidates: Dict[str, Dict[str, Any]] = {}
    for reference in parsed["links"]:
        if reference["section_type"] != "experimental":
            continue
        if reference["kind"] in {"anchor", "external", "token"}:
            continue
        if reference.get("block_label") != "Results":
            continue
        identity = reference.get("path") or reference["target"]
        item = candidates.setdefault(
            identity,
            {
                "identity": (
                    display_path(Path(identity), project_root)
                    if reference.get("path")
                    else identity
                ),
                "resolved_path": reference.get("path"),
                "kind": "figure" if reference.get("image") else reference["kind"],
                "presented": True,
                "sections": [],
                "occurrences": [],
            },
        )
        if reference["section"] not in item["sections"]:
            item["sections"].append(reference["section"])
        item["occurrences"].append(
            {"line": reference["line"], "label": reference.get("label", "")}
        )
    return list(candidates.values())


def merge_command_candidates(
    candidates: Sequence[Dict[str, Any]],
    commands: Sequence[Mapping[str, Any]],
    project_root: Path,
) -> list[Dict[str, Any]]:
    """Merge literal command paths into an entry's candidate inventory."""

    by_identity = {candidate["identity"]: candidate for candidate in candidates}
    for command in commands:
        for argument in command.get("path_arguments", []):
            if argument["role_hint"] in {"workspace", "dependency-container"}:
                continue
            path = Path(argument["path"])
            identity = display_path(path, project_root)
            candidate = by_identity.setdefault(
                identity,
                {
                    "identity": identity,
                    "resolved_path": path.as_posix(),
                    "kind": "command-path",
                    "sections": [],
                    "occurrences": [],
                    "role_hints": [],
                    "presented": False,
                },
            )
            if command["section"] not in candidate["sections"]:
                candidate["sections"].append(command["section"])
            candidate["occurrences"].append(
                {
                    "line": command["line"],
                    "label": argument.get("option") or argument["raw"],
                    "role_hint": argument["role_hint"],
                }
            )
            hints = candidate.setdefault("role_hints", [])
            if argument["role_hint"] not in hints:
                hints.append(argument["role_hint"])
    return list(by_identity.values())


def validate_entry_evidence_records(
    records: Mapping[Path, Dict[str, Any]],
    folder_entry_ids: Mapping[Path, set[str]],
    entry_sections: Mapping[str, set[str]],
    entry_section_types: Mapping[str, Mapping[str, list[str]]],
) -> None:
    """Record entry-folder association errors against current sections."""

    for folder, record in records.items():
        valid_ids = folder_entry_ids.get(folder, set())
        for row in record["rows"]:
            line = row["line"]
            entry_id = row["entry"]
            section = row["section"]
            if entry_id not in valid_ids:
                record["errors"].append(
                    f"line {line}: entry {entry_id!r} is not in this entry folder"
                )
            elif section and section not in entry_sections.get(entry_id, set()):
                record["errors"].append(
                    f"line {line}: section {section!r} does not exist in {entry_id}"
                )
            elif entry_section_types.get(entry_id, {}).get(section) != ["experimental"]:
                record["errors"].append(
                    f"line {line}: section {section!r} is not a unique "
                    "experimental section"
                )


def validate_summary_evidence(
    summary_evidence: Dict[str, Any],
    summary_statistics: Sequence[Mapping[str, Any]],
    entry_sections: Mapping[str, set[str]],
    entry_section_types: Mapping[str, Mapping[str, list[str]]],
) -> None:
    """Record maintained-summary association and coverage errors."""

    for row in summary_evidence["rows"]:
        line = row["line"]
        entry_id = row["entry"]
        section = row["section"]
        if entry_id not in entry_sections:
            summary_evidence["errors"].append(
                f"line {line}: unknown supporting entry {entry_id!r}"
            )
        elif section and section not in entry_sections[entry_id]:
            summary_evidence["errors"].append(
                f"line {line}: section {section!r} does not exist in {entry_id}"
            )
        elif entry_section_types[entry_id].get(section) != ["experimental"]:
            summary_evidence["errors"].append(
                f"line {line}: supporting section {section!r} is not a "
                "unique experimental section"
            )

    expected = {item["selector"] for item in summary_statistics}
    actual = {row["statistic"] for row in summary_evidence["rows"]}
    summary_evidence["errors"].extend(
        f"missing summary statistic association: {selector!r}"
        for selector in sorted(expected - actual)
    )
    summary_evidence["errors"].extend(
        f"extra summary statistic association: {selector!r}"
        for selector in sorted(actual - expected)
    )


def finalize_entry_candidates(
    entries: Sequence[Dict[str, Any]],
    bib_keys: Sequence[str],
    mechanics: Mapping[str, Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Attach citation and mechanical facts before graph classification."""

    real_entries = []
    for entry in entries:
        if "error" in entry:
            continue
        entry["unresolved_citations"] = sorted(
            {
                citation["key"]
                for citation in entry["citations"]
                if citation["key"] not in bib_keys
            }
        )
        for candidate in entry["candidate_targets"]:
            candidate["mechanical"] = mechanics.get(
                candidate["identity"],
                {
                    "status": (
                        "unavailable" if candidate["kind"] == "external" else "missing"
                    )
                },
            )
        entry["orphan_candidates"] = []
        entry["orphan_inventory"] = []
        real_entries.append(entry)
    return real_entries


def add_reference_inventory(entries: Sequence[Dict[str, Any]]) -> None:
    """Add each indexed resource once at its owning validation scope."""

    by_index: Dict[str, list[Dict[str, Any]]] = {}
    for entry in entries:
        path = entry.get("data_index", {}).get("path")
        if isinstance(path, str):
            by_index.setdefault(path, []).append(entry)
    for grouped in by_index.values():
        shared_scope = next(
            (
                entry
                for entry in grouped
                if entry.get("scope_kind") == "entry-global"
            ),
            None,
        )
        owners = [shared_scope] if shared_scope is not None else grouped
        if len(owners) != 1:
            raise ValidationToolError(
                "one data index is attached to multiple entries without an "
                "entry-global validation scope"
            )
        entry = owners[0]
        assert entry is not None
        names = sorted(
            {
                row.get("name", "")
                for row in entry["data_index"].get("rows", [])
                if row.get("name")
            }
        )
        entry["orphan_inventory"].extend(
            {"kind": "reference", "identity": f"<{name}>"} for name in names
        )


def directory_memberships(
    resolved_paths: Mapping[str, str],
    log_root: Path,
    project_root: Path,
    record_names: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """Fingerprint direct membership of resolved non-project directories."""

    generated_records = {(log_root / name).resolve() for name in record_names}
    memberships: Dict[str, Dict[str, Any]] = {}
    for identity, raw_path in sorted(resolved_paths.items()):
        path = Path(raw_path)
        if not path.is_dir() or path.resolve() == project_root:
            continue
        try:
            memberships[identity] = dict(
                directory_membership_identity(path, generated_records)
            )
        except (OSError, ValidationToolError) as exc:
            memberships[identity] = {"error": str(exc)}
    return memberships


IdentityFunction = Callable[[Path], Dict[str, Any]]
DisplayIdentity = Callable[[Path, Path], str]


@dataclass(frozen=True)
class IdentityInspectionPolicy:
    """Concrete identity and structure operations used by scan inspection."""

    display_identity: DisplayIdentity
    file_identity: IdentityFunction
    summary_identity: IdentityFunction
    entry_identity: IdentityFunction
    inspect_structure: IdentityFunction


class IdentityInspectionInput(NamedTuple):
    """Inputs for bounded parallel identity and structure inspection."""

    paths: set[Path]
    summary_path: Path
    entry_paths: set[Path]
    project_root: Path
    logical_identities: Mapping[Path, str]
    prior_files: Mapping[str, Any]
    prior_checks: Mapping[str, Any]
    observed_identities: Mapping[Path, Mapping[str, Any]]
    jobs: int
    policy: IdentityInspectionPolicy
    activity: ValidationActivityLog | None = None


class IdentityInspectionResult(NamedTuple):
    """Material identities, mechanics, and actual hashing work."""

    files: Dict[str, Dict[str, Any]]
    mechanics: Dict[str, Dict[str, Any]]
    files_hashed: int
    bytes_hashed: int
    files_reused: int
    inspections_reused: int


def _inspection_key(path: Path, inputs: IdentityInspectionInput) -> str:
    policy = inputs.policy
    logical_path = Path(os.path.abspath(str(path)))
    return inputs.logical_identities.get(
        logical_path, policy.display_identity(path, inputs.project_root)
    )


def _inspect_projected_source(
    path: Path, inputs: IdentityInspectionInput, key: str
) -> Tuple[str, Dict[str, Any], Dict[str, Any], Optional[int], bool]:
    policy = inputs.policy
    summary = path.resolve() == inputs.summary_path
    identity = (
        policy.summary_identity(path) if summary else policy.entry_identity(path)
    )
    observed = inputs.observed_identities.get(path.resolve())
    if observed is not None and identity != observed:
        raise FileChangedError(f"file changed after validation read: {path}")
    structure = policy.inspect_structure(path)
    after_identity = (
        policy.summary_identity(path) if summary else policy.entry_identity(path)
    )
    if identity != after_identity:
        raise FileChangedError(f"file changed during structure inspection: {path}")
    return key, identity, structure, identity["size"], False


def _inspect_observed_artifact(
    path: Path, inputs: IdentityInspectionInput, key: str
) -> Tuple[str, Dict[str, Any], Dict[str, Any], Optional[int], bool]:
    previous = inputs.prior_files.get(key)
    observed = inputs.observed_identities.get(path.resolve())
    session = ObservationSession()
    observation, structure, inspection_reused = session.inspect(
        path,
        inputs.policy.inspect_structure,
        previous if isinstance(previous, dict) else None,
        (
            inputs.prior_checks.get(key)
            if observed is None or previous == observed
            else None
        ),
    )
    if observation.status == CHANGED_DURING_OBSERVATION:
        raise FileChangedError(f"file changed during structure inspection: {path}")
    if not observation.resolved or observation.identity is None:
        raise ValidationToolError(
            observation.detail or f"could not observe validation input: {path}"
        )
    identity = dict(observation.identity)
    if structure is None:
        raise ValidationToolError(f"could not inspect validation input: {path}")
    if observed is not None and identity != observed:
        raise FileChangedError(f"file changed after validation read: {path}")
    hashed = (
        identity["size"]
        if observation.status in {CONTENT_UNCHANGED, CONTENT_CHANGED, NEW}
        else None
    )
    return key, identity, cast(Dict[str, Any], structure), hashed, inspection_reused


def _inspect_identity_result(
    path: Path, inputs: IdentityInspectionInput
) -> Tuple[str, Dict[str, Any], Dict[str, Any], Optional[int], bool]:
    """Inspect one input, reusing a stable prior identity when possible."""

    key = _inspection_key(path, inputs)
    if path.resolve() == inputs.summary_path or path.resolve() in inputs.entry_paths:
        return _inspect_projected_source(path, inputs, key)
    return _inspect_observed_artifact(path, inputs, key)


def _inspect_identity(
    path: Path, inputs: IdentityInspectionInput
) -> Tuple[str, Dict[str, Any], Dict[str, Any], Optional[int], bool]:
    activity = inputs.activity
    subject = inputs.logical_identities.get(
        Path(os.path.abspath(str(path))),
        inputs.policy.display_identity(path, inputs.project_root),
    )
    with log_operation(activity, "inspect-identity", subject=subject):
        return _inspect_identity_result(path, inputs)


def inspect_identities(inputs: IdentityInspectionInput) -> IdentityInspectionResult:
    """Inspect all material inputs with deterministic bounded concurrency."""

    files: Dict[str, Dict[str, Any]] = {}
    mechanics: Dict[str, Dict[str, Any]] = {}
    files_hashed = 0
    bytes_hashed = 0
    files_reused = 0
    inspections_reused = 0
    log_checkpoint(
        inputs.activity,
        "identity-inspection-start",
        paths=len(inputs.paths),
        jobs=inputs.jobs,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=inputs.jobs) as executor:
        futures = {
            executor.submit(_inspect_identity, path, inputs): path
            for path in sorted(inputs.paths)
        }
        for future in concurrent.futures.as_completed(futures):
            path = futures[future]
            try:
                key, identity, structure, hashed, inspection_reused = future.result()
            except FileChangedError:
                raise
            except (OSError, ValidationToolError) as exc:
                key = inputs.policy.display_identity(path, inputs.project_root)
                mechanics[key] = {"status": "fail", "detail": str(exc)}
                continue
            files[key] = identity
            mechanics[key] = structure
            if path.is_file() and hashed is not None:
                bytes_hashed += hashed
                files_hashed += 1
            elif path.is_file():
                files_reused += 1
                inspections_reused += int(inspection_reused)
    log_checkpoint(
        inputs.activity,
        "identity-inspection-complete",
        paths=len(inputs.paths),
        files_hashed=files_hashed,
        bytes_hashed=bytes_hashed,
        files_reused=files_reused,
        inspections_reused=inspections_reused,
    )
    return IdentityInspectionResult(
        files,
        mechanics,
        files_hashed,
        bytes_hashed,
        files_reused,
        inspections_reused,
    )


@dataclass(frozen=True)
class ScannedEntry:
    """One parsed entry record and its section lookup facts."""

    entry: Dict[str, Any]
    sections: set[str]
    section_types: Dict[str, list[str]]
    source_identity: Optional[Dict[str, Any]]


EntryCommands = Callable[
    [Dict[str, Any], Path, Path, Optional[set[Path]]], list[Dict[str, Any]]
]


@dataclass(frozen=True)
class EntryScanPolicy:
    """Concrete parsing, command, evidence, and structure operations for entries."""

    markdown_parser: Callable[[Path], Dict[str, Any]]
    evidence_reader: Callable[[Path], Dict[str, Any]]
    commands: EntryCommands
    inspect_structure: IdentityFunction


class EntryScanWorkspace(NamedTuple):
    """Shared mutable registries and immutable paths for entry scanning."""

    project_root: Path
    owned_by_folder: Mapping[Path, Sequence[OwnedMaterial]]
    owned_paths: Mapping[str, Path]
    owned_aliases: Mapping[Path, str]
    log_command_scripts: set[Path]
    evidence_records: Dict[Path, Dict[str, Any]]
    identity_paths: set[Path]
    resolved_paths: Dict[str, str]
    mechanics: Dict[str, Dict[str, Any]]
    observed_identities: Dict[Path, Dict[str, Any]]
    policy: EntryScanPolicy


def _owned_scan_identity(
    workspace: EntryScanWorkspace, raw: Optional[str]
) -> Optional[str]:
    return workspace.owned_aliases.get(Path(raw).resolve()) if raw else None


def _entry_folder_evidence_record(
    workspace: EntryScanWorkspace, folder: Path
) -> Dict[str, Any]:
    if folder not in workspace.evidence_records:
        evidence_path = folder / "evidence.csv"
        if evidence_path.is_file():
            record, identity = stable_file_read(
                evidence_path, lambda: workspace.policy.evidence_reader(evidence_path)
            )
            workspace.observed_identities[evidence_path.resolve()] = identity
        else:
            record = workspace.policy.evidence_reader(evidence_path)
        record["identity"] = (
            display_path(Path(record["path"]), workspace.project_root)
            if record["path"]
            else None
        )
        workspace.evidence_records[folder] = record
    return workspace.evidence_records[folder]


class _EntryEvidenceInput(NamedTuple):
    entry_id: str
    entry_path: Path
    parsed: Mapping[str, Any]
    data_index: Dict[str, Any]
    record: Mapping[str, Any]
    workspace: EntryScanWorkspace


def _resolved_entry_evidence_rows(
    inputs: _EntryEvidenceInput,
) -> list[Dict[str, Any]]:
    presented_by_key = {
        (item["section"], item["kind"], item["selector"]): item
        for item in inputs.parsed["presented_items"]
    }
    rows = []
    for stored_row in inputs.record["rows"]:
        if stored_row["entry"] != inputs.entry_id:
            continue
        row = dict(stored_row)
        row["resolved_sources"] = [
            resolve_evidence_source(
                source,
                inputs.entry_path,
                inputs.workspace.project_root,
                inputs.data_index,
            )
            for source in row["source_specs"]
        ]
        for source in row["resolved_sources"]:
            alias = _owned_scan_identity(inputs.workspace, source.get("path"))
            if alias is not None:
                source["identity"] = alias
        row["presented_item"] = presented_by_key.get(
            (row["section"], row["kind"], row["evidence"])
        )
        rows.append(row)
    return rows


def _record_entry_evidence_mismatches(
    entry_id: str,
    parsed: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
    record: Dict[str, Any],
) -> None:
    expected = {
        (item["section"], item["kind"], item["selector"])
        for item in parsed["presented_items"]
    }
    actual = {(row["section"], row["kind"], row["evidence"]) for row in evidence_rows}
    record["errors"].extend(
        f"{entry_id}: missing {kind} association in {section!r}: {selector!r}"
        for section, kind, selector in sorted(expected - actual)
    )
    record["errors"].extend(
        f"{entry_id}: extra {kind} association in {section!r}: {selector!r}"
        for section, kind, selector in sorted(actual - expected)
    )


def _register_material_path(
    workspace: EntryScanWorkspace, identity: str, path: Path
) -> None:
    workspace.resolved_paths.setdefault(identity, path.resolve().as_posix())
    if path.is_file():
        workspace.identity_paths.add(workspace.owned_paths.get(identity, path))
    elif path.is_dir():
        workspace.mechanics[identity] = workspace.policy.inspect_structure(path)


def _register_command_material(
    workspace: EntryScanWorkspace, commands: Sequence[Mapping[str, Any]]
) -> None:
    for command in commands:
        raw_script = command.get("script")
        if raw_script and Path(raw_script).is_file():
            script_path = Path(raw_script)
            identity = _owned_scan_identity(workspace, raw_script) or display_path(
                script_path, workspace.project_root
            )
            _register_material_path(workspace, identity, script_path)
        for argument in command.get("path_arguments", []):
            if argument["role_hint"] in {"workspace", "dependency-container"}:
                continue
            argument_path = Path(argument["path"])
            identity = _owned_scan_identity(
                workspace, argument["path"]
            ) or display_path(argument_path, workspace.project_root)
            _register_material_path(workspace, identity, argument_path)
        for token in command.get("data_tokens", []):
            raw = token.get("path")
            if raw and Path(raw).exists():
                token_path = Path(raw)
                identity = _owned_scan_identity(workspace, raw) or display_path(
                    token_path, workspace.project_root
                )
                _register_material_path(workspace, identity, token_path)


class _EntryMaterialInput(NamedTuple):
    workspace: EntryScanWorkspace
    entry_path: Path
    data_index: Mapping[str, Any]
    evidence_record: Mapping[str, Any]
    evidence_rows: Sequence[Mapping[str, Any]]
    candidates: Sequence[Mapping[str, Any]]
    commands: Sequence[Mapping[str, Any]]


def _register_entry_material(inputs: _EntryMaterialInput) -> None:
    workspace = inputs.workspace
    entry_identity = display_path(inputs.entry_path, workspace.project_root)
    workspace.identity_paths.add(inputs.entry_path)
    workspace.resolved_paths[entry_identity] = inputs.entry_path.resolve().as_posix()
    for raw in (inputs.data_index.get("path"), inputs.evidence_record.get("path")):
        if raw:
            path = Path(raw)
            _register_material_path(
                workspace, display_path(path, workspace.project_root), path
            )
    for row in inputs.evidence_rows:
        for source in row["resolved_sources"]:
            raw = source.get("path")
            if raw and Path(raw).exists():
                _register_material_path(workspace, source["identity"], Path(raw))
    for candidate in inputs.candidates:
        raw = candidate.get("resolved_path")
        if raw and Path(raw).exists():
            _register_material_path(workspace, candidate["identity"], Path(raw))
    _register_command_material(workspace, inputs.commands)


class InitialMaterialRegistry(NamedTuple):
    """Initial owned and log-record material registries for one scan."""

    identity_paths: set[Path]
    resolved_paths: Dict[str, str]
    mechanics: Dict[str, Dict[str, Any]]
    logical_identities: Dict[Path, str]


class InitialMaterialInput(NamedTuple):
    """Paths, owned material, and inspection used to seed one scan."""

    summary_path: Path
    refs_path: Path
    summary_evidence: Mapping[str, Any]
    owned_paths: Mapping[str, Path]
    project_root: Path
    inspect_structure: IdentityFunction


def initial_material_registry(inputs: InitialMaterialInput) -> InitialMaterialRegistry:
    """Register owned material and top-level source records before entry scans."""

    identity_paths = {inputs.summary_path}
    resolved_paths = {
        display_path(
            inputs.summary_path, inputs.project_root
        ): inputs.summary_path.as_posix()
    }
    mechanics: Dict[str, Dict[str, Any]] = {}
    logical_identities = {
        Path(os.path.abspath(str(logical_path))): identity
        for identity, logical_path in inputs.owned_paths.items()
    }
    for identity, logical_path in inputs.owned_paths.items():
        resolved_paths[identity] = logical_path.as_posix()
        if logical_path.is_dir():
            mechanics[identity] = inputs.inspect_structure(logical_path)
        else:
            identity_paths.add(logical_path)
    for path in (
        inputs.refs_path if inputs.refs_path.is_file() else None,
        (
            Path(inputs.summary_evidence["path"])
            if inputs.summary_evidence.get("path")
            else None
        ),
    ):
        if path is not None:
            identity_paths.add(path)
            resolved_paths[display_path(path, inputs.project_root)] = (
                path.resolve().as_posix()
            )
    return InitialMaterialRegistry(
        identity_paths, resolved_paths, mechanics, logical_identities
    )


def scan_listed_entry(
    listed: Mapping[str, Any], workspace: EntryScanWorkspace
) -> ScannedEntry:
    """Parse, resolve, and register one maintained research-log entry."""

    entry_path = Path(listed["path"])
    if not entry_path.is_file():
        return ScannedEntry({**listed, "error": "missing entry"}, set(), {}, None)
    parsed, source_identity = stable_file_read(
        entry_path,
        lambda: workspace.policy.markdown_parser(entry_path),
        entry_validation_identity,
    )
    index = data_index(entry_path)
    if index.get("path"):
        data_path = Path(index["path"])
        reread, data_identity = stable_file_read(
            data_path, lambda: data_index(entry_path)
        )
        index = reread
        workspace.observed_identities[data_path.resolve()] = data_identity
    evidence_record = _entry_folder_evidence_record(workspace, entry_path.parent)
    commands = workspace.policy.commands(
        parsed,
        entry_path,
        workspace.project_root,
        workspace.log_command_scripts
        | {
            item.resolved_path
            for item in workspace.owned_by_folder.get(entry_path.parent, [])
            if item.kind == "script"
        },
    )
    candidates = merge_command_candidates(
        candidate_references(parsed, entry_path, workspace.project_root),
        commands,
        workspace.project_root,
    )
    used_tokens = sorted(
        {
            result["name"]
            for command in commands
            for result in command.get("data_tokens", [])
            if result["name"] not in {"project", "log"}
        }
    )
    indexed_names = {row.get("name", "") for row in index["rows"] if row.get("name")}
    index["used_tokens"] = used_tokens
    index["unused_names"] = sorted(indexed_names - set(used_tokens))
    evidence_rows = _resolved_entry_evidence_rows(
        _EntryEvidenceInput(
            listed["id"], entry_path, parsed, index, evidence_record, workspace
        )
    )
    for candidate in candidates:
        alias = _owned_scan_identity(workspace, candidate.get("resolved_path"))
        if alias is not None:
            candidate["identity"] = alias
    _record_entry_evidence_mismatches(
        listed["id"], parsed, evidence_rows, evidence_record
    )

    def experimental(item: Mapping[str, Any]) -> bool:
        return item.get("section_type") == "experimental"

    entry_identity = display_path(entry_path, workspace.project_root)
    validation_notes: list[dict[str, Any]] = []
    validation_note_errors: list[dict[str, Any]] = []
    for authored_note in parsed["validation_notes"]:
        note = dict(authored_note)
        scope = note.get("retention_scope")
        if isinstance(scope, str):
            normalized_scope = normalized_retention_scope(scope, entry_identity)
            note["retention_scope"] = normalized_scope
            error = blanket_retention_error(normalized_scope)
            if error is not None:
                validation_note_errors.append({**note, "error": error})
                continue
        validation_notes.append(note)

    entry = {
        "id": listed["id"],
        "title": parsed["title"],
        "path": entry_identity,
        "headings": parsed["headings"],
        "sections": parsed["sections"],
        "section_errors": [
            section for section in parsed["sections"] if section["type"] == "invalid"
        ],
        "links": [item for item in parsed["links"] if experimental(item)],
        "tables": [item for item in parsed["tables"] if experimental(item)],
        "fenced_blocks": [
            item for item in parsed["fenced_blocks"] if experimental(item)
        ],
        "numeric_evidence": [
            item for item in parsed["numeric_evidence"] if experimental(item)
        ],
        "presented_items": parsed["presented_items"],
        "validation_notes": validation_notes,
        "validation_note_errors": validation_note_errors,
        "citations": [item for item in parsed["citations"] if experimental(item)],
        "commands": commands,
        "data_index": index,
        "evidence_record": {
            "path": evidence_record["path"],
            "identity": evidence_record["identity"],
            "expected_path": evidence_record["expected_path"],
            "rows": evidence_rows,
            "errors": evidence_record["errors"],
        },
        "candidate_targets": candidates,
    }
    _register_entry_material(
        _EntryMaterialInput(
            workspace,
            entry_path,
            index,
            evidence_record,
            evidence_rows,
            candidates,
            commands,
        )
    )
    type_map: Dict[str, list[str]] = {}
    for section in parsed["sections"]:
        type_map.setdefault(section["section"], []).append(section["type"])
    return ScannedEntry(
        entry,
        {heading["text"] for heading in parsed["headings"]},
        type_map,
        source_identity,
    )


@dataclass(frozen=True)
class OrphanScopeInput:
    """Owned material and metadata needed to add synthetic orphan scopes."""

    scope_material: Mapping[str, Sequence[OwnedMaterial]]
    scope_metadata: Mapping[str, Mapping[str, Any]]
    real_entries: Sequence[Dict[str, Any]]
    project_root: Path


@dataclass(frozen=True)
class MaterialScopeInput:
    """Owned material and paths needed to define validation scopes."""

    owned_by_folder: Mapping[Path, list[OwnedMaterial]]
    log_owned_material: list[OwnedMaterial]
    entries_by_folder: Mapping[Path, list[Dict[str, Any]]]
    log_root: Path
    project_root: Path
    real_entries: Sequence[Dict[str, Any]]


def material_scopes(
    inputs: MaterialScopeInput,
) -> tuple[Dict[str, list[OwnedMaterial]], Dict[str, Dict[str, Any]], set[Path]]:
    """Assign complete owned material to entry, shared-entry, and log scopes."""

    scoped: Dict[str, list[OwnedMaterial]] = {}
    metadata: Dict[str, Dict[str, Any]] = {}
    scripts: set[Path] = set()
    for folder in sorted(set(inputs.entries_by_folder) | set(inputs.owned_by_folder)):
        folder_entries = inputs.entries_by_folder.get(folder, [])
        material = inputs.owned_by_folder.get(folder, [])
        shared_index_paths = {
            entry.get("data_index", {}).get("path")
            for entry in folder_entries
            if isinstance(entry.get("data_index", {}).get("path"), str)
        }
        shared_index = len(folder_entries) > 1 and len(shared_index_paths) == 1
        if not material and not shared_index:
            continue
        if len(folder_entries) == 1:
            scope_id = folder_entries[0]["id"]
        else:
            relative = logical_display_path(folder, inputs.project_root)
            scope_id = f"Entry global — {relative}"
            metadata[scope_id] = {
                "id": scope_id,
                "title": "Shared entry-folder research material",
                "path": relative,
                "scope_kind": "entry-global",
                "scope_paths": (
                    [entry["path"] for entry in folder_entries]
                    if folder_entries
                    else [relative]
                ),
                "validation_notes": [
                    {**note, "entry": entry["id"]}
                    for entry in folder_entries
                    for note in orphan_retention_notes(
                        entry.get("validation_notes", [])
                    )
                ],
            }
            if shared_index:
                source = folder_entries[0]["data_index"]
                metadata[scope_id]["data_index"] = {
                    "path": source["path"],
                    "rows": source.get("rows", []),
                    "used_tokens": sorted(
                        {
                            token
                            for entry in folder_entries
                            for token in entry.get("data_index", {}).get(
                                "used_tokens", []
                            )
                        }
                    ),
                }
        scoped.setdefault(scope_id, []).extend(material)
        scripts.update(item.resolved_path for item in material if item.kind == "script")

    if inputs.log_owned_material:
        scope_id = "Log level"
        scoped[scope_id] = list(inputs.log_owned_material)
        scripts.update(
            item.resolved_path
            for item in inputs.log_owned_material
            if item.kind == "script"
        )
        metadata[scope_id] = {
            "id": scope_id,
            "title": "Log-level research material",
            "path": logical_display_path(inputs.log_root, inputs.project_root),
            "scope_kind": "log-level",
            "scope_paths": [entry["path"] for entry in inputs.real_entries],
            "validation_notes": [
                {**note, "entry": entry["id"]}
                for entry in inputs.real_entries
                for note in orphan_retention_notes(entry.get("validation_notes", []))
            ],
        }
    return scoped, metadata, scripts


def orphan_scope_entries(inputs: OrphanScopeInput) -> list[Dict[str, Any]]:
    """Attach owned material inventories to entry and synthetic scopes."""

    entries_by_id = {entry["id"]: entry for entry in inputs.real_entries}
    extra_entries: list[Dict[str, Any]] = []
    for scope_id, material in inputs.scope_material.items():
        inventory = sorted(
            {
                (
                    item.kind,
                    logical_display_path(item.logical_path, inputs.project_root),
                )
                for item in material
            }
        )
        inventory_rows = [
            {"kind": kind, "identity": identity} for kind, identity in inventory
        ]
        if scope_id in entries_by_id:
            entries_by_id[scope_id]["orphan_inventory"].extend(inventory_rows)
            continue
        extra_entries.append(
            {
                **inputs.scope_metadata[scope_id],
                "headings": [],
                "sections": [],
                "section_errors": [],
                "validation_note_errors": [],
                "links": [],
                "tables": [],
                "fenced_blocks": [],
                "numeric_evidence": [],
                "presented_items": [],
                "citations": [],
                "commands": [],
                "data_index": inputs.scope_metadata[scope_id].get(
                    "data_index", {"path": None, "rows": [], "used_tokens": []}
                ),
                "evidence_record": {
                    "path": None,
                    "identity": None,
                    "expected_path": None,
                    "rows": [],
                    "errors": [],
                },
                "candidate_targets": [],
                "orphan_inventory": inventory_rows,
                "orphan_candidates": [],
            }
        )
    return extra_entries


def classify_local_orphan_inventory(scan: Mapping[str, Any]) -> DependencyGraph:
    """Populate orphan candidates from this log's deterministic local graph."""

    graph = build_dependency_graph(scan)
    namespace = Path(str(scan["summary"])).with_suffix("").as_posix()
    locations = orphan_locations(graph, namespace)
    for entry in scan.get("entries", []):
        entry_id = entry["id"]
        entry["orphan_candidates"] = [
            candidate
            for candidate in entry.get("orphan_inventory", [])
            if (entry_id, candidate["identity"]) in locations
        ]
    return graph


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _owned_snapshot_payload(
    summary: str,
    files: Mapping[str, Any],
    directory_memberships: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return complete source identities observed by this log."""
    return {
        "schema": "local-research-snapshot-v1",
        "summary": summary,
        "files": {
            identity: content_identity(value)
            for identity, value in sorted(files.items())
        },
        "directory_memberships": {
            identity: value
            for identity, value in sorted(directory_memberships.items())
        },
    }


def _local_snapshot_payload(scan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return source identities sufficient to reproduce every local projection."""

    return _owned_snapshot_payload(
        scan["summary"],
        scan.get("files", {}),
        scan.get("directory_memberships", {}),
    )


def local_snapshot_identity(scan: Mapping[str, Any]) -> str:
    """Identify local research inputs without generated or foreign state."""

    return _json_fingerprint(_local_snapshot_payload(scan))


def input_fingerprint(scan: Mapping[str, Any]) -> str:
    """Fingerprint the complete input surface observed for this log."""

    return _json_fingerprint(_local_snapshot_payload(scan))


@dataclass(frozen=True)
class ScanDocumentFacts:
    """Document and evidence facts assembled by one scan."""

    entry_order: Sequence[str]
    entry_listing: Mapping[str, Any]
    summary_items: Sequence[Dict[str, Any]]
    entries: Sequence[Dict[str, Any]]
    summary_evidence: Mapping[str, Any]
    entry_evidence_records: Sequence[Mapping[str, Any]]
    bibtex_path: Optional[str]
    bibtex_keys: Sequence[str]


@dataclass(frozen=True)
class ScanMaterialFacts:
    """Material identities and code relationships assembled by one scan."""

    files: Mapping[str, Any]
    directory_memberships: Mapping[str, Any]
    resolved_paths: Mapping[str, str]
    mechanical_checks: Mapping[str, Any]
    script_inventory: Sequence[str]
    script_dependency_graph: Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class ScanAssembly:
    """Typed owner of the deterministic scan-record assembly boundary."""

    schema_version: int
    rules_version: str
    mode: str
    summary: str
    log_root: str
    project_root: str
    documents: ScanDocumentFacts
    materials: ScanMaterialFacts
    component_versions: Mapping[str, int]
    input_projection_versions: Mapping[str, int]

    def record(self) -> ScanRecord:
        """Serialize the typed assembly into the persisted scan contract."""

        documents = self.documents
        materials = self.materials
        return cast(
            ScanRecord,
            {
                "schema_version": self.schema_version,
                "validation_rules_version": self.rules_version,
                "component_versions": dict(self.component_versions),
                "input_projection_versions": dict(self.input_projection_versions),
                "requested_mode": self.mode,
                "summary": self.summary,
                "log_root": self.log_root,
                "project_root": self.project_root,
                "entry_order": list(documents.entry_order),
                "entry_listing": dict(documents.entry_listing),
                "summary_items": list(documents.summary_items),
                "entries": list(documents.entries),
                "evidence_records": {
                    "summary": dict(documents.summary_evidence),
                    "entry_folders": [
                        dict(record) for record in documents.entry_evidence_records
                    ],
                },
                "bibtex": {
                    "path": documents.bibtex_path,
                    "keys": list(documents.bibtex_keys),
                },
                "files": dict(materials.files),
                "directory_memberships": dict(materials.directory_memberships),
                "resolved_paths": dict(materials.resolved_paths),
                "mechanical_checks": dict(materials.mechanical_checks),
                "script_inventory": list(materials.script_inventory),
                "script_dependency_graph": {
                    path: list(dependencies)
                    for path, dependencies in materials.script_dependency_graph.items()
                },
                "input_fingerprint": "",
            },
        )


ScriptDependencyGraph = Callable[[set[Path]], Dict[Path, list[Path]]]


@dataclass(frozen=True)
class ScanLifecyclePolicy:
    """Versions and concrete mechanics governing one complete log scan."""

    scan_schema_version: int
    orphan_inventory_version: int
    validation_record_names: tuple[str, ...]
    material_inventory: MaterialInventoryPolicy
    entry_scan: EntryScanPolicy
    identity_inspection: IdentityInspectionPolicy
    script_dependency_graph: ScriptDependencyGraph
    incremental_operations: IncrementalOperations
    component_versions: Mapping[str, int]
    input_projection_versions: Mapping[str, int]


class ScanRequest(NamedTuple):
    """Complete public request for one scan lifecycle."""

    summary_path: Path
    jobs: int
    prior_record: Optional[Dict[str, Any]]
    prior_cache: Optional[Dict[str, Any]]
    rules_version: str
    mode: str
    policy: ScanLifecyclePolicy
    project_root: Optional[Path] = None
    activity: ValidationActivityLog | None = None


@dataclass(frozen=True)
class _DiscoveredScanDocuments:
    """Parsed document records owned by the discovery stage."""

    discovery: Mapping[str, Any]
    entries: Sequence[Dict[str, Any]]
    evidence_records: Mapping[Path, Dict[str, Any]]
    summary_evidence: Mapping[str, Any]
    bibliography: Sequence[str]
    summary_path: Path
    refs_path: Path


@dataclass(frozen=True)
class _DiscoveredScanMaterials:
    """Material registries owned by discovery and identity inspection."""

    files: Mapping[str, Dict[str, Any]]
    mechanics: Mapping[str, Dict[str, Any]]
    identity_paths: set[Path]
    resolved_paths: Mapping[str, str]
    logical_identities: Mapping[Path, str]
    observed_identities: Mapping[Path, Mapping[str, Any]]
    owned_by_folder: Mapping[Path, list[OwnedMaterial]]
    log_material: Sequence[OwnedMaterial]
    owned_aliases: Mapping[Path, str]
    owned_directory_memberships: Mapping[Path, Mapping[str, object]]
    project_root: Path
    log_root: Path


@dataclass(frozen=True)
class _ScanFinalizationPolicy:
    """Inspection policy and prior state needed only during finalization."""

    jobs: int
    policy: ScanLifecyclePolicy
    valid_prior: Mapping[str, Any]
    activity: ValidationActivityLog | None


class _FinalizedScanFacts(NamedTuple):
    documents: ScanDocumentFacts
    materials: ScanMaterialFacts
    entry_count: int
    orphan_scope_count: int
    files_hashed: int
    bytes_hashed: int
    files_reused: int
    inspections_reused: int


def _finalize_scan_facts(
    documents_input: _DiscoveredScanDocuments,
    material_input: _DiscoveredScanMaterials,
    finalization: _ScanFinalizationPolicy,
) -> _FinalizedScanFacts:
    discovery = documents_input.discovery
    project_root = material_input.project_root
    activity = finalization.activity
    entry_paths = {
        Path(entry["path"]).resolve()
        for entry in discovery["listed"]
        if Path(entry["path"]).is_file()
    }
    log_phase(
        activity,
        "scan.inspect-identities",
        paths=len(material_input.identity_paths),
        jobs=finalization.jobs,
    )
    inspected = inspect_identities(
        IdentityInspectionInput(
            set(material_input.identity_paths),
            documents_input.summary_path,
            entry_paths,
            project_root,
            material_input.logical_identities,
            finalization.valid_prior.get("input_files", {}),
            finalization.valid_prior.get("mechanical_checks", {}),
            material_input.observed_identities,
            finalization.jobs,
            finalization.policy.identity_inspection,
            activity,
        )
    )
    files = {**material_input.files, **inspected.files}
    mechanics = {**material_input.mechanics, **inspected.mechanics}
    entries = copy.deepcopy(list(documents_input.entries))
    log_phase(activity, "scan.finalize-entry-candidates", entries=len(entries))
    with log_operation(
        activity,
        "finalize-entry-candidates",
        subject=documents_input.summary_path.name,
    ):
        real_entries = finalize_entry_candidates(
            entries, documents_input.bibliography, mechanics
        )
    entries_by_folder: Dict[Path, list[Dict[str, Any]]] = {}
    for entry in real_entries:
        entries_by_folder.setdefault(
            Path(material_input.resolved_paths[entry["path"]]).parent, []
        ).append(entry)
    log_phase(activity, "scan.material-scopes", entries=len(real_entries))
    with log_operation(
        activity, "material-scopes", subject=material_input.log_root.as_posix()
    ):
        scope_material, scope_metadata, script_inventory = material_scopes(
            MaterialScopeInput(
                material_input.owned_by_folder,
                list(material_input.log_material),
                entries_by_folder,
                material_input.log_root,
                project_root,
                real_entries,
            )
        )
    orphan_entries = orphan_scope_entries(
        OrphanScopeInput(scope_material, scope_metadata, real_entries, project_root)
    )
    entries.extend(orphan_entries)
    add_reference_inventory(entries)
    log_phase(
        activity, "scan.script-dependency-graph", scripts=len(script_inventory)
    )
    with log_operation(
        activity,
        "script-dependency-graph",
        subject=material_input.log_root.as_posix(),
    ):
        script_graph = finalization.policy.script_dependency_graph(script_inventory)
    log_phase(
        activity,
        "scan.directory-memberships",
        resolved_paths=len(material_input.resolved_paths),
    )
    memberships = directory_memberships(
        material_input.resolved_paths,
        material_input.log_root,
        project_root,
        (*finalization.policy.validation_record_names, LOCK_FILENAME),
    )
    for path, captured in material_input.owned_directory_memberships.items():
        identity = logical_display_path(path, project_root)
        if memberships.get(identity) != captured:
            raise FileChangedError(
                f"owned directory changed after inventory: {path}"
            )

    def material_identity(path: Path) -> str:
        return material_input.owned_aliases.get(
            path.resolve(), display_path(path, project_root)
        )

    documents = ScanDocumentFacts(
        entry_order=[
            *[entry["id"] for entry in discovery["listed"]],
            *[entry["id"] for entry in orphan_entries],
        ],
        entry_listing={
            "missing_entries": discovery["missing"],
            "unlisted_entries": [
                display_path(Path(path), project_root)
                for path in discovery["unlisted"]
            ],
        },
        summary_items=discovery["summary"]["summary_statistics"],
        entries=entries,
        summary_evidence=documents_input.summary_evidence,
        entry_evidence_records=[
            documents_input.evidence_records[path]
            for path in sorted(documents_input.evidence_records)
        ],
        bibtex_path=(
            display_path(documents_input.refs_path, project_root)
            if documents_input.refs_path.exists()
            else None
        ),
        bibtex_keys=documents_input.bibliography,
    )
    materials = ScanMaterialFacts(
        files=dict(sorted(files.items())),
        directory_memberships=dict(sorted(memberships.items())),
        resolved_paths=dict(sorted(material_input.resolved_paths.items())),
        mechanical_checks=dict(sorted(mechanics.items())),
        script_inventory=[material_identity(path) for path in sorted(script_inventory)],
        script_dependency_graph={
            material_identity(path): [
                material_identity(dependency) for dependency in dependencies
            ]
            for path, dependencies in sorted(
                script_graph.items(), key=lambda item: item[0].as_posix()
            )
            if dependencies
        },
    )
    return _FinalizedScanFacts(
        documents,
        materials,
        len(real_entries),
        len(orphan_entries),
        inspected.files_hashed,
        inspected.bytes_hashed,
        inspected.files_reused,
        inspected.inspections_reused,
    )


def _decode_scan(scan: Any, schema_version: int) -> ScanRecord:
    try:
        return decode_scan_record(scan, schema_version=schema_version)
    except LifecycleRecordContractError as exc:
        raise ValidationToolError(f"invalid scan record: {exc}") from exc


def _scan_discovered_entries(
    discovery: Mapping[str, Any],
    workspace: EntryScanWorkspace,
    observed_identities: Dict[Path, Dict[str, Any]],
    activity: ValidationActivityLog | None = None,
) -> tuple[list[Dict[str, Any]], Dict[str, set[str]], Dict[str, Dict[str, list[str]]]]:
    log_phase(activity, "scan.entries", entries=len(discovery["listed"]))
    entries = []
    entry_sections: Dict[str, set[str]] = {}
    entry_section_types: Dict[str, Dict[str, list[str]]] = {}
    for listed in discovery["listed"]:
        with log_operation(
            activity,
            "scan-entry",
            subject=str(listed["path"]),
            entry=str(listed["id"]),
        ):
            scanned = scan_listed_entry(listed, workspace)
        entries.append(scanned.entry)
        if "error" in scanned.entry:
            continue
        assert scanned.source_identity is not None
        observed_identities[Path(listed["path"]).resolve()] = scanned.source_identity
        entry_sections[listed["id"]] = scanned.sections
        entry_section_types[listed["id"]] = scanned.section_types
    return entries, entry_sections, entry_section_types


def _discover_entries_for_scan(
    request: ScanRequest,
    summary_path: Path,
    log_root: Path,
    summary_identity: str,
    jobs: int,
) -> Dict[str, Any]:
    """Discover summary entry links with activity detail."""

    log_phase(
        request.activity,
        "scan.discover-entries",
        summary=summary_identity,
        jobs=jobs,
    )
    discovery = discover_entries(
        summary_path,
        log_root,
        request.policy.entry_scan.markdown_parser,
    )
    log_checkpoint(
        request.activity,
        "entry-discovery-complete",
        listed=len(discovery["listed"]),
        missing=len(discovery["missing"]),
        unlisted=len(discovery["unlisted"]),
    )
    return discovery


def _owned_inventory_for_scan(
    request: ScanRequest,
    log_root: Path,
    project_root: Path,
    folder_entry_ids: Mapping[Path, set[str]],
) -> OwnedInventory:
    """Inventory log-owned material with activity detail."""

    entry_folders = owned_entry_folders(log_root, folder_entry_ids)
    log_phase(
        request.activity,
        "scan.inventory-materials",
        entry_folders=len(entry_folders),
    )
    owned = owned_inventory(
        log_root,
        entry_folders,
        project_root,
        request.policy.material_inventory,
        membership_ignored_paths=(
            log_root / name
            for name in (*request.policy.validation_record_names, LOCK_FILENAME)
        ),
    )
    log_checkpoint(
        request.activity,
        "material-inventory-complete",
        owned_paths=len(owned.paths),
        owned_folders=len(owned.by_folder),
        log_material=len(owned.log_material),
    )
    return owned


def _apply_scan_reuse(
    raw_scan: ScanRecord,
    request: ScanRequest,
    policy: ScanLifecyclePolicy,
) -> None:
    if request.prior_record is not None:
        raw_scan["incremental"] = compare_prior_record(
            cast(Dict[str, Any], raw_scan),
            request.prior_record,
            policy.incremental_operations,
        )
        raw_scan["resolved_paths"] = dict(
            sorted(raw_scan["resolved_paths"].items())
        )


def scan_log(request: ScanRequest) -> tuple[ScanRecord, ValidationMetrics]:
    """Discover, identify, classify, and incrementally compare one research log."""

    policy = request.policy
    jobs = validated_jobs(request.jobs)
    if request.mode not in {"standard", "reproduction"}:
        raise ValidationToolError("validation mode must be standard or reproduction")
    started = time.monotonic()
    activity = request.activity
    summary_path = request.summary_path.resolve()
    if not summary_path.is_file():
        raise ValidationToolError(f"summary does not exist: {summary_path}")
    log_root = summary_path.with_suffix("")
    project_root = request.project_root or infer_project_root(summary_path)
    prior_cache = request.prior_cache or {}
    prior = {
        "input_files": prior_cache.get("files", {}),
        "mechanical_checks": prior_cache.get("inspections", {}),
    }
    summary_identity = display_path(summary_path, project_root)
    discovery = _discover_entries_for_scan(
        request, summary_path, log_root, summary_identity, jobs
    )
    refs_path = log_root / "refs.bib"
    bibliography = bibtex_keys(refs_path)
    summary_evidence_path = log_root / "evidence.csv"
    observed_identities: Dict[Path, Dict[str, Any]] = {
        summary_path: discovery["source_identity"]
    }
    summary_evidence, evidence_identities = optional_stable_file_read(
        summary_evidence_path,
        lambda: summary_evidence_record(summary_evidence_path),
    )
    observed_identities.update(evidence_identities)
    summary_evidence["identity"] = (
        display_path(Path(summary_evidence["path"]), project_root)
        if summary_evidence["path"]
        else None
    )

    evidence_records: Dict[Path, Dict[str, Any]] = {}
    folder_entry_ids: Dict[Path, set[str]] = {}
    for listed in discovery["listed"]:
        folder_entry_ids.setdefault(Path(listed["path"]).parent, set()).add(
            listed["id"]
        )
    owned = _owned_inventory_for_scan(
        request, log_root, project_root, folder_entry_ids
    )

    files: Dict[str, Dict[str, Any]] = {}
    registry = initial_material_registry(
        InitialMaterialInput(
            summary_path,
            refs_path,
            summary_evidence,
            owned.paths,
            project_root,
            policy.entry_scan.inspect_structure,
        )
    )
    mechanics = registry.mechanics
    identity_paths = registry.identity_paths
    resolved_paths = {
        **owned.resolved_directory_boundaries(project_root),
        **registry.resolved_paths,
    }
    logical_identities = registry.logical_identities
    workspace = EntryScanWorkspace(
        project_root,
        owned.by_folder,
        owned.paths,
        owned.aliases,
        {
            item.resolved_path
            for item in owned.log_material
            if item.kind == "script"
        },
        evidence_records,
        identity_paths,
        resolved_paths,
        mechanics,
        observed_identities,
        policy.entry_scan,
    )
    entries, entry_sections, entry_section_types = _scan_discovered_entries(
        discovery, workspace, observed_identities, activity
    )

    log_phase(
        activity,
        "scan.validate-evidence-records",
        entry_records=len(evidence_records),
    )
    validate_entry_evidence_records(
        evidence_records,
        folder_entry_ids,
        entry_sections,
        entry_section_types,
    )
    validate_summary_evidence(
        summary_evidence,
        discovery["summary"]["summary_statistics"],
        entry_sections,
        entry_section_types,
    )
    finalized = _finalize_scan_facts(
        _DiscoveredScanDocuments(
            discovery=discovery,
            entries=entries,
            evidence_records=evidence_records,
            summary_evidence=summary_evidence,
            bibliography=bibliography,
            summary_path=summary_path,
            refs_path=refs_path,
        ),
        _DiscoveredScanMaterials(
            files=files,
            mechanics=mechanics,
            identity_paths=identity_paths,
            resolved_paths=resolved_paths,
            logical_identities=logical_identities,
            observed_identities=observed_identities,
            owned_by_folder=owned.by_folder,
            log_material=owned.log_material,
            owned_aliases=owned.aliases,
            owned_directory_memberships=owned.directory_memberships,
            project_root=project_root,
            log_root=log_root,
        ),
        _ScanFinalizationPolicy(
            jobs=jobs,
            policy=policy,
            valid_prior=prior or {},
            activity=activity,
        ),
    )
    raw_scan = ScanAssembly(
        schema_version=policy.scan_schema_version,
        rules_version=request.rules_version,
        mode=request.mode,
        summary=summary_identity,
        log_root=display_path(log_root, project_root),
        project_root=project_root.as_posix(),
        documents=finalized.documents,
        materials=finalized.materials,
        component_versions=policy.component_versions,
        input_projection_versions=policy.input_projection_versions,
    ).record()
    log_phase(
        activity,
        "scan.provenance-graph",
        entries=len(raw_scan["entries"]),
        scripts=len(raw_scan["script_inventory"]),
    )
    with log_operation(
        activity, "build-provenance-graph", subject=summary_identity
    ):
        classify_local_orphan_inventory(raw_scan)
    raw_scan["input_fingerprint"] = input_fingerprint(raw_scan)
    log_phase(activity, "scan.incremental-comparison")
    _apply_scan_reuse(raw_scan, request, policy)
    metrics = scan_metrics(
        ScanMetricsInput(
            started,
            raw_scan,
            finalized.entry_count,
            finalized.orphan_scope_count,
            finalized.files_hashed,
            finalized.bytes_hashed,
            finalized.files_reused,
            finalized.inspections_reused,
        )
    )
    if "incremental" in raw_scan:
        add_incremental_metrics(metrics, raw_scan)
    log_checkpoint(
        activity,
        "scan-complete",
        elapsed_seconds=metrics["elapsed_seconds"],
        entries=metrics["entries"],
        files_identified=metrics["files_identified"],
        files_hashed=metrics["files_hashed"],
        bytes_hashed=metrics["bytes_hashed"],
    )
    return _decode_scan(raw_scan, policy.scan_schema_version), metrics


class ScanMetricsInput(NamedTuple):
    """Inputs required to summarize one completed mechanical scan."""

    started: float
    scan: ScanRecord
    entry_count: int
    orphan_scope_count: int
    files_hashed: int
    bytes_hashed: int
    files_reused: int
    inspections_reused: int


def _section_count(entries: Sequence[Mapping[str, Any]], section_type: str) -> int:
    return sum(
        section.get("type") == section_type
        for entry in entries
        for section in entry.get("sections", [])
    )


def scan_metrics(inputs: ScanMetricsInput) -> ValidationMetrics:
    """Build deterministic result metrics for one completed scan."""

    scan = inputs.scan
    entries = scan["entries"]
    summary_evidence = scan["evidence_records"]["summary"]
    entry_evidence_records = scan["evidence_records"]["entry_folders"]
    return cast(
        ValidationMetrics,
        {
            "elapsed_seconds": round(time.monotonic() - inputs.started, 6),
            "entries": inputs.entry_count,
            "orphan_scopes": inputs.orphan_scope_count,
            "summary_items": len(scan["summary_items"]),
            "candidate_targets": sum(
                len(entry.get("candidate_targets", [])) for entry in entries
            ),
            "tables": sum(len(entry.get("tables", [])) for entry in entries),
            "fenced_blocks": sum(
                len(entry.get("fenced_blocks", [])) for entry in entries
            ),
            "numeric_evidence": sum(
                len(entry.get("numeric_evidence", [])) for entry in entries
            ),
            "evidence_rows": len(summary_evidence["rows"])
            + sum(len(record["rows"]) for record in entry_evidence_records),
            "evidence_errors": len(summary_evidence["errors"])
            + sum(len(record["errors"]) for record in entry_evidence_records),
            "section_errors": sum(
                len(entry.get("section_errors", [])) for entry in entries
            ),
            "validation_note_errors": sum(
                len(entry.get("validation_note_errors", [])) for entry in entries
            ),
            "experimental_sections": _section_count(entries, "experimental"),
            "synthesis_sections": _section_count(entries, "synthesis"),
            "prose_sections": _section_count(entries, "prose"),
            "files_identified": len(scan["files"]),
            "files_hashed": inputs.files_hashed,
            "bytes_hashed": inputs.bytes_hashed,
            "files_reused": inputs.files_reused,
            "inspections_reused": inputs.inspections_reused,
        },
    )


def add_incremental_metrics(metrics: ValidationMetrics, scan: ScanRecord) -> None:
    """Add reusable-result metrics when a prior state was supplied."""

    incremental = scan["incremental"]
    metrics["reusable_checks"] = incremental.get("reusable_checks", 0)
    metrics["rerun_checks"] = incremental.get("rerun_checks", 0)
    metrics["incremental_status"] = cast(str, incremental.get("status"))
    metrics["semantic_review_required"] = incremental.get(
        "semantic_review_required", True
    )

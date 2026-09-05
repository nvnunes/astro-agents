"""Shared validation of current ``pyrun`` output-support signatures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn

from research_log_data import Fingerprint

from .commands import Invocation
from .errors import MechanicalContractError
from .pyrun_outputs import (
    OutputSupport,
    PyrunOutputsFile,
    code_target_path,
    portable_output_path,
)


class OutputSupportValidationError(MechanicalContractError):
    """One exact current-output support failure."""


@dataclass(frozen=True)
class ResolvedOutputSupport:
    """The support identity covering one exact output or directory member."""

    subject: str
    key: str
    path: Path
    record: OutputSupport | None


@dataclass(frozen=True)
class ResolvedCodeSupport:
    """One logical code identity and its resolved regular-file target."""

    key: str
    path: Path
    resolved: Path


def resolve_output_support(
    invocation: Invocation,
    material: str,
    *,
    entry_root: Path,
    project_root: Path,
    support: PyrunOutputsFile,
) -> ResolvedOutputSupport:
    """Resolve exact support, including an owning output-directory record."""

    material_path = Path(material).resolve()
    key = portable_output_path(
        material_path,
        entry_root=entry_root,
        project_root=project_root,
    )
    record = support.outputs.get(key)
    if record is not None:
        return ResolvedOutputSupport(material, key, material_path, record)
    covering = {
        Path(collection.root).resolve()
        for collection in invocation.collections
        if collection.direction == "output"
        and collection.mechanism == "directory"
        and collection.root is not None
        and _within(material_path, Path(collection.root).resolve())
    }
    if len(covering) > 1:
        _fail(
            "provenance.output.signature_unsupported",
            material,
            {"reason": "ambiguous_output_directory"},
        )
    if covering:
        path = next(iter(covering))
        key = portable_output_path(
            path,
            entry_root=entry_root,
            project_root=project_root,
        )
        return ResolvedOutputSupport(
            path.as_posix(), key, path, support.outputs.get(key)
        )
    return ResolvedOutputSupport(material, key, material_path, None)


def confirmed_output_record(
    invocation: Invocation,
    material: str,
    *,
    entry_root: Path,
    project_root: Path,
    support: PyrunOutputsFile,
) -> bool:
    """Return whether one exact producer output has a confirmed record."""

    resolved = resolve_output_support(
        invocation,
        material,
        entry_root=entry_root,
        project_root=project_root,
        support=support,
    )
    return resolved.record is not None and resolved.record.confirmed


def require_current_output_support(
    invocation: Invocation,
    resolved: ResolvedOutputSupport,
    *,
    current_output: Fingerprint,
    current_code: Mapping[str, Fingerprint] | None = None,
) -> OutputSupport:
    """Require one confirmed output record matching the current invocation."""

    material = resolved.subject
    key = resolved.key
    record = resolved.record
    if record is None:
        _fail(
            "provenance.output.unrecorded",
            material,
            {"output": key, "producer": invocation.identity},
        )
    if not record.confirmed:
        _fail(
            "provenance.output.unconfirmed",
            material,
            {"output": key, "producer": invocation.identity},
        )
    mismatches = output_signature_mismatches(
        invocation,
        record,
        current_output,
        current_code=current_code,
        material=material,
    )
    if mismatches:
        _fail(
            "provenance.output.signature_mismatch",
            material,
            {
                "fields": mismatches,
                "output": key,
                "producer": invocation.identity,
            },
        )
    return record


def output_signature_mismatches(
    invocation: Invocation,
    record: OutputSupport,
    current_output: Fingerprint,
    *,
    current_code: Mapping[str, Fingerprint] | None = None,
    material: str,
) -> list[str]:
    """Return exact signature fields that disagree with one invocation."""

    mismatches: list[str] = []
    if record.fingerprint != current_output:
        mismatches.append("output_fingerprint")
    mismatches.extend(
        output_producer_mismatches(invocation, record, material=material)
    )
    if current_code is not None and dict(record.code) != current_code:
        mismatches.append("code")
    return mismatches


def output_support_matches_invocation(
    invocation: Invocation,
    record: OutputSupport,
    *,
    material: str,
) -> bool:
    """Return whether stable authored fields associate support with a command."""

    try:
        expected_inputs = _output_signature_inputs(invocation, material)
    except OutputSupportValidationError:
        return False
    return (
        record.script.path == invocation.script_argument
        and record.parameters == invocation.parameters
        and set(dict(record.inputs)) == set(expected_inputs)
    )


def resolve_code_support(
    record: OutputSupport,
    *,
    entry_root: Path,
    subject: str,
) -> tuple[ResolvedCodeSupport, ...]:
    """Resolve one code mapping and reject unavailable or aliased targets."""

    result: list[ResolvedCodeSupport] = []
    identities: dict[str, str] = {}
    for key, _ in record.code:
        path = code_target_path(key, entry_root=entry_root)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            _fail(
                "provenance.output.code_invalid",
                subject,
                {"code": key, "error": str(error), "reason": "unavailable"},
            )
        if not resolved.is_file():
            _fail(
                "provenance.output.code_invalid",
                subject,
                {"code": key, "reason": "not_regular_file"},
            )
        identity = resolved.as_posix()
        prior = identities.setdefault(identity, key)
        if prior != key:
            _fail(
                "provenance.output.code_invalid",
                subject,
                {
                    "code": sorted((prior, key)),
                    "reason": "duplicate_resolved_identity",
                },
            )
        result.append(ResolvedCodeSupport(key, path, resolved))
    return tuple(result)


def output_producer_mismatches(
    invocation: Invocation,
    record: OutputSupport,
    *,
    material: str,
) -> list[str]:
    """Return producer-signature fields that disagree with one invocation."""

    expected_inputs = _output_signature_inputs(invocation, material)
    current_script = (
        Fingerprint("sha256", digest=invocation.script_identity)
        if invocation.script_identity is not None
        else None
    )
    mismatches: list[str] = []
    if record.script.path != invocation.script_argument:
        mismatches.append("script")
    if current_script is None or record.script.fingerprint != current_script:
        mismatches.append("script_fingerprint")
    if record.parameters != invocation.parameters:
        mismatches.append("parameters")
    if dict(record.inputs) != expected_inputs:
        mismatches.append("inputs")
    return mismatches


def _output_signature_inputs(
    invocation: Invocation, material: str
) -> Mapping[str, Fingerprint]:
    expected: dict[str, Fingerprint] = {}
    for relationship in invocation.inputs:
        resource = relationship.input_resource
        if resource is None:
            _fail(
                "provenance.output.signature_unsupported",
                material,
                {"input": relationship.path, "producer": invocation.identity},
            )
        prior = expected.setdefault(resource.name, resource.fingerprint)
        if prior != resource.fingerprint:
            _fail(
                "provenance.output.signature_unsupported",
                material,
                {"input": resource.name, "reason": "conflicting_identity"},
            )
    return expected


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise OutputSupportValidationError(
        code,
        subject,
        observed,
        "Pyrun Output Support Records",
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

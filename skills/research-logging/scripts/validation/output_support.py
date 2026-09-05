"""Shared validation of current ``pyrun`` output-support signatures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn

from research_log_data import Fingerprint

from .commands import Invocation
from .errors import MechanicalContractError
from .pyrun_outputs import OutputSupport, PyrunOutputsFile, portable_output_path


class OutputSupportValidationError(MechanicalContractError):
    """One exact current-output support failure."""


@dataclass(frozen=True)
class ResolvedOutputSupport:
    """The support identity covering one exact output or directory member."""

    subject: str
    key: str
    path: Path
    record: OutputSupport | None


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
        invocation, record, current_output, material=material
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
    material: str,
) -> list[str]:
    """Return exact signature fields that disagree with one invocation."""

    mismatches: list[str] = []
    if record.fingerprint != current_output:
        mismatches.append("output_fingerprint")
    mismatches.extend(
        output_producer_mismatches(invocation, record, material=material)
    )
    return mismatches


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

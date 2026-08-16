"""Native rule, input-projection, and producer compatibility contracts."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .contracts import ScanRecord, ValidationToolError

COMPONENT_VERSIONS: dict[str, int] = {
    "material_identity": 1,
    "mechanical_inspection": 1,
    "integrity": 1,
    "summary_provenance": 1,
    "entry_provenance": 2,
    "mechanical_producer": 2,
    "reviewed_producer": 2,
    "upstream_reviewed_producer": 2,
    "reproducibility": 1,
    "orphan_inventory": 1,
    "orphan_graph": 2,
    "orphan_semantic_adjudication": 1,
}
INPUT_PROJECTION_VERSIONS: dict[str, int] = {
    "entry": 1,
    "exact-material": 1,
    "collection-member": 1,
    "collection-membership": 1,
    "experimental-section": 1,
    "presented-item": 1,
    "evidence-association": 1,
    "recorded-invocation": 1,
    "validation-note": 1,
    "orphan-candidate": 1,
    "orphan-disposition": 1,
}
PRODUCER_BINDING_KINDS = frozenset({"exact-target", "scoped-collection"})
PRODUCER_BASES = frozenset(
    {"mechanical", "reviewed", "upstream-reviewed"}
)
_HEX_IDENTITY = re.compile(r"[0-9a-f]{64}")
_REFRESHABLE_LOCATOR_FIELDS = frozenset(
    {
        "ctime_ns",
        "end_line",
        "line",
        "locator",
        "mtime_ns",
        "scan_index",
        "start_line",
    }
)


def json_identity(value: Any) -> str:
    """Return the canonical SHA-256 identity of one JSON-compatible value."""

    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def semantic_projection(value: Any) -> Any:
    """Remove refreshable source locators from a compatibility projection."""

    if isinstance(value, Mapping):
        return {
            key: semantic_projection(item)
            for key, item in value.items()
            if key not in _REFRESHABLE_LOCATOR_FIELDS
        }
    if isinstance(value, list):
        return [semantic_projection(item) for item in value]
    if isinstance(value, tuple):
        return [semantic_projection(item) for item in value]
    return value


def normalized_command(value: str) -> str:
    """Return a stable shell-token projection without source locators."""

    try:
        return " ".join(shlex.split(value))
    except ValueError:
        return " ".join(value.split())


def recorded_invocation_identity(
    entry_id: str,
    section: str,
    command: str,
    duplicate_ordinal: int,
) -> str:
    """Identify a command by semantic section, normalized text, and duplicate."""

    normalized_section = " ".join(section.split()).casefold()
    section_identity = hashlib.sha256(normalized_section.encode("utf-8")).hexdigest()
    command_identity = hashlib.sha256(
        normalized_command(command).encode("utf-8")
    ).hexdigest()
    return (
        f"{entry_id}:{section_identity[:16]}:{command_identity[:16]}:"
        f"{duplicate_ordinal}"
    )


def invocation_identities(
    entry_id: str, commands: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Return native identities in command order for one entry."""

    counts: dict[tuple[str, str], int] = {}
    result = []
    for command in commands:
        section = str(command.get("section", ""))
        command_text = str(command.get("command", ""))
        group = (
            " ".join(section.split()).casefold(),
            normalized_command(command_text),
        )
        counts[group] = counts.get(group, 0) + 1
        result.append(
            recorded_invocation_identity(
                entry_id, section, command_text, counts[group]
            )
        )
    return result


def rule_dependencies_for_check(check: Mapping[str, Any]) -> dict[str, int]:
    """Return the conservative rule-component set governing one outcome."""

    name = str(check.get("check", ""))
    entry = str(check.get("entry", ""))
    if name == "Integrity":
        names = {"material_identity", "mechanical_inspection", "integrity"}
    elif name == "Provenance" and entry == "Summary":
        names = {
            "material_identity",
            "summary_provenance",
            "entry_provenance",
        }
    elif name == "Provenance":
        names = {
            "material_identity",
            "entry_provenance",
            "mechanical_producer",
            "reviewed_producer",
            "upstream_reviewed_producer",
        }
    elif name == "Reproducibility":
        names = {
            "material_identity",
            "mechanical_producer",
            "reviewed_producer",
            "upstream_reviewed_producer",
            "reproducibility",
        }
    else:
        names = set(COMPONENT_VERSIONS)
    return {name: COMPONENT_VERSIONS[name] for name in sorted(names)}


def orphan_rule_dependencies() -> dict[str, int]:
    """Return the conservative components governing orphan dispositions."""

    names = {
        "material_identity",
        "orphan_inventory",
        "orphan_graph",
        "orphan_semantic_adjudication",
        "mechanical_producer",
        "reviewed_producer",
        "upstream_reviewed_producer",
    }
    return {name: COMPONENT_VERSIONS[name] for name in sorted(names)}


def changed_components(
    stored: Mapping[str, Any], current: Mapping[str, int] = COMPONENT_VERSIONS
) -> tuple[list[str], list[str]]:
    """Return changed and unknown component names for one stored registry."""

    changed = sorted(
        name
        for name, version in current.items()
        if stored.get(name) != version and name in stored
    )
    unknown = sorted(set(stored) - set(current))
    changed.extend(sorted(set(current) - set(stored)))
    return sorted(set(changed)), unknown


def components_compatible(
    dependencies: Mapping[str, Any],
    current: Mapping[str, int] = COMPONENT_VERSIONS,
) -> tuple[bool, list[str]]:
    """Compare one outcome's declared component versions with the registry."""

    blockers = sorted(
        name
        for name, version in dependencies.items()
        if name not in current or current[name] != version
    )
    return not blockers, blockers


def outcome_compatibility_identity(
    rule_dependencies: Mapping[str, int],
    input_dependencies: Sequence[Mapping[str, Any]],
    producer_bindings: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Identify the complete native compatibility surface for one outcome."""

    return json_identity(
        {
            "rule_dependencies": dict(rule_dependencies),
            "input_dependencies": [dict(item) for item in input_dependencies],
            "producer_bindings": [dict(item) for item in producer_bindings],
        }
    )


def projection(
    kind: str,
    semantic_identity: str,
    value: Any,
    relationship: str,
    *,
    source_locator: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one exact typed input projection."""

    if kind not in INPUT_PROJECTION_VERSIONS:
        raise ValidationToolError(f"unknown input projection kind: {kind}")
    result: dict[str, Any] = {
        "kind": kind,
        "semantic_identity": semantic_identity,
        "projection_version": INPUT_PROJECTION_VERSIONS[kind],
        "content_identity": json_identity(semantic_projection(value)),
        "relationship": relationship,
    }
    if source_locator is not None:
        result["source_locator"] = dict(source_locator)
    return result


def _entry(scan: Mapping[str, Any], entry_id: str) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in scan.get("entries", [])
            if item.get("id") == entry_id and "error" not in item
        ),
        None,
    )


def _dependency_projection(
    scan: Mapping[str, Any], dependency: Mapping[str, Any]
) -> list[dict[str, Any]]:
    path = str(dependency.get("path", ""))
    role = str(dependency.get("role", "dependency"))
    identity = dependency.get("identity")
    if not isinstance(identity, Mapping):
        identity = (
            scan.get("files", {}).get(path)
            or scan.get("directory_memberships", {}).get(path)
            or {"missing": True}
        )
    members = dependency.get("members")
    if members is None and isinstance(identity, Mapping):
        members = identity.get("members")
    if isinstance(members, list):
        result = [
            projection(
                "collection-membership",
                f"collection-membership:{path}",
                sorted(members),
                role,
                source_locator={"path": path},
            )
        ]
        result.extend(
            projection(
                "collection-member",
                f"collection-member:{path}:{member}",
                {"collection_identity": identity, "member": member},
                role,
                source_locator={"path": path, "member": member},
            )
            for member in sorted(members)
        )
        return result
    kind = (
        "entry"
        if role in {"entry", "summary", "supporting-entry"}
        else "exact-material"
    )
    return [
        projection(
            kind,
            f"{kind}:{path}",
            identity,
            role,
            source_locator={"path": path},
        )
    ]


def _presented_value(item: Any) -> Any:
    if not isinstance(item, Mapping):
        return item
    return {
        key: value
        for key, value in semantic_projection(item).items()
        if key != "identity"
    }


def _association_value(item: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(item)
    if "presented_item" in value:
        value["presented_item"] = _presented_value(value["presented_item"])
    return value


def _summary_section_projections(
    scan: Mapping[str, Any], check: Mapping[str, Any]
) -> list[dict[str, Any]]:
    item = next(
        (
            row
            for row in scan.get("summary_items", [])
            if row.get("selector") == check.get("target")
        ),
        None,
    )
    result = (
        [
            projection(
                "presented-item",
                f"presented-item:Summary:{check.get('target', '')}",
                _presented_value(item),
                "outcome-subject",
                source_locator={
                    "path": scan.get("summary", ""),
                    "line": item.get("line") if isinstance(item, Mapping) else None,
                },
            )
        ]
        if item is not None
        else []
    )
    resolution = check.get("resolution")
    if not isinstance(resolution, Mapping):
        return result
    supporting = _entry(scan, str(resolution.get("entry", "")))
    if supporting is None:
        return result
    section = next(
        (
            row
            for row in supporting.get("sections", [])
            if row.get("section") == resolution.get("section")
        ),
        None,
    )
    if section is not None:
        result.append(
            projection(
                "experimental-section",
                "experimental-section:"
                f"{supporting.get('id', '')}:{section['semantic_identity']}",
                section["content_identity"],
                "summary-support",
                source_locator={
                    "path": supporting.get("path", ""),
                    "line": section.get("line"),
                    "end_line": section.get("end_line"),
                },
            )
        )
    return result


def _entry_section_projections(
    scan: Mapping[str, Any], check: Mapping[str, Any], entry_id: str
) -> list[dict[str, Any]]:
    entry = _entry(scan, entry_id)
    if entry is None:
        return []
    target = check.get("target")
    associations = [
        row
        for row in entry.get("evidence_record", {}).get("rows", [])
        if any(
            source.get("identity") == target
            for source in row.get("resolved_sources", [])
        )
    ]
    section_names = {str(row.get("section", "")) for row in associations}
    result = [
        projection(
            "experimental-section",
            f"experimental-section:{entry_id}:{section['semantic_identity']}",
            section["content_identity"],
            "owning-section",
            source_locator={
                "path": entry.get("path", ""),
                "line": section.get("line"),
                "end_line": section.get("end_line"),
            },
        )
        for section in entry.get("sections", [])
        if section.get("section") in section_names
    ]
    for ordinal, association in enumerate(associations, 1):
        association_scope = json_identity(
            {
                "entry": entry_id,
                "target": target,
                "section": association.get("section"),
                "kind": association.get("kind"),
                "ordinal": ordinal,
            }
        )
        result.append(
            projection(
                "evidence-association",
                f"evidence-association:{association_scope}",
                _association_value(association),
                "evidence-association",
                source_locator={
                    "path": entry.get("evidence_record", {}).get("identity", ""),
                    "line": association.get("line"),
                },
            )
        )
        presented = association.get("presented_item")
        if presented is not None:
            result.append(
                projection(
                    "presented-item",
                    f"presented-item:{association_scope}",
                    _presented_value(presented),
                    "presented-evidence",
                    source_locator={
                        "path": entry.get("path", ""),
                        "line": association.get("line"),
                    },
                )
            )
    return result


def _section_projections(
    scan: Mapping[str, Any], check: Mapping[str, Any]
) -> list[dict[str, Any]]:
    entry_id = str(check.get("entry", ""))
    if entry_id == "Summary":
        return _summary_section_projections(scan, check)
    return _entry_section_projections(scan, check, entry_id)


def input_dependencies_for_check(
    scan: Mapping[str, Any], check: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Project one outcome's explicit typed content and graph dependencies."""

    if check.get("target") == "Orphaned artifacts, scripts, and references":
        entry = _entry(scan, str(check.get("entry", "")))
        if entry is not None:
            return orphan_input_dependencies(
                scan, entry, list(entry.get("orphan_inventory", []))
            )
    result = [
        item
        for dependency in check.get("dependencies", [])
        if isinstance(dependency, Mapping)
        for item in _dependency_projection(scan, dependency)
    ]
    if check.get("check") == "Integrity":
        result = [item for item in result if item["kind"] != "entry"]
    scoped = (
        []
        if check.get("check") == "Integrity"
        else _section_projections(scan, check)
    )
    result.extend(scoped)
    if scoped:
        result = [item for item in result if item["kind"] != "entry"]
    unique = {
        (item["kind"], item["semantic_identity"], item["relationship"]): item
        for item in result
    }
    return sorted(
        unique.values(),
        key=lambda item: (
            item["kind"],
            item["semantic_identity"],
            item["relationship"],
        ),
    )


def producer_bindings_for_check(
    scan: Mapping[str, Any], check: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Project producer resolutions through the shared verifier."""

    from .producer_bindings import producer_bindings_for_check as verify

    return verify(cast(ScanRecord, scan), check)


def orphan_input_dependencies(
    scan: Mapping[str, Any],
    entry: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project item-level orphan inventory, notes, and resolver inputs."""

    entry_id = str(entry.get("id", ""))
    inventory = {
        str(item.get("identity", "")): item
        for item in entry.get("orphan_inventory", [])
    }
    result = [
        projection(
            "orphan-candidate",
            f"orphan-candidate:{entry_id}:{item.get('identity', '')}",
            inventory.get(str(item.get("identity", ""))),
            "orphan-inventory",
            source_locator={"path": str(item.get("identity", ""))},
        )
        for item in items
    ]
    result.extend(
        projection(
            "validation-note",
            f"validation-note:{entry_id}:{note.get('sha256', '')}",
            {
                "section": note.get("section"),
                "sha256": note.get("sha256"),
                "text": note.get("text"),
            },
            "orphan-retention-instruction",
            source_locator={
                "path": entry.get("path", ""),
                "line": note.get("line"),
            },
        )
        for note in entry.get("validation_notes", [])
    )
    unique = {
        (item["kind"], item["semantic_identity"], item["relationship"]): item
        for item in result
    }
    return sorted(
        unique.values(),
        key=lambda item: (
            item["kind"],
            item["semantic_identity"],
            item["projection_version"],
            item["relationship"],
        ),
    )


def decode_component_versions(value: Any, description: str) -> dict[str, int]:
    """Decode an exact nonempty component-version mapping."""

    if not isinstance(value, Mapping) or not value:
        raise ValidationToolError(f"{description} must be a nonempty object")
    decoded = dict(value)
    if any(
        not isinstance(name, str)
        or not name
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        for name, version in decoded.items()
    ):
        raise ValidationToolError(f"{description} is invalid")
    return decoded


def _decode_input_dependency(
    raw: Any, description: str, *, require_supported: bool
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    if not isinstance(raw, Mapping) or not {
            "kind",
            "semantic_identity",
            "projection_version",
            "content_identity",
            "relationship",
        } <= set(raw) <= {
            "kind",
            "semantic_identity",
            "projection_version",
            "content_identity",
            "relationship",
            "source_locator",
        }:
        raise ValidationToolError(f"{description} has incorrect fields")
    kind = raw["kind"]
    version = raw["projection_version"]
    if require_supported and kind not in INPUT_PROJECTION_VERSIONS:
        raise ValidationToolError(f"{description} kind is unsupported")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        raise ValidationToolError(f"{description} projection version is invalid")
    if require_supported and version != INPUT_PROJECTION_VERSIONS[kind]:
        raise ValidationToolError(f"{description} projection version is unsupported")
    if any(
        not isinstance(raw[field], str) or not raw[field]
        for field in ("semantic_identity", "relationship")
    ):
        raise ValidationToolError(f"{description} semantic fields are invalid")
    if (
        not isinstance(raw["content_identity"], str)
        or _HEX_IDENTITY.fullmatch(raw["content_identity"]) is None
    ):
        raise ValidationToolError(f"{description} content identity is invalid")
    locator = raw.get("source_locator")
    if locator is not None and not isinstance(locator, Mapping):
        raise ValidationToolError(f"{description} locator is invalid")
    return dict(raw), (
        kind,
        raw["semantic_identity"],
        version,
        raw["relationship"],
    )


def decode_input_dependencies(
    value: Any,
    description: str,
    *,
    require_supported: bool = True,
) -> list[dict[str, Any]]:
    """Decode a deterministic list of exact typed input projections."""

    if not isinstance(value, list):
        raise ValidationToolError(f"{description} must be a list")
    decoded = []
    seen = set()
    for index, raw in enumerate(value):
        item, key = _decode_input_dependency(
            raw,
            f"{description} item {index}",
            require_supported=require_supported,
        )
        if key in seen:
            raise ValidationToolError(f"{description} contains duplicate scopes")
        seen.add(key)
        decoded.append(item)
    expected = sorted(
        decoded,
        key=lambda item: (
            item["kind"],
            item["semantic_identity"],
            item["projection_version"],
            item["relationship"],
        ),
    )
    if decoded != expected:
        raise ValidationToolError(f"{description} is not deterministic")
    return decoded


def _validate_producer_binding_shape(value: Any, description: str) -> None:
    if not isinstance(value, Mapping) or not {
        "kind",
        "invocation_identity",
        "producer_basis",
        "coverage_identity",
        "direction_evidence",
    } <= set(value) <= {
        "kind",
        "invocation_identity",
        "producer_basis",
        "coverage_identity",
        "direction_evidence",
        "duplicate_count",
        "members",
        "target_member",
        "source_locator",
    }:
        raise ValidationToolError(f"{description} has incorrect fields")


def _validate_producer_binding_members(
    value: Mapping[str, Any], description: str
) -> None:
    members = value.get("members")
    scoped = value["kind"] == "scoped-collection"
    valid_members = (
        isinstance(members, list)
        and bool(members)
        and members == sorted(set(members))
        and all(isinstance(member, str) and member for member in members)
    )
    if members is not None and (not scoped or not valid_members):
        raise ValidationToolError(f"{description} members are invalid")
    if scoped and members is None:
        raise ValidationToolError(f"{description} requires collection members")
    target_member = value.get("target_member")
    if scoped and (
        not isinstance(target_member, str)
        or not target_member
        or not isinstance(members, list)
        or target_member not in members
    ):
        raise ValidationToolError(f"{description} target_member is invalid")
    if not scoped and target_member is not None:
        raise ValidationToolError(f"{description} target_member is invalid")


def _validate_producer_binding_direction(
    value: Mapping[str, Any], description: str
) -> None:
    direction = value["direction_evidence"]
    if direction not in {"mechanical-output-role", "reviewed-output-direction"}:
        raise ValidationToolError(f"{description} direction_evidence is invalid")
    if value["kind"] == "scoped-collection" and direction != (
        "reviewed-output-direction"
    ):
        raise ValidationToolError(f"{description} direction_evidence is invalid")
    if value["producer_basis"] == "mechanical" and direction != (
        "mechanical-output-role"
    ):
        raise ValidationToolError(f"{description} direction_evidence is invalid")


def decode_producer_binding(value: Any, description: str) -> dict[str, Any]:
    """Decode one exact producer-binding discriminated union."""

    _validate_producer_binding_shape(value, description)
    assert isinstance(value, Mapping)
    if value["kind"] not in PRODUCER_BINDING_KINDS:
        raise ValidationToolError(f"{description} kind is invalid")
    if value["producer_basis"] not in PRODUCER_BASES:
        raise ValidationToolError(f"{description} basis is invalid")
    for field in ("invocation_identity", "coverage_identity", "direction_evidence"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValidationToolError(f"{description} {field} is invalid")
    _validate_producer_binding_members(value, description)
    _validate_producer_binding_direction(value, description)
    duplicate_count = value.get("duplicate_count")
    if duplicate_count is not None and (
        not isinstance(duplicate_count, int)
        or isinstance(duplicate_count, bool)
        or duplicate_count < 1
    ):
        raise ValidationToolError(f"{description} duplicate_count is invalid")
    locator = value.get("source_locator")
    if locator is not None and not isinstance(locator, Mapping):
        raise ValidationToolError(f"{description} locator is invalid")
    return dict(value)

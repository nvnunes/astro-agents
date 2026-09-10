"""Primary repair ownership derived from recorded validation relationships.

This projection never changes provenance chains or admits a rejected command.
Ambiguous causes remain inspectable, individually reconciled fallback members.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .json_codec import canonical_json
from .mechanical_results import MechanicalGeneratedRecord

MAX_INSPECTION_MEMBERS = 50
_ENTRY = re.compile(r"(?:^|:)(e[0-9]+[a-z]?)(?::|$)", re.IGNORECASE)
_CONFLICT_CODES = {
    "producer.ambiguous",
    "lineage.ambiguous",
    "directory.producer.conflict",
    "collection.output_directory.shared",
    "directory.origin.conflict",
}
_MATERIAL_CODES = {
    "producer.missing",
    "lineage.missing",
    "provenance.output.reproduction_required",
    "provenance.output.missing",
}


def objects(value: Any) -> list[dict[str, Any]]:
    """Return mapping members of a bounded recorded sequence."""
    return (
        [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, (list, tuple))
        else []
    )


def finding_entry(finding: Mapping[str, Any]) -> str | None:
    """Resolve an explicitly recorded finding owner, never infer it from paths."""
    for value in (finding.get("identity"), finding.get("observed", {}).get("owner")):
        match = _ENTRY.search(value) if isinstance(value, str) else None
        if match:
            return match.group(1).lower()
    return None


def command_anchor(command: Mapping[str, Any]) -> dict[str, Any]:
    """Identify a command location independently of its changing arguments."""
    return {
        "kind": "command",
        **{key: command[key] for key in ("entry", "document", "fence", "ordinal")},
    }


def direct_findings(record: MechanicalGeneratedRecord) -> dict[str, dict[str, Any]]:
    """Index direct failures; dependent/not-applicable checks are not repair items."""
    return {
        check.identity: {
            "identity": check.identity,
            "scope": check.scope.value,
            "status": check.status.value,
            "dependencies": list(check.dependencies),
            **check.failure.as_dict(),
        }
        for check in record.checks
        if check.status.value in {"fail", "unavailable"} and check.failure is not None
    }


def _anchors(finding: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    observed = finding["observed"]
    rejected = objects(observed.get("rejected_commands"))
    if isinstance(observed.get("rejected_command"), dict):
        rejected.append(observed["rejected_command"])
    if rejected:
        return [("rejected_command", command_anchor(command)) for command in rejected]
    argument = observed.get("output_argument")
    if isinstance(argument, dict):
        return [("output_argument", dict(argument))]
    registration = observed.get("registration")
    if isinstance(registration, dict):
        return [
            (
                "exact_material",
                {"kind": "registration", **registration, "defect": finding["code"]},
            )
        ]
    code = finding["code"]
    material = observed.get("material", finding["subject"])
    conflict = code in _CONFLICT_CODES
    if code == "directory.producer.conflict":
        conflict = (
            len(
                set(observed.get("exact_producers", []))
                | set(observed.get("covering_producers", []))
                | set(observed.get("conflicts", []))
            )
            > 1
        )
    if code in _CONFLICT_CODES | _MATERIAL_CODES and isinstance(material, str):
        if PurePosixPath(material).is_absolute():
            reason = "competing_ownership" if conflict else "exact_material"
            defect = "ownership" if conflict else code
            return [(reason, {"kind": "material", "path": material, "defect": defect})]
    return []


def _batch(
    reason: str,
    anchors: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    extra_entries: Sequence[str] = (),
) -> dict[str, Any]:
    entries = sorted(
        {entry for finding in findings if (entry := finding_entry(finding)) is not None}
        | set(extra_entries)
    )
    body = {
        "batch_type": "chain" if reason == "command_chain" else "structural",
        "grouping_reason": reason,
        "scope": "entries" if entries else "log",
        "entries": entries,
        "anchors": sorted(anchors, key=canonical_json),
        "primary_finding_ids": sorted(finding["identity"] for finding in findings),
    }
    return {
        **body,
        "batch_id": "batch-"
        + hashlib.sha256(canonical_json(body).encode()).hexdigest(),
        "related_chain_ids": [],
        "related_batch_ids": [],
    }


def _provenance_index(
    chains: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    by_finding: dict[str, set[str]] = defaultdict(set)
    commands: dict[str, dict[str, Any]] = {}
    for chain in chains:
        for finding in objects(chain.get("findings")):
            by_finding[finding["identity"]].add(str(chain["chain_id"]))
        for command in objects(chain.get("commands")):
            commands[command["identity"]] = command
    return by_finding, commands


def _inspection_groups(
    fallback: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        _batch("inspection_group", [], members[start : start + MAX_INSPECTION_MEMBERS])
        for members in fallback.values()
        for start in range(0, len(members), MAX_INSPECTION_MEMBERS)
    ]


def build_repair_batches(
    record: MechanicalGeneratedRecord,
    chains: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Assign each direct finding once, using structural anchors before chains.

    Input is the existing scan's result and provenance projection. All membership
    is derived in memory; no source reads, command execution, or prose parsing.
    """
    findings = direct_findings(record)
    by_finding, commands = _provenance_index(chains)
    candidates = {
        identity: {
            canonical_json([reason, anchor]): (reason, anchor)
            for reason, anchor in _anchors(finding)
        }
        for identity, finding in findings.items()
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fallback: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for identity, finding in sorted(findings.items()):
        if len(candidates[identity]) == 1:
            grouped[next(iter(candidates[identity]))].append(finding)
        elif not candidates[identity] and len(by_finding[identity]) == 1:
            chain_groups[next(iter(by_finding[identity]))].append(finding)
        else:
            fallback[
                (finding_entry(finding) or "", finding["code"].split(".")[0])
            ].append(finding)
    batches = []
    anchor_batches: dict[str, str] = {}
    for key, members in sorted(grouped.items()):
        reason, anchor = candidates[members[0]["identity"]][key]
        entries = _related_entries(anchor, members, commands)
        batch = _batch(reason, [anchor], members, entries)
        anchor_batches[key] = batch["batch_id"]
        batches.append(batch)
    for chain in chains:
        members = chain_groups.get(str(chain["chain_id"]), [])
        if members:
            batches.append(
                _batch(
                    "command_chain",
                    [command_anchor(c) for c in objects(chain["commands"])],
                    members,
                    [str(chain["entry"])],
                )
            )
    batches.extend(_inspection_groups(fallback))
    for batch in batches:
        ids = batch["primary_finding_ids"]
        batch["related_chain_ids"] = sorted(
            {chain for identity in ids for chain in by_finding[identity]}
        )
        batch["related_batch_ids"] = sorted(
            {
                anchor_batches[key]
                for identity in ids
                for key in candidates[identity]
                if key in anchor_batches and anchor_batches[key] != batch["batch_id"]
            }
        )
    return sorted(batches, key=lambda batch: batch["batch_id"])


def _related_entries(
    anchor: dict[str, Any],
    members: list[dict[str, Any]],
    commands: dict[str, dict[str, Any]],
) -> list[str]:
    entries = {anchor["entry"]} if "entry" in anchor else set()
    for member in members:
        observed = member["observed"]
        owners = [observed.get("producer"), observed.get("consumer")]
        for field in (
            "producers",
            "exact_producers",
            "covering_producers",
            "conflicts",
        ):
            owners.extend(observed.get(field, []))
        for owner in owners:
            if isinstance(owner, str) and owner in commands:
                entries.add(commands[owner]["entry"])
    return sorted(entries)

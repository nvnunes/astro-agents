"""Validate the primary repair projection without reconstructing its grouping."""

from __future__ import annotations

import hashlib
from typing import Any

from .json_codec import canonical_json
from .mechanical_results import MechanicalGeneratedRecord
from .repair_batches import MAX_INSPECTION_MEMBERS, direct_findings

_BODY = {
    "batch_type",
    "grouping_reason",
    "scope",
    "entries",
    "anchors",
    "primary_finding_ids",
}
_FIELDS = _BODY | {"batch_id", "related_chain_ids", "related_batch_ids"}
_REASONS = {
    "command_chain",
    "rejected_command",
    "output_argument",
    "exact_material",
    "competing_ownership",
    "inspection_group",
}


def _sorted_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(v, str) and v for v in value)
        and value == sorted(set(value))
    )


def _valid_anchor(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("kind") == "registration":
        return (
            set(value) == {"kind", "entry", "name", "path", "defect"}
            and all(
                isinstance(value[k], str) and value[k]
                for k in ("entry", "name", "path", "defect")
            )
            and value["path"].startswith("/")
        )
    if value.get("kind") == "output_argument":
        command = {k: v for k, v in value.items() if k not in {"target", "path"}}
        command["kind"] = "command"
        return (
            _valid_anchor(command)
            and isinstance(value.get("target"), str)
            and bool(value["target"])
            and isinstance(value.get("path"), str)
            and value["path"].startswith("/")
        )
    if value.get("kind") == "command":
        return (
            set(value) == {"kind", "entry", "document", "fence", "ordinal"}
            and all(
                isinstance(value[k], str) and value[k] for k in ("entry", "document")
            )
            and all(
                type(value[k]) is int and value[k] >= 1 for k in ("fence", "ordinal")
            )
        )
    return (
        set(value) == {"kind", "path", "defect"}
        and value["kind"] == "material"
        and all(isinstance(value[k], str) and value[k] for k in ("path", "defect"))
        and value["path"].startswith("/")
    )


def _valid_batch(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        return False
    reason = value["grouping_reason"]
    if (
        not isinstance(reason, str)
        or reason not in _REASONS
        or value["batch_type"]
        != ("chain" if reason == "command_chain" else "structural")
    ):
        return False
    valid_scope = (
        all(
            _sorted_strings(value[k])
            for k in (
                "entries",
                "primary_finding_ids",
                "related_chain_ids",
                "related_batch_ids",
            )
        )
        and bool(value["primary_finding_ids"])
        and value["scope"] == ("entries" if value["entries"] else "log")
    )
    if not valid_scope:
        return False
    anchors = value["anchors"]
    if not isinstance(anchors, list) or not all(_valid_anchor(a) for a in anchors):
        return False
    canonical = [canonical_json(a) for a in anchors]
    if (
        canonical != sorted(set(canonical))
        or bool(anchors) == (reason == "inspection_group")
        or (
            reason == "inspection_group"
            and len(value["primary_finding_ids"]) > MAX_INSPECTION_MEMBERS
        )
    ):
        return False
    body = {key: value[key] for key in _BODY}
    return (
        value["batch_id"]
        == "batch-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()
    )


def valid_repair_projection(
    projection: dict[str, Any], record: MechanicalGeneratedRecord
) -> bool:
    """Require exact primary coverage, deterministic identities, and valid links."""
    batches = projection.get("repair_batches")
    if not isinstance(batches, list) or not all(_valid_batch(b) for b in batches):
        return False
    batch_ids = [b["batch_id"] for b in batches]
    if batch_ids != sorted(set(batch_ids)):
        return False
    primary = [identity for b in batches for identity in b["primary_finding_ids"]]
    if len(primary) != len(set(primary)) or set(primary) != set(
        direct_findings(record)
    ):
        return False
    chain_ids = {c["chain_id"] for c in projection["chains"]}
    batch_set = set(batch_ids)
    return all(
        set(b["related_chain_ids"]) <= chain_ids
        and set(b["related_batch_ids"]) <= batch_set - {b["batch_id"]}
        for b in batches
    )

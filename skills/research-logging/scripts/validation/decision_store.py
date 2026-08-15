"""Durable, content-addressed semantic judgments for validation reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, TypedDict, cast

from .compatibility import (
    COMPONENT_VERSIONS,
    INPUT_PROJECTION_VERSIONS,
    decode_component_versions,
    decode_input_dependencies,
    decode_producer_binding,
    orphan_rule_dependencies,
    projection,
)
from .contracts import ValidationToolError

DECISION_STORE_SCHEMA_VERSION = 2
_HEX_IDENTITY = re.compile(r"[0-9a-f]{64}")
_JUDGMENT_REQUIRED_FIELDS = {
    "identity",
    "provenance",
    "kind",
    "subject",
    "decision_input_fingerprint",
    "validation_rules_version",
    "result",
    "decision_date",
    "date_provenance",
    "rationale_provenance",
    "rule_dependencies",
    "input_dependencies",
}
_JUDGMENT_OPTIONAL_FIELDS = {"basis", "producer_bindings", "rationale"}


class ValidationDecisionStore(TypedDict):
    """Exact schema-2 durable semantic-judgment record."""

    schema_version: int
    validation_rules_version: str
    component_versions: dict[str, int]
    input_projection_versions: dict[str, int]
    local_snapshot_identity: str
    judgments: list[dict[str, Any]]


class NativeOrphanJudgmentInput(NamedTuple):
    """Complete persisted inputs for one reviewed orphan candidate."""

    entry: str
    identity: str
    fingerprint: str
    rationale: str
    classification: tuple[str, str | None]
    rules_version: str
    decision_date: str


class StoredOrphanJudgmentInput(NamedTuple):
    """Persisted orphan outcome and its native compatibility surface."""

    entry: str
    item: Mapping[str, Any]
    rules_version: str
    report_date: str
    rule_dependencies: Mapping[str, int]
    input_dependencies: Sequence[Mapping[str, Any]]


def _json_identity(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _judgment_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("identity", None)
    return _json_identity(payload)


def _decision_date(result: str, report_date: str) -> tuple[str, str]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", result):
        return result, "recorded"
    return report_date, "report-date-fallback"


def _completed_check_judgment(
    check: Mapping[str, Any], rules_version: str, report_date: str
) -> dict[str, Any] | None:
    """Extract one reusable semantic outcome when the state retained one."""

    fingerprint = check.get("compatibility_identity")
    if not isinstance(fingerprint, str) or _HEX_IDENTITY.fullmatch(fingerprint) is None:
        return None
    findings = check.get("findings")
    resolution = check.get("resolution")
    if not findings and not resolution:
        return None
    result = check.get("result")
    if not isinstance(result, str):
        return None
    decision_date, date_provenance = _decision_date(result, report_date)
    judgment: dict[str, Any] = {
        "provenance": "legacy-attested",
        "kind": "completed-check",
        "subject": {
            "entry": check.get("entry"),
            "target": check.get("target"),
            "check": check.get("check"),
        },
        "decision_input_fingerprint": fingerprint,
        "validation_rules_version": rules_version,
        "result": result,
        "decision_date": decision_date,
        "date_provenance": date_provenance,
        "rationale_provenance": (
            "recorded"
            if isinstance(findings, list) and findings
            else "unavailable-in-v43"
        ),
    }
    if isinstance(resolution, Mapping):
        judgment["basis"] = dict(resolution)
    if isinstance(findings, list) and findings:
        judgment["rationale"] = list(findings)
    judgment["rule_dependencies"] = dict(check["rule_dependencies"])
    judgment["input_dependencies"] = list(check["input_dependencies"])
    if check.get("producer_bindings"):
        judgment["producer_bindings"] = list(check["producer_bindings"])
    judgment["identity"] = _judgment_identity(judgment)
    return judgment


def _orphan_judgment(inputs: StoredOrphanJudgmentInput) -> dict[str, Any] | None:
    entry = inputs.entry
    item = inputs.item
    rules_version = inputs.rules_version
    report_date = inputs.report_date
    fingerprint = item.get("fingerprint")
    if not isinstance(fingerprint, str) or _HEX_IDENTITY.fullmatch(fingerprint) is None:
        return None
    result = item.get("decision")
    if not isinstance(result, str):
        return None
    judgment: dict[str, Any] = {
        "provenance": "legacy-attested",
        "kind": "orphan-disposition",
        "subject": {"entry": entry, "identity": item.get("identity")},
        "decision_input_fingerprint": fingerprint,
        "validation_rules_version": rules_version,
        "result": result,
        "decision_date": report_date,
        "date_provenance": "report-date-fallback",
        "rationale_provenance": "unavailable-in-v43",
    }
    basis = item.get("basis")
    if isinstance(basis, str) and basis != "-":
        judgment["basis"] = basis
    judgment["rule_dependencies"] = dict(inputs.rule_dependencies)
    subject_identity = str(item.get("identity", ""))
    judgment["input_dependencies"] = [
        dict(value)
        for value in inputs.input_dependencies
        if value.get("kind") == "validation-note"
        or value.get("semantic_identity")
        == f"orphan-candidate:{entry}:{subject_identity}"
    ]
    judgment["identity"] = _judgment_identity(judgment)
    return judgment


def build_decision_store(
    completed_checks: Sequence[Mapping[str, Any]],
    orphan_dispositions: Sequence[Mapping[str, Any]],
    *,
    validation_rules_version: str,
    local_snapshot_identity: str,
    report_date: str,
) -> ValidationDecisionStore:
    """Build the current reusable judgment set reachable from one report."""

    judgments = [
        judgment
        for check in completed_checks
        if (
            judgment := _completed_check_judgment(
                check, validation_rules_version, report_date
            )
        )
        is not None
    ]
    for disposition in orphan_dispositions:
        entry = disposition.get("entry")
        if not isinstance(entry, str):
            continue
        judgments.extend(
            judgment
            for item in disposition.get("items", [])
            if isinstance(item, Mapping)
            and (
                judgment := _orphan_judgment(
                    StoredOrphanJudgmentInput(
                        entry,
                        item,
                        validation_rules_version,
                        report_date,
                        disposition["rule_dependencies"],
                        disposition["input_dependencies"],
                    )
                )
            )
            is not None
        )
    judgments.sort(
        key=lambda judgment: (
            str(judgment["kind"]),
            json.dumps(
                judgment["subject"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            str(judgment["identity"]),
        )
    )
    return {
        "schema_version": DECISION_STORE_SCHEMA_VERSION,
        "validation_rules_version": validation_rules_version,
        "component_versions": dict(COMPONENT_VERSIONS),
        "input_projection_versions": dict(INPUT_PROJECTION_VERSIONS),
        "local_snapshot_identity": local_snapshot_identity,
        "judgments": judgments,
    }


def merge_native_orphan_batch_judgments(
    store: Mapping[str, Any] | None,
    actions: Sequence[Mapping[str, Any]],
    *,
    validation_rules_version: str,
    local_snapshot_identity: str,
    decision_date: str,
) -> tuple[ValidationDecisionStore, dict[str, int]]:
    """Merge explicit candidate-scoped batch judgments into the durable store."""

    if store is None:
        current: ValidationDecisionStore = {
            "schema_version": DECISION_STORE_SCHEMA_VERSION,
            "validation_rules_version": validation_rules_version,
            "component_versions": dict(COMPONENT_VERSIONS),
            "input_projection_versions": dict(INPUT_PROJECTION_VERSIONS),
            "local_snapshot_identity": local_snapshot_identity,
            "judgments": [],
        }
    else:
        current = decode_decision_store(store)
        if current["validation_rules_version"] != validation_rules_version:
            raise ValidationToolError(
                "decision store and orphan batch use different rules versions"
            )
    judgments = list(current["judgments"])
    compatible = {
        (
            judgment["kind"],
            json.dumps(judgment["subject"], sort_keys=True, separators=(",", ":")),
            judgment["decision_input_fingerprint"],
        ): judgment
        for judgment in judgments
    }
    merged = 0
    unchanged = 0
    for action in actions:
        if action.get("decision") != "orphan-batch":
            continue
        entry, fingerprints, rationales, classifications = _native_batch_inputs(action)
        for identity in sorted(fingerprints):
            judgment = _native_orphan_judgment(
                NativeOrphanJudgmentInput(
                    entry,
                    identity,
                    fingerprints[identity],
                    rationales[identity],
                    classifications[identity],
                    validation_rules_version,
                    decision_date,
                )
            )
            key = (
                judgment["kind"],
                json.dumps(
                    judgment["subject"], sort_keys=True, separators=(",", ":")
                ),
                judgment["decision_input_fingerprint"],
            )
            prior = compatible.get(key)
            if prior is not None:
                if prior != judgment:
                    raise ValidationToolError(
                        "durable orphan judgment conflicts with existing decision: "
                        f"{identity}"
                    )
                unchanged += 1
                continue
            judgments.append(judgment)
            compatible[key] = judgment
            merged += 1
    judgments.sort(
        key=lambda judgment: (
            str(judgment["kind"]),
            json.dumps(
                judgment["subject"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            str(judgment["identity"]),
        )
    )
    updated = dict(current)
    updated["judgments"] = judgments
    decoded = decode_decision_store(updated)
    return decoded, {"decision-store-merged": merged, "decision-store-noop": unchanged}


def _native_batch_inputs(
    action: Mapping[str, Any],
) -> tuple[
    str,
    Mapping[str, str],
    Mapping[str, str],
    dict[str, tuple[str, str | None]],
]:
    matcher = action.get("match")
    fingerprints = action.get("candidate_fingerprints")
    rationales = action.get("rationales")
    if (
        not isinstance(matcher, Mapping)
        or not isinstance(matcher.get("entry"), str)
        or not isinstance(fingerprints, Mapping)
        or not isinstance(rationales, Mapping)
        or set(rationales) != set(fingerprints)
    ):
        raise ValidationToolError(
            "durable orphan-batch merge requires one rationale per candidate"
        )
    classifications: dict[str, tuple[str, str | None]] = {
        identity: ("unresolved", None) for identity in action.get("unresolved", [])
    }
    classifications.update(
        {
            identity: ("accepted", "semantic-connection")
            for identity in action.get("connected", [])
        }
    )
    classifications.update(
        {
            item["identity"]: (
                "accepted",
                f"validation-note:{item['validation_note']}",
            )
            for item in action.get("retained", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("identity"), str)
            and isinstance(item.get("validation_note"), str)
        }
    )
    if set(classifications) != set(fingerprints):
        raise ValidationToolError(
            "durable orphan-batch merge requires a complete candidate partition"
        )
    return matcher["entry"], fingerprints, rationales, classifications


def _native_orphan_judgment(
    inputs: NativeOrphanJudgmentInput,
) -> dict[str, Any]:
    entry, identity, fingerprint, rationale = inputs[:4]
    classification, rules_version, decision_date = inputs[4:]
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValidationToolError(
            f"durable orphan-batch rationale is invalid: {identity}"
        )
    result, basis = classification
    judgment: dict[str, Any] = {
        "provenance": "native-reviewed",
        "kind": "orphan-disposition",
        "subject": {"entry": entry, "identity": identity},
        "decision_input_fingerprint": fingerprint,
        "validation_rules_version": rules_version,
        "result": result,
        "decision_date": decision_date,
        "date_provenance": "recorded",
        "rationale_provenance": "recorded",
        "rationale": [rationale.strip()],
        "rule_dependencies": orphan_rule_dependencies(),
        "input_dependencies": [
            projection(
                "orphan-candidate",
                f"orphan-candidate:{entry}:{identity}",
                fingerprint,
                "reviewed-candidate",
            )
        ],
    }
    if basis is not None:
        judgment["basis"] = basis
    judgment["identity"] = _judgment_identity(judgment)
    return judgment


def _require_text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationToolError(f"{description} must be nonempty text")
    return value


def _decode_judgment(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationToolError(f"decision judgment {index} must be an object")
    if not _JUDGMENT_REQUIRED_FIELDS <= set(value) <= (
        _JUDGMENT_REQUIRED_FIELDS | _JUDGMENT_OPTIONAL_FIELDS
    ):
        raise ValidationToolError(f"decision judgment {index} has incorrect fields")
    _decode_judgment_identity(value, index)
    _decode_judgment_subject(value, index)
    _decode_judgment_date(value, index)
    _decode_judgment_rationale(value, index)
    decode_component_versions(
        value["rule_dependencies"],
        f"decision judgment {index} rule_dependencies",
    )
    decode_input_dependencies(
        value["input_dependencies"],
        f"decision judgment {index} input_dependencies",
        require_supported=False,
    )
    bindings = value.get("producer_bindings", [])
    if not isinstance(bindings, list):
        raise ValidationToolError(
            f"decision judgment {index} producer_bindings must be a list"
        )
    for binding_index, binding in enumerate(bindings):
        decode_producer_binding(
            binding,
            f"decision judgment {index} producer binding {binding_index}",
        )
    return dict(value)


def _decode_judgment_identity(value: Mapping[str, Any], index: int) -> None:
    identity = _require_text(value["identity"], f"decision judgment {index} identity")
    if (
        _HEX_IDENTITY.fullmatch(identity) is None
        or identity != _judgment_identity(value)
    ):
        raise ValidationToolError(f"decision judgment {index} identity is invalid")


def _decode_judgment_subject(value: Mapping[str, Any], index: int) -> None:
    if value["provenance"] not in {"native-reviewed", "legacy-attested"}:
        raise ValidationToolError(f"decision judgment {index} provenance is invalid")
    if value["kind"] not in {"completed-check", "orphan-disposition"}:
        raise ValidationToolError(f"decision judgment {index} kind is invalid")
    subject = value["subject"]
    if not isinstance(subject, Mapping) or not subject or not all(
        isinstance(key, str) and isinstance(item, str) and item
        for key, item in subject.items()
    ):
        raise ValidationToolError(f"decision judgment {index} subject is invalid")
    fingerprint = _require_text(
        value["decision_input_fingerprint"],
        f"decision judgment {index} fingerprint",
    )
    if _HEX_IDENTITY.fullmatch(fingerprint) is None:
        raise ValidationToolError(f"decision judgment {index} fingerprint is invalid")
    if not isinstance(value["validation_rules_version"], str) or not value[
        "validation_rules_version"
    ]:
        raise ValidationToolError(f"decision judgment {index} rules version is invalid")


def _decode_judgment_date(value: Mapping[str, Any], index: int) -> None:
    _require_text(value["result"], f"decision judgment {index} result")
    _require_text(value["decision_date"], f"decision judgment {index} decision_date")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["decision_date"]) is None:
        raise ValidationToolError(f"decision judgment {index} date is invalid")
    if value["date_provenance"] not in {"recorded", "report-date-fallback"}:
        raise ValidationToolError(
            f"decision judgment {index} date provenance is invalid"
        )


def _decode_judgment_rationale(value: Mapping[str, Any], index: int) -> None:
    if value["rationale_provenance"] not in {
        "recorded",
        "unavailable-in-v43",
    }:
        raise ValidationToolError(
            f"decision judgment {index} rationale provenance is invalid"
        )
    rationale = value.get("rationale")
    if value["rationale_provenance"] == "recorded":
        if not isinstance(rationale, list) or not rationale or not all(
            isinstance(item, str) and item for item in rationale
        ):
            raise ValidationToolError(
                f"decision judgment {index} recorded rationale is invalid"
            )
    elif rationale is not None:
        raise ValidationToolError(
            f"decision judgment {index} manufactures unavailable rationale"
        )


def decode_decision_store(value: Any) -> ValidationDecisionStore:
    """Decode one exact schema-2 semantic-judgment store."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "validation_rules_version",
        "component_versions",
        "input_projection_versions",
        "local_snapshot_identity",
        "judgments",
    }:
        raise ValidationToolError("validation decision store has incorrect fields")
    if value["schema_version"] != DECISION_STORE_SCHEMA_VERSION:
        raise ValidationToolError("unsupported validation decision store schema")
    _require_text(value["validation_rules_version"], "decision store rules version")
    decode_component_versions(value["component_versions"], "decision components")
    decode_component_versions(
        value["input_projection_versions"], "decision input projections"
    )
    snapshot = _require_text(
        value["local_snapshot_identity"], "decision store local snapshot identity"
    )
    if _HEX_IDENTITY.fullmatch(snapshot) is None:
        raise ValidationToolError("decision store local snapshot identity is invalid")
    raw_judgments = value["judgments"]
    if not isinstance(raw_judgments, list):
        raise ValidationToolError("decision store judgments must be a list")
    judgments = [
        _decode_judgment(judgment, index)
        for index, judgment in enumerate(raw_judgments)
    ]
    if len({judgment["identity"] for judgment in judgments}) != len(judgments):
        raise ValidationToolError("decision store contains duplicate judgments")
    expected = sorted(
        judgments,
        key=lambda judgment: (
            str(judgment["kind"]),
            json.dumps(
                judgment["subject"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            str(judgment["identity"]),
        ),
    )
    if judgments != expected:
        raise ValidationToolError("decision store judgments are not deterministic")
    return cast(ValidationDecisionStore, dict(value))

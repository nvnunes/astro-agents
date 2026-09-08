"""Indexed correspondence and positive postconditions for original repair members."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from validation.inspection_store import encode
from validation.repair_batches import command_anchor, finding_entry, objects


def _artifact_targets(value: dict[str, Any]) -> set[str]:
    return {
        path
        for dependency in objects(value.get("dependencies"))
        for path in dependency.get("artifacts", [])
        if isinstance(path, str)
    }


def _covered_target(original: dict[str, Any], check: dict[str, Any]) -> bool:
    if not _artifact_targets(original) <= _artifact_targets(check):
        return False
    material = original["observed"].get("material", original["subject"])
    if original["scope"] != "provenance" or not str(material).startswith("/"):
        return True
    evaluated = {
        path
        for dependency in objects(check.get("dependencies"))
        for path in dependency.get("evaluated_materials", [])
    }
    return material in evaluated


def anchor_key(anchor: dict[str, Any]) -> str:
    """Match an underlying target across changed diagnostic classifications."""
    return encode({key: value for key, value in anchor.items() if key != "defect"})


def _subject_key(finding: dict[str, Any]) -> tuple[str, ...]:
    registration = finding["observed"].get("registration")
    if isinstance(registration, dict):
        return ("registration", registration["entry"], registration["name"])
    material = finding["observed"].get("material", finding["subject"])
    if finding["scope"] == "provenance" and str(material).startswith("/"):
        return ("material", material)
    return (finding["scope"], finding_entry(finding) or "", finding["subject"])


class ReconciliationIndex:
    """Index one fresh evaluation once, then reconcile each original finding.

    Missing correspondence is incomplete. A pass must cover the original
    evidence target; shifted positional finding IDs never establish clearance.
    """

    def __init__(
        self,
        record: dict[str, Any],
        projection: dict[str, Any],
        findings: dict[str, dict[str, Any]],
        verified_inputs: tuple[dict[str, str], ...] = (),
    ):
        self.checks = {check["identity"]: check for check in record["checks"]}
        self.output_arguments = {
            anchor_key(dependency["output_argument"]): check
            for check in record["checks"]
            for dependency in objects(check.get("dependencies"))
            if isinstance(dependency.get("output_argument"), dict)
        }
        self.parents: dict[str, set[str]] = defaultdict(set)
        self.subjects: dict[tuple[str, ...], set[str]] = defaultdict(set)
        self.commands: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.chains: dict[str, dict[str, Any]] = {}
        self.chain_targets: dict[str, set[str]] = defaultdict(set)
        self.findings = findings
        self.verified_inputs = {(r["entry"], r["name"]): r for r in verified_inputs}
        for identity, finding in findings.items():
            self.parents[identity.split(":finding:")[0]].add(identity)
            self.subjects[_subject_key(finding)].add(identity)
        for chain in objects(projection["chains"]):
            self.chains[chain["chain_id"]] = chain
            for command in objects(chain["commands"]):
                key = anchor_key(command_anchor(command))
                self.commands[key].append(command)
                self.chain_targets[key].add(chain["chain_id"])
                for field in ("inputs", "outputs"):
                    for relationship in objects(command[field]):
                        material = {"kind": "material", "path": relationship["path"]}
                        self.chain_targets[anchor_key(material)].add(chain["chain_id"])

    def current_chains(self, anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return reached provenance membership even when no repair findings remain."""
        anchors = [
            {
                "kind": "material",
                "path": self.verified_inputs.get((a["entry"], a["name"]), a)["path"],
            }
            if a["kind"] == "registration"
            else a
            for a in anchors
        ]
        identities = {
            identity
            for anchor in anchors
            for identity in self.chain_targets[
                anchor_key(
                    command_anchor(anchor)
                    if anchor["kind"] == "output_argument"
                    else anchor
                )
            ]
        }
        return [
            {
                "chain_id": identity,
                "entry": self.chains[identity]["entry"],
                "commands": [c["identity"] for c in self.chains[identity]["commands"]],
            }
            for identity in sorted(identities)
        ]

    def reconcile(self, original: dict[str, Any]) -> dict[str, Any]:
        """Return independent member status and the observed supporting identity."""
        identity = original["identity"]
        parent = identity.split(":finding:")[0]
        result = {
            "finding_id": identity,
            "status": "incomplete",
            "reason": "correspondence_unresolved",
        }
        argument = original["observed"].get("output_argument")
        if isinstance(argument, dict):
            return self._output_argument_result(argument, result)
        registration = original["observed"].get("registration")
        related = self.subjects[_subject_key(original)]
        if not isinstance(registration, dict):
            related = related | self.parents[parent]
        if related:
            unavailable = any(
                self.findings[i]["status"] == "unavailable" for i in related
            )
            return {
                **result,
                "status": "incomplete" if unavailable else "remaining",
                "reason": "rule_unavailable" if unavailable else None,
                "current_finding_ids": sorted(related),
            }
        check = self.checks.get(parent)
        if isinstance(registration, dict):
            verified = self.verified_inputs.get(
                (registration["entry"], registration["name"])
            )
            return (
                {
                    **result,
                    "status": "clear",
                    "reason": None,
                    "relationship": {"verified_registration": verified},
                }
                if verified
                else result
            )
        if "rejected_command" in original["observed"]:
            evidence = self._admitted_command(original)
            return (
                {**result, "status": "clear", "reason": None, "relationship": evidence}
                if evidence
                else result
            )
        if check and check["status"] == "pass" and _covered_target(original, check):
            return {**result, "status": "clear", "reason": None, "check_id": parent}
        return result

    def _output_argument_result(
        self, argument: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        """Require the same command selector and output path in a fresh check."""
        check = self.output_arguments.get(anchor_key(argument))
        if check is None:
            return result
        status = {"pass": "clear", "fail": "remaining"}.get(
            check["status"], "incomplete"
        )
        return {
            **result,
            "status": status,
            "reason": None if status != "incomplete" else "rule_unavailable",
            "check_id": check["identity"],
            "current_finding_ids": [check["identity"]]
            if check["identity"] in self.findings
            else [],
        }

    def _admitted_command(self, original: dict[str, Any]) -> dict[str, Any] | None:
        rejected = original["observed"].get("rejected_command")
        if not isinstance(rejected, dict):
            return None
        matches = [
            c
            for c in self.commands[anchor_key(command_anchor(rejected))]
            if c.get("script") == rejected.get("script")
        ]
        expected = {o["path"] for o in objects(rejected.get("declared_outputs"))}
        if len(matches) != 1 or not expected <= {
            o["path"] for o in objects(matches[0].get("outputs"))
        }:
            return None
        return {
            "admitted_command": matches[0]["identity"],
            "declared_outputs": sorted(expected),
        }

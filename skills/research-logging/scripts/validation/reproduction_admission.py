"""Validator-owned refinement of source-repair admission for one execution."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Mapping, Sequence


def repair_verification_exempt_findings(
    chain: Mapping[str, object],
    *,
    outputs: set[str],
    retained_inputs: set[str],
) -> frozenset[str]:
    """Prove which code-only findings cannot affect the selected execution.

    Keep the published chain intact. Follow material relationships backwards,
    including collection roots/members and additional recorded inputs. Missing
    or ambiguous ownership yields no exemption; ordinary admission is unchanged.
    """

    try:
        commands = _commands(chain)
        producers = {
            identity: _materials(command, "output")
            for identity, command in commands.items()
        }
        consumers = {
            identity: _materials(command, "input")
            for identity, command in commands.items()
        }
        selected = [
            identity for identity, paths in producers.items() if outputs <= paths
        ]
        if len(selected) != 1 or not outputs:
            return frozenset()
        required = _required_commands(
            producers, consumers, selected[0], retained_inputs
        )
        return frozenset(
            str(finding["identity"])
            for finding in _mappings(chain["findings"])
            if _independent_code_finding(finding, producers, required)
        )
    except (KeyError, TypeError, ValueError):
        return frozenset()


def _commands(chain: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    values = _mappings(chain["commands"])
    commands = {str(value["identity"]): value for value in values}
    if len(commands) != len(values):
        raise ValueError("ambiguous command identity")
    return commands


def _materials(command: Mapping[str, object], direction: str) -> set[str]:
    paths = {str(value["path"]) for value in _mappings(command[direction + "s"])}
    for collection in _mappings(command["collections"]):
        if collection["direction"] == direction:
            members = collection["members"]
            if not isinstance(members, list) or not all(
                isinstance(path, str) for path in members
            ):
                raise ValueError("invalid collection members")
            paths.update(members)
            if collection["root"] is not None:
                paths.add(str(collection["root"]))
    if any(
        not PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
        for path in paths
    ):
        raise ValueError("unresolved material path")
    return paths


def _mappings(value: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise ValueError("invalid projected mappings")
    return value


def _required_commands(
    producers: Mapping[str, set[str]],
    consumers: Mapping[str, set[str]],
    selected: str,
    retained_inputs: set[str],
) -> set[str]:
    """Walk each required material once, conservatively including directory overlap."""

    by_path: dict[str, set[str]] = defaultdict(set)
    for identity, paths in producers.items():
        for path in paths:
            by_path[path].add(identity)
    ordered = sorted(by_path)
    required = {selected}
    pending = list(consumers[selected] | retained_inputs)
    visited: set[str] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for identity in _material_owners(path, by_path, ordered) - required:
            required.add(identity)
            pending.extend(consumers[identity])
    return required


def _material_owners(
    path: str, by_path: Mapping[str, set[str]], ordered: Sequence[str]
) -> set[str]:
    owners = set(by_path.get(path, ()))
    for parent in PurePosixPath(path).parents:
        owners.update(by_path.get(str(parent), ()))
    prefix = path.rstrip("/") + "/"
    index = bisect_left(ordered, prefix)
    while index < len(ordered) and ordered[index].startswith(prefix):
        owners.update(by_path[ordered[index]])
        index += 1
    return owners


def _independent_code_finding(
    finding: Mapping[str, object],
    producers: Mapping[str, set[str]],
    required: set[str],
) -> bool:
    observed = finding.get("observed")
    if (
        finding.get("admission_effect") != "chain"
        or finding.get("status") != "fail"
        or finding.get("scope") != "provenance"
        or finding.get("code") != "provenance.output.signature_mismatch"
        or not isinstance(observed, Mapping)
        or observed.get("fields") != ["code"]
    ):
        return False
    owners = [
        identity
        for identity, paths in producers.items()
        if finding.get("subject") in paths
    ]
    return (
        len(owners) == 1
        and owners[0] == observed.get("producer")
        and owners[0] not in required
    )

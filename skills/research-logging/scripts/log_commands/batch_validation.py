"""Lock-free ephemeral validation of one projected repair batch."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from validation.batch_projection import build_batch_projection
from validation.controller import evaluate_entry_record
from validation.filesystem import BoundedTraversalError, bounded_descendants
from validation.json_codec import canonical_json
from validation.mechanical_results import CompletionState

from .context import (
    EntryContext,
    LogContext,
    parse_entry_document_name,
    resolve_entry,
)
from .findings import batch_findings, load_batch_projection
from .model import ActionError

BATCH_VALIDATION_SCHEMA = "research-log-batch-validation/1"
MAX_SNAPSHOT_PATHS = 1_000_000


def validate_batch(
    log: LogContext, *, projection_id: str, entry: str, chain_id: str
) -> tuple[dict[str, object], bool]:
    """Evaluate one current projected chain and return result plus completeness."""

    published = batch_findings(
        log, projection_id=projection_id, entry=entry, chain_id=chain_id
    )
    published_projection = load_batch_projection(log)
    old = published["batch"]
    assert isinstance(old, Mapping)
    if "commands" not in old:
        return _incomplete(published, old, reason="membership_unresolvable"), False
    context = _resolve_entry_root(log, entry)
    for attempt in range(2):
        before = _source_snapshot(context, old)
        evaluation = evaluate_entry_record(
            log.summary,
            result_date=str(published["result_date"]),
            entry_id=entry,
        )
        projection = build_batch_projection(
            evaluation.result,
            invocations=evaluation.scan["invocations"],
            registries=evaluation.scan["registries"],
            source_identity=hashlib.sha256(
                canonical_json(before).encode("utf-8")
            ).hexdigest(),
        )
        current = _current_groups(old, projection)
        after = _source_snapshot(context, old)
        if before != after:
            if attempt == 0:
                continue
            return _incomplete(published, old, reason="source_changed"), False
        if evaluation.result.completion is CompletionState.INCOMPLETE:
            return _incomplete(published, old, reason="evaluation_incomplete"), False
        if not current:
            return _incomplete(published, old, reason="membership_unresolvable"), False
        malformed = _anchor_findings(old, projection)
        if malformed:
            return _incomplete(
                published,
                old,
                reason="membership_unresolvable",
                findings=malformed,
            ), False
        findings = _unique_findings(
            finding for group in current for finding in _finding_sequence(group)
        )
        findings = _unique_findings(
            [*findings, *_related_unresolved(old, current, projection)]
        )
        overlaps = _pending_overlaps(old, current, published_projection)
        result: dict[str, object] = {
            "current_membership": [
                {
                    "artifacts": group.get("artifacts", []),
                    "chain_id": group["chain_id"],
                    "commands": group.get("commands", []),
                    "entry": group["entry"],
                }
                for group in current
            ],
            "evaluated_checks": len(evaluation.result.checks),
            "findings": findings,
            "pending_batch_overlaps": overlaps,
            "projection_id": projection_id,
            "schema": BATCH_VALIDATION_SCHEMA,
            "status": "complete_findings" if findings else "complete_clear",
        }
        return result, True
    raise AssertionError("bounded retry loop did not return")


def _resolve_entry_root(log: LogContext, entry: str) -> EntryContext:
    """Resolve one batch document ID to its owning stable entry directory."""

    identity = parse_entry_document_name(f"{entry}.md")
    if identity is None:
        raise ActionError("entry.id.invalid", f"invalid entry ID: {entry}")
    return resolve_entry(log, identity.id)


def _current_groups(
    old: Mapping[str, object], projection: Mapping[str, object]
) -> list[Mapping[str, object]]:
    old_anchors = _anchors(old)
    chains = [
        value
        for value in _sequence_items(projection.get("chains"))
        if isinstance(value, Mapping) and value.get("entry") == old.get("entry")
    ]
    anchored = [value for value in chains if _anchors(value) & old_anchors]
    if anchored:
        return anchored
    old_outputs = _outputs(old)
    if not old_outputs:
        return []
    return [value for value in chains if _outputs(value) & old_outputs]


def _anchors(value: Mapping[str, object]) -> set[tuple[str, int, int]]:
    result: set[tuple[str, int, int]] = set()
    for command in _mapping_items(value.get("commands")):
        if not isinstance(command, Mapping):
            continue
        document = command.get("document")
        fence = command.get("fence")
        ordinal = command.get("ordinal")
        if (
            isinstance(document, str)
            and isinstance(fence, int)
            and isinstance(ordinal, int)
        ):
            result.add((document, fence, ordinal))
    return result


def _outputs(value: Mapping[str, object]) -> set[str]:
    return {
        str(relationship["path"])
        for command in _mapping_items(value.get("commands"))
        for relationship in _mapping_items(command.get("outputs"))
        if "path" in relationship
    }


def _finding_sequence(value: Mapping[str, object]) -> Sequence[object]:
    findings = value.get("findings", [])
    return findings if isinstance(findings, list) else []


def _anchor_findings(
    old: Mapping[str, object], projection: Mapping[str, object]
) -> list[dict[str, object]]:
    aliases = {
        f"entry:{old['entry']}:command:{fence}:{ordinal}"
        for _document, fence, ordinal in _anchors(old)
    }
    return _unique_findings(
        finding
        for group in _sequence_items(projection.get("unresolved"))
        if isinstance(group, Mapping) and group.get("entry") == old.get("entry")
        for finding in _finding_sequence(group)
        if isinstance(finding, Mapping) and finding.get("identity") in aliases
    )


def _related_unresolved(
    old: Mapping[str, object],
    current: Sequence[Mapping[str, object]],
    projection: Mapping[str, object],
) -> list[dict[str, object]]:
    context = _group_context(old)
    for group in current:
        context.update(_group_context(group))
    return _unique_findings(
        finding
        for group in _sequence_items(projection.get("unresolved"))
        if isinstance(group, Mapping) and group.get("entry") == old.get("entry")
        for finding in _finding_sequence(group)
        if isinstance(finding, Mapping) and _finding_context(finding) & context
    )


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {item for child in value.values() for item in _strings(child)}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return {item for child in value for item in _strings(child)}
    return set()


def _group_context(group: Mapping[str, object]) -> set[str]:
    result = {str(value) for value in _sequence_items(group.get("artifacts"))}
    for record in _mapping_items(group.get("registry")):
        result.update(
            str(record[key])
            for key in ("location", "name", "path")
            if isinstance(record.get(key), str)
        )
    for command in _mapping_items(group.get("commands")):
        for key in ("document", "identity"):
            if isinstance(command.get(key), str):
                result.add(str(command[key]))
        entry = command.get("entry")
        fence = command.get("fence")
        ordinal = command.get("ordinal")
        if (
            isinstance(entry, str)
            and isinstance(fence, int)
            and isinstance(ordinal, int)
        ):
            result.add(f"entry:{entry}:command:{fence}:{ordinal}")
        for family in ("inputs", "outputs"):
            for relationship in _mapping_items(command.get(family)):
                result.update(
                    str(relationship[key])
                    for key in ("artifact", "path")
                    if isinstance(relationship.get(key), str)
                )
    return result


def _finding_context(finding: Mapping[str, object]) -> set[str]:
    result = {
        str(finding[key])
        for key in ("identity", "subject")
        if isinstance(finding.get(key), str)
    }
    result.update(_strings(finding.get("observed")))
    result.update(_strings(finding.get("dependencies")))
    return result


def _unique_findings(values: Iterable[object]) -> list[dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for value in values:
        if isinstance(value, Mapping) and isinstance(value.get("identity"), str):
            result[str(value["identity"])] = dict(value)
    return [result[key] for key in sorted(result)]


def _pending_overlaps(
    old: Mapping[str, object],
    current: Sequence[Mapping[str, object]],
    published: Mapping[str, object],
) -> list[dict[str, str]]:
    current_anchors = set().union(*(_anchors(value) for value in current))
    current_outputs = set().union(*(_outputs(value) for value in current))
    # The current projection is entry-scoped; compare against the originally
    # published sibling batches to identify repairs whose IDs need reconciliation.
    original_projection = published.get("chains")
    candidates = original_projection if isinstance(original_projection, list) else []
    result = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("chain_id") == old.get(
            "chain_id"
        ):
            continue
        if (
            _anchors(candidate) & current_anchors
            or _outputs(candidate) & current_outputs
        ):
            result.append(
                {
                    "chain_id": str(candidate["chain_id"]),
                    "entry": str(candidate["entry"]),
                }
            )
    return sorted(result, key=lambda value: (value["entry"], value["chain_id"]))


def _source_snapshot(
    entry: EntryContext, batch: Mapping[str, object]
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    paths = {entry.log.summary, entry.log.root / "validation" / "batches.json"}
    try:
        paths.update(
            bounded_descendants(entry.root, maximum_entries=MAX_SNAPSHOT_PATHS)
        )
    except BoundedTraversalError as error:
        raise ActionError("validate_batch.source.too_large", str(error)) from error
    for record in _mapping_items(batch.get("registry")):
        if not isinstance(record.get("path"), str):
            continue
        path = Path(str(record["path"]))
        paths.add(path)
        if record.get("kind") != "directory" or not path.is_dir():
            continue
        remaining = MAX_SNAPSHOT_PATHS - len(paths)
        if remaining <= 0:
            raise ActionError(
                "validate_batch.source.too_large",
                "batch source snapshot crossed its path bound",
            )
        try:
            paths.update(bounded_descendants(path, maximum_entries=remaining))
        except BoundedTraversalError as error:
            raise ActionError("validate_batch.source.too_large", str(error)) from error
    if len(paths) > MAX_SNAPSHOT_PATHS:
        raise ActionError(
            "validate_batch.source.too_large",
            "batch source snapshot crossed its path bound",
        )
    result = []
    for path in sorted(paths, key=lambda value: value.as_posix()):
        identity: tuple[int, ...]
        try:
            info = path.stat(follow_symlinks=False)
            identity = (
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_ino,
            )
        except OSError:
            identity = (-1,)
        result.append((path.as_posix(), identity))
    return tuple(result)


def _sequence_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence_items(value) if isinstance(item, Mapping))


def _incomplete(
    published: Mapping[str, object],
    old: Mapping[str, object],
    *,
    reason: str,
    findings: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    return {
        "current_membership": [],
        "findings": [dict(value) for value in findings],
        "pending_batch_overlaps": [],
        "projection_id": published["projection_id"],
        "requested_batch": {
            "chain_id": old["chain_id"],
            "entry": old["entry"],
        },
        "reason": reason,
        "schema": BATCH_VALIDATION_SCHEMA,
        "status": "incomplete",
    }

"""Verify one published primary repair batch without changing published state."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from validation.batch_projection import build_batch_projection
from validation.controller import evaluate_entries_record
from validation.filesystem import BoundedTraversalError, bounded_descendants
from validation.inspection import retain_result, timestamp
from validation.inspection_store import encode
from validation.repair_batches import finding_entry, objects

from .context import EntryContext, LogContext, parse_entry_document_name, resolve_entry
from .findings import load_batch_projection
from .model import ActionError
from .repair_reconciliation import ReconciliationIndex, anchor_key

MAX_SNAPSHOT_PATHS = 1_000_000


def _resolve_entry_root(log: LogContext, entry: str) -> EntryContext:
    """Resolve one batch document ID to its owning stable entry directory."""

    identity = parse_entry_document_name(f"{entry}.md")
    if identity is None:
        raise ActionError("entry.id.invalid", f"invalid entry ID: {entry}")
    return resolve_entry(log, identity.id)


def _findings(projection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f["identity"]: f
        for group in objects(projection.get("chains"))
        + objects(projection.get("unresolved"))
        for f in objects(group.get("findings"))
    }


def _snapshot(
    log: LogContext, entries: set[str], dependencies: set[str]
) -> tuple[Any, ...]:
    paths = {log.summary, log.root / "validation/batches.json"}
    roots = {_resolve_entry_root(log, entry).root for entry in entries}
    paths.update(Path(path) for path in dependencies)
    roots.update(path for path in paths if path.is_dir())
    try:
        for root in sorted(roots):
            if any(parent in roots for parent in root.parents):
                continue
            paths.add(root)
            paths.update(
                bounded_descendants(
                    root, maximum_entries=MAX_SNAPSHOT_PATHS - len(paths)
                )
            )
            if len(paths) > MAX_SNAPSHOT_PATHS:
                raise ActionError(
                    "validate_batch.source.too_large",
                    "batch source snapshot crossed its path bound",
                )
    except BoundedTraversalError as error:
        raise ActionError("validate_batch.source.too_large", str(error)) from error
    result = []
    for path in sorted(paths):
        identity: tuple[int, ...]
        try:
            stat = path.lstat()
            identity = (
                stat.st_mode,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                stat.st_ino,
            )
        except OSError:
            identity = (-1,)
        result.append((str(path), identity))
    return tuple(result)


def _dependencies(
    batch: dict[str, Any], projection: dict[str, Any]
) -> tuple[set[str], set[str]]:
    entries = set(batch["entries"])
    paths: set[str] = set()
    by_entry: dict[str, list[dict[str, Any]]] = {}
    for chain in objects(projection["chains"]):
        by_entry.setdefault(chain["entry"], []).extend(objects(chain.get("registry")))
    pending = list(entries)
    while pending:
        for record in by_entry.get(pending.pop(), []):
            if isinstance(record.get("path"), str):
                paths.add(record["path"])
            owner = record.get("from_entry")
            if isinstance(owner, str) and owner not in entries:
                entries.add(owner)
                pending.append(owner)
    return entries, paths


def _outcome(batch: dict[str, Any], validation_id: str) -> dict[str, Any]:
    return {
        "schema": "research-log-batch-validation/2",
        "validation_id": validation_id,
        "batch_id": batch["batch_id"],
        "requested_repair_batch": batch,
        "status": "incomplete",
        "reason": "evaluation_incomplete",
        "findings": [],
        "current_membership": [],
        "pending_batch_overlaps": [],
        "reconciliation": [],
        "coverage": {"entries": [], "dependencies": [], "rules": [], "missing": []},
    }


def _evaluate(
    log: LogContext,
    batch: dict[str, Any],
    published: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    outcome = _outcome(batch, published["validation_id"])
    observed: dict[str, Any] = {"checks": []}
    projection: dict[str, Any] = {"chains": [], "unresolved": [], "repair_batches": []}
    entries, dependencies = _dependencies(batch, published)
    if not entries or batch["scope"] == "log":
        outcome["reason"] = "full_evaluation_required"
        outcome["coverage"]["missing"] = ["log-scoped rules require full validation"]
        return outcome, observed, projection
    for _ in range(2):
        before = _snapshot(log, entries, dependencies)
        evaluation = evaluate_entries_record(
            log.summary,
            result_date=published["result_date"],
            entry_ids=frozenset(entries),
        )
        observed = evaluation.result.as_dict()
        projection = build_batch_projection(
            evaluation.result,
            invocations=evaluation.scan["invocations"],
            registries=evaluation.scan["registries"],
            source_identity=hashlib.sha256(encode(before).encode()).hexdigest(),
        )
        resources = [
            resource
            for _, data in evaluation.scan["registries"]
            for resource in data.inputs
        ]
        dependencies.update(resource.canonical_target for resource in resources)
        entries.update(
            resource.reference_entry
            for resource in resources
            if resource.reference_entry is not None
        )
        stable = before == _snapshot(log, entries, dependencies)
        if stable:
            break
    outcome = _reconciled(
        batch,
        published,
        observed,
        projection,
        (
            set(evaluation.scan["entries"]),
            dependencies,
            set(evaluation.scan["declared_entries"]),
            tuple(evaluation.scan["verified_inputs"]),
        ),
    )
    if not stable:
        outcome.update(status="incomplete", reason="source_changed")
        outcome["coverage"].update(rules=[], missing=list(batch["primary_finding_ids"]))
        for member in outcome["reconciliation"]:
            member.update(status="incomplete", reason="source_changed")
            member.pop("check_id", None)
            member.pop("relationship", None)
    return outcome, observed, projection


def _reconciled(
    batch: dict[str, Any],
    published: dict[str, Any],
    record: dict[str, Any],
    projection: dict[str, Any],
    covered: tuple[set[str], set[str], set[str], tuple[dict[str, str], ...]],
) -> dict[str, Any]:
    entries, dependencies, declared_entries, verified_inputs = covered
    outcome = _outcome(batch, published["validation_id"])
    original = _findings(published)
    current = _findings(projection)
    index = ReconciliationIndex(record, projection, current, verified_inputs)
    reconciled = [
        index.reconcile(original[identity]) for identity in batch["primary_finding_ids"]
    ]
    returned = {
        identity
        for item in reconciled
        for identity in item.get("current_finding_ids", [])
    }
    anchors = {anchor_key(a) for a in batch["anchors"]}
    current_chains = index.current_chains(batch["anchors"])
    chain_ids = {chain["chain_id"] for chain in current_chains}
    members = [
        b
        for b in objects(projection["repair_batches"])
        if returned.intersection(b["primary_finding_ids"])
        or anchors.intersection(anchor_key(a) for a in b["anchors"])
        or chain_ids.intersection(b["related_chain_ids"])
    ]
    returned.update(
        identity
        for member in members
        for identity in member["primary_finding_ids"]
        if identity not in original or identity in batch["primary_finding_ids"]
    )
    successors: dict[str, list[str]] = {}
    for batch_member in members:
        for identity in batch_member["primary_finding_ids"]:
            successors.setdefault(identity, []).append(batch_member["batch_id"])
    for member in reconciled:
        member["successor_batch_ids"] = sorted(
            {
                successor
                for identity in member.get("current_finding_ids", [])
                for successor in successors.get(identity, [])
            }
        )
        member["current_validation_id"] = projection["validation_id"]
    for member in reconciled:
        previous = original[member["finding_id"]]
        owner = finding_entry(previous)
        missing = owner is not None and owner not in entries
        needs_producers = (
            previous["code"].startswith(
                ("producer.", "lineage.", "directory.", "provenance.")
            )
            and "registration" not in previous["observed"]
        )
        if missing or (needs_producers and declared_entries - entries):
            member.update(
                status="incomplete",
                reason=(
                    "entry_not_evaluated"
                    if missing
                    else "producer_context_not_evaluated"
                ),
            )
    coverage = {
        "entries": sorted(entries),
        "required_entries": sorted(set(batch["entries"])),
        "dependencies": sorted(dependencies),
        "rules": sorted(
            {
                f["rule"]
                for r in reconciled
                if r["status"] != "incomplete"
                for f in [original[r["finding_id"]]]
            }
        ),
        "missing": [r["finding_id"] for r in reconciled if r["status"] == "incomplete"],
        "unavailable_checks": [
            check["identity"]
            for check in record["checks"]
            if check["status"] == "unavailable"
        ],
        "missing_producer_entries": sorted(declared_entries - entries)
        if any(r["reason"] == "producer_context_not_evaluated" for r in reconciled)
        else [],
    }
    complete = not coverage["missing"] and record["completion"] != "incomplete"
    outcome.update(
        status=("complete_findings" if returned else "complete_clear")
        if complete
        else "incomplete",
        reason=None if complete else "coverage_incomplete",
        coverage=coverage,
        reconciliation=reconciled,
        findings=[current[i] for i in sorted(returned)],
        current_membership=[
            {**m, "validation_id": projection["validation_id"]} for m in members
        ]
        + [{**c, "validation_id": projection["validation_id"]} for c in current_chains],
        pending_batch_overlaps=[
            {"batch_id": b["batch_id"], "validation_id": published["validation_id"]}
            for b in objects(published["repair_batches"])
            if b["batch_id"] != batch["batch_id"]
            and (
                returned.intersection(b["primary_finding_ids"])
                or chain_ids.intersection(b["related_chain_ids"])
                or anchors.intersection(anchor_key(a) for a in b["anchors"])
            )
        ],
    )
    return outcome


def _publication_identity(path: Path) -> str | None:
    """Pin publication changes without treating read access as a mutation."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return encode(
        [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    )


def validate_repair_batch(
    log: LogContext, *, validation_id: str, batch_id: str
) -> tuple[dict[str, Any], bool]:
    """Evaluate and retain one exact published primary request; never publish it."""
    path = log.root / "validation/batches.json"
    before = _publication_identity(path)
    published = load_batch_projection(log)
    stat = _publication_identity(path)
    if stat is None or before != stat:
        raise ActionError(
            "findings.validation_superseded", "publication changed during selection"
        )
    if published["validation_id"] != validation_id:
        raise ActionError(
            "findings.validation_superseded",
            "requested validation is no longer published",
        )
    matches = [
        b for b in objects(published.get("repair_batches")) if b["batch_id"] == batch_id
    ]
    if len(matches) != 1:
        raise ActionError(
            "findings.batch.unknown", "unknown repair batch in this publication"
        )
    batch = matches[0]
    request = {
        "kind": "batch",
        "validation": validation_id,
        "batch": batch_id,
        "entries": encode(batch["entries"]),
        "started_at": timestamp(),
        "published_stat": stat,
    }
    result, record, projection = _evaluate(log, batch, published)
    if _publication_identity(path) != stat:
        raise ActionError(
            "findings.validation_superseded", "publication changed during evaluation"
        )
    projection = _inspection_projection(projection, result)
    result["_inspection_id"] = retain_result(
        log.summary, result, record, projection, request
    )
    return result, result["status"] != "incomplete"


def _inspection_projection(
    projection: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Retain returned findings and relevant context, keeping all checks separately."""
    selected = {f["identity"] for f in result["findings"]}
    chains = {
        member["chain_id"]
        for member in result["current_membership"]
        if "chain_id" in member
    }
    scoped = {**projection}
    for family in ("chains", "unresolved"):
        groups = []
        for group in objects(projection[family]):
            findings = [
                f for f in objects(group["findings"]) if f["identity"] in selected
            ]
            if findings or group["chain_id"] in chains:
                groups.append({**group, "findings": findings})
        scoped[family] = groups
    return scoped

"""Narrow provenance refresh for matched reproduction confirmations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from research_log_data import (
    DataFile,
    Fingerprint,
    compose_directory_fingerprint,
    load_data_file,
    observe_directory_tree,
    observe_file_content,
)

from .commands import CommandContext, Invocation, discover_commands, order_invocations
from .errors import MechanicalContractError
from .evidence import index_entry_documents
from .fingerprint_cache import project_root
from .mechanical_results import (
    CheckScope,
    CheckStatus,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from .output_support import (
    confirmed_output_record,
    require_current_output_support,
    resolve_code_support,
    resolve_output_support,
)
from .provenance import (
    ProducerIndex,
    build_producer_index,
    evaluate_provenance,
    require_origin_boundary,
)
from .pyrun_state import (
    PyrunFile,
    execution_id,
    legacy_output_projection,
    load_pyrun_state,
    recipe_from_invocation,
    validated_pyrun_serialization,
)


class TargetedRefreshError(ValueError):
    """The approved narrow refresh could not establish coherent provenance."""


@dataclass(frozen=True)
class _EntryState:
    entry: str
    root: Path
    data: DataFile
    state: PyrunFile
    record_digest: str

    @property
    def owner(self) -> str:
        return self.root.relative_to(self.root.parent.parent).as_posix()


@dataclass(frozen=True)
class _RefreshState:
    project_root: Path
    invocations: tuple[Invocation, ...]
    producer_index: ProducerIndex
    entries: Mapping[str, _EntryState]
    owners: Mapping[str, _EntryState]


def refresh_confirmed_provenance(
    summary: Path,
    prior: MechanicalGeneratedRecord,
    candidate_states: Mapping[str, PyrunFile],
    changed_execution_ids: Mapping[str, frozenset[str]],
    *,
    result_date: str,
) -> MechanicalGeneratedRecord:
    """Refresh only direct unconfirmed checks reached by matched executions.

    This service intentionally does not perform general validation. It reuses
    the prior check inventory, discovers the current command graph, and replaces
    only Provenance checks whose sole blocker was one newly confirmed execution,
    plus summary-Provenance checks that depend directly on them.
    """

    summary = summary.resolve()
    if Path(prior.summary).resolve() != summary:
        raise TargetedRefreshError("validation summary identity changed")
    if set(candidate_states) != set(changed_execution_ids):
        raise TargetedRefreshError("candidate state and execution sets disagree")
    state = _load_refresh_state(summary, candidate_states)
    affected = _affected_checks(prior, state, changed_execution_ids)
    replacements: dict[str, MechanicalCheck] = {}
    for check in affected:
        replacements[check.identity] = _refresh_direct_check(check, state)
    direct_ids = set(replacements)
    for check in prior.checks:
        dependency = _summary_dependency(check)
        if dependency in direct_ids:
            replacements[check.identity] = MechanicalCheck(
                check.identity,
                CheckScope.PROVENANCE,
                CheckStatus.PASS,
                check.identity,
                ({"target": dependency},),
            )
    checks = tuple(replacements.get(check.identity, check) for check in prior.checks)
    return MechanicalGeneratedRecord.build(
        prior.summary, prior.rules_version, result_date, checks
    )


def _load_refresh_state(
    summary: Path, candidate_states: Mapping[str, PyrunFile]
) -> _RefreshState:
    try:
        summary_text = summary.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise TargetedRefreshError(str(error)) from error
    log_root = summary.with_suffix("").resolve()
    root = project_root(summary)
    entries: dict[str, _EntryState] = {}
    documents: list[tuple[Invocation, ...]] = []
    for target in index_entry_documents(summary_text):
        document = (summary.parent / target).resolve()
        entry = document.stem.lower()
        entry_root = document.parent
        try:
            data = load_data_file(entry_root / "data.json", entry_root=entry_root)
            text = document.read_text(encoding="utf-8")
            discovery = discover_commands(
                text,
                CommandContext(
                    log_id=log_root.as_posix(),
                    entry=entry,
                    document=document.relative_to(log_root).as_posix(),
                    entry_root=entry_root,
                    log_root=log_root,
                    project_root=root,
                    data_file=data,
                ),
            )
        except (OSError, UnicodeError, MechanicalContractError) as error:
            raise TargetedRefreshError(str(error)) from error
        if discovery.failures:
            first = discovery.failures[0]
            raise TargetedRefreshError(
                f"current command discovery failed: {first.error.code}"
            )
        documents.append(discovery.invocations)
        try:
            pyrun = candidate_states.get(entry) or load_pyrun_state(
                entry_root / "pyrun.json",
                entry_root=entry_root,
                project_root=root,
            )
            serialized = (
                validated_pyrun_serialization(pyrun, project_root=root)
                if entry in candidate_states
                else (entry_root / "pyrun.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, MechanicalContractError) as error:
            raise TargetedRefreshError(str(error)) from error
        entries[entry] = _EntryState(
            entry,
            entry_root,
            data,
            pyrun,
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )
    try:
        invocations = order_invocations(documents)
        producer_index = build_producer_index(invocations)
    except MechanicalContractError as error:
        raise TargetedRefreshError(str(error)) from error
    owners = {value.owner: value for value in entries.values()}
    return _RefreshState(root, invocations, producer_index, entries, owners)


def _affected_checks(
    prior: MechanicalGeneratedRecord,
    state: _RefreshState,
    changed_execution_ids: Mapping[str, frozenset[str]],
) -> tuple[MechanicalCheck, ...]:
    changed_producers: set[str] = set()
    for entry, identities in changed_execution_ids.items():
        selected = state.entries.get(entry)
        if selected is None or not identities <= set(selected.state.executions):
            raise TargetedRefreshError("changed execution identity is unavailable")
        for invocation in state.invocations:
            if invocation.entry != entry:
                continue
            try:
                recipe = recipe_from_invocation(
                    invocation,
                    entry_root=selected.root,
                    project_root=state.project_root,
                )
            except MechanicalContractError:
                continue
            if execution_id(recipe) in identities:
                changed_producers.add(invocation.identity)
    result: list[MechanicalCheck] = []
    for check in prior.checks:
        if (
            check.scope is not CheckScope.PROVENANCE
            or check.status is not CheckStatus.FAIL
            or check.failure is None
            or check.failure.code != "provenance.output.unconfirmed"
            or not check.identity.startswith("provenance:")
        ):
            continue
        producer = check.failure.observed.get("producer")
        if isinstance(producer, str) and producer in changed_producers:
            result.append(check)
    return tuple(result)


def _refresh_direct_check(
    check: MechanicalCheck, state: _RefreshState
) -> MechanicalCheck:
    if not check.dependencies:
        raise TargetedRefreshError(f"missing artifact dependency: {check.identity}")
    dependency = check.dependencies[0]
    raw_inputs = dependency.get("inputs")
    if not isinstance(raw_inputs, list):
        raise TargetedRefreshError(f"invalid artifact dependency: {check.identity}")
    entry_id = check.identity.split(":", 2)[1]
    entry = state.entries.get(entry_id)
    if entry is None:
        raise TargetedRefreshError(f"unknown entry in check: {check.identity}")
    dependencies: list[Mapping[str, object]] = [dependency]
    try:
        for raw in raw_inputs:
            if not isinstance(raw, Mapping):
                raise TargetedRefreshError(
                    f"invalid input dependency: {check.identity}"
                )
            name = raw.get("name")
            material = raw.get("path")
            if not isinstance(name, str) or not isinstance(material, str):
                raise TargetedRefreshError(
                    f"invalid input dependency: {check.identity}"
                )
            local_name = name.rsplit(":", 1)[-1]
            resource = entry.data.by_name.get(local_name)
            if resource is None:
                raise TargetedRefreshError(
                    f"missing declared input {name!r}: {check.identity}"
                )
            if resource.origin:
                require_origin_boundary(
                    material,
                    resource,
                    state.invocations,
                    confirmed_record=lambda invocation, output: _confirmed(
                        invocation, output, state
                    ),
                    producer_index=state.producer_index,
                )
                dependencies.append({"kind": "origin", "material": material})
            else:
                provenance = evaluate_provenance(
                    material,
                    state.invocations,
                    producer_validator=lambda invocation, output: _support(
                        invocation, output, state
                    ),
                    confirmed_record=lambda invocation, output: _confirmed(
                        invocation, output, state
                    ),
                    producer_index=state.producer_index,
                )
                dependencies.append(
                    {
                        "dependency_projection": provenance.dependency_projection,
                        "material": provenance.material,
                    }
                )
    except MechanicalContractError as error:
        raise TargetedRefreshError(
            f"targeted provenance refresh failed: {error.code}"
        ) from error
    return MechanicalCheck(
        check.identity,
        CheckScope.PROVENANCE,
        CheckStatus.PASS,
        check.identity,
        tuple(dependencies),
    )


def _entry_for(invocation: Invocation, state: _RefreshState) -> _EntryState:
    entry = state.owners.get(invocation.material_owner)
    if entry is None:
        raise TargetedRefreshError(
            f"unknown invocation material owner: {invocation.material_owner}"
        )
    return entry


def _confirmed(invocation: Invocation, material: str, state: _RefreshState) -> bool:
    try:
        entry = _entry_for(invocation, state)
        support = legacy_output_projection(
            entry.state,
            tuple(item for item in state.invocations if item.entry == entry.entry),
            project_root=state.project_root,
        )
        return confirmed_output_record(
            invocation,
            material,
            entry_root=entry.root,
            project_root=state.project_root,
            support=support,
        )
    except (MechanicalContractError, TargetedRefreshError):
        return False


def _support(
    invocation: Invocation, material: str, state: _RefreshState
) -> Mapping[str, object]:
    entry = _entry_for(invocation, state)
    support = legacy_output_projection(
        entry.state,
        tuple(item for item in state.invocations if item.entry == entry.entry),
        project_root=state.project_root,
    )
    path = Path(material)
    if not path.is_file() and not path.is_dir():
        raise TargetedRefreshError(f"reproduced output is unavailable: {material}")
    resolved = resolve_output_support(
        invocation,
        material,
        entry_root=entry.root,
        project_root=state.project_root,
        support=support,
    )
    current_output = _observe(resolved.path)
    candidate = resolved.record
    current_code = None
    if candidate is not None:
        current_code = {
            item.key: _observe(item.resolved)
            for item in resolve_code_support(
                candidate, entry_root=entry.root, subject=resolved.subject
            )
        }
    record = require_current_output_support(
        invocation,
        resolved,
        current_output=current_output,
        current_code=current_code,
    )
    return {
        "output": resolved.key,
        "record": record.as_dict(),
        "record_file": entry.state.path.resolve().as_posix(),
        "record_file_sha256": entry.record_digest,
    }


def _observe(path: Path) -> Fingerprint:
    if path.is_file():
        digest, _ = observe_file_content(path)
        return Fingerprint("sha256", digest=digest)
    if path.is_dir():
        _, entries, _ = observe_directory_tree(path)
        return compose_directory_fingerprint(entries)
    raise TargetedRefreshError(f"provenance path is unavailable: {path}")


def _summary_dependency(check: MechanicalCheck) -> str | None:
    if not check.identity.startswith("provenance:summary:"):
        return None
    if check.failure is not None:
        return check.failure.dependency
    if len(check.dependencies) != 1:
        return None
    dependency = check.dependencies[0].get("dependency")
    return dependency if isinstance(dependency, str) else None

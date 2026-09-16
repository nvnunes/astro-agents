"""Relevant-state checks and fresh-registry merges for short authoring writes."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Mapping

from research_log_data import (
    DataFile,
    InputResource,
    data_file_from_inputs,
    load_data_file,
)
from research_log_reservations import artifact_transaction

from .context import EntryContext, resolve_entry, resolve_project_root
from .model import ActionError


def require_unchanged(before: object, after: object, subject: str) -> None:
    """Reject a relevant concurrent edit before publishing any replacements."""

    if before != after:
        raise ActionError(
            "authoring.state.changed",
            f"{subject} changed during preparation; review the change and rerun sync",
        )


def resource_authority(resource: InputResource | None) -> InputResource | None:
    """Comparison policy does not define command/evidence extraction authority."""

    return replace(resource, comparison=None) if resource is not None else None


def merge_data(
    before: DataFile | None,
    candidate: DataFile | None,
    current: DataFile | None,
    *,
    names: set[str],
    entry: EntryContext,
) -> DataFile | None:
    """Check used declarations and apply only this operation's additions/edits."""

    original = before.by_name if before else {}
    prepared = candidate.by_name if candidate else {}
    fresh = dict(current.by_name) if current else {}
    changed = {
        name
        for name in original.keys() | prepared.keys()
        if original.get(name) != prepared.get(name)
    }
    for name in names | changed:
        if resource_authority(fresh.get(name)) != resource_authority(
            prepared.get(name)
        ):
            require_unchanged(
                resource_authority(original.get(name)),
                resource_authority(fresh.get(name)),
                f"{entry.id}/{name}",
            )
    for name in changed:
        if name in prepared:
            declaration = prepared[name]
            if name in fresh:
                declaration = replace(
                    declaration, comparison=fresh[name].comparison
                )
            fresh[name] = declaration
        else:
            fresh.pop(name, None)
    if not fresh and current is None and candidate is None:
        return None
    return data_file_from_inputs(
        entry.root / "data.json", entry_root=entry.root, inputs=tuple(fresh.values())
    )


def artifact_locations(data: DataFile | None, names: set[str]) -> tuple[Path, ...]:
    """Select live file/directory boundaries, not pinned Git commit locators."""

    if data is None:
        return ()
    return tuple(
        Path(data.by_name[name].canonical_target)
        for name in names
        if name in data.by_name and data.by_name[name].kind != "git-repository"
    )


def data_change_paths(
    before: DataFile | None, after: DataFile | None
) -> tuple[Path, ...]:
    """Protect changed declaration identities/locations, not comparison policy."""

    old = before.by_name if before else {}
    new = after.by_name if after else {}
    names = {
        name
        for name in old.keys() | new.keys()
        if (old[name].as_dict() if name in old else None)
        != (new[name].as_dict() if name in new else None)
        and not (
            name in old
            and name in new
            and old[name].kind == new[name].kind
            and old[name].identity == new[name].identity
            and old[name].canonical_target == new[name].canonical_target
            and old[name].origin == new[name].origin
            and old[name].reference_entry == new[name].reference_entry
        )
    }
    return (*artifact_locations(before, names), *artifact_locations(after, names))


@contextmanager
def data_publication_transaction(
    entries: tuple[EntryContext, ...], updates: Mapping[Path, str | None]
) -> Iterator[None]:
    """Refuse registry identity/locator changes affecting reserved artifacts."""

    paths = tuple(
        path for entry in entries for path in _registry_change_paths(entry, updates)
    )
    with artifact_transaction(resolve_project_root(entries[0].root), writes=paths):
        yield


def _registry_change_paths(
    entry: EntryContext, updates: Mapping[Path, str | None]
) -> tuple[Path, ...]:
    path = entry.root / "data.json"
    if path not in updates:
        return ()
    before = load_data_file(path, entry_root=entry.root) if path.exists() else None
    text = updates[path]
    after = (
        {item["name"]: item for item in json.loads(text)["inputs"]}
        if text is not None
        else {}
    )
    old = before.by_name if before else {}
    paths = []
    for name in old.keys() | after.keys():
        prior = old[name].as_dict() if name in old else {}
        candidate = after.get(name, {})
        if _physical_fields(prior) == _physical_fields(candidate):
            continue
        if name in old and old[name].kind != "git-repository":
            paths.append(Path(old[name].canonical_target))
        if "location" in candidate and candidate.get("kind") != "git-repository":
            paths.append((entry.root / candidate["location"]).resolve())
        elif "from_entry" in candidate:
            source = resolve_entry(entry.log, candidate["from_entry"])
            paths.append(_reference_target(source, name, updates))
    return tuple(paths)


def _physical_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Return declaration fields that define a live filesystem boundary."""

    if fields.get("kind") == "git-repository":
        return {}
    return {
        key: value for key, value in fields.items() if key != "reproduction_comparison"
    }


def _reference_target(
    source: EntryContext, name: str, updates: Mapping[Path, str | None]
) -> Path:
    """Resolve a reference against coupled pending owner changes, e.g. rename."""

    path = source.root / "data.json"
    if path in updates and (text := updates[path]) is not None:
        candidates = {item["name"]: item for item in json.loads(text)["inputs"]}
        candidate = candidates.get(name, {})
        if "location" in candidate:
            return (source.root / candidate["location"]).resolve()
        raise ActionError(
            "data.reference.inconsistent",
            f"{source.id}/{name}: no direct generated owner",
        )
    data = load_data_file(path, entry_root=source.root)
    resource = data.by_name.get(name)
    if resource is None:
        raise ActionError(
            "data.reference.inconsistent", f"{source.id}/{name}: missing owner"
        )
    return Path(resource.canonical_target)

"""Transient full-specification evidence-definition authoring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

from validation.filesystem import BoundedFileReadError, bounded_file_bytes
from validation.json_codec import V2JsonError, decode_json
from validation.presentation import evaluate_candidate_record

from . import evidence
from .context import EntryContext
from .model import ActionError, ActionResult
from .storage import entry_lock

MAX_DEFINITION_BYTES = 8 * 1024 * 1024
PRIVATE_TMP = Path("/private/tmp").resolve()


def add_or_update(
    entry: EntryContext,
    *,
    action: str,
    record_id: str,
    definition: Path,
    dry_run: bool,
) -> ActionResult:
    """Evaluate and apply one transient full-specification definition."""

    with entry_lock(entry):
        value = _read_definition(definition)
        evaluated = evaluate_candidate_record(
            entry_root=entry.root,
            log_root=entry.log.root,
            record_id=record_id,
            raw_sources=value["sources"],
            transformation=value["transformation"],
        )
        current = evidence.load_current(entry)
        return evidence.apply_candidate_locked(
            entry,
            action,
            evaluated.record,
            current=current,
            dry_run=dry_run,
        )


def _read_definition(path: Path) -> Mapping[str, Any]:
    lexical = path if path.is_absolute() else Path.cwd() / path
    if lexical.is_symlink():
        raise ActionError(
            "evidence.definition.unsafe", "definition must not be a symlink"
        )
    resolved = lexical.resolve()
    try:
        resolved.relative_to(PRIVATE_TMP)
    except ValueError as error:
        raise ActionError(
            "evidence.definition.location",
            "definition must be beneath /private/tmp",
        ) from error
    try:
        raw = bounded_file_bytes(resolved, maximum_bytes=MAX_DEFINITION_BYTES)
        value = decode_json(
            raw.decode("utf-8"),
            maximum_bytes=MAX_DEFINITION_BYTES,
            subject="evidence definition",
        )
    except (BoundedFileReadError, UnicodeError, V2JsonError) as error:
        raise ActionError("evidence.definition.invalid", str(error)) from error
    if not isinstance(value, Mapping) or set(value) != {
        "sources",
        "transformation",
    }:
        fields = sorted(value) if isinstance(value, Mapping) else None
        raise ActionError(
            "evidence.definition.invalid",
            f"definition fields must be sources and transformation: {fields}",
        )
    return cast(Mapping[str, Any], value)

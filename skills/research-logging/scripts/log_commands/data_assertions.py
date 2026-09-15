"""Shared ensure semantics for declarations asserted during authoring sync."""

from pathlib import Path

from research_log_data import InputResource

from .context import EntryContext
from .model import ActionError


def assignment(value: str, flag: str) -> tuple[str, str]:
    """Read an explicit name/target assertion without guessing missing pieces."""

    left, separator, right = value.partition("=")
    if not separator or not left or not right:
        raise ActionError("cli.arguments.invalid", f"{flag} requires NAME=TARGET")
    return left, right


def require_local_target(
    entry: EntryContext, name: str, location: str, *, kind: str, origin: bool
) -> None:
    """Require origin existence and reject an explicitly mismatched physical kind."""

    path = Path(location) if Path(location).is_absolute() else entry.root / location
    if path.is_symlink() or origin and not path.exists():
        raise ActionError("data.target.missing", f"{name}: {location}")
    if path.exists() and (
        (kind == "file" and not path.is_file())
        or (kind == "directory" and not path.is_dir())
    ):
        raise ActionError(
            "data.kind.conflict",
            f"{name} is not a {kind}; use the matching directory or file add form",
            records=({"name": name, "observed": location, "required": kind},),
            diagnostic_log=entry.log.root,
        )


def ensure_declaration(
    entry: EntryContext,
    items: dict[str, InputResource],
    candidate: InputResource,
    flag: str,
    task: str = "command.sync",
) -> None:
    """Accept consistent assertions while preserving unasserted maintained metadata."""

    existing = items.get(candidate.name)
    if existing is None:
        items[candidate.name] = candidate
        return
    consistent = (
        existing.reference_entry == candidate.reference_entry
        and existing.kind == candidate.kind
        and existing.location == candidate.location
        and existing.origin == candidate.origin
        and (
            candidate.kind != "git-repository"
            or existing.identity.commit == candidate.identity.commit
        )
    )
    if consistent:
        return
    raise ActionError(
        f"{task}.declaration.conflict",
        f"{flag} conflicts with maintained data; use log data update for changes",
        records=(
            {
                "maintained": existing.as_dict(),
                "name": candidate.name,
                "requested": candidate.as_dict(),
            },
        ),
        diagnostic_log=entry.log.root,
    )

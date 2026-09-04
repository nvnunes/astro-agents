"""Shared typed result contract for research-owned log mutations."""

from __future__ import annotations

from dataclasses import dataclass

AUTHORING_RESULT_SCHEMA = "research-log-authoring-result/1"


@dataclass(frozen=True)
class InitArguments:
    """Typed arguments for creating one empty maintained research log."""

    title: str
    dry_run: bool


@dataclass(frozen=True)
class AddArguments:
    """Typed arguments for creating one minimal maintained-log entry."""

    date: str
    title: str
    slug: str
    dry_run: bool


@dataclass(frozen=True)
class EvidenceCommonArguments:
    """Typed arguments for one common evidence add or update action."""

    record_id: str
    source: str
    select: tuple[str, ...]
    identity: tuple[str, ...]
    where: tuple[tuple[str, str, str], ...]
    as_percentage: bool
    scale: str | None
    dry_run: bool


@dataclass(frozen=True)
class RetentionArguments:
    """Typed arguments for one complete retention add or update action."""

    record_id: str
    targets: tuple[str, ...]
    reason: str | None
    dry_run: bool


@dataclass(frozen=True)
class ActionResult:
    """One bounded semantic outcome returned by an authoring action."""

    task: str
    status: str
    code: str
    changed: bool
    paths: tuple[str, ...] = ()
    records: tuple[dict[str, object], ...] | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the stable public result envelope."""

        value: dict[str, object] = {
            "changed": self.changed,
            "code": self.code,
            "paths": list(self.paths),
            "schema": AUTHORING_RESULT_SCHEMA,
            "status": self.status,
            "task": self.task,
        }
        if self.records is not None:
            value["records"] = list(self.records)
        return value


class ActionError(Exception):
    """One bounded authoring conflict or failed precondition."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

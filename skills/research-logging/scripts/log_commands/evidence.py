"""Read-only entry-scoped evidence inventory."""

from __future__ import annotations

from validation.evidence import EvidenceFile, load_evidence_file

from .context import EntryContext
from .model import ActionResult


def list_records(entry: EntryContext) -> ActionResult:
    """Return bounded semantic evidence records without registry details."""

    current = load_current(entry)
    records = () if current is None else current.records
    return ActionResult(
        "evidence.list",
        "unchanged",
        "evidence.listed",
        False,
        records=tuple(
            {
                "document": record.document,
                "id": record.id,
                "kind": record.kind,
                "sources": [
                    {
                        "source": source.source,
                        "locator": {
                            key: value
                            for key, value in source.locator.items()
                            if key != "expect"
                        }
                        if source.locator
                        else None,
                    }
                    for source in record.sources
                ],
                "transformation": record.transformation,
                "reproduction_tolerance": record.reproduction_tolerance.absolute
                if record.reproduction_tolerance
                else None,
            }
            for record in records
        ),
    )


def load_current(entry: EntryContext) -> EvidenceFile | None:
    """Load the complete current evidence registry when present."""

    path = entry.root / "evidence.json"
    return (
        load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )

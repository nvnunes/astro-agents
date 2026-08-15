"""Staged, linted publication of canonical validation bundles."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Protocol, Sequence

from .contracts import ValidationToolError
from .records import publish_record_bundle

VALIDATION_BUNDLE_FILENAMES = frozenset(
    {
        "validation.md",
        "validation-failures.md",
        "validation-state.json",
        "validation-index.json",
    }
)


class ValidationBundle(Protocol):
    """Canonical content needed by the publication boundary."""

    @property
    def report_text(self) -> str: ...

    @property
    def failure_text(self) -> Optional[str]: ...

    @property
    def state(self) -> Mapping[str, Any]: ...

    @property
    def graph_record(self) -> Mapping[str, Any]: ...


class ValidationPublicationTarget(NamedTuple):
    """Filesystem and contract inputs for one exclusive publication."""

    output_dir: Path
    expected_identity: str
    record_names: Sequence[str]
    expected_entry_order: Sequence[str]
    slice_filename: str


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _stage_bundle(
    staged_dir: Path,
    bundle: ValidationBundle,
    target: ValidationPublicationTarget,
    lint_bundle: Callable[[Path, Optional[Sequence[str]]], Mapping[str, Any]],
) -> None:
    (staged_dir / "validation.md").write_text(bundle.report_text, encoding="utf-8")
    if bundle.failure_text is not None:
        (staged_dir / "validation-failures.md").write_text(
            bundle.failure_text, encoding="utf-8"
        )
    _write_json(staged_dir / "validation-state.json", bundle.state)
    _write_json(
        staged_dir / target.slice_filename,
        bundle.graph_record,
    )
    lint = lint_bundle(staged_dir, target.expected_entry_order)
    if not lint["ok"]:
        raise ValidationToolError(
            "generated validation records failed lint: " + "; ".join(lint["issues"])
        )


def publish_validation_bundle(
    bundle: ValidationBundle,
    target: ValidationPublicationTarget,
    validate_publication: Callable[[], None],
    lint_bundle: Callable[[Path, Optional[Sequence[str]]], Mapping[str, Any]],
) -> None:
    """Lint staged content, then publish it under the repository lock."""

    if (
        frozenset(target.record_names) != VALIDATION_BUNDLE_FILENAMES
        or len(target.record_names) != len(VALIDATION_BUNDLE_FILENAMES)
        or target.slice_filename != "validation-index.json"
    ):
        raise ValidationToolError(
            "canonical validation publication has an invalid generated-file allowlist"
        )
    target.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=target.output_dir.parent,
        prefix=f".{target.output_dir.name}-validation-staging-",
    ) as directory:
        staged_dir = Path(directory)
        _stage_bundle(staged_dir, bundle, target, lint_bundle)
        publish_record_bundle(
            staged_dir,
            target.output_dir,
            target.record_names,
            expected_identity=target.expected_identity,
            validate_publication=validate_publication,
        )

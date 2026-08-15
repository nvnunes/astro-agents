"""Staged, linted publication of canonical validation bundles."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Protocol, Sequence

from .contracts import ValidationToolError
from .decision_store import decode_decision_store
from .records import (
    PublicationGuard,
    publish_record_bundle,
    record_bundle_identity,
    validation_lock,
)

VALIDATION_BUNDLE_FILENAMES = frozenset(
    {
        "validation.md",
        "validation-decisions.json",
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
    def decisions(self) -> Mapping[str, Any]: ...

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
    _write_json(staged_dir / "validation-decisions.json", bundle.decisions)
    _write_json(staged_dir / "validation-state.json", bundle.state)
    _write_json(
        staged_dir / target.slice_filename,
        bundle.graph_record,
    )
    lint = lint_bundle(staged_dir, target.expected_entry_order)
    if not lint["ok"] or not lint.get("cache_usable", False):
        raise ValidationToolError(
            "generated validation records failed lint: " + "; ".join(lint["issues"])
        )


def publish_validation_bundle(
    bundle: ValidationBundle,
    target: ValidationPublicationTarget,
    validate_publication: Callable[[], None],
    lint_bundle: Callable[[Path, Optional[Sequence[str]]], Mapping[str, Any]],
) -> None:
    """Lint staged content, then publish it under one log's lock."""

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
        with validation_lock(target.output_dir), tempfile.TemporaryDirectory(
            dir=target.output_dir.parent,
            prefix=f".{target.output_dir.name}-decision-merge-",
        ) as merge_directory:
            merged: dict[str, Any] = dict(bundle.decisions)
            prior_path = target.output_dir / "validation-decisions.json"
            if prior_path.is_file():
                try:
                    prior = decode_decision_store(
                        json.loads(prior_path.read_text(encoding="utf-8"))
                    )
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    ValidationToolError,
                ):
                    prior = None
                if prior is not None:
                    by_identity = {
                        judgment["identity"]: judgment
                        for judgment in [
                            *prior["judgments"],
                            *bundle.decisions["judgments"],
                        ]
                    }
                    merged = dict(prior)
                    merged["judgments"] = sorted(
                        by_identity.values(),
                        key=lambda judgment: (
                            str(judgment["kind"]),
                            json.dumps(
                                judgment["subject"],
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            ),
                            str(judgment["identity"]),
                        ),
                    )
            merged_dir = Path(merge_directory)
            _write_json(merged_dir / "validation-decisions.json", merged)
            publish_record_bundle(
                merged_dir,
                target.output_dir,
                ("validation-decisions.json",),
                PublicationGuard(
                    target.expected_identity,
                    ("validation-decisions.json", "validation.md"),
                    validate_publication,
                ),
            )
            report_identity = record_bundle_identity(
                target.output_dir, ("validation.md",)
            )
            publish_record_bundle(
                staged_dir,
                target.output_dir,
                ("validation.md",),
                PublicationGuard(
                    report_identity, validate_publication=validate_publication
                ),
            )
            for names in (
                ("validation-decisions.json",),
                ("validation-failures.md",),
                ("validation-state.json", target.slice_filename),
            ):
                expected = record_bundle_identity(target.output_dir, names)
                publish_record_bundle(
                    staged_dir,
                    target.output_dir,
                    names,
                    PublicationGuard(expected),
                )

"""Frozen v1 locator reader available only to explicit evidence upgrade tooling."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from .evidence_v1_upgrade import locator_values as legacy_locator_values
from .json_codec import canonical_json
from .locator import canonical_source_value
from .mechanical_values import (
    SelectionItem,
    SelectionResult,
    selection_dependency,
    source_content_identity,
)

CLAUSE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")
VERSION_RE = re.compile(r"v(?P<version>[0-9]+):")


class LocatorV1UpgradeError(ValueError):
    """One precise migration-only v1 parse or evaluation failure."""

    def __init__(self, code: str, subject: str, observed: object):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = "Legacy V1 Evidence Upgrade Reference"


@dataclass(frozen=True)
class V1Locator:
    """Strictly parsed frozen v1 locator."""

    canonical: str
    evaluator_text: str


@dataclass(frozen=True)
class V1SourceExpression:
    """One ordered v1 source reference and optional locator."""

    source: str
    locator: V1Locator | None


def parse_source_expressions(value: str) -> tuple[V1SourceExpression, ...]:
    """Parse the exact frozen outer source-reference grammar."""

    if not isinstance(value, str) or not value:
        _fail("evidence.declaration.invalid", "sources", {"value": value})
    expressions: list[V1SourceExpression] = []
    for number, expression in enumerate(value.split(" | "), 1):
        if not expression or expression != expression.strip():
            _fail(
                "locator.v1.delimiter_ambiguous",
                f"sources[{number}]",
                {"value": expression},
            )
        parts = expression.split(" :: ")
        if len(parts) > 2 or not parts[0]:
            _fail(
                "locator.v1.delimiter_ambiguous",
                f"sources[{number}]",
                {"value": expression},
            )
        source = parts[0]
        if " :: " in source or " | " in source:
            _fail(
                "locator.v1.delimiter_ambiguous",
                f"sources[{number}]",
                {"source": source},
            )
        locator = parse_locator(parts[1]) if len(parts) == 2 else None
        expressions.append(V1SourceExpression(source=source, locator=locator))
    return tuple(expressions)


def parse_locator(value: str) -> V1Locator:
    """Parse and canonicalize the frozen compact v1 clause language."""

    if not isinstance(value, str) or not value:
        _fail("locator.syntax.invalid", "v1 locator", {"value": value})
    unprefixed = _strip_version(value)
    assignments = _parse_assignments(unprefixed)
    normalized = _normalize_assignments(assignments)
    ordered = _ordered_assignments(normalized)
    evaluator_text = "; ".join(f"{name}={content}" for name, content in ordered)
    return V1Locator(canonical=f"v1:{evaluator_text}", evaluator_text=evaluator_text)


def _strip_version(value: str) -> str:
    version = VERSION_RE.match(value)
    if version is not None:
        if version.group("version") != "1":
            _fail(
                "locator.version.unsupported",
                "v1 locator",
                {"version": version.group("version")},
            )
        value = value[version.end() :]
    if not value or ";" in value.replace("; ", ""):
        _fail("locator.syntax.invalid", "v1 locator", {"value": value})
    return value


def _parse_assignments(value: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for clause in value.split("; "):
        if "=" not in clause:
            _fail("locator.syntax.invalid", "v1 locator", {"clause": clause})
        name, content = clause.split("=", 1)
        if (
            not name
            or CLAUSE_NAME_RE.fullmatch(name) is None
            or not content
            or content != content.strip()
            or " | " in content
        ):
            _fail("locator.syntax.invalid", "v1 locator", {"clause": clause})
        if name in assignments:
            _fail("locator.v1.duplicate_clause", "v1 locator", {"name": name})
        assignments[name] = content
    if "field" in assignments and "fields" in assignments:
        _fail(
            "locator.syntax.invalid",
            "v1 locator",
            {"fields": ["field", "fields"]},
        )
    if "text" in assignments and set(assignments) != {"text"}:
        _fail("locator.syntax.invalid", "v1 locator", {"text_conflict": True})
    return assignments


def _normalize_assignments(assignments: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, content in assignments.items():
        alternatives = content.split("|")
        if any(not part or part != part.strip() for part in alternatives):
            _fail("locator.syntax.invalid", "v1 locator", {"name": name})
        if name == "field" and len(alternatives) != 1:
            _fail("locator.syntax.invalid", "v1 locator", {"name": name})
        if name != "fields" and not _filter_name(name) and len(alternatives) != 1:
            _fail("locator.syntax.invalid", "v1 locator", {"name": name})
        if len(alternatives) != len(set(alternatives)):
            _fail("locator.syntax.invalid", "v1 locator", {"name": name})
        normalized[name] = (
            "|".join(alternatives)
            if name == "fields"
            else "|".join(sorted(alternatives))
            if _filter_name(name)
            else content
        )
    return normalized


def _ordered_assignments(normalized: dict[str, str]) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    if "text" in normalized:
        ordered.append(("text", normalized["text"]))
    else:
        if "path" in normalized:
            ordered.append(("path", normalized["path"]))
        ordered.extend(
            (name, normalized[name])
            for name in sorted(normalized)
            if _filter_name(name)
        )
        for name in ("field", "fields", "property"):
            if name in normalized:
                ordered.append((name, normalized[name]))
    return ordered


def evaluate_locator(source: Path, locator: V1Locator) -> SelectionResult:
    """Evaluate frozen v1 through the isolated legacy reader and common result."""

    try:
        payload = source.read_bytes()
    except OSError as exc:
        _fail("locator.reader.unavailable", str(source), {"error": str(exc)})
    status, values, detail = legacy_locator_values(source, locator.evaluator_text)
    if status != "ok":
        code = (
            "locator.source.unsafe"
            if "prohibited" in detail
            else "locator.path.unresolved"
        )
        _fail(code, str(source), {"status": status, "detail": detail})
    items = tuple(
        SelectionItem(("v1-ordinal", number), canonical_source_value(value))
        for number, value in enumerate(values)
    )
    if not items:
        _fail("locator.selection.empty", str(source), {"items": 0})
    source_identity = source_content_identity(payload)
    dependency = selection_dependency(
        source_identity=source_identity,
        locator_identity=locator.canonical,
        items=items,
    )
    return SelectionResult(
        declared_version="v1",
        effective_version="v1",
        locator_identity=locator.canonical,
        source_identity=source_identity,
        source_profile=source.suffix.lower().lstrip("."),
        items=items,
        matches=len(items),
        membership=tuple(
            hashlib.sha256(
                canonical_json(["v1-ordinal", number]).encode("utf-8")
            ).hexdigest()
            for number in range(len(items))
        ),
        dependency_projection=dependency,
        limit_profile="v1-upgrade",
    )


def _filter_name(name: str) -> bool:
    return name not in {"field", "fields", "path", "property", "text"}


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise LocatorV1UpgradeError(code, subject, observed)

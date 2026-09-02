"""Public result contracts for mechanical research-log validation.

The generated record contains mechanical checks only. It deliberately excludes
semantic judgments, review continuation, reproduction results, and generated
repair instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

GENERATED_RECORD_SCHEMA = "research-log-mechanical/1"


class MechanicalResultContractError(ValueError):
    """Raised when an internal mechanical result violates its exact contract."""


class CheckStatus(str, Enum):
    """Terminal status of one applicable mechanical check."""

    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class CheckScope(str, Enum):
    """Independent mechanical conclusion owned by one check."""

    CONFORMANCE = "conformance"
    EVIDENCE = "evidence"
    PROVENANCE = "provenance"
    ORPHAN = "orphan"


class CompletionState(str, Enum):
    """Operation-level completion state outside individual check meaning."""

    COMPLETE_CLEAR = "complete_clear"
    COMPLETE_FINDINGS = "complete_findings"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class FailurePayload:
    """Exact machine-readable explanation of one non-passing check.

    Attributes:
        code: Stable specification-owned failure or limitation code.
        subject: Exact record, marker, command, material, or path identity.
        observed: Bounded structured projection of the state that was found.
        rule: Exact specification section or normative rule that was violated.
        dependency: Optional identity of the prerequisite result causing this
            dependent status. It identifies a cause and never proposes a fix.
    """

    code: str
    subject: str
    observed: Mapping[str, Any]
    rule: str
    dependency: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("code", self.code),
            ("subject", self.subject),
            ("rule", self.rule),
        ):
            if not isinstance(value, str) or not value.strip():
                raise MechanicalResultContractError(
                    f"failure {field} must be a nonempty string"
                )
        if self.dependency is not None and not self.dependency.strip():
            raise MechanicalResultContractError(
                "failure dependency must be absent or a nonempty string"
            )
        _json_value(dict(self.observed), "failure observed state")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical field projection for generated records."""

        value: dict[str, Any] = {
            "code": self.code,
            "observed": dict(self.observed),
            "rule": self.rule,
            "subject": self.subject,
        }
        if self.dependency is not None:
            value["dependency"] = self.dependency
        return value

    @classmethod
    def from_dict(cls, value: object) -> FailurePayload:
        """Decode one exact failure payload without accepting extra fields."""

        item = _mapping(value, "failure")
        required = {"code", "observed", "rule", "subject"}
        if not required <= set(item) <= required | {"dependency"}:
            raise MechanicalResultContractError("failure has incorrect fields")
        return cls(
            code=_string(item["code"], "failure.code"),
            subject=_string(item["subject"], "failure.subject"),
            observed=_mapping(item["observed"], "failure.observed"),
            rule=_string(item["rule"], "failure.rule"),
            dependency=(
                _string(item["dependency"], "failure.dependency")
                if "dependency" in item
                else None
            ),
        )


@dataclass(frozen=True)
class MechanicalCheck:
    """One independent mechanical conclusion with exact dependencies."""

    identity: str
    scope: CheckScope
    status: CheckStatus
    subject: str
    dependencies: tuple[Mapping[str, Any], ...] = ()
    failure: FailurePayload | None = None

    def __post_init__(self) -> None:
        if not self.identity.strip() or not self.subject.strip():
            raise MechanicalResultContractError(
                "check identity and subject must be nonempty"
            )
        for number, dependency in enumerate(self.dependencies):
            _json_value(dict(dependency), f"check dependency {number}")
        if self.status in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}:
            if self.failure is None:
                raise MechanicalResultContractError(
                    "failed or unavailable check requires a failure payload"
                )
        elif self.failure is not None:
            raise MechanicalResultContractError(
                "passing or not-applicable check cannot contain a failure payload"
            )
        if self.failure is not None and self.failure.subject != self.subject:
            raise MechanicalResultContractError(
                "check and failure subjects must be identical"
            )

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical generated-record projection."""

        value: dict[str, Any] = {
            "dependencies": [dict(item) for item in self.dependencies],
            "identity": self.identity,
            "scope": self.scope.value,
            "status": self.status.value,
            "subject": self.subject,
        }
        if self.failure is not None:
            value["failure"] = self.failure.as_dict()
        return value

    @classmethod
    def from_dict(cls, value: object) -> MechanicalCheck:
        """Decode one exact generated check."""

        item = _mapping(value, "check")
        required = {"dependencies", "identity", "scope", "status", "subject"}
        if not required <= set(item) <= required | {"failure"}:
            raise MechanicalResultContractError("check has incorrect fields")
        dependencies = _sequence(item["dependencies"], "check.dependencies")
        try:
            scope = CheckScope(item["scope"])
            status = CheckStatus(item["status"])
        except (TypeError, ValueError) as exc:
            raise MechanicalResultContractError(
                "check scope or status is unsupported"
            ) from exc
        return cls(
            identity=_string(item["identity"], "check.identity"),
            scope=scope,
            status=status,
            subject=_string(item["subject"], "check.subject"),
            dependencies=tuple(
                _mapping(value, f"check.dependencies[{number}]")
                for number, value in enumerate(dependencies)
            ),
            failure=(
                FailurePayload.from_dict(item["failure"]) if "failure" in item else None
            ),
        )


@dataclass(frozen=True)
class ScopeResult:
    """Deterministic aggregate of all checks in one independent scope."""

    scope: CheckScope
    status: CheckStatus
    checks: int
    counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        """Return the generated-record projection."""

        return {
            "checks": self.checks,
            "counts": dict(self.counts),
            "scope": self.scope.value,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class MechanicalGeneratedRecord:
    """Complete public record for one mechanical validation evaluation."""

    summary: str
    rules_version: str
    result_date: str
    checks: tuple[MechanicalCheck, ...]
    scopes: tuple[ScopeResult, ...]
    schema: str = GENERATED_RECORD_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != GENERATED_RECORD_SCHEMA:
            raise MechanicalResultContractError(
                f"unsupported generated-record schema: {self.schema!r}"
            )
        if not self.summary.strip() or not self.rules_version.strip():
            raise MechanicalResultContractError(
                "summary and rules version must be nonempty"
            )
        identities = [check.identity for check in self.checks]
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise MechanicalResultContractError(
                "checks must have unique identities in canonical order"
            )
        expected = aggregate_scopes(self.checks)
        if self.scopes != expected:
            raise MechanicalResultContractError(
                "scope aggregates do not match generated checks"
            )

    @property
    def completion(self) -> CompletionState:
        """Return the operation state implied by every check."""

        statuses = {check.status for check in self.checks}
        if CheckStatus.UNAVAILABLE in statuses:
            return CompletionState.INCOMPLETE
        if CheckStatus.FAIL in statuses:
            return CompletionState.COMPLETE_FINDINGS
        return CompletionState.COMPLETE_CLEAR

    def as_dict(self) -> dict[str, Any]:
        """Return the complete public generated record."""

        return {
            "checks": [check.as_dict() for check in self.checks],
            "completion": self.completion.value,
            "result_date": self.result_date,
            "rules_version": self.rules_version,
            "schema": self.schema,
            "scopes": [scope.as_dict() for scope in self.scopes],
            "summary": self.summary,
        }

    def canonical_json(self) -> str:
        """Serialize the public record deterministically."""

        return json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def build(
        cls,
        summary: str,
        rules_version: str,
        result_date: str,
        checks: Sequence[MechanicalCheck],
    ) -> MechanicalGeneratedRecord:
        """Build a canonical record from unordered component checks."""

        checks = tuple(sorted(checks, key=lambda value: value.identity))
        return cls(
            summary=summary,
            rules_version=rules_version,
            result_date=result_date,
            checks=checks,
            scopes=aggregate_scopes(checks),
        )

    @classmethod
    def from_dict(cls, value: object) -> MechanicalGeneratedRecord:
        """Read only the exact public schema version owned here."""

        item = _mapping(value, "generated record")
        expected = {
            "checks",
            "completion",
            "result_date",
            "rules_version",
            "schema",
            "scopes",
            "summary",
        }
        if set(item) != expected:
            raise MechanicalResultContractError("generated record has incorrect fields")
        checks = tuple(
            MechanicalCheck.from_dict(value)
            for value in _sequence(item["checks"], "generated record.checks")
        )
        record = cls.build(
            summary=_string(item["summary"], "generated record.summary"),
            rules_version=_string(
                item["rules_version"], "generated record.rules_version"
            ),
            result_date=_string(item["result_date"], "generated record.result_date"),
            checks=checks,
        )
        if item["schema"] != GENERATED_RECORD_SCHEMA:
            raise MechanicalResultContractError(
                f"unsupported generated-record schema: {item['schema']!r}"
            )
        if item["completion"] != record.completion.value:
            raise MechanicalResultContractError(
                "generated record completion does not match checks"
            )
        decoded_scopes = _sequence(item["scopes"], "generated record.scopes")
        if decoded_scopes != [scope.as_dict() for scope in record.scopes]:
            raise MechanicalResultContractError(
                "generated record scope projection does not match checks"
            )
        return record

    @classmethod
    def from_json(cls, text: str) -> MechanicalGeneratedRecord:
        """Decode strict JSON and reject duplicate keys or trailing content."""

        try:
            value = json.loads(text, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, MechanicalResultContractError) as exc:
            raise MechanicalResultContractError(
                f"invalid generated-record JSON: {exc}"
            ) from exc
        return cls.from_dict(value)


def aggregate_scopes(checks: Sequence[MechanicalCheck]) -> tuple[ScopeResult, ...]:
    """Aggregate checks independently using deterministic status precedence."""

    results: list[ScopeResult] = []
    for scope in CheckScope:
        members = [check for check in checks if check.scope is scope]
        statuses = {check.status for check in members}
        if not members or statuses == {CheckStatus.NOT_APPLICABLE}:
            status = CheckStatus.NOT_APPLICABLE
        elif CheckStatus.UNAVAILABLE in statuses:
            status = CheckStatus.UNAVAILABLE
        elif CheckStatus.FAIL in statuses:
            status = CheckStatus.FAIL
        else:
            status = CheckStatus.PASS
        counts = {
            member_status.value: sum(check.status is member_status for check in members)
            for member_status in CheckStatus
        }
        results.append(
            ScopeResult(
                scope=scope,
                status=status,
                checks=len(members),
                counts=counts,
            )
        )
    return tuple(results)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MechanicalResultContractError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise MechanicalResultContractError(f"{field} keys must be strings")
    return value


def _sequence(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise MechanicalResultContractError(f"{field} must be an array")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MechanicalResultContractError(f"{field} must be a nonempty string")
    return value


def _json_value(value: object, field: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MechanicalResultContractError(
            f"{field} must contain only finite JSON values"
        ) from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MechanicalResultContractError(f"duplicate JSON key: {key}")
        value[key] = item
    return value

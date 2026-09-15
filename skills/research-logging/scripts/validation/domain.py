"""Canonical validation checks, findings, batches, and snapshots.

Rule evaluation is private.  This module is the single boundary that turns
confirmed defects into durable findings and mechanically justified repair
batches. Passing checks remain private; completed snapshots retain canonical
blocked and failed checks for diagnosis.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

SNAPSHOT_SCHEMA = "research-log-validation-snapshot/3"


class ValidationDomainError(ValueError):
    """Raised when a validation-domain value violates its closed contract."""


class RuleArea(str, Enum):
    """Private rule area copied to a finding type only on confirmed failure."""

    CONFORMANCE = "conformance"
    EVIDENCE = "evidence"
    PROVENANCE = "provenance"
    ORPHAN = "orphan"


class CheckOutcome(str, Enum):
    """Outcome of one private rule check."""

    PASS = "pass"
    FINDING = "finding"
    BLOCKED = "blocked"
    FAILED = "failed"


class SnapshotOutcome(str, Enum):
    """Public outcome of one completed validation snapshot."""

    CLEAR = "clear"
    FINDINGS = "findings"
    FAILED = "failed"


class TargetKind(str, Enum):
    """Evaluation target scope, independent of finding type."""

    LOG = "log"
    ENTRY = "entry"


class FailureOperation(str, Enum):
    """Bounded operation categories for failed validation checks."""

    READ = "read"
    OBSERVE = "observe"
    CACHE = "cache"
    GRAPH = "graph"
    OTHER = "other"


class AdmissionOwner(str, Enum):
    """Private owner used when deriving Reproduce admission."""

    EXECUTION = "execution"
    COMMAND = "command"
    MATERIAL = "material"
    ENTRY = "entry"
    LOG = "log"


class RepairKeyKind(str, Enum):
    """Mechanically proven identities that may join repair findings."""

    FAILED_PREREQUISITE = "failed-prerequisite"
    SOURCE = "source"
    RECORD = "record"
    COMMAND = "command"
    EXECUTION = "execution"
    MATERIAL = "material"
    OWNERSHIP = "ownership"


@dataclass(frozen=True, order=True)
class ValidationTarget:
    """One full-log or stable-entry validation target."""

    kind: TargetKind
    log: str
    entry: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.log, "target.log")
        if self.kind is TargetKind.LOG and self.entry is not None:
            raise ValidationDomainError("log target cannot name an entry")
        if self.kind is TargetKind.ENTRY:
            _nonempty(self.entry, "entry target.entry")

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"kind": self.kind.value, "log": self.log}
        if self.entry is not None:
            value["entry"] = self.entry
        return value

    @classmethod
    def from_dict(cls, value: object) -> ValidationTarget:
        item = _object(value, "target")
        _fields(item, {"kind", "log"}, {"entry"}, "target")
        try:
            kind = TargetKind(item["kind"])
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError("target.kind is unsupported") from exc
        return cls(
            kind,
            _string(item["log"], "target.log"),
            _optional_string(item.get("entry"), "target.entry"),
        )


@dataclass(frozen=True, order=True)
class SourceLocation:
    """Exact source location useful to a repair without implying grouping."""

    path: str
    line: int | None = None

    def __post_init__(self) -> None:
        _nonempty(self.path, "source location path")
        if self.line is not None and self.line < 1:
            raise ValidationDomainError("source location line must be positive")

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"path": self.path}
        if self.line is not None:
            value["line"] = self.line
        return value

    @classmethod
    def from_dict(cls, value: object) -> SourceLocation:
        item = _object(value, "source location")
        _fields(item, {"path"}, {"line"}, "source location")
        line = item.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool)):
            raise ValidationDomainError("source location.line must be an integer")
        return cls(_string(item["path"], "source location.path"), line)


@dataclass(frozen=True, order=True)
class GraphReference:
    """Typed graph-node reference carried by a finding."""

    kind: str
    identity: str
    entry: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.kind, "graph reference kind")
        _nonempty(self.identity, "graph reference identity")
        if self.entry is not None:
            _nonempty(self.entry, "graph reference entry")

    @property
    def node_id(self) -> str:
        owner = f":{self.entry}" if self.entry is not None else ""
        return f"{self.kind}{owner}:{self.identity}"

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"identity": self.identity, "kind": self.kind}
        if self.entry is not None:
            value["entry"] = self.entry
        return value

    @classmethod
    def from_dict(cls, value: object) -> GraphReference:
        item = _object(value, "graph reference")
        _fields(item, {"identity", "kind"}, {"entry"}, "graph reference")
        return cls(
            _string(item["kind"], "graph reference.kind"),
            _string(item["identity"], "graph reference.identity"),
            _optional_string(item.get("entry"), "graph reference.entry"),
        )


@dataclass(frozen=True, order=True)
class RepairKey:
    """One typed, proven same-repair identity."""

    kind: RepairKeyKind
    identity: str
    repair_entry: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.identity, "repair key identity")
        if self.repair_entry is not None:
            _nonempty(self.repair_entry, "repair key entry")

    @property
    def value(self) -> str:
        return f"{self.kind.value}:{self.identity}"

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"identity": self.identity, "kind": self.kind.value}
        if self.repair_entry is not None:
            value["repair_entry"] = self.repair_entry
        return value

    @classmethod
    def from_dict(cls, value: object) -> RepairKey:
        item = _object(value, "repair key")
        _fields(item, {"identity", "kind"}, {"repair_entry"}, "repair key")
        try:
            kind = RepairKeyKind(item["kind"])
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError("repair key.kind is unsupported") from exc
        return cls(
            kind,
            _string(item["identity"], "repair key.identity"),
            _optional_string(item.get("repair_entry"), "repair key.repair_entry"),
        )


@dataclass(frozen=True)
class IssueContext:
    """Typed ownership and graph context captured at a rule failure site."""

    entry: str | None = None
    source_locations: tuple[SourceLocation, ...] = ()
    repair_keys: tuple[RepairKey, ...] = ()
    context_nodes: tuple[GraphReference, ...] = ()
    admission_owner: AdmissionOwner | None = None

    def __post_init__(self) -> None:
        if self.entry is not None:
            _nonempty(self.entry, "issue context entry")
        object.__setattr__(
            self,
            "source_locations",
            _sorted_unique(self.source_locations, "source locations"),
        )
        object.__setattr__(
            self, "repair_keys", _sorted_unique(self.repair_keys, "repair keys")
        )
        object.__setattr__(
            self, "context_nodes", _sorted_unique(self.context_nodes, "context nodes")
        )

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "context_nodes": [item.as_dict() for item in self.context_nodes],
            "repair_keys": [item.as_dict() for item in self.repair_keys],
            "source_locations": [item.as_dict() for item in self.source_locations],
        }
        if self.entry is not None:
            value["entry"] = self.entry
        if self.admission_owner is not None:
            value["admission_owner"] = self.admission_owner.value
        return value


@dataclass(frozen=True)
class CheckDiagnostic:
    """Exact evidence for one failed or unavailable private check."""

    code: str
    subject: str
    rule: str
    observed: Mapping[str, object]
    dependency: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.code, "diagnostic code")
        _nonempty(self.subject, "diagnostic subject")
        _nonempty(self.rule, "diagnostic rule")
        if self.dependency is not None:
            _nonempty(self.dependency, "diagnostic dependency")
        object.__setattr__(
            self, "observed", _freeze_object(self.observed, "diagnostic observed")
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "observed": _thaw(self.observed),
            "rule": self.rule,
            "subject": self.subject,
        }


@dataclass(frozen=True)
class RuleCheck:
    """One private rule evaluation conclusion."""

    check_id: str
    area: RuleArea
    outcome: CheckOutcome
    subject: str
    dependencies: tuple[str, ...] = ()
    diagnostic: CheckDiagnostic | None = None
    issue_context: IssueContext | None = None
    dependency_evidence: tuple[Mapping[str, object], ...] = ()
    rule: str = ""
    failure_operation: FailureOperation | None = None

    def __post_init__(self) -> None:
        _nonempty(self.check_id, "check id")
        _nonempty(self.subject, "check subject")
        rule = self.rule or (
            self.diagnostic.rule
            if self.diagnostic is not None
            else f"{self.area.value.title()} Validation"
        )
        _nonempty(rule, "check rule")
        object.__setattr__(self, "rule", rule)
        dependencies = _sorted_unique(self.dependencies, "check dependencies")
        if self.check_id in dependencies:
            raise ValidationDomainError("check cannot depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        needs_diagnostic = self.outcome in {
            CheckOutcome.FINDING,
            CheckOutcome.FAILED,
        }
        if needs_diagnostic != (self.diagnostic is not None):
            raise ValidationDomainError(
                "finding or failed checks require exactly one diagnostic"
            )
        if self.diagnostic is not None and self.diagnostic.subject != self.subject:
            raise ValidationDomainError("check and diagnostic subjects must match")
        if (self.outcome is CheckOutcome.FAILED) != (
            self.failure_operation is not None
        ):
            raise ValidationDomainError(
                "failed checks require exactly one typed failure operation"
            )
        if self.outcome is CheckOutcome.BLOCKED and not (
            dependencies or self.dependency_evidence
        ):
            raise ValidationDomainError(
                "blocked check requires dependency evidence"
            )
        object.__setattr__(
            self,
            "dependency_evidence",
            tuple(
                _freeze_object(item, "check dependency evidence")
                for item in self.dependency_evidence
            ),
        )


@dataclass(frozen=True)
class FailedCheck:
    """One applicable check the validator could not complete reliably."""

    check_id: str
    area: RuleArea
    entry: str | None
    code: str
    subject: str
    rule: str
    operation: FailureOperation
    reason: Mapping[str, object]

    def __post_init__(self) -> None:
        _nonempty(self.check_id, "failed check id")
        _nonempty(self.code, "failed check code")
        _nonempty(self.subject, "failed check subject")
        _nonempty(self.rule, "failed check rule")
        if self.entry is not None:
            _nonempty(self.entry, "failed check entry")
        object.__setattr__(
            self, "reason", _freeze_object(self.reason, "failed check reason")
        )

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "area": self.area.value,
            "check_id": self.check_id,
            "code": self.code,
            "operation": self.operation.value,
            "reason": _thaw(self.reason),
            "rule": self.rule,
            "subject": self.subject,
        }
        if self.entry is not None:
            value["entry"] = self.entry
        return value

    @classmethod
    def from_dict(cls, value: object) -> FailedCheck:
        item = _object(value, "failed check")
        _fields(
            item,
            {"area", "check_id", "code", "operation", "reason", "rule", "subject"},
            {"entry"},
            "failed check",
        )
        try:
            area = RuleArea(item["area"])
            operation = FailureOperation(item["operation"])
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError("failed check enum is unsupported") from exc
        return cls(
            _string(item["check_id"], "failed check.check_id"),
            area,
            _optional_string(item.get("entry"), "failed check.entry"),
            _string(item["code"], "failed check.code"),
            _string(item["subject"], "failed check.subject"),
            _string(item["rule"], "failed check.rule"),
            operation,
            _json_object(item["reason"], "failed check.reason"),
        )


@dataclass(frozen=True)
class Finding:
    """One confirmed failed mechanical check."""

    finding_id: str
    type: RuleArea
    code: str
    entry: str | None
    subject: str
    rule: str
    observed: Mapping[str, object]
    caused_by: tuple[str, ...] = ()
    repair_keys: tuple[RepairKey, ...] = ()
    context_nodes: tuple[GraphReference, ...] = ()
    source_locations: tuple[SourceLocation, ...] = ()
    admission_owner: AdmissionOwner | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("finding id", self.finding_id),
            ("code", self.code),
            ("subject", self.subject),
            ("rule", self.rule),
        ):
            _nonempty(value, name)
        if self.entry is not None:
            _nonempty(self.entry, "finding entry")
        object.__setattr__(
            self, "observed", _freeze_object(self.observed, "finding observed")
        )
        object.__setattr__(
            self, "caused_by", _sorted_unique(self.caused_by, "finding causes")
        )
        object.__setattr__(
            self, "repair_keys", _sorted_unique(self.repair_keys, "finding repair keys")
        )
        object.__setattr__(
            self,
            "context_nodes",
            _sorted_unique(self.context_nodes, "finding context nodes"),
        )
        object.__setattr__(
            self,
            "source_locations",
            _sorted_unique(self.source_locations, "finding source locations"),
        )
        if self.finding_id in self.caused_by:
            raise ValidationDomainError("finding cannot cause itself")

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "caused_by": list(self.caused_by),
            "code": self.code,
            "context_nodes": [item.as_dict() for item in self.context_nodes],
            "finding_id": self.finding_id,
            "observed": _thaw(self.observed),
            "repair_keys": [item.as_dict() for item in self.repair_keys],
            "rule": self.rule,
            "source_locations": [item.as_dict() for item in self.source_locations],
            "subject": self.subject,
            "type": self.type.value,
        }
        if self.entry is not None:
            value["entry"] = self.entry
        if self.admission_owner is not None:
            value["admission_owner"] = self.admission_owner.value
        return value

    @classmethod
    def from_dict(cls, value: object) -> Finding:
        item = _object(value, "finding")
        required = {
            "caused_by",
            "code",
            "context_nodes",
            "finding_id",
            "observed",
            "repair_keys",
            "rule",
            "source_locations",
            "subject",
            "type",
        }
        _fields(item, required, {"admission_owner", "entry"}, "finding")
        try:
            area = RuleArea(item["type"])
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError("finding.type is unsupported") from exc
        owner_value = item.get("admission_owner")
        try:
            owner = None if owner_value is None else AdmissionOwner(owner_value)
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError(
                "finding.admission_owner is unsupported"
            ) from exc
        return cls(
            _string(item["finding_id"], "finding.finding_id"),
            area,
            _string(item["code"], "finding.code"),
            _optional_string(item.get("entry"), "finding.entry"),
            _string(item["subject"], "finding.subject"),
            _string(item["rule"], "finding.rule"),
            _json_object(item["observed"], "finding.observed"),
            _string_array(item["caused_by"], "finding.caused_by"),
            tuple(
                RepairKey.from_dict(value)
                for value in _array(item["repair_keys"], "finding.repair_keys")
            ),
            tuple(
                GraphReference.from_dict(value)
                for value in _array(item["context_nodes"], "finding.context_nodes")
            ),
            tuple(
                SourceLocation.from_dict(value)
                for value in _array(
                    item["source_locations"], "finding.source_locations"
                )
            ),
            owner,
        )


@dataclass(frozen=True)
class BlockedCheck:
    """One applicable check prevented by a finding or failed check."""

    check_id: str
    area: RuleArea
    entry: str | None
    subject: str
    rule: str
    blocked_by: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonempty(self.check_id, "blocked check id")
        _nonempty(self.subject, "blocked check subject")
        _nonempty(self.rule, "blocked check rule")
        if self.entry is not None:
            _nonempty(self.entry, "blocked check entry")
        if not self.blocked_by:
            raise ValidationDomainError(
                "blocked check requires at least one blocker"
            )
        object.__setattr__(
            self,
            "blocked_by",
            _sorted_unique(self.blocked_by, "blocked check blockers"),
        )

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "area": self.area.value,
            "blocked_by": list(self.blocked_by),
            "check_id": self.check_id,
            "rule": self.rule,
            "subject": self.subject,
        }
        if self.entry is not None:
            value["entry"] = self.entry
        return value

    @classmethod
    def from_dict(cls, value: object) -> BlockedCheck:
        item = _object(value, "blocked check")
        _fields(
            item,
            {"area", "blocked_by", "check_id", "rule", "subject"},
            {"entry"},
            "blocked check",
        )
        try:
            area = RuleArea(item["area"])
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError("blocked check.area is unsupported") from exc
        return cls(
            _string(item["check_id"], "blocked check.check_id"),
            area,
            _optional_string(item.get("entry"), "blocked check.entry"),
            _string(item["subject"], "blocked check.subject"),
            _string(item["rule"], "blocked check.rule"),
            _string_array(item["blocked_by"], "blocked check.blocked_by"),
        )


@dataclass(frozen=True)
class Batch:
    """Complete repair unit derived from exact relationships and orphan fallback."""

    batch_id: str
    finding_ids: tuple[str, ...]
    repair_entries: tuple[str, ...]
    context_entries: tuple[str, ...]
    repair_keys: tuple[RepairKey, ...]
    rationale: tuple[str, ...]
    focus_finding_id: str

    def __post_init__(self) -> None:
        _nonempty(self.batch_id, "batch id")
        supplied_keys = tuple(self.repair_keys)
        object.__setattr__(
            self, "finding_ids", _sorted_unique(self.finding_ids, "batch finding ids")
        )
        object.__setattr__(
            self,
            "repair_entries",
            _sorted_unique(
                (
                    *self.repair_entries,
                    *(
                        key.repair_entry
                        for key in supplied_keys
                        if key.repair_entry is not None
                    ),
                ),
                "batch repair entries",
            ),
        )
        object.__setattr__(
            self,
            "context_entries",
            _sorted_unique(self.context_entries, "batch context entries"),
        )
        object.__setattr__(
            self,
            "repair_keys",
            tuple(
                sorted(
                    {
                        key.value: RepairKey(key.kind, key.identity)
                        for key in supplied_keys
                    }.values()
                )
            ),
        )
        object.__setattr__(
            self, "rationale", _sorted_unique(self.rationale, "batch rationale")
        )
        if not self.finding_ids:
            raise ValidationDomainError("batch must contain at least one finding")
        if self.focus_finding_id not in self.finding_ids:
            raise ValidationDomainError("batch focus must be one of its findings")
        if set(self.repair_entries) & set(self.context_entries):
            raise ValidationDomainError(
                "batch entries cannot be both repair and context"
            )
        expected_id = "batch-" + _digest(
            {
                "finding_ids": list(self.finding_ids),
                "repair_keys": [key.value for key in self.repair_keys],
            }
        )
        if self.batch_id != expected_id:
            raise ValidationDomainError("batch identity does not match its members")

    def as_dict(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "context_entries": list(self.context_entries),
            "finding_ids": list(self.finding_ids),
            "focus_finding_id": self.focus_finding_id,
            "rationale": list(self.rationale),
            "repair_entries": list(self.repair_entries),
            "repair_keys": [item.as_dict() for item in self.repair_keys],
        }

    @classmethod
    def from_dict(cls, value: object) -> Batch:
        item = _object(value, "batch")
        required = {
            "batch_id",
            "context_entries",
            "finding_ids",
            "focus_finding_id",
            "rationale",
            "repair_entries",
            "repair_keys",
        }
        _fields(item, required, set(), "batch")
        return cls(
            _string(item["batch_id"], "batch.batch_id"),
            _string_array(item["finding_ids"], "batch.finding_ids"),
            _string_array(item["repair_entries"], "batch.repair_entries"),
            _string_array(item["context_entries"], "batch.context_entries"),
            tuple(
                RepairKey.from_dict(value)
                for value in _array(item["repair_keys"], "batch.repair_keys")
            ),
            _string_array(item["rationale"], "batch.rationale"),
            _string(item["focus_finding_id"], "batch.focus_finding_id"),
        )


@dataclass(frozen=True)
class ValidationAttempt:
    """Complete private record of one successful validation operation."""

    target: ValidationTarget
    source_identity: str
    rules_version: str
    started_at: str
    finished_at: str
    checks: tuple[RuleCheck, ...]
    findings: tuple[Finding, ...]
    metrics: Mapping[str, object]

    def __post_init__(self) -> None:
        for name, value in (
            ("source identity", self.source_identity),
            ("rules version", self.rules_version),
            ("started at", self.started_at),
            ("finished at", self.finished_at),
        ):
            _nonempty(value, name)
        object.__setattr__(
            self,
            "checks",
            _unique_by(self.checks, lambda item: item.check_id, "checks"),
        )
        object.__setattr__(
            self,
            "findings",
            _unique_by(self.findings, lambda item: item.finding_id, "findings"),
        )
        object.__setattr__(
            self, "metrics", _freeze_object(self.metrics, "attempt metrics")
        )
        _validate_attempt_findings(self.checks, self.findings)
        _validate_attempt_dependencies(self.checks)

    @classmethod
    def build(  # noqa: PLR0913 -- explicit attempt identity is the contract
        cls,
        *,
        target: ValidationTarget,
        source_identity: str,
        rules_version: str,
        started_at: str,
        finished_at: str,
        checks: Sequence[RuleCheck],
        metrics: Mapping[str, object] | None = None,
    ) -> ValidationAttempt:
        ordered = _unique_by(checks, lambda item: item.check_id, "checks")
        normalized = _normalize_failed_dependencies(ordered)
        findings = _findings_from_checks(normalized)
        return cls(
            target,
            source_identity,
            rules_version,
            started_at,
            finished_at,
            normalized,
            findings,
            metrics or {},
        )


@dataclass(frozen=True)
class ValidationSnapshot:
    """One immutable completed validation snapshot consumed by all readers."""

    internal_snapshot_id: str
    target: ValidationTarget
    source_identity: str
    rules_version: str
    started_at: str
    finished_at: str
    stored_at: str | None
    outcome: SnapshotOutcome
    passed_check_count: int
    findings: tuple[Finding, ...]
    batches: tuple[Batch, ...]
    blocked_checks: tuple[BlockedCheck, ...]
    failed_checks: tuple[FailedCheck, ...]
    repair_context: Mapping[str, object]
    report_context: Mapping[str, object]
    schema: str = SNAPSHOT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SNAPSHOT_SCHEMA:
            raise ValidationDomainError(
                f"unsupported validation snapshot schema: {self.schema!r}"
            )
        _nonempty(self.internal_snapshot_id, "snapshot id")
        if isinstance(self.passed_check_count, bool) or self.passed_check_count < 0:
            raise ValidationDomainError("snapshot passed check count is invalid")
        object.__setattr__(
            self,
            "repair_context",
            _freeze_object(
                _canonical_repair_context(self.repair_context),
                "snapshot repair context",
            ),
        )
        object.__setattr__(
            self,
            "report_context",
            _freeze_object(self.report_context, "snapshot report context"),
        )
        object.__setattr__(
            self,
            "findings",
            _unique_by(self.findings, lambda item: item.finding_id, "findings"),
        )
        object.__setattr__(
            self,
            "batches",
            _unique_by(self.batches, lambda item: item.batch_id, "batches"),
        )
        object.__setattr__(
            self,
            "blocked_checks",
            _unique_by(
                self.blocked_checks,
                lambda item: item.check_id,
                "blocked checks",
            ),
        )
        object.__setattr__(
            self,
            "failed_checks",
            _unique_by(
                self.failed_checks,
                lambda item: item.check_id,
                "failed checks",
            ),
        )
        if self.batches != build_batches(self.findings):
            raise ValidationDomainError(
                "snapshot batches must be derived exactly from its findings"
        )
        _validate_snapshot_batches(self.findings, self.batches)
        _validate_snapshot_outcome(
            self.outcome,
            self.findings,
            self.failed_checks,
        )
        _validate_snapshot_checks(
            self.findings, self.blocked_checks, self.failed_checks
        )
        expected_id = "snapshot-" + _digest(
            _snapshot_identity_body(
                target=self.target,
                source_identity=self.source_identity,
                rules_version=self.rules_version,
                started_at=self.started_at,
                finished_at=self.finished_at,
                outcome=self.outcome,
                passed_check_count=self.passed_check_count,
                findings=self.findings,
                batches=self.batches,
                blocked_checks=self.blocked_checks,
                failed_checks=self.failed_checks,
                repair_context=self.repair_context,
                report_context=self.report_context,
            )
        )
        if self.internal_snapshot_id != expected_id:
            raise ValidationDomainError(
                "snapshot identity does not match its canonical content"
            )

    @classmethod
    def from_attempt(
        cls,
        attempt: ValidationAttempt,
        *,
        stored_at: str | None = None,
        repair_context: Mapping[str, object] | None = None,
        report_context: Mapping[str, object] | None = None,
    ) -> ValidationSnapshot:
        batches = build_batches(attempt.findings)
        blocked_checks = _blocked_checks_from_checks(attempt.checks)
        failed_checks = _failed_checks_from_checks(attempt.checks)
        passed_check_count = sum(
            check.outcome is CheckOutcome.PASS for check in attempt.checks
        )
        outcome = SnapshotOutcome.FAILED if failed_checks else (
            SnapshotOutcome.FINDINGS if attempt.findings else SnapshotOutcome.CLEAR
        )
        normalized_repair_context = _canonical_repair_context(repair_context or {})
        normalized_report_context = report_context or {}
        body = _snapshot_identity_body(
            target=attempt.target,
            source_identity=attempt.source_identity,
            rules_version=attempt.rules_version,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            outcome=outcome,
            passed_check_count=passed_check_count,
            findings=attempt.findings,
            batches=batches,
            blocked_checks=blocked_checks,
            failed_checks=failed_checks,
            repair_context=normalized_repair_context,
            report_context=normalized_report_context,
        )
        snapshot_id = "snapshot-" + _digest(body)
        return cls(
            snapshot_id,
            attempt.target,
            attempt.source_identity,
            attempt.rules_version,
            attempt.started_at,
            attempt.finished_at,
            stored_at,
            outcome,
            passed_check_count,
            attempt.findings,
            batches,
            blocked_checks,
            failed_checks,
            normalized_repair_context,
            normalized_report_context,
        )

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "batches": [item.as_dict() for item in self.batches],
            "blocked_checks": [item.as_dict() for item in self.blocked_checks],
            "failed_checks": [item.as_dict() for item in self.failed_checks],
            "findings": [item.as_dict() for item in self.findings],
            "finished_at": self.finished_at,
            "internal_snapshot_id": self.internal_snapshot_id,
            "outcome": self.outcome.value,
            "passed_check_count": self.passed_check_count,
            "repair_context": _thaw(self.repair_context),
            "report_context": _thaw(self.report_context),
            "rules_version": self.rules_version,
            "schema": self.schema,
            "source_identity": self.source_identity,
            "started_at": self.started_at,
            "target": self.target.as_dict(),
        }
        if self.stored_at is not None:
            value["stored_at"] = self.stored_at
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )

    @classmethod
    def from_dict(cls, value: object) -> ValidationSnapshot:
        """Load the one accepted snapshot schema without compatibility parsing."""

        item = _object(value, "validation snapshot")
        required = {
            "batches",
            "blocked_checks",
            "failed_checks",
            "findings",
            "finished_at",
            "internal_snapshot_id",
            "outcome",
            "passed_check_count",
            "repair_context",
            "report_context",
            "rules_version",
            "schema",
            "source_identity",
            "started_at",
            "target",
        }
        _fields(item, required, {"stored_at"}, "validation snapshot")
        try:
            outcome = SnapshotOutcome(item["outcome"])
        except (TypeError, ValueError) as exc:
            raise ValidationDomainError(
                "validation snapshot.outcome is unsupported"
            ) from exc
        passed = item["passed_check_count"]
        if not isinstance(passed, int) or isinstance(passed, bool):
            raise ValidationDomainError(
                "validation snapshot.passed_check_count must be an integer"
            )
        return cls(
            _string(
                item["internal_snapshot_id"],
                "validation snapshot.internal_snapshot_id",
            ),
            ValidationTarget.from_dict(item["target"]),
            _string(item["source_identity"], "validation snapshot.source_identity"),
            _string(item["rules_version"], "validation snapshot.rules_version"),
            _string(item["started_at"], "validation snapshot.started_at"),
            _string(item["finished_at"], "validation snapshot.finished_at"),
            _optional_string(item.get("stored_at"), "validation snapshot.stored_at"),
            outcome,
            passed,
            tuple(
                Finding.from_dict(value)
                for value in _array(item["findings"], "validation snapshot.findings")
            ),
            tuple(
                Batch.from_dict(value)
                for value in _array(item["batches"], "validation snapshot.batches")
            ),
            tuple(
                BlockedCheck.from_dict(value)
                for value in _array(
                    item["blocked_checks"], "validation snapshot.blocked_checks"
                )
            ),
            tuple(
                FailedCheck.from_dict(value)
                for value in _array(
                    item["failed_checks"], "validation snapshot.failed_checks"
                )
            ),
            _json_object(item["repair_context"], "validation snapshot.repair_context"),
            _json_object(item["report_context"], "validation snapshot.report_context"),
            _string(item["schema"], "validation snapshot.schema"),
        )

    @classmethod
    def from_json(cls, value: str) -> ValidationSnapshot:
        try:
            payload = json.loads(value, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationDomainError(
                "validation snapshot is not valid JSON"
            ) from exc
        return cls.from_dict(payload)


def _snapshot_identity_body(  # noqa: PLR0913 -- identity covers the whole contract
    *,
    target: ValidationTarget,
    source_identity: str,
    rules_version: str,
    started_at: str,
    finished_at: str,
    outcome: SnapshotOutcome,
    passed_check_count: int,
    findings: Sequence[Finding],
    batches: Sequence[Batch],
    blocked_checks: Sequence[BlockedCheck],
    failed_checks: Sequence[FailedCheck],
    repair_context: Mapping[str, object],
    report_context: Mapping[str, object],
) -> dict[str, object]:
    return {
        "batches": [item.as_dict() for item in batches],
        "blocked_checks": [item.as_dict() for item in blocked_checks],
        "failed_checks": [item.as_dict() for item in failed_checks],
        "findings": [item.as_dict() for item in findings],
        "finished_at": finished_at,
        "outcome": outcome.value,
        "passed_check_count": passed_check_count,
        "repair_context": dict(repair_context),
        "report_context": dict(report_context),
        "rules_version": rules_version,
        "source_identity": source_identity,
        "started_at": started_at,
        "target": target.as_dict(),
    }


def _validate_snapshot_batches(
    findings: Sequence[Finding], batches: Sequence[Batch]
) -> None:
    finding_ids = {item.finding_id for item in findings}
    members = [
        finding_id for batch in batches for finding_id in batch.finding_ids
    ]
    if sorted(members) != sorted(finding_ids):
        raise ValidationDomainError("every finding must belong to exactly one batch")


def _validate_snapshot_outcome(
    outcome: SnapshotOutcome,
    findings: Sequence[Finding],
    failed_checks: Sequence[FailedCheck],
) -> None:
    expected = (
        SnapshotOutcome.FAILED
        if failed_checks
        else SnapshotOutcome.FINDINGS
        if findings
        else SnapshotOutcome.CLEAR
    )
    if outcome is not expected:
        raise ValidationDomainError("snapshot outcome does not match its checks")


def _validate_snapshot_checks(
    findings: Sequence[Finding],
    blocked_checks: Sequence[BlockedCheck],
    failed_checks: Sequence[FailedCheck],
) -> None:
    roots = {item.finding_id for item in findings} | {
        item.check_id for item in failed_checks
    }
    for check in blocked_checks:
        if check.check_id in roots:
            raise ValidationDomainError("one check cannot have two outcomes")
        if not set(check.blocked_by) <= roots:
            raise ValidationDomainError(
                "blocked check must reference a finding or failed check"
            )


def build_batches(findings: Sequence[Finding]) -> tuple[Batch, ...]:
    """Build deterministic repair components plus bounded orphan fallback groups."""

    ordered = _unique_by(findings, lambda item: item.finding_id, "findings")
    components = _merge_residual_orphan_singletons(_finding_components(ordered))
    return tuple(
        sorted(
            (_batch_from_component(component) for component in components),
            key=lambda item: item.batch_id,
        )
    )


def plain_json(value: object) -> object:
    """Return a mutable JSON-compatible projection of a frozen domain value."""

    return _thaw(value)


def _finding_components(findings: Sequence[Finding]) -> tuple[tuple[Finding, ...], ...]:
    by_id = {item.finding_id: item for item in findings}
    parent = {identity: identity for identity in by_id}
    by_key: dict[str, list[str]] = {}
    for finding in findings:
        for key in finding.repair_keys:
            by_key.setdefault(key.value, []).append(finding.finding_id)
        for cause in finding.caused_by:
            if cause in by_id:
                _join_components(parent, finding.finding_id, cause)
    for keyed_ids in by_key.values():
        for member in keyed_ids[1:]:
            _join_components(parent, keyed_ids[0], member)

    components: dict[str, list[Finding]] = {}
    for finding in findings:
        root = _component_root(parent, finding.finding_id)
        components.setdefault(root, []).append(finding)
    return tuple(
        tuple(sorted(component, key=lambda item: item.finding_id))
        for component in components.values()
    )


def _merge_residual_orphan_singletons(
    components: Sequence[tuple[Finding, ...]],
) -> tuple[tuple[Finding, ...], ...]:
    retained: list[tuple[Finding, ...]] = []
    orphan_singletons: dict[tuple[str | None, str], list[Finding]] = {}
    for component in components:
        finding = component[0]
        if len(component) == 1 and finding.type is RuleArea.ORPHAN:
            orphan_singletons.setdefault((finding.entry, finding.code), []).append(
                finding
            )
        else:
            retained.append(component)
    retained.extend(
        tuple(sorted(group, key=lambda item: item.finding_id))
        for group in orphan_singletons.values()
    )
    return tuple(retained)


def _component_root(parent: dict[str, str], identity: str) -> str:
    while parent[identity] != identity:
        parent[identity] = parent[parent[identity]]
        identity = parent[identity]
    return identity


def _join_components(parent: dict[str, str], left: str, right: str) -> None:
    left_root = _component_root(parent, left)
    right_root = _component_root(parent, right)
    if left_root != right_root:
        parent[max(left_root, right_root)] = min(left_root, right_root)


def _batch_from_component(members: Sequence[Finding]) -> Batch:
    member_ids = tuple(item.finding_id for item in members)
    member_id_set = frozenset(member_ids)
    supplied_keys = tuple(key for item in members for key in item.repair_keys)
    key_counts: dict[str, int] = {}
    keys_by_value: dict[str, RepairKey] = {}
    for key in supplied_keys:
        key_counts[key.value] = key_counts.get(key.value, 0) + 1
        keys_by_value[key.value] = RepairKey(key.kind, key.identity)
    keys = tuple(
        sorted(keys_by_value.values())
    )
    repair_entries = tuple(
        sorted(
            {
                key.repair_entry
                for key in supplied_keys
                if key.repair_entry is not None
            }
        )
    )
    context_entries = tuple(
        sorted(
            {
                node.entry
                for item in members
                for node in item.context_nodes
                if node.entry is not None and node.entry not in repair_entries
            }
        )
    )
    rationale = _batch_rationale(members, member_ids, member_id_set, key_counts)
    roots = [
        item.finding_id
        for item in members
        if not set(item.caused_by) & member_id_set
    ]
    identity_body = {
        "finding_ids": list(member_ids),
        "repair_keys": [key.value for key in keys],
    }
    return Batch(
        "batch-" + _digest(identity_body),
        member_ids,
        repair_entries,
        context_entries,
        keys,
        rationale,
        min(roots or list(member_ids)),
    )


def _batch_rationale(
    members: Sequence[Finding],
    member_ids: Sequence[str],
    member_id_set: frozenset[str],
    key_counts: Mapping[str, int],
) -> tuple[str, ...]:
    rationale = {
        f"repair-key:{key}"
        for key, count in key_counts.items()
        if count > 1
    }
    rationale.update(
        f"caused-by:{item.finding_id}:{cause}"
        for item in members
        for cause in item.caused_by
        if cause in member_id_set
    )
    if not rationale:
        if len(members) > 1:
            scope = members[0].entry or "log"
            rationale.add(f"orphan-singletons:{scope}:{members[0].code}")
        else:
            rationale.add(f"singleton:{member_ids[0]}")
    return tuple(sorted(rationale))


def _validate_attempt_findings(
    checks: Sequence[RuleCheck], findings: Sequence[Finding]
) -> None:
    expected = _findings_from_checks(checks)
    if tuple(findings) != expected:
        raise ValidationDomainError(
            "findings must be derived exactly from finding checks"
        )
    finding_ids = {item.finding_id for item in findings}
    for finding in findings:
        if not set(finding.caused_by) <= finding_ids:
            raise ValidationDomainError("finding cause must identify another finding")


def _validate_attempt_dependencies(
    checks: Sequence[RuleCheck],
) -> None:
    checks_by_id = {item.check_id: item for item in checks}
    check_ids = set(checks_by_id)
    for check in checks:
        if not set(check.dependencies) <= check_ids:
            raise ValidationDomainError("check dependency is outside the attempt")
        if _depends_on(check.check_id, check.check_id, checks_by_id):
            raise ValidationDomainError("check dependency graph contains a cycle")


def _normalize_failed_dependencies(
    checks: tuple[RuleCheck, ...],
) -> tuple[RuleCheck, ...]:
    """Keep one failed root and classify failed dependents as blocked."""

    by_id = {item.check_id: item for item in checks}
    roots = [
        item
        for item in checks
        if item.outcome is CheckOutcome.FAILED
        and not any(
            dependency in by_id
            and by_id[dependency].outcome is CheckOutcome.FAILED
            for dependency in item.dependencies
        )
    ]
    root_ids = {item.check_id for item in roots}
    normalized: list[RuleCheck] = []
    for check in checks:
        if check.outcome is CheckOutcome.FAILED and check.check_id not in root_ids:
            normalized.append(
                RuleCheck(
                    check.check_id,
                    check.area,
                    CheckOutcome.BLOCKED,
                    check.subject,
                    check.dependencies,
                    issue_context=check.issue_context,
                    dependency_evidence=check.dependency_evidence,
                    rule=check.rule,
                    failure_operation=None,
                )
            )
        else:
            normalized.append(check)
    return tuple(normalized)


def _findings_from_checks(checks: Sequence[RuleCheck]) -> tuple[Finding, ...]:
    finding_ids = {
        item.check_id for item in checks if item.outcome is CheckOutcome.FINDING
    }
    findings: list[Finding] = []
    for check in checks:
        if check.outcome is not CheckOutcome.FINDING:
            continue
        assert check.diagnostic is not None
        context = check.issue_context or IssueContext()
        findings.append(
            Finding(
                check.check_id,
                check.area,
                check.diagnostic.code,
                context.entry,
                check.subject,
                check.diagnostic.rule,
                check.diagnostic.observed,
                tuple(
                    dependency
                    for dependency in check.dependencies
                    if dependency in finding_ids
                ),
                context.repair_keys,
                context.context_nodes,
                context.source_locations,
                context.admission_owner,
            )
        )
    return tuple(sorted(findings, key=lambda item: item.finding_id))


def _blocked_checks_from_checks(
    checks: Sequence[RuleCheck],
) -> tuple[BlockedCheck, ...]:
    """Project each blocked validation rule application with root blockers."""

    by_id = {item.check_id: item for item in checks}
    projections = [
        BlockedCheck(
            check.check_id,
            check.area,
            check.issue_context.entry if check.issue_context is not None else None,
            check.subject,
            check.rule,
            _root_blockers(check, by_id),
        )
        for check in checks
        if check.outcome is CheckOutcome.BLOCKED
    ]
    return tuple(sorted(projections, key=lambda item: item.check_id))


def _failed_checks_from_checks(
    checks: Sequence[RuleCheck],
) -> tuple[FailedCheck, ...]:
    """Project each localized validator failure exactly once."""

    projections: list[FailedCheck] = []
    for check in checks:
        if check.outcome is not CheckOutcome.FAILED:
            continue
        assert check.diagnostic is not None
        assert check.failure_operation is not None
        projections.append(
            FailedCheck(
                check.check_id,
                check.area,
                check.issue_context.entry if check.issue_context is not None else None,
                check.diagnostic.code,
                check.subject,
                check.diagnostic.rule,
                check.failure_operation,
                check.diagnostic.observed,
            )
        )
    return tuple(sorted(projections, key=lambda item: item.check_id))


def _root_blockers(
    check: RuleCheck,
    checks: Mapping[str, RuleCheck],
    seen: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    blockers: set[str] = set()
    for dependency_id in check.dependencies:
        dependency = checks[dependency_id]
        if dependency.outcome in {CheckOutcome.FINDING, CheckOutcome.FAILED}:
            blockers.add(dependency_id)
        elif dependency.outcome is CheckOutcome.BLOCKED and dependency_id not in seen:
            blockers.update(
                _root_blockers(dependency, checks, seen | {check.check_id})
            )
    if not blockers:
        raise ValidationDomainError(
            "blocked check must trace to a finding or failed check: "
            f"{check.check_id} depends on {check.dependencies}; "
            f"evidence={check.dependency_evidence}"
        )
    return tuple(sorted(blockers))


def _depends_on(
    check_id: str,
    root_id: str,
    checks: Mapping[str, RuleCheck],
    seen: frozenset[str] = frozenset(),
) -> bool:
    if check_id in seen:
        return False
    check = checks[check_id]
    if root_id in check.dependencies:
        return True
    return any(
        dependency in checks
        and _depends_on(dependency, root_id, checks, seen | {check_id})
        for dependency in check.dependencies
    )


def _unique_by(values: Iterable[Any], key: Any, field: str) -> tuple[Any, ...]:
    ordered = sorted(values, key=key)
    identities = [key(value) for value in ordered]
    if len(identities) != len(set(identities)):
        raise ValidationDomainError(f"{field} must have unique identities")
    return tuple(ordered)


def _sorted_unique(values: Iterable[Any], field: str) -> tuple[Any, ...]:
    ordered = tuple(sorted(values))
    if len(ordered) != len(set(ordered)):
        raise ValidationDomainError(f"{field} must be unique")
    return ordered


def _canonical_repair_context(
    value: Mapping[str, object],
) -> Mapping[str, object]:
    """Give every snapshot the same explicit empty repair-context collections."""

    normalized = dict(value)
    for name in ("ambiguities", "nodes", "relationships"):
        normalized.setdefault(name, ())
    return normalized


def _freeze(value: object, field: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not (float("-inf") < value < float("inf")):
            raise ValidationDomainError(f"{field} must contain finite JSON values")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValidationDomainError(f"{field} keys must be strings")
        return MappingProxyType(
            {key: _freeze(value[key], field) for key in sorted(value)}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    raise ValidationDomainError(f"{field} must contain only JSON values")


def _freeze_object(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    frozen = _freeze(value, field)
    assert isinstance(frozen, Mapping)
    return frozen


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _thaw(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def _nonempty(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationDomainError(f"{field} must be a nonempty string")


def _object(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValidationDomainError(f"{field} must be an object")
    return value


def _json_object(value: object, field: str) -> Mapping[str, object]:
    item = _object(value, field)
    _freeze_object(item, field)
    return item


def _integer_object(value: object, field: str) -> Mapping[str, int]:
    item = _object(value, field)
    result: dict[str, int] = {}
    for key, number in item.items():
        if not isinstance(number, int) or isinstance(number, bool):
            raise ValidationDomainError(f"{field} values must be integers")
        result[key] = number
    return result


def _array(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValidationDomainError(f"{field} must be an array")
    return value


def _string_array(value: object, field: str) -> tuple[str, ...]:
    return tuple(_string(item, field) for item in _array(value, field))


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationDomainError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _fields(
    value: Mapping[str, object], required: set[str], optional: set[str], field: str
) -> None:
    if not required <= set(value) <= required | optional:
        raise ValidationDomainError(f"{field} has incorrect fields")


def _string(value: object, field: str) -> str:
    _nonempty(value, field)
    assert isinstance(value, str)
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)

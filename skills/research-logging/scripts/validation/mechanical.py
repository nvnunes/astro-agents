"""Internal composition boundary for complete mechanical evaluation.

This module owns only lifecycle sequencing. Concrete scan and evaluation
contracts are supplied by the mechanical engine so this boundary does not
depend on controller or publication policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypeVar

from .fingerprint_cache import FingerprintCache
from .validation_cache import CheckComparisonEntry, ValidationCache

MechanicalScan = Mapping[str, Any]
MechanicalMetrics = Mapping[str, Any]
MechanicalResult = TypeVar("MechanicalResult")


@dataclass(frozen=True)
class MechanicalEvaluationRequest:
    """Inputs shared by scanning and evaluating one maintained log."""

    summary_path: Path
    date: str
    fingerprint_cache: FingerprintCache | None = None
    validation_cache: ValidationCache | None = None
    check_comparison: Mapping[str, CheckComparisonEntry] | None = None


MechanicalScanRunner = Callable[
    [MechanicalEvaluationRequest], tuple[MechanicalScan, MechanicalMetrics]
]
MechanicalResultEvaluator = Callable[[MechanicalScan, str], MechanicalResult]


@dataclass(frozen=True)
class MechanicalEvaluationPolicy(Generic[MechanicalResult]):
    """Concrete scan and result stages for one mechanical-engine version."""

    scan: MechanicalScanRunner
    evaluate: MechanicalResultEvaluator[MechanicalResult]


@dataclass(frozen=True)
class MechanicalEvaluation(Generic[MechanicalResult]):
    """Complete internal evaluation plus its scan projection and diagnostics."""

    result: MechanicalResult
    scan: MechanicalScan
    metrics: MechanicalMetrics


def evaluate_mechanical(
    request: MechanicalEvaluationRequest,
    policy: MechanicalEvaluationPolicy[MechanicalResult],
) -> MechanicalEvaluation[MechanicalResult]:
    """Run one complete mechanical evaluation without publication."""

    scan, metrics = policy.scan(request)
    result = policy.evaluate(scan, request.date)
    return MechanicalEvaluation(result=result, scan=scan, metrics=metrics)

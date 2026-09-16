"""Artifact-owned results created at the existing mechanical comparison boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, cast

from research_log_data import parse_fingerprint
from validation.domain import SourceLocation, plain_json
from validation.evidence import EvidenceFile, evidence_record_from_canonical_fields
from validation.evidence_comparison import (
    EvidenceComparisonDefinition,
    evidence_comparison_definition,
)

from .reproduction_comparison import (
    ArtifactComparison,
    _compare_evidence_change,
    _evidence_only_rows_match,
    _observed_fingerprint,
    _retained_baseline_matches,
    compare_artifacts,
)
from .reproduction_domain import (
    ArtifactOutcome,
    NotComparedReason,
    ProblemStage,
    ReproductionDomainError,
    ReproductionProblem,
)
from .reproduction_run import ArtifactResult
from .reproduction_work import ArtifactWork, CommandWork


@dataclass(frozen=True)
class ArtifactObservationContext:
    """Origin and frozen auxiliary selections for one actual comparison write.

    ``run_id`` and ``recorded_at`` identify the new observation, not a reused
    result's origin. ``evidence_only`` comes from accepted work and preserves
    the existing per-definition auxiliary-resource guard. This is invocation
    context, not another persisted result or diagnostic record.
    """

    run_id: str
    recorded_at: str
    evidence_only: Sequence[Mapping[str, object]] = ()


def accepted_evidence_definition(
    work: ArtifactWork, producer: CommandWork
) -> EvidenceComparisonDefinition | None:
    """Decode only this artifact's accepted definition, with no registry reads."""

    if work.producer != producer.identity:
        raise ReproductionDomainError("comparison has an unrelated accepted producer")
    if work.definition_identity is None:
        return None
    data = producer.data
    if data is None:
        raise ReproductionDomainError(
            "accepted evidence comparison has no data declaration"
        )
    root = Path(producer.entry_root)
    resource = next(
        (item for item in data.inputs if item.canonical_target == work.retained_path),
        None,
    )
    if resource is None:
        raise ReproductionDomainError("accepted evidence comparison target is missing")
    records = tuple(
        evidence_record_from_canonical_fields(
            subject="accepted artifact evidence",
            entry_relative=PurePosixPath(str(record["document"])).parent.as_posix(),
            fields=cast(Mapping[str, object], plain_json(record)),
        )
        for record in work.evidence_records
    )
    definition = evidence_comparison_definition(
        resource,
        data=data,
        evidence=EvidenceFile(root / "evidence.json", root, records),
    )
    if definition.identity != work.definition_identity:
        raise ReproductionDomainError("accepted evidence definition identity changed")
    return definition


def compare_accepted_artifact(
    work: ArtifactWork,
    producer: CommandWork,
    regenerated: Path,
    *,
    context: ArtifactObservationContext,
) -> tuple[ArtifactResult, tuple[ReproductionProblem, ...]]:
    """Compare a succeeded producer's output using the original rules and guards.

    Command failure/block/policy outcomes are handled by their command owner,
    without calling a comparator or synthesizing an artifact diagnosis.
    """

    if work.producer != producer.identity or work.output is None:
        raise ReproductionDomainError("comparison has no exact producer/output binding")
    kind = dict(producer.execution.recipe.outputs).get(work.output)
    if kind is None:
        raise ReproductionDomainError(
            "comparison output is not declared by its producer"
        )
    expected = Path(work.retained_path)
    definition = accepted_evidence_definition(work, producer)
    if not _retained_baseline_matches(expected, work.baseline, kind):
        compared = _inability(expected, regenerated, "baseline_changed")
    elif work.definition_identity is not None and not _evidence_only_rows_match(
        context.evidence_only, work.identity.entry, work.definition_identity
    ):
        compared = _inability(expected, regenerated, "evidence_context_changed")
    elif not regenerated.exists() or regenerated.is_symlink():
        compared = _inability(expected, regenerated, "output_missing")
    else:
        compared = compare_artifacts(expected, regenerated)
        if definition is not None and compared.outcome == "changed":
            compared = _compare_evidence_change(
                work.output,
                regenerated=regenerated,
                compared=compared,
                definition=definition,
            )
    return _artifact_result(
        work, regenerated, compared, context.run_id, context.recorded_at
    )


def _inability(expected: Path, regenerated: Path, code: str) -> ArtifactComparison:
    return ArtifactComparison(
        regenerated.name,
        "comparison_failed",
        code,
        None,
        _observed_fingerprint(expected),
        _observed_fingerprint(regenerated),
    )


def _artifact_result(
    work: ArtifactWork,
    regenerated: Path,
    compared: ArtifactComparison,
    run_id: str,
    recorded_at: str,
) -> tuple[ArtifactResult, tuple[ReproductionProblem, ...]]:
    problems: tuple[ReproductionProblem, ...] = ()
    if compared.outcome != "matched":
        code = compared.reason or "comparator_error"
        problem = ReproductionProblem(
            work.identity,
            code,
            ProblemStage.COMPARE,
            f"{code.replace('_', ' ').capitalize()}: {work.identity.artifact}.",
            {
                "retained_path": work.retained_path,
                "regenerated_path": regenerated.as_posix(),
                "accepted_baseline": work.baseline.as_dict()
                if work.baseline is not None
                else None,
                "expected": dict(compared.expected)
                if compared.expected is not None
                else None,
                "regenerated": dict(compared.regenerated)
                if compared.regenerated is not None
                else None,
                "profile": compared.profile,
                "definition_identity": work.definition_identity,
                **compared.observed,
            },
            (
                SourceLocation(work.retained_path),
                SourceLocation(regenerated.as_posix()),
            ),
        )
        problems = (problem,)
    failed = compared.outcome == "comparison_failed"
    return ArtifactResult(
        work.identity,
        ArtifactOutcome.NOT_COMPARED
        if failed
        else ArtifactOutcome.MATCHED
        if compared.outcome == "matched"
        else ArtifactOutcome.NOT_MATCHED,
        recorded_at,
        run_id,
        regenerated.as_posix(),
        compared.profile,
        parse_fingerprint(compared.expected, "expected comparison")
        if compared.expected is not None
        else None,
        parse_fingerprint(compared.regenerated, "regenerated comparison")
        if compared.regenerated is not None
        else None,
        NotComparedReason.COMPARISON_FAILED if failed else None,
        compared.evidence,
        tuple(problem.problem_id for problem in problems),
        work.definition_identity,
    ), problems

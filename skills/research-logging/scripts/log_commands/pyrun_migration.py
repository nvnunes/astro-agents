"""Metadata-only project cutover from legacy output support to executions."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict, deque
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from research_log_data import (
    DataContractError,
    DataFile,
    Fingerprint,
    InputResource,
    load_data_file,
)
from validation.commands import CommandContext, Invocation, discover_commands
from validation.discovery import SummaryDiscoveryError, discover_summaries
from validation.errors import MechanicalContractError
from validation.fingerprint_cache import FingerprintCache, FingerprintCacheError
from validation.pyrun_outputs import (
    PYRUN_OUTPUTS_FILENAME,
    OutputSupport,
    PyrunOutputsFile,
    code_target_path,
    load_pyrun_outputs,
    output_target_path,
    portable_code_path,
)
from validation.pyrun_state import (
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_FILENAME,
    PYRUN_RUNNER,
    PYRUN_SCHEMA,
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    PyrunStateError,
    execution_id,
    load_pyrun_state,
    portable_script_path,
    recipe_from_invocation,
    script_target_path,
    validated_pyrun_serialization,
)

from .context import EntryContext, LogContext
from .model import ActionError, ActionResult
from .scaffold import observe_entries
from .storage import PublicationError, log_lock, remove_or_write

MIGRATION_SCHEMA = "research-log-pyrun-migration/1"
MIGRATION_RECORD = Path("docs/research-log-pyrun-migration.json")
MAX_MIGRATION_FILES = 10_000
MAX_STATIC_CODE_PATHS = 256


@dataclass(frozen=True)
class _RecipeCandidate:
    """One unique current recipe and every equivalent Markdown invocation."""

    entry: EntryContext
    recipe: ExecutionRecipe
    slow: bool
    invocations: tuple[Invocation, ...]

    @property
    def identity(self) -> str:
        return execution_id(self.recipe)

    @property
    def legacy_parameters(self) -> tuple[tuple[str, ...], ...]:
        return tuple(sorted({item.parameters for item in self.invocations}))


@dataclass(frozen=True)
class _LegacyGroup:
    """Legacy records that share one command-support signature."""

    entry: EntryContext
    signature: str
    case_id: str
    script: str
    records: tuple[tuple[str, OutputSupport], ...]

    @property
    def first(self) -> OutputSupport:
        return self.records[0][1]

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(output for output, _ in self.records)

    @property
    def all_confirmed(self) -> bool:
        return all(record.confirmed for _, record in self.records)

@dataclass(frozen=True)
class _LegacyDisposition:
    """One complete accounting outcome for one legacy execution group."""

    group: _LegacyGroup
    candidate_key: tuple[Path, str] | None
    disposition: str


@dataclass(frozen=True)
class PyrunMigrationPlan:
    """One fully observed, production-validated, write-free migration plan.

    ``updates`` is the complete cutover transaction: canonical ``pyrun.json``
    replacements, legacy removals, and the final completion marker. ``sources``
    records exact bytes whose continued identity is required at publication.
    """

    project_root: Path
    record_path: Path
    updates: tuple[tuple[Path, str | None], ...]
    sources: tuple[tuple[Path, str], ...]
    local_observations: tuple[tuple[Path, str, Fingerprint], ...]
    input_observations: tuple[tuple[InputResource, Fingerprint], ...]
    record: Mapping[str, object]

    def result(self, *, dry_run: bool) -> ActionResult:
        counts = self.record["counts"]
        assert isinstance(counts, Mapping)
        return ActionResult(
            "pyrun.migrate",
            "dry-run" if dry_run else "changed",
            "pyrun.migration.ready" if dry_run else "pyrun.migration.completed",
            True,
            tuple(
                path.relative_to(self.project_root).as_posix()
                for path, _ in self.updates
            ),
            (dict(counts),),
        )


@dataclass(frozen=True)
class _StateBuildContext:
    """Read-only state needed to reconstruct every current execution."""

    groups: Mapping[tuple[Path, str], list[_LegacyGroup]]
    code_seeds: Mapping[tuple[Path, str], tuple[str, ...]]
    data_files: Mapping[Path, DataFile | None]
    project_root: Path
    tracker: _SourceTracker
    observations: _ObservationTracker


class _ObservationTracker:
    """Retain exact artifact observations for pre-publication revalidation."""

    def __init__(self) -> None:
        self._local: dict[tuple[Path, str], Fingerprint] = {}
        self._inputs: dict[InputResource, Fingerprint] = {}

    def local(self, path: Path, kind: str, fingerprint: Fingerprint) -> None:
        key = (path.resolve(), kind)
        prior = self._local.setdefault(key, fingerprint)
        if prior != fingerprint:
            raise ActionError("pyrun.migration.source_changed", str(path))

    def input(self, resource: InputResource, fingerprint: Fingerprint) -> None:
        prior = self._inputs.setdefault(resource, fingerprint)
        if prior != fingerprint:
            raise ActionError(
                "pyrun.migration.source_changed", resource.canonical_target
            )

    def local_snapshot(self) -> tuple[tuple[Path, str, Fingerprint], ...]:
        return tuple(
            (path, kind, fingerprint)
            for (path, kind), fingerprint in sorted(
                self._local.items(),
                key=lambda item: (item[0][0].as_posix(), item[0][1]),
            )
        )

    def input_snapshot(self) -> tuple[tuple[InputResource, Fingerprint], ...]:
        return tuple(
            sorted(
                self._inputs.items(),
                key=lambda item: (item[0].canonical_target, item[0].name),
            )
        )


@dataclass(frozen=True)
class _ObservationContext:
    """Current observation service and its pre-publication proof collector."""

    project_root: Path
    cache: FingerprintCache
    tracker: _ObservationTracker


class _SourceTracker:
    """Track exact bytes used to construct one migration plan."""

    def __init__(self) -> None:
        self._digests: dict[Path, str] = {}

    def read_text(self, path: Path) -> str:
        raw = path.read_bytes()
        self._remember(path, raw)
        return raw.decode("utf-8")

    def capture(self, path: Path) -> None:
        self._remember(path, path.read_bytes())

    def snapshot(self) -> tuple[tuple[Path, str], ...]:
        return tuple(sorted(self._digests.items(), key=lambda item: item[0].as_posix()))

    def verify(self) -> None:
        for path, expected in self.snapshot():
            try:
                current = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise ActionError(
                    "pyrun.migration.source_changed", str(path)
                ) from error
            if current != expected:
                raise ActionError("pyrun.migration.source_changed", str(path))

    def _remember(self, path: Path, raw: bytes) -> None:
        path = path.resolve()
        digest = hashlib.sha256(raw).hexdigest()
        previous = self._digests.setdefault(path, digest)
        if previous != digest:
            raise ActionError("pyrun.migration.source_changed", str(path))


def migrate_project(
    project_root: Path,
    *,
    dry_run: bool,
    approved_retirements: tuple[str, ...] = (),
) -> ActionResult:
    """Preflight or publish one complete metadata-only project cutover.

    The operation acquires every maintained-log lock in stable order, executes
    no recorded command, and publishes only after the complete corpus has one
    validated disposition. An error leaves all legacy and current state in its
    pre-operation form.
    """

    root = _project_root(project_root)
    logs = _discover_logs(root)
    with ExitStack() as stack:
        for log in logs:
            stack.enter_context(log_lock(log))
        plan = build_migration_plan(
            root,
            approved_retirements=approved_retirements,
            logs=logs,
        )
        if dry_run:
            _verify_sources(plan.sources)
            _verify_observations(plan)
            return plan.result(dry_run=True)
        _publish_plan(plan)
        return plan.result(dry_run=False)


def build_migration_plan(
    project_root: Path,
    *,
    approved_retirements: tuple[str, ...] = (),
    logs: tuple[LogContext, ...] | None = None,
) -> PyrunMigrationPlan:
    """Construct the complete cutover without changing project files."""

    root = _project_root(project_root)
    logs = logs if logs is not None else _discover_logs(root)
    tracker = _SourceTracker()
    observations = _ObservationTracker()
    candidates, data_files = _discover_candidates(logs, root, tracker)
    legacy_files, legacy_groups = _load_legacy(logs, root, tracker)
    if not legacy_files:
        raise ActionError(
            "pyrun.migration.legacy_missing", "no pyrun-outputs.json files found"
        )
    _require_no_current_state(logs, root / MIGRATION_RECORD)
    _require_unique_output_owners(candidates.values(), root)
    dispositions = _match_legacy(
        candidates,
        legacy_groups,
        data_files,
        root,
        frozenset(approved_retirements),
    )
    by_candidate: dict[tuple[Path, str], list[_LegacyGroup]] = defaultdict(list)
    for disposition in dispositions:
        if disposition.candidate_key is not None:
            by_candidate[disposition.candidate_key].append(disposition.group)
    code_seeds = _legacy_code_seeds(legacy_groups)
    states = _build_states(
        candidates,
        _StateBuildContext(
            by_candidate,
            code_seeds,
            data_files,
            root,
            tracker,
            observations,
        ),
    )
    tracker.verify()
    record = _completion_record(root, states, legacy_files, dispositions)
    record_path = root / MIGRATION_RECORD
    updates: list[tuple[Path, str | None]] = [
        (state.path, state.serialized()) for state in states
    ]
    updates.extend((path, None) for path in legacy_files)
    updates.append((record_path, _canonical_pretty(record)))
    if len(updates) > MAX_MIGRATION_FILES:
        raise ActionError(
            "pyrun.migration.too_large",
            f"migration has {len(updates)} file mutations",
        )
    return PyrunMigrationPlan(
        root,
        record_path,
        tuple(updates),
        tracker.snapshot(),
        observations.local_snapshot(),
        observations.input_snapshot(),
        record,
    )


def _project_root(value: Path) -> Path:
    lexical = value if value.is_absolute() else Path.cwd() / value
    if lexical.is_symlink() or not lexical.is_dir():
        raise ActionError("pyrun.migration.root_invalid", str(value))
    root = lexical.resolve()
    marker = root / ".git"
    if marker.is_symlink() or not (marker.is_dir() or marker.is_file()):
        raise ActionError("pyrun.migration.root_invalid", str(value))
    return root


def _discover_logs(root: Path) -> tuple[LogContext, ...]:
    try:
        discovered = discover_summaries(root)
    except SummaryDiscoveryError as error:
        raise ActionError("pyrun.migration.discovery_failed", str(error)) from error
    summaries = discovered.get("summaries")
    if not isinstance(summaries, list) or not summaries:
        raise ActionError(
            "pyrun.migration.discovery_failed", "no maintained logs found"
        )
    return tuple(
        LogContext(Path(value), Path(value).with_suffix(""))
        for value in summaries
        if isinstance(value, str)
    )


def _discover_candidates(
    logs: tuple[LogContext, ...],
    project_root: Path,
    tracker: _SourceTracker,
) -> tuple[dict[tuple[Path, str], _RecipeCandidate], dict[Path, DataFile | None]]:
    candidates: dict[tuple[Path, str], _RecipeCandidate] = {}
    data_files: dict[Path, DataFile | None] = {}
    for log in logs:
        tracker.capture(log.summary)
        for observed in observe_entries(log):
            entry = EntryContext(log, observed.id, observed.root)
            data = _discover_entry_candidates(
                entry,
                observed.documents,
                project_root,
                tracker,
                candidates,
            )
            data_files[entry.root] = data
    if not candidates:
        raise ActionError(
            "pyrun.migration.command_missing", "no artifact-producing commands found"
        )
    return candidates, data_files


def _discover_entry_candidates(
    entry: EntryContext,
    documents: Iterable[Path],
    project_root: Path,
    tracker: _SourceTracker,
    candidates: dict[tuple[Path, str], _RecipeCandidate],
) -> DataFile | None:
    data = _load_data(entry.root, tracker)
    for document in documents:
        for invocation in _discover_document(
            entry, document, data, project_root, tracker
        ):
            if _has_outputs(invocation):
                _add_candidate(candidates, entry, invocation, project_root)
    return data


def _discover_document(
    entry: EntryContext,
    document: Path,
    data: DataFile | None,
    project_root: Path,
    tracker: _SourceTracker,
) -> tuple[Invocation, ...]:
    result = discover_commands(
        tracker.read_text(document),
        CommandContext(
            log_id=entry.log.root.as_posix(),
            entry=entry.id,
            document=document.relative_to(entry.log.root).as_posix(),
            entry_root=entry.root,
            log_root=entry.log.root,
            project_root=project_root,
            data_file=data,
        ),
    )
    if result.failures:
        failure = result.failures[0]
        raise ActionError(
            "pyrun.migration.command_invalid",
            f"{document}: fence {failure.fence}, command "
            f"{failure.ordinal}: {failure.error}",
        )
    return result.invocations


def _add_candidate(
    candidates: dict[tuple[Path, str], _RecipeCandidate],
    entry: EntryContext,
    invocation: Invocation,
    project_root: Path,
) -> None:
    try:
        recipe = recipe_from_invocation(
            invocation,
            entry_root=entry.root,
            project_root=project_root,
        )
    except PyrunStateError as error:
        raise ActionError("pyrun.migration.recipe_invalid", str(error)) from error
    key = (entry.root, execution_id(recipe))
    prior = candidates.get(key)
    if prior is None:
        candidates[key] = _RecipeCandidate(
            entry, recipe, invocation.slow, (invocation,)
        )
        return
    if prior.recipe != recipe or prior.slow != invocation.slow:
        raise ActionError(
            "pyrun.migration.recipe_ambiguous",
            f"inconsistent duplicate recipe {key[1]}",
        )
    candidates[key] = _RecipeCandidate(
        entry,
        recipe,
        prior.slow,
        prior.invocations + (invocation,),
    )


def _load_data(entry_root: Path, tracker: _SourceTracker) -> DataFile | None:
    path = entry_root / "data.json"
    if not path.exists() and not path.is_symlink():
        return None
    tracker.capture(path)
    try:
        return load_data_file(path, entry_root=entry_root)
    except DataContractError as error:
        raise ActionError("pyrun.migration.data_invalid", str(error)) from error


def _has_outputs(invocation: Invocation) -> bool:
    return bool(invocation.outputs) or any(
        item.direction == "output" for item in invocation.collections
    )


def _load_legacy(
    logs: tuple[LogContext, ...],
    project_root: Path,
    tracker: _SourceTracker,
) -> tuple[tuple[Path, ...], tuple[_LegacyGroup, ...]]:
    files: list[Path] = []
    groups: list[_LegacyGroup] = []
    for log in logs:
        for observed in observe_entries(log):
            entry = EntryContext(log, observed.id, observed.root)
            path = entry.root / PYRUN_OUTPUTS_FILENAME
            if not path.exists() and not path.is_symlink():
                continue
            tracker.capture(path)
            try:
                state = load_pyrun_outputs(
                    path, entry_root=entry.root, project_root=project_root
                )
            except MechanicalContractError as error:
                raise ActionError(
                    "pyrun.migration.legacy_invalid", str(error)
                ) from error
            files.append(path)
            groups.extend(_group_legacy(entry, state, project_root))
    return tuple(files), tuple(groups)


def _group_legacy(
    entry: EntryContext,
    state: PyrunOutputsFile,
    project_root: Path,
) -> tuple[_LegacyGroup, ...]:
    grouped: dict[str, list[tuple[str, OutputSupport]]] = defaultdict(list)
    for output, record in state.outputs.items():
        signature = _canonical_compact(
            {
                "code": {name: value.as_dict() for name, value in record.code},
                "inputs": {name: value.as_dict() for name, value in record.inputs},
                "parameters": list(record.parameters),
                "script": record.script.as_dict(),
            }
        )
        grouped[signature].append((output, record))
    relative = entry.root.relative_to(project_root).as_posix()
    return tuple(
        _LegacyGroup(
            entry,
            signature,
            _stable_id("migration", [relative, signature]),
            portable_script_path(
                records[0][1].script.path,
                entry_root=entry.root,
                project_root=project_root,
                authored=True,
            ),
            tuple(sorted(records, key=lambda item: item[0])),
        )
        for signature, records in sorted(grouped.items())
    )


def _require_no_current_state(logs: tuple[LogContext, ...], record: Path) -> None:
    conflicts: list[Path] = []
    for log in logs:
        for observed in observe_entries(log):
            path = observed.root / PYRUN_FILENAME
            if path.exists() or path.is_symlink():
                conflicts.append(path)
    if record.exists() or record.is_symlink():
        conflicts.append(record)
    if conflicts:
        raise ActionError(
            "pyrun.migration.target_exists",
            ", ".join(str(path) for path in conflicts[:10]),
        )


def _require_unique_output_owners(
    candidates: Iterable[_RecipeCandidate], project_root: Path
) -> None:
    owners: list[tuple[Path, tuple[Path, str]]] = []
    for candidate in candidates:
        key = (candidate.entry.root, candidate.identity)
        for output, _ in candidate.recipe.outputs:
            target = output_target_path(
                output,
                entry_root=candidate.entry.root,
                project_root=project_root,
                authored=True,
            ).resolve()
            owners.append((target, key))
    owners.sort(key=lambda item: item[0].as_posix())
    for index, (left, left_owner) in enumerate(owners):
        for right, right_owner in owners[index + 1 :]:
            if right_owner == left_owner:
                continue
            if not _paths_overlap(left, right):
                right_name = right.as_posix()
                left_prefix = left.as_posix().rstrip("/") + "/"
                if (
                    right_name > left.as_posix()
                    and not right_name.startswith(left_prefix)
                ):
                    break
                continue
            raise ActionError(
                "pyrun.migration.output_owner_conflict",
                f"{left} and {right}",
            )


def _match_legacy(
    candidates: Mapping[tuple[Path, str], _RecipeCandidate],
    groups: tuple[_LegacyGroup, ...],
    data_files: Mapping[Path, DataFile | None],
    project_root: Path,
    approved_retirements: frozenset[str],
) -> tuple[_LegacyDisposition, ...]:
    by_entry: dict[Path, list[_RecipeCandidate]] = defaultdict(list)
    for candidate in candidates.values():
        by_entry[candidate.entry.root].append(candidate)
    dispositions: list[_LegacyDisposition] = []
    used_retirements: set[str] = set()
    output_owners = _output_owner_index(candidates.values(), project_root)
    for group in groups:
        possible = []
        for candidate in by_entry[group.entry.root]:
            moved = _moved_legacy_outputs(
                group,
                candidate,
                data_files[candidate.entry.root],
                output_owners,
                project_root,
            )
            if moved is not None:
                possible.append((candidate, moved))
        exact = [
            item
            for item, moved in possible
            if not moved and _same_outputs(group, item)
        ]
        selected = _one_candidate(exact)
        if selected is None:
            selected = _one_candidate(
                [
                    item
                    for item, moved in possible
                    if _compatible_outputs(group, item, moved)
                ]
            )
        if selected is not None:
            dispositions.append(
                _LegacyDisposition(
                    group, (selected.entry.root, selected.identity), "migrated"
                )
            )
            continue
        if group.case_id not in approved_retirements:
            raise ActionError(
                "pyrun.migration.legacy_unresolved",
                f"{group.case_id}: expected one current execution, found "
                f"{len(possible)}",
            )
        _verify_retirement(group, output_owners, project_root)
        used_retirements.add(group.case_id)
        dispositions.append(_LegacyDisposition(group, None, "retired"))
    unused = sorted(approved_retirements - used_retirements)
    if unused:
        raise ActionError(
            "pyrun.migration.retirement_unresolved", ", ".join(unused)
        )
    return tuple(dispositions)


def _moved_legacy_outputs(
    group: _LegacyGroup,
    candidate: _RecipeCandidate,
    data: DataFile | None,
    output_owners: Mapping[Path, set[tuple[Path, str]]],
    project_root: Path,
) -> frozenset[str] | None:
    """Return legacy outputs now represented as generated recipe inputs.

    This is the narrow migration bridge for a corrected material role. Every
    added input must identify one legacy output now owned by exactly one other
    current execution, and the legacy argument vector must equal the current
    vector after replacing only those new input tokens with their old literal
    locations.
    """

    if group.script != candidate.recipe.script:
        return None
    legacy_inputs = {name for name, _ in group.first.inputs}
    current_inputs = set(candidate.recipe.inputs)
    if not legacy_inputs <= current_inputs:
        return None
    added = current_inputs - legacy_inputs
    resources = data.by_name if data is not None else {}
    moved: set[str] = set()
    replacements: dict[str, str] = {}
    candidate_key = (candidate.entry.root, candidate.identity)
    group_targets = {
        output_target_path(
            output,
            entry_root=group.entry.root,
            project_root=project_root,
            authored=True,
        ).resolve(): output
        for output in group.outputs
    }
    for name in added:
        resource = resources.get(name)
        if resource is None or resource.origin:
            return None
        target = Path(resource.canonical_target).resolve()
        output = group_targets.get(target)
        owners = output_owners.get(target, set())
        if output is None or len(owners) != 1 or candidate_key in owners:
            return None
        moved.add(output)
        replacements[f"<{name}>"] = resource.location
    parameter_forms = {
        tuple(replacements.get(value, value) for value in parameters)
        for parameters in candidate.legacy_parameters
    }
    if group.first.parameters not in parameter_forms:
        return None
    return frozenset(moved)


def _same_outputs(group: _LegacyGroup, candidate: _RecipeCandidate) -> bool:
    return group.outputs == tuple(name for name, _ in candidate.recipe.outputs)


def _compatible_outputs(
    group: _LegacyGroup,
    candidate: _RecipeCandidate,
    moved: frozenset[str],
) -> bool:
    outputs = candidate.recipe.outputs
    matched = 0
    for legacy in group.outputs:
        if legacy in moved or any(
            legacy == current
            or (kind == "directory" and legacy.startswith(current.rstrip("/") + "/"))
            for current, kind in outputs
        ):
            matched += 1
    return matched == len(group.outputs) and matched > 0


def _one_candidate(
    candidates: Iterable[_RecipeCandidate],
) -> _RecipeCandidate | None:
    unique = {(item.entry.root, item.identity): item for item in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _output_owner_index(
    candidates: Iterable[_RecipeCandidate], project_root: Path
) -> Mapping[Path, set[tuple[Path, str]]]:
    owners: dict[Path, set[tuple[Path, str]]] = defaultdict(set)
    for candidate in candidates:
        for output, _ in candidate.recipe.outputs:
            target = output_target_path(
                output,
                entry_root=candidate.entry.root,
                project_root=project_root,
                authored=True,
            ).resolve()
            owners[target].add((candidate.entry.root, candidate.identity))
    return owners


def _verify_retirement(
    group: _LegacyGroup,
    owners: Mapping[Path, set[tuple[Path, str]]],
    project_root: Path,
) -> None:
    for output in group.outputs:
        target = output_target_path(
            output,
            entry_root=group.entry.root,
            project_root=project_root,
            authored=True,
        ).resolve()
        covering = {
            owner
            for owned_path, path_owners in owners.items()
            if target == owned_path or _within(target, owned_path)
            for owner in path_owners
        }
        if len(covering) != 1:
            raise ActionError(
                "pyrun.migration.retirement_orphans_output",
                f"{group.case_id}: {output}",
            )


def _legacy_code_seeds(
    groups: tuple[_LegacyGroup, ...],
) -> Mapping[tuple[Path, str], tuple[str, ...]]:
    values: dict[tuple[Path, str], set[str]] = defaultdict(set)
    for group in groups:
        values[(group.entry.root, group.script)].update(
            name for name, _ in group.first.code
        )
    return {key: tuple(sorted(paths)) for key, paths in values.items()}


def _build_states(
    candidates: Mapping[tuple[Path, str], _RecipeCandidate],
    context: _StateBuildContext,
) -> tuple[PyrunFile, ...]:
    executions: dict[Path, dict[str, PyrunExecution]] = defaultdict(dict)
    with FingerprintCache(
        context.project_root, writable=False, reuse=True
    ) as cache:
        observation_context = _ObservationContext(
            context.project_root, cache, context.observations
        )
        for key, candidate in sorted(
            candidates.items(), key=lambda item: (item[0][0].as_posix(), item[0][1])
        ):
            mapped = tuple(context.groups.get(key, ()))
            code = _code_paths(
                candidate,
                mapped,
                context.code_seeds,
                context.tracker,
                context.project_root,
            )
            observed = _observe_execution(
                candidate,
                code,
                context.data_files[candidate.entry.root],
                observation_context,
            )
            confirmed = _is_confirmed(candidate, mapped)
            if confirmed:
                _require_confirmed_observation(candidate, mapped[0], observed)
            executions[candidate.entry.root][candidate.identity] = PyrunExecution(
                confirmed,
                candidate.slow,
                None,
                PYRUN_RUNNER,
                PYRUN_ENVIRONMENT_PROFILE,
                PYRUN_EXECUTION_CONTRACT,
                candidate.recipe,
                observed,
            )
    states: list[PyrunFile] = []
    for entry_root, values in sorted(
        executions.items(), key=lambda item: item[0].as_posix()
    ):
        state = PyrunFile(entry_root / PYRUN_FILENAME, entry_root, values)
        try:
            validated_pyrun_serialization(
                state, project_root=context.project_root
            )
        except PyrunStateError as error:
            raise ActionError("pyrun.migration.state_invalid", str(error)) from error
        states.append(state)
    return tuple(states)


def _code_paths(
    candidate: _RecipeCandidate,
    groups: tuple[_LegacyGroup, ...],
    code_seeds: Mapping[tuple[Path, str], tuple[str, ...]],
    tracker: _SourceTracker,
    project_root: Path,
) -> tuple[str, ...]:
    if groups:
        return tuple(
            sorted(
                {
                    name
                    for group in groups
                    for name, _ in group.first.code
                }
            )
        )
    seeds = code_seeds.get((candidate.entry.root, candidate.recipe.script), ())
    scanner = _StaticCodeScanner(candidate.entry, tracker, project_root)
    return scanner.scan(candidate.recipe.script, seeds)


def _observe_execution(
    candidate: _RecipeCandidate,
    code: tuple[str, ...],
    data: DataFile | None,
    context: _ObservationContext,
) -> ObservedExecution:
    try:
        script_path = script_target_path(
            candidate.recipe.script,
            entry_root=candidate.entry.root,
            project_root=context.project_root,
        )
        script = context.cache.observe_regular_file(script_path).fingerprint
        context.tracker.local(script_path, "file", script)
        inputs = _observe_inputs(candidate.recipe, data, context)
        observed_code = _observe_code(candidate, code, context)
        outputs = _observe_outputs(candidate, context)
    except (DataContractError, FingerprintCacheError, OSError) as error:
        raise ActionError("pyrun.migration.observation_failed", str(error)) from error
    return ObservedExecution(script, inputs, observed_code, outputs)


def _observe_code(
    candidate: _RecipeCandidate,
    code: tuple[str, ...],
    context: _ObservationContext,
) -> tuple[tuple[str, Fingerprint], ...]:
    observed: list[tuple[str, Fingerprint]] = []
    for name in code:
        path = code_target_path(name, entry_root=candidate.entry.root).resolve()
        fingerprint = context.cache.observe_regular_file(path).fingerprint
        context.tracker.local(path, "file", fingerprint)
        observed.append((name, fingerprint))
    return tuple(observed)


def _observe_outputs(
    candidate: _RecipeCandidate,
    context: _ObservationContext,
) -> tuple[tuple[str, Fingerprint], ...]:
    observed: list[tuple[str, Fingerprint]] = []
    for name, kind in candidate.recipe.outputs:
        path = output_target_path(
            name,
            entry_root=candidate.entry.root,
            project_root=context.project_root,
            authored=True,
        )
        fingerprint = _observe_output(path, kind, context.cache)
        context.tracker.local(path, kind, fingerprint)
        observed.append((name, fingerprint))
    return tuple(observed)


def _observe_inputs(
    recipe: ExecutionRecipe,
    data: DataFile | None,
    context: _ObservationContext,
) -> tuple[tuple[str, Fingerprint], ...]:
    by_name = data.by_name if data is not None else {}
    observed: list[tuple[str, Fingerprint]] = []
    for name in recipe.inputs:
        resource = by_name.get(name)
        if resource is None:
            raise ActionError(
                "pyrun.migration.input_missing", f"undeclared input <{name}>"
            )
        observation = context.cache.verify(resource)
        if observation is None:
            raise ActionError("pyrun.migration.input_missing", name)
        context.tracker.input(resource, observation.fingerprint)
        observed.append((name, observation.fingerprint))
    return tuple(observed)


def _observe_output(path: Path, kind: str, cache: FingerprintCache) -> Fingerprint:
    if kind == "directory":
        return cache.observe_directory(path).fingerprint
    return cache.observe_regular_file(path).fingerprint


def _is_confirmed(
    candidate: _RecipeCandidate, groups: tuple[_LegacyGroup, ...]
) -> bool:
    return (
        len(groups) == 1
        and _same_outputs(groups[0], candidate)
        and groups[0].all_confirmed
    )


def _require_confirmed_observation(
    candidate: _RecipeCandidate,
    group: _LegacyGroup,
    observed: ObservedExecution,
) -> None:
    first = group.first
    expected_outputs = tuple(
        (name, record.fingerprint) for name, record in group.records
    )
    if (
        first.script.fingerprint != observed.script
        or first.inputs != observed.inputs
        or first.code != observed.code
        or expected_outputs != observed.outputs
    ):
        raise ActionError(
            "pyrun.migration.confirmed_observation_changed",
            f"{candidate.entry.root}:{candidate.identity}",
        )


class _StaticCodeScanner:
    """Bounded provisional discovery for an execution with no legacy support."""

    def __init__(
        self,
        entry: EntryContext,
        tracker: _SourceTracker,
        project_root: Path,
    ) -> None:
        self.entry = entry
        self.log_root = entry.log.root.absolute()
        self.project_root = project_root
        self.tracker = tracker

    def scan(self, script: str, seeds: tuple[str, ...]) -> tuple[str, ...]:
        entry_script = script_target_path(
            script,
            entry_root=self.entry.root,
            project_root=self.project_root,
        )
        queued: deque[Path] = deque([entry_script])
        queued.extend(
            code_target_path(seed, entry_root=self.entry.root) for seed in seeds
        )
        observed: dict[str, Path] = {}
        visited: set[Path] = set()
        while queued:
            logical = queued.popleft().absolute()
            if logical in visited:
                continue
            visited.add(logical)
            if not self._eligible(logical):
                continue
            try:
                tree = ast.parse(self.tracker.read_text(logical), filename=str(logical))
            except (OSError, UnicodeError, SyntaxError) as error:
                raise ActionError(
                    "pyrun.migration.code_unavailable", f"{logical}: {error}"
                ) from error
            if logical != entry_script.absolute():
                key = portable_code_path(logical, entry_root=self.entry.root)
                observed[key] = logical
                if len(observed) > MAX_STATIC_CODE_PATHS:
                    raise ActionError(
                        "pyrun.migration.code_too_large",
                        f"more than {MAX_STATIC_CODE_PATHS} code paths",
                    )
            queued.extend(self._dependencies(tree, logical))
        return tuple(sorted(observed))

    def _eligible(self, path: Path) -> bool:
        try:
            path.relative_to(self.log_root)
        except ValueError:
            return False
        return path.suffix == ".py" and path.is_file()

    def _dependencies(self, tree: ast.AST, current: Path) -> tuple[Path, ...]:
        selected: list[Path] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    selected.extend(self._resolve_module(alias.name, current, 0))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                selected.extend(self._resolve_module(module, current, node.level))
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    child = f"{module}.{alias.name}" if module else alias.name
                    selected.extend(
                        self._resolve_module(child, current, node.level)
                    )
            elif isinstance(node, ast.Call):
                selected.extend(self._literal_children(node, current))
                self._reject_unsupported_call(node, current)
        return tuple(dict.fromkeys(selected))

    def _resolve_module(
        self, module: str, current: Path, level: int
    ) -> tuple[Path, ...]:
        if not module and level == 0:
            return ()
        parts = tuple(part for part in module.split(".") if part)
        roots: list[Path] = []
        if level:
            base = current.parent
            for _ in range(max(0, level - 1)):
                base = base.parent
            roots.append(base)
        else:
            roots.extend((current.parent, self.entry.root, self.log_root))
        for root in dict.fromkeys(roots):
            result = self._module_at(root, parts)
            if result:
                return result
        return ()

    def _module_at(self, root: Path, parts: tuple[str, ...]) -> tuple[Path, ...]:
        if not parts:
            return ()
        module = root.joinpath(*parts).with_suffix(".py")
        package = root.joinpath(*parts, "__init__.py")
        target = module if module.is_file() else package if package.is_file() else None
        if target is None or not self._eligible(target.absolute()):
            return ()
        initializers = [
            root.joinpath(*parts[:index], "__init__.py")
            for index in range(1, len(parts) + 1)
        ]
        return tuple(
            path.absolute()
            for path in (*initializers, target)
            if path.is_file() and self._eligible(path.absolute())
        )

    def _literal_children(self, node: ast.Call, current: Path) -> tuple[Path, ...]:
        values = [
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and child.value.endswith(".py")
        ]
        selected: list[Path] = []
        for value in values:
            raw = Path(value)
            candidates = (
                (raw,)
                if raw.is_absolute()
                else (current.parent / raw, self.entry.root / raw, self.log_root / raw)
            )
            for candidate in candidates:
                if self._eligible(candidate.absolute()):
                    selected.append(candidate.absolute())
                    break
        return tuple(selected)

    def _reject_unsupported_call(self, node: ast.Call, current: Path) -> None:
        name = _call_name(node.func)
        if name in {"exec", "eval", "runpy.run_module", "runpy.run_path"}:
            raise ActionError(
                "pyrun.migration.code_dynamic", f"{current}: {name}"
            )
        if name in {"__import__", "importlib.import_module"}:
            literal = bool(
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
            if not literal:
                raise ActionError(
                    "pyrun.migration.code_dynamic", f"{current}: {name}"
                )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _completion_record(
    project_root: Path,
    states: tuple[PyrunFile, ...],
    legacy_files: tuple[Path, ...],
    dispositions: tuple[_LegacyDisposition, ...],
) -> Mapping[str, object]:
    migrated = [item for item in dispositions if item.disposition == "migrated"]
    retired = [item for item in dispositions if item.disposition == "retired"]
    execution_count = sum(len(state.executions) for state in states)
    confirmed_count = sum(
        execution.confirmed
        for state in states
        for execution in state.executions.values()
    )
    slow_count = sum(
        execution.slow
        for state in states
        for execution in state.executions.values()
    )
    legacy_records = sum(len(item.group.records) for item in dispositions)
    entries = [
        {
            "entry": state.entry_root.relative_to(project_root).as_posix(),
            "executions": len(state.executions),
            "pyrun_json": state.path.relative_to(project_root).as_posix(),
            "sha256": hashlib.sha256(state.serialized().encode("utf-8")).hexdigest(),
        }
        for state in states
    ]
    legacy = [
        {
            "case_id": item.group.case_id,
            "disposition": item.disposition,
            "entry": item.group.entry.root.relative_to(project_root).as_posix(),
            "execution_id": item.candidate_key[1] if item.candidate_key else None,
            "outputs": list(item.group.outputs),
        }
        for item in dispositions
    ]
    core: dict[str, object] = {
        "counts": {
            "confirmed_executions": confirmed_count,
            "entries": len(states),
            "executions": execution_count,
            "legacy_execution_groups": len(dispositions),
            "legacy_files": len(legacy_files),
            "legacy_output_records": legacy_records,
            "migrated_legacy_groups": len(migrated),
            "retired_legacy_groups": len(retired),
            "slow_executions": slow_count,
            "unconfirmed_executions": execution_count - confirmed_count,
        },
        "entries": entries,
        "legacy_groups": legacy,
        "source_schema": "research-log-pyrun-outputs/v1",
        "target_schema": PYRUN_SCHEMA,
    }
    digest = hashlib.sha256(_canonical_compact(core).encode("utf-8")).hexdigest()
    return {**core, "manifest_sha256": digest, "schema": MIGRATION_SCHEMA}


def _publish_plan(plan: PyrunMigrationPlan) -> None:
    _verify_sources(plan.sources)
    _verify_observations(plan)
    updates = dict(plan.updates)
    before = _prior_values(updates)
    try:
        _publish_updates(plan.updates, before)
    except PublicationError as error:
        _raise_publication_error(error)
    try:
        _verify_publication(plan)
    except (
        ActionError,
        OSError,
        UnicodeError,
        ValueError,
        MechanicalContractError,
    ) as error:
        _restore_failed_publication(before, updates, error)


def _prior_values(updates: Mapping[Path, str | None]) -> dict[Path, str | None]:
    return {
        path: path.read_text(encoding="utf-8") if path.exists() else None
        for path in updates
    }


def _raise_publication_error(error: PublicationError) -> None:
    code = (
        "pyrun.migration.rollback_failed"
        if not error.rollback_complete
        else "pyrun.migration.publication_failed"
    )
    raise ActionError(code, str(error)) from error


def _verify_publication(plan: PyrunMigrationPlan) -> None:
    for path, value in plan.updates:
        if value is None:
            if path.exists() or path.is_symlink():
                raise ActionError("pyrun.migration.publication_failed", str(path))
            continue
        if path == plan.record_path:
            if json.loads(path.read_text(encoding="utf-8")) != plan.record:
                raise ActionError("pyrun.migration.publication_failed", str(path))
            continue
        legacy = path.with_name(PYRUN_OUTPUTS_FILENAME)
        if legacy.exists():
            raise ActionError("pyrun.migration.publication_failed", str(legacy))
        load_pyrun_state(
            path,
            entry_root=path.parent,
            project_root=plan.project_root,
        )


def _restore_failed_publication(
    before: Mapping[Path, str | None],
    updates: Mapping[Path, str | None],
    error: BaseException,
) -> None:
    try:
        _publish_updates(tuple(before.items()), updates)
    except PublicationError as rollback_error:
        raise ActionError(
            "pyrun.migration.rollback_failed", str(rollback_error)
        ) from error
    if isinstance(error, ActionError):
        raise error
    raise ActionError("pyrun.migration.publication_failed", str(error)) from error


def _publish_updates(
    updates: tuple[tuple[Path, str | None], ...],
    rollback_values: Mapping[Path, str | None],
) -> None:
    """Publish in lifecycle order and restore every attempted path on failure."""

    written: list[Path] = []
    try:
        for path, value in updates:
            written.append(path)
            remove_or_write(path, value)
    except (OSError, UnicodeError) as error:
        rollback_errors: list[str] = []
        for path in reversed(written):
            try:
                remove_or_write(path, rollback_values[path])
            except (OSError, UnicodeError) as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        raise PublicationError(error, tuple(rollback_errors)) from error


def _verify_sources(sources: tuple[tuple[Path, str], ...]) -> None:
    for path, expected in sources:
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ActionError("pyrun.migration.source_changed", str(path)) from error
        if current != expected:
            raise ActionError("pyrun.migration.source_changed", str(path))


def _verify_observations(plan: PyrunMigrationPlan) -> None:
    try:
        with FingerprintCache(
            plan.project_root, writable=False, reuse=True
        ) as cache:
            for path, kind, expected in plan.local_observations:
                current = (
                    cache.observe_directory(path)
                    if kind == "directory"
                    else cache.observe_regular_file(path)
                )
                if current.fingerprint != expected:
                    raise ActionError(
                        "pyrun.migration.source_changed", str(path)
                    )
            for resource, expected in plan.input_observations:
                input_current = cache.verify(resource)
                if input_current is None or input_current.fingerprint != expected:
                    raise ActionError(
                        "pyrun.migration.source_changed",
                        resource.canonical_target,
                    )
    except (DataContractError, FingerprintCacheError, OSError) as error:
        raise ActionError("pyrun.migration.source_changed", str(error)) from error


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or _within(left, right) or _within(right, left)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _stable_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(_canonical_compact(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _canonical_compact(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _canonical_pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

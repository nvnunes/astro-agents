"""Current recorded-command and producer state for authoring commands."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from research_log_data import (
    DataFile,
    Fingerprint,
    FingerprintObservation,
    InputResource,
    ResourceIdentity,
    load_data_file,
    observe_fingerprint,
)
from validation.command_diagnostics import (
    RejectedProducerIndex,
    rejected_producer_message,
)
from validation.commands import (
    CommandContext,
    CommandDiscoveryFailure,
    Invocation,
    command_input_names,
    discover_commands,
    order_invocations,
)
from validation.fingerprint_cache import FingerprintCache, FingerprintCacheError
from validation.output_support import (
    confirmed_output_record,
    declared_output_resource,
    require_current_execution_output,
    require_current_output_support,
    resolve_output_support,
)
from validation.provenance import (
    ProducerIndex,
    ProvenanceResult,
    ProvenanceV2Error,
    build_producer_index,
    evaluate_provenance,
    require_declared_producer,
)
from validation.pyrun_outputs import (
    PyrunOutputsFile,
    empty_pyrun_outputs,
    load_pyrun_outputs,
)
from validation.pyrun_state import (
    PYRUN_FILENAME,
    OutputOwnerIndex,
    PyrunFile,
    associate_execution,
    execution_output_owners,
    load_pyrun_state,
    resolve_execution_output,
)

from .context import LogContext, resolve_project_root
from .model import ActionError
from .scaffold import observe_entries


@dataclass
class LogMaterials:
    """One bounded same-log command view built with candidate data overrides."""

    log: LogContext
    project_root: Path
    invocations: tuple[Invocation, ...]
    roots: Mapping[str, Path]
    input_names: Mapping[Path, frozenset[str]]
    failures: Mapping[Path, tuple[CommandDiscoveryFailure, ...]]
    _support: dict[str, PyrunOutputsFile] = field(default_factory=dict)
    _states: dict[str, PyrunFile] = field(default_factory=dict)
    _owners: dict[str, OutputOwnerIndex] = field(default_factory=dict)
    _producer_index: ProducerIndex | None = field(default=None, init=False, repr=False)
    _rejected_index: RejectedProducerIndex | None = field(
        default=None, init=False, repr=False
    )

    def _explain_producer_failure(self, error: ProvenanceV2Error) -> None:
        """Attach relevant discovery failures without changing producer admission."""
        if error.code not in {"producer.missing", "lineage.missing"}:
            return
        if self._rejected_index is None:
            self._rejected_index = RejectedProducerIndex()
            for failures in self.failures.values():
                for failure in failures:
                    self._rejected_index.add(failure.error.observed)
        related = self._rejected_index.related(error.subject)
        if related:
            raise ActionError(
                error.code,
                f"{error}\n{rejected_producer_message(related)}",
                records=related,
                diagnostic_log=self.log.root,
            ) from error

    def confirmed(self, invocation: Invocation, material: str) -> bool:
        """Return confirmed support using the validator's exact output identity."""

        root = self._root(invocation)
        state = self._execution_state(invocation.material_owner, root)
        if state is not None:
            resolved = resolve_execution_output(
                invocation,
                material,
                project_root=self.project_root,
                association=associate_execution(
                    state, invocation, project_root=self.project_root
                ),
                owners=self._owners[invocation.material_owner],
            )
            return (
                resolved.association is not None
                and not resolved.association.execution.requires_reproduction
            )
        return confirmed_output_record(
            invocation,
            material,
            entry_root=root,
            project_root=self.project_root,
            support=self._output_support(invocation.material_owner, root),
        )

    @staticmethod
    def _current_input_observations(
        invocation: Invocation, cache: FingerprintCache
    ) -> Mapping[str, Fingerprint]:
        """Observe the invocation inputs once for its output-signature check."""

        observations: dict[str, Fingerprint] = {}
        for relationship in invocation.inputs:
            resource = relationship.input_resource
            if resource is None:
                continue
            observed = cache.observe_resource(resource).fingerprint
            prior = observations.setdefault(resource.name, observed)
            if prior != observed:
                raise ActionError(
                    "provenance.output.signature_unsupported",
                    f"conflicting current input observation: {resource.name}",
                )
        return observations

    def require_generated(self, resource: InputResource) -> ProvenanceResult:
        """Require current confirmed same-log production for one declaration."""

        observations: dict[str, Fingerprint] = {}
        try:
            with FingerprintCache(
                self.project_root, writable=False, reuse=True
            ) as cache:

                def validate(
                    invocation: Invocation, material: str
                ) -> Mapping[str, object]:
                    root = self._root(invocation)
                    current_inputs = self._current_input_observations(invocation, cache)
                    state = self._execution_state(invocation.material_owner, root)
                    if state is not None:
                        execution_output = resolve_execution_output(
                            invocation,
                            material,
                            project_root=self.project_root,
                            association=associate_execution(
                                state, invocation, project_root=self.project_root
                            ),
                            owners=self._owners[invocation.material_owner],
                        )
                        canonical = execution_output.path.resolve().as_posix()
                        current = observations.get(canonical)
                        if current is None:
                            declared = declared_output_resource(
                                invocation, execution_output.path
                            )
                            observation = (
                                observe_fingerprint(declared)
                                if declared is not None
                                else (
                                    cache.observe_directory(execution_output.path)
                                    if execution_output.path.is_dir()
                                    else cache.observe_regular_file(
                                        execution_output.path
                                    )
                                )
                            )
                            current = observation.fingerprint
                            observations[canonical] = current
                        execution = require_current_execution_output(
                            invocation,
                            execution_output,
                            current_output=current,
                            current_inputs=current_inputs,
                        )
                        return {"output": material, "execution": execution.as_dict()}
                    support = self._output_support(invocation.material_owner, root)
                    legacy_output = resolve_output_support(
                        invocation,
                        material,
                        entry_root=root,
                        project_root=self.project_root,
                        support=support,
                    )
                    canonical = legacy_output.path.resolve().as_posix()
                    current = observations.get(canonical)
                    if current is None:
                        declared = declared_output_resource(
                            invocation, legacy_output.path
                        )
                        if declared is not None:
                            observation = observe_fingerprint(declared)
                        else:
                            path = legacy_output.path
                            observation = (
                                cache.observe_directory(path)
                                if path.is_dir()
                                else cache.observe_regular_file(path)
                            )
                        current = observation.fingerprint
                        observations[canonical] = current
                    record = require_current_output_support(
                        invocation,
                        legacy_output,
                        current_output=current,
                        current_inputs=current_inputs,
                    )
                    return {"output": material, "record": record.as_dict()}

                result = evaluate_provenance(
                    resource.canonical_target,
                    self.invocations,
                    producer_validator=validate,
                    confirmed_record=self.confirmed,
                    producer_index=self._index(),
                )
        except ProvenanceV2Error as error:
            self._explain_producer_failure(error)
            raise
        except FingerprintCacheError as error:
            raise ActionError(
                "provenance.observation.unavailable", str(error)
            ) from error
        observe_fingerprint(resource)
        return result

    def require_pending_generated(self, resource: InputResource) -> Invocation:
        """Require one producer while leaving absent execution proof pending."""

        try:
            producer = require_declared_producer(
                resource.canonical_target,
                self.invocations,
                producer_index=self._index(),
                allow_missing=True,
            )
        except ProvenanceV2Error as error:
            self._explain_producer_failure(error)
            raise
        root = self._root(producer)
        try:
            with FingerprintCache(
                self.project_root, writable=False, reuse=True
            ) as cache:
                current_inputs = self._current_input_observations(producer, cache)
                state = self._execution_state(producer.material_owner, root)
                if state is not None:
                    execution_output = resolve_execution_output(
                        producer,
                        resource.canonical_target,
                        project_root=self.project_root,
                        association=associate_execution(
                            state, producer, project_root=self.project_root
                        ),
                        owners=self._owners[producer.material_owner],
                    )
                    if (
                        execution_output.owner is not None
                        and not execution_output.owner.execution.requires_reproduction
                    ):
                        current = observe_fingerprint(resource).fingerprint
                        require_current_execution_output(
                            producer,
                            execution_output,
                            current_output=current,
                            current_inputs=current_inputs,
                        )
                    return producer
                legacy_output = resolve_output_support(
                    producer,
                    resource.canonical_target,
                    entry_root=root,
                    project_root=self.project_root,
                    support=self._output_support(producer.material_owner, root),
                )
                if legacy_output.record is not None and legacy_output.record.confirmed:
                    if (
                        legacy_output.path.resolve().as_posix()
                        == resource.canonical_target
                    ):
                        current = observe_fingerprint(resource).fingerprint
                    else:
                        observation = (
                            cache.observe_directory(legacy_output.path)
                            if legacy_output.path.is_dir()
                            else cache.observe_regular_file(legacy_output.path)
                        )
                        current = observation.fingerprint
                    require_current_output_support(
                        producer,
                        legacy_output,
                        current_output=current,
                        current_inputs=current_inputs,
                    )
        except FingerprintCacheError as error:
            raise ActionError(
                "provenance.observation.unavailable", str(error)
            ) from error
        return producer

    def rerun_commands(
        self, entry_root: Path, *, old_name: str, new_name: str
    ) -> tuple[dict[str, object], ...]:
        """Return producer commands whose support still names a renamed input."""

        owner = entry_root.resolve().relative_to(self.log.root).as_posix()
        records: list[dict[str, object]] = []
        for invocation in self.invocations:
            if invocation.material_owner != owner or not any(
                relationship.named_input == new_name
                for relationship in invocation.inputs
            ):
                continue
            state = self._execution_state(owner, entry_root)
            if state is not None:
                association = associate_execution(
                    state, invocation, project_root=self.project_root
                )
                material = next(
                    (relationship.path for relationship in invocation.outputs),
                    next(
                        (
                            collection.root
                            for collection in invocation.collections
                            if collection.direction == "output"
                            and collection.root is not None
                        ),
                        None,
                    ),
                )
                if material is None:
                    continue
                execution_output = resolve_execution_output(
                    invocation,
                    material,
                    project_root=self.project_root,
                    association=association,
                    owners=self._owners[owner],
                )
                execution = (
                    association.execution
                    if association is not None
                    else (
                        execution_output.owner.execution
                        if execution_output.owner is not None
                        else None
                    )
                )
                if execution is not None and old_name in dict(
                    execution.observed.inputs
                ):
                    records.append(
                        {
                            "document": invocation.document,
                            "fence": invocation.fence,
                            "ordinal": invocation.ordinal,
                            "tokens": list(invocation.tokens),
                        }
                    )
                continue
            support = self._output_support(owner, entry_root)
            requires_rerun = False
            for output in invocation.outputs:
                try:
                    legacy_output = resolve_output_support(
                        invocation,
                        output.path,
                        entry_root=entry_root,
                        project_root=self.project_root,
                        support=support,
                    )
                except ValueError:
                    continue
                record = legacy_output.record
                if record is not None and old_name in dict(record.inputs):
                    requires_rerun = True
                    break
            if requires_rerun:
                records.append(
                    {
                        "document": invocation.document,
                        "fence": invocation.fence,
                        "ordinal": invocation.ordinal,
                        "tokens": list(invocation.tokens),
                    }
                )
        return tuple(records)

    def _root(self, invocation: Invocation) -> Path:
        root = self.roots.get(invocation.material_owner)
        if root is None:
            raise ActionError(
                "producer.owner.invalid", f"unknown producer {invocation.identity}"
            )
        return root

    def _output_support(self, owner: str, root: Path) -> PyrunOutputsFile:
        support = self._support.get(owner)
        if support is not None:
            return support
        path = root / "pyrun-outputs.json"
        if path.exists() or path.is_symlink():
            support = load_pyrun_outputs(
                path,
                entry_root=root,
                project_root=self.project_root,
            )
        else:
            support = empty_pyrun_outputs(root)
        self._support[owner] = support
        return support

    def _execution_state(self, owner: str, root: Path) -> PyrunFile | None:
        """Load current state directly; legacy support remains read-only fallback."""

        current = root / PYRUN_FILENAME
        legacy = root / "pyrun-outputs.json"
        if (current.exists() or current.is_symlink()) and (
            legacy.exists() or legacy.is_symlink()
        ):
            raise ActionError(
                "pyrun.state.conflict", f"both execution-state formats exist: {root}"
            )
        if not current.exists() and not current.is_symlink():
            return None
        state = self._states.get(owner)
        if state is None:
            state = load_pyrun_state(
                current, entry_root=root, project_root=self.project_root
            )
            self._states[owner] = state
            self._owners[owner] = execution_output_owners(state)
        return state

    def _index(self) -> ProducerIndex:
        """Return the one producer index for this inspected command state."""

        if self._producer_index is None:
            self._producer_index = build_producer_index(self.invocations)
        return self._producer_index


def inspect_log_materials(
    log: LogContext,
    *,
    data_overrides: Mapping[Path, DataFile | None] | None = None,
) -> LogMaterials:
    """Discover same-log commands against exact current or candidate registries."""

    project_root = resolve_project_root(log.root)
    documents: list[tuple[Invocation, ...]] = []
    roots: dict[str, Path] = {}
    names: dict[Path, set[str]] = {}
    failures: dict[Path, list[CommandDiscoveryFailure]] = {}
    normalized_overrides = {
        root.resolve(): data for root, data in (data_overrides or {}).items()
    }
    observations: dict[tuple[str, str, ResourceIdentity], FingerprintObservation] = {}
    with ExitStack() as stack:
        try:
            cache = stack.enter_context(
                FingerprintCache(project_root, writable=False)
            )
        except FingerprintCacheError:
            cache = None

        def observe_input(resource: InputResource) -> FingerprintObservation:
            key = (resource.canonical_target, resource.kind, resource.identity)
            if key not in observations:
                try:
                    observations[key] = (
                        cache.observe_resource(resource)
                        if cache is not None
                        else observe_fingerprint(resource)
                    )
                except FingerprintCacheError:
                    observations[key] = observe_fingerprint(resource)
            return observations[key]

        for entry in observe_entries(log):
            root = entry.root.resolve()
            owner = root.relative_to(log.root).as_posix()
            roots[owner] = root
            data_file = (
                normalized_overrides[root]
                if root in normalized_overrides
                else _load_data(root)
            )
            for document in entry.documents:
                try:
                    text = document.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    raise ActionError(
                        "association.document_unavailable", f"{document}: {error}"
                    ) from error
                names.setdefault(root, set()).update(command_input_names(text))
                discovery = discover_commands(
                    text,
                    CommandContext(
                        log_id=log.root.as_posix(),
                        entry=document.stem,
                        document=document.relative_to(log.root).as_posix(),
                        entry_root=root,
                        log_root=log.root,
                        project_root=project_root,
                        data_file=data_file,
                        input_fingerprint_verifier=observe_input,
                    ),
                )
                documents.append(discovery.invocations)
                failures.setdefault(root, []).extend(discovery.failures)
    return LogMaterials(
        log,
        project_root,
        order_invocations(documents),
        roots,
        {root: frozenset(values) for root, values in names.items()},
        {root: tuple(values) for root, values in failures.items()},
    )


def _load_data(root: Path) -> DataFile | None:
    path = root / "data.json"
    legacy = root / "data.csv"
    if path.exists() and legacy.exists():
        raise ActionError(
            "data.file.location_invalid", f"conflicting data.json and data.csv: {root}"
        )
    if legacy.exists() or legacy.is_symlink():
        raise ActionError("data.file.location_invalid", f"legacy data.csv: {legacy}")
    return (
        load_data_file(path, entry_root=root)
        if path.exists() or path.is_symlink()
        else None
    )

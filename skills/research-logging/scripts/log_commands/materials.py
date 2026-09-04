"""Current recorded-command and producer state for authoring commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from research_log_data import (
    DataFile,
    Fingerprint,
    InputResource,
    load_data_file,
    observe_fingerprint,
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
    require_current_output_support,
    resolve_output_support,
)
from validation.provenance import ProvenanceResult, evaluate_provenance
from validation.pyrun_outputs import (
    PyrunOutputsFile,
    empty_pyrun_outputs,
    load_pyrun_outputs,
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

    def confirmed(self, invocation: Invocation, material: str) -> bool:
        """Return confirmed support using the validator's exact output identity."""

        root = self._root(invocation)
        return confirmed_output_record(
            invocation,
            material,
            entry_root=root,
            project_root=self.project_root,
            support=self._output_support(invocation.material_owner, root),
        )

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
                    support = self._output_support(invocation.material_owner, root)
                    resolved = resolve_output_support(
                        invocation,
                        material,
                        entry_root=root,
                        project_root=self.project_root,
                        support=support,
                    )
                    canonical = resolved.path.resolve().as_posix()
                    current = observations.get(canonical)
                    if current is None:
                        path = resolved.path
                        observation = (
                            cache.observe_directory(path)
                            if path.is_dir()
                            else cache.observe_regular_file(path)
                        )
                        current = observation.fingerprint
                        observations[canonical] = current
                    record = require_current_output_support(
                        invocation,
                        resolved,
                        current_output=current,
                    )
                    return {"output": material, "record": record.as_dict()}

                result = evaluate_provenance(
                    resource.canonical_target,
                    self.invocations,
                    producer_validator=validate,
                    confirmed_record=self.confirmed,
                )
        except FingerprintCacheError as error:
            raise ActionError(
                "provenance.observation.unavailable", str(error)
            ) from error
        observed = observe_fingerprint(resource).fingerprint
        if observed != resource.fingerprint:
            raise ActionError(
                "data.fingerprint.mismatch",
                "generated target changed during producer verification",
            )
        return result

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
            support = self._output_support(owner, entry_root)
            requires_rerun = False
            for output in invocation.outputs:
                try:
                    resolved = resolve_output_support(
                        invocation,
                        output.path,
                        entry_root=entry_root,
                        project_root=self.project_root,
                        support=support,
                    )
                except ValueError:
                    continue
                record = resolved.record
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
        support = (
            load_pyrun_outputs(
                path,
                entry_root=root,
                project_root=self.project_root,
            )
            if path.exists() or path.is_symlink()
            else empty_pyrun_outputs(root)
        )
        self._support[owner] = support
        return support


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
    for entry in observe_entries(log):
        root = entry.root.resolve()
        owner = root.relative_to(log.root).as_posix()
        roots[owner] = root
        data_file = normalized_overrides.get(root, _load_data(root))
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

"""Current authored and normalized consumers for graph-level authoring."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from effective_code import EffectiveCodeError, analyze_effective_code
from python_execution import PythonExecutionContext
from research_log_data import (
    DataFile,
    input_token_parts,
    load_data_file,
    resolve_input_token,
)
from validation.commands import CommandDeclarationContext, Invocation, index_commands
from validation.evidence import authored_eid_comments, load_evidence_file
from validation.operation_state import (
    begin_registry_transaction,
    finish_guarded_publication,
)
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import load_pyrun_state

from .authoring_transactions import data_publication_transaction
from .context import EntryContext, resolve_project_root
from .materials import inspect_log_materials
from .model import ActionError
from .scaffold import observe_physical_entries
from .storage import PublicationError, atomic_write_texts


def token_name(value: str) -> str | None:
    """Extract a declared name without inspecting its target."""

    parts = input_token_parts(value)
    return parts[0] if parts else None


def source_tokens(definition: str) -> tuple[str, ...]:
    """Read only declared source names, without evaluating evidence."""

    lexer = shlex.shlex(definition, posix=True, punctuation_chars=";")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        values = [
            token.partition("=")[2] for token in lexer if token.startswith("source=")
        ]
    except ValueError as error:
        raise ActionError("evidence.definition.invalid", str(error)) from error
    return tuple(
        value if value.startswith("<") else "<" + value.partition("/")[0] + ">"
        for value in values
    )


def authored_evidence_uses(
    entry: EntryContext, name: str, *, excluded_ids: frozenset[str] = frozenset()
) -> tuple[dict[str, Any], ...]:
    """Find relevant Markdown-only evidence users without parsing unrelated ones."""

    hint = re.compile(
        r"(?:^|[\s;])source=(?:[\"'])?<?" + re.escape(name) + r"(?=[/>\s;\"']|$)"
    )
    uses: list[dict[str, Any]] = []
    for document in sorted(entry.root.glob("*.md")):
        for marker in authored_eid_comments(document.read_text(encoding="utf-8")):
            if marker["id"] in excluded_ids or not hint.search(marker["definition"]):
                continue
            try:
                consumes = any(
                    token_name(source) == name
                    for source in source_tokens(marker["definition"])
                )
            except ActionError:
                consumes = True
            if consumes:
                uses.append(
                    {
                        "entry": entry.id,
                        "evidence": marker["id"],
                        "document": document.relative_to(entry.log.root).as_posix(),
                    }
                )
    return tuple(uses)


def related_entries(
    entry: EntryContext, name: str
) -> tuple[tuple[EntryContext, DataFile | None], ...]:
    """Select the owner and explicit same-name cross-entry references."""

    related = []
    for observed in observe_physical_entries(entry.log):
        candidate = EntryContext(entry.log, observed.id, observed.root)
        path = candidate.root / "data.json"
        data = (
            load_data_file(path, entry_root=candidate.root)
            if path.exists() or path.is_symlink()
            else None
        )
        item = data.by_name.get(name) if data else None
        if (
            candidate.id == entry.id
            or item is not None
            and item.reference_entry == entry.id
        ):
            related.append((candidate, data))
    return tuple(related)


def authored_uses(entry: EntryContext, name: str) -> tuple[dict[str, Any], ...]:
    """Find current Markdown command and evidence owners of one name."""

    uses = []
    for document in sorted(entry.root.glob("*.md")):
        text = document.read_text(encoding="utf-8")
        relative = document.relative_to(entry.log.root).as_posix()
        context = CommandDeclarationContext(
            entry.log.root.as_posix(),
            entry.id,
            relative,
            entry.root,
            entry.log.root,
            resolve_project_root(entry.root),
            None,
        )
        indexed = index_commands(text, context)
        for declaration in indexed.declarations:
            if any(token_name(value) == name for value in declaration.tokens):
                uses.append(
                    {
                        "entry": entry.id,
                        "document": relative,
                        "command": declaration.parsed.cid,
                    }
                )
        for marker in authored_eid_comments(text):
            if any(
                token_name(value) == name
                for value in source_tokens(marker["definition"])
            ):
                uses.append(
                    {"entry": entry.id, "document": relative, "evidence": marker["id"]}
                )
    return tuple(uses)


def normalized_uses(
    entry: EntryContext, name: str, *, include_targets: bool = True
) -> tuple[dict[str, Any], ...]:
    """Find normalized recipe/evidence uses that still require owning syncs."""

    uses: list[dict[str, Any]] = []
    path = entry.root / "evidence.json"
    if path.exists() or path.is_symlink():
        evidence = load_evidence_file(
            path, log_root=entry.log.root, entry_root=entry.root
        )
        uses.extend(
            {"entry": entry.id, "evidence": record.id, "registry": str(path)}
            for record in evidence.records
            if any(token_name(source.source) == name for source in record.sources)
        )
    path = entry.root / "pyrun.json"
    if path.exists() or path.is_symlink():
        data_path = entry.root / "data.json"
        data = (
            load_data_file(data_path, entry_root=entry.root)
            if data_path.exists()
            else None
        )
        resource = data.by_name.get(name) if data else None
        state = load_pyrun_state(
            path, entry_root=entry.root, project_root=resolve_project_root(entry.root)
        )
        for cid, identity, execution in state.execution_items():
            if (
                name in execution.recipe.inputs
                or include_targets
                and resource is not None
                and any(
                    output_target_path(
                        target,
                        entry_root=entry.root,
                        project_root=resolve_project_root(entry.root),
                    )
                    .resolve()
                    .as_posix()
                    == resource.canonical_target
                    for target, _ in execution.recipe.outputs
                )
                or any(
                    token_name(value) == name for value in execution.recipe.parameters
                )
            ):
                uses.append(
                    {
                        "entry": entry.id,
                        "command": cid,
                        "execution_id": identity,
                        "registry": str(path),
                    }
                )
    return tuple(uses)


def declaration_uses(entry: EntryContext, name: str) -> tuple[dict[str, Any], ...]:
    """Return every live authored, normalized, or reference use."""

    uses: list[dict[str, Any]] = []
    for candidate, _ in related_entries(entry, name):
        uses.extend(authored_uses(candidate, name))
        uses.extend(normalized_uses(candidate, name))
        if candidate.id != entry.id:
            uses.append({"entry": candidate.id, "from_entry": entry.id, "name": name})
    return tuple(uses)


def describe_uses(uses: tuple[dict[str, Any], ...]) -> str:
    """Give the first rejection concrete owners and locations."""

    return "\n" + "\n".join(
        sorted(
            {
                str(use.get("entry", ""))
                + ": "
                + str(use.get("command", use.get("evidence", "reference")))
                + " — "
                + str(
                    use.get("document", use.get("registry", use.get("from_entry", "")))
                )
                for use in uses
            }
        )
    )


def material_consumers(
    entry: EntryContext,
    target: Path,
    *,
    excluded_command: str | None = None,
    data_overrides: Mapping[Path, DataFile | None] | None = None,
    include_reached_code: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Find physical consumers, including different names for the same material."""

    uses = material_consumers_many(
        entry,
        (target,),
        data_overrides=data_overrides,
        include_reached_code=include_reached_code,
    )[target]
    return tuple(
        use
        for use in uses
        if not (
            excluded_command is not None
            and use.get("entry") == entry.id
            and use.get("command") == excluded_command
        )
    )


def material_consumers_many(
    entry: EntryContext,
    targets: tuple[Path, ...],
    *,
    data_overrides: Mapping[Path, DataFile | None] | None = None,
    include_reached_code: bool = False,
    retained_origins: frozenset[Path] = frozenset(),
) -> dict[Path, tuple[dict[str, Any], ...]]:
    """Find consumers with one discovery, optionally exempting retained origins.

    For targets explicitly retained as origins, only file/directory origin-input
    uses are exempt. Generated inputs, outputs and reached code remain consumers.
    """

    if not targets:
        return {}
    consumers: dict[Path, list[dict[str, Any]]] = {target: [] for target in targets}
    resolved_targets = {target: target.resolve() for target in consumers}
    retained_origins = frozenset(target.resolve() for target in retained_origins)
    materials = inspect_log_materials(entry.log, data_overrides=data_overrides)
    code_cache: dict[tuple[Path, Path], tuple[Path, ...]] = {}
    for invocation in materials.invocations:
        origin_paths = _origin_input_paths(invocation)
        paths = [
            Path(relationship.path).resolve()
            for relationship in (*invocation.inputs, *invocation.outputs)
        ]
        paths.extend(
            Path(collection.root).resolve()
            for collection in invocation.collections
            if collection.root
        )
        if invocation.script:
            script = Path(invocation.script).resolve()
            paths.append(script)
            if include_reached_code and any(
                target.suffix == ".py" or target.is_dir()
                for target in resolved_targets.values()
            ):
                owner_root = materials.roots[invocation.material_owner]
                cache_key = (script, owner_root)
                if cache_key not in code_cache:
                    code_cache[cache_key] = _reached_code_paths(
                        script,
                        owner_root,
                        entry.log.root,
                        materials.project_root,
                    )
                paths.extend(code_cache[cache_key])
                origin_paths.difference_update(code_cache[cache_key])
        for target, resolved in resolved_targets.items():
            if any(
                _resolved_paths_overlap(resolved, path)
                and (resolved not in retained_origins or path not in origin_paths)
                for path in paths
            ):
                consumers[target].append(
                    {
                        "entry": invocation.entry,
                        "command": invocation.cid,
                        "document": invocation.document,
                    }
                )
    evidence_consumers = _material_evidence_many(
        entry,
        tuple(consumers),
        data_overrides=data_overrides,
        retained_origins=retained_origins,
    )
    for target in consumers:
        consumers[target].extend(evidence_consumers[target])
    return {target: tuple(uses) for target, uses in consumers.items()}


def _origin_input_paths(invocation: Invocation) -> set[Path]:
    origins = {
        Path(relationship.path).resolve()
        for relationship in invocation.inputs
        if relationship.origin
        and relationship.input_resource is not None
        and relationship.input_resource.kind in {"file", "directory"}
    }
    origins.update(
        Path(relationship.input_resource.canonical_target).resolve()
        for relationship in invocation.inputs
        if relationship.origin
        and relationship.input_resource is not None
        and relationship.input_resource.kind == "directory"
    )
    dependencies = {
        Path(relationship.path).resolve() for relationship in invocation.outputs
    }
    dependencies.update(
        Path(relationship.path).resolve()
        for relationship in invocation.inputs
        if not relationship.origin
    )
    dependencies.update(
        Path(collection.root).resolve()
        for collection in invocation.collections
        if collection.direction == "output" and collection.root
    )
    if invocation.script:
        dependencies.add(Path(invocation.script).resolve())
    return origins - dependencies


def _reached_code_paths(
    script: Path, entry_root: Path, log_root: Path, project_root: Path
) -> tuple[Path, ...]:
    if script.suffix != ".py":
        return ()
    try:
        context = PythonExecutionContext.for_research_script(
            script,
            entry_root=entry_root,
            log_root=log_root,
            project_root=project_root,
        )
        analysis = analyze_effective_code(
            script,
            project_root=project_root,
            import_roots=context.import_roots,
        )
    except (EffectiveCodeError, OSError, ValueError):
        return ()
    return tuple(path for path in analysis.reached_sources if path != script)


def _material_evidence_many(
    entry: EntryContext,
    targets: tuple[Path, ...],
    *,
    data_overrides: Mapping[Path, DataFile | None] | None = None,
    retained_origins: frozenset[Path] = frozenset(),
) -> dict[Path, tuple[dict[str, Any], ...]]:
    consumers: dict[Path, list[dict[str, Any]]] = {target: [] for target in targets}
    for observed in observe_physical_entries(entry.log):
        data_path = observed.root / "data.json"
        evidence_path = observed.root / "evidence.json"
        if not data_path.exists() or not evidence_path.exists():
            continue
        data = (
            data_overrides[observed.root]
            if data_overrides is not None and observed.root in data_overrides
            else load_data_file(data_path, entry_root=observed.root)
        )
        if data is None:
            continue
        evidence = load_evidence_file(
            evidence_path, log_root=entry.log.root, entry_root=observed.root
        )
        for record in evidence.records:
            for target in targets:
                relevant = [
                    source
                    for source in record.sources
                    if (resource := data.by_name.get(token_name(source.source) or ""))
                    is not None
                    and paths_overlap(target, Path(resource.canonical_target))
                    and not (
                        target.resolve() in retained_origins
                        and resource.origin
                        and resource.kind in {"file", "directory"}
                    )
                ]
                if any(
                    paths_overlap(
                        target, Path(resolve_input_token(source.source, data).path)
                    )
                    for source in relevant
                ):
                    consumers[target].append(
                        {
                            "entry": observed.id,
                            "evidence": record.id,
                            "document": record.document,
                        }
                    )
    return {target: tuple(uses) for target, uses in consumers.items()}


def paths_overlap(first: Path, second: Path) -> bool:
    return _resolved_paths_overlap(first.resolve(), second.resolve())


def _resolved_paths_overlap(first: Path, second: Path) -> bool:
    return (
        first == second or first.is_relative_to(second) or second.is_relative_to(first)
    )


def publish_updates(
    entries: tuple[EntryContext, ...], updates: Mapping[Path, str | None]
) -> None:
    """Publish coupled registry changes using existing per-entry residue guards."""

    with data_publication_transaction(entries, updates):
        _publish_updates_locked(entries, updates)


def publish_same_target_location_repair(entry: EntryContext, text: str) -> None:
    """Publish a proven same-target registry repair without artifact writes."""

    _publish_updates_locked((entry,), {entry.root / "data.json": text})


def _publish_updates_locked(
    entries: tuple[EntryContext, ...], updates: Mapping[Path, str | None]
) -> None:
    residues = []
    try:
        for entry in entries:
            residues.append(begin_registry_transaction(entry.log.root, entry.id))
        atomic_write_texts(updates)
    except PublicationError as error:
        if error.rollback_complete:
            for residue in residues:
                finish_guarded_publication(residue)
        raise
    except BaseException:
        for residue in residues:
            finish_guarded_publication(residue)
        raise
    for residue in residues:
        finish_guarded_publication(residue)

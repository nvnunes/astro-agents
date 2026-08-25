"""Versioned policy composition for research-log validation lifecycles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .adjudication import (
    AdjudicationPreparationPolicy,
    prepare_adjudication,
)
from .commands import (
    SCRIPT_SUFFIXES,
    commands,
    complete_script_dependency_graph,
)
from .compatibility import (
    COMPONENT_VERSIONS,
    INPUT_PROJECTION_VERSIONS,
)
from .contracts import (
    AdjudicationRecord,
    ScanRecord,
    ValidationMetrics,
)
from .discovery import (
    entry_evidence_record,
    parse_markdown,
)
from .evidence import (
    inspect_structure,
    mechanical_evidence_support,
)
from .identities import (
    entry_validation_identity,
    summary_validation_identity,
)
from .incremental import (
    IncrementalOperations,
    dependency_identity_snapshot,
    orphan_item_fingerprints,
)
from .inventory import (
    MaterialInventoryPolicy,
    display_path,
    file_identity,
    hash_file,
    infer_project_root,
)
from .render import (
    RenderLifecyclePolicy,
)
from .scan import (
    EntryScanPolicy,
    IdentityInspectionPolicy,
    ScanLifecyclePolicy,
    ScanRequest,
)
from .scan import scan_log as run_scan
from .sharded_state import STATE_DIRECTORY

SCAN_SCHEMA_VERSION = 18
ADJUDICATION_SCHEMA_VERSION = 8
RULES_VERSION = "research-log-validation-v48"
ORPHAN_INVENTORY_VERSION = 7
VALIDATION_RECORD_FILENAMES = (
    "validation.md",
    STATE_DIRECTORY,
)
OWNED_INVENTORY_EXCLUDED_NAMES = frozenset(
    {
        "data.csv",
        "evidence.csv",
        "refs.bib",
        STATE_DIRECTORY,
        *VALIDATION_RECORD_FILENAMES,
    }
)
MATERIAL_INVENTORY_POLICY = MaterialInventoryPolicy(
    frozenset(SCRIPT_SUFFIXES), OWNED_INVENTORY_EXCLUDED_NAMES
)


def _content_identity(path: Path) -> dict[str, Any]:
    digest, size = hash_file(path)
    return {"size": size, "sha256": digest}


def incremental_operations() -> IncrementalOperations:
    """Return current concrete mechanics for incremental outcome reuse."""

    return IncrementalOperations(
        dependency_snapshot=dependency_identity_snapshot,
        orphan_fingerprints=orphan_item_fingerprints,
    )


def scan_policy() -> ScanLifecyclePolicy:
    """Return the current complete scan contract."""

    return ScanLifecyclePolicy(
        SCAN_SCHEMA_VERSION,
        ORPHAN_INVENTORY_VERSION,
        VALIDATION_RECORD_FILENAMES,
        MATERIAL_INVENTORY_POLICY,
        EntryScanPolicy(
            parse_markdown, entry_evidence_record, commands, inspect_structure
        ),
        IdentityInspectionPolicy(
            display_path,
            file_identity,
            summary_validation_identity,
            entry_validation_identity,
            inspect_structure,
        ),
        complete_script_dependency_graph,
        incremental_operations(),
        COMPONENT_VERSIONS,
        INPUT_PROJECTION_VERSIONS,
    )


def scan_log(
    summary_path: Path,
    jobs: int = 8,
    prior_record: Optional[dict[str, Any]] = None,
    prior_cache: Optional[dict[str, Any]] = None,
    mode: str = "standard",
) -> tuple[ScanRecord, ValidationMetrics]:
    """Run one independently scoped scan through the current policy."""

    return run_scan(
        ScanRequest(
            summary_path,
            jobs,
            prior_record,
            prior_cache,
            RULES_VERSION,
            mode,
            scan_policy(),
            infer_project_root(summary_path),
        )
    )


def prepare_adjudication_record(
    scan: ScanRecord, date: str, mode: str = "standard"
) -> AdjudicationRecord:
    """Prepare one adjudication under the current schema and mechanics."""

    return prepare_adjudication(
        scan,
        date,
        RULES_VERSION,
        AdjudicationPreparationPolicy(
            ADJUDICATION_SCHEMA_VERSION,
            lambda row, source: mechanical_evidence_support(
                row, source, inspect_structure
            ),
        ),
        mode,
    )


def render_policy() -> RenderLifecyclePolicy:
    """Return the current canonical render contract."""

    return RenderLifecyclePolicy(
        SCAN_SCHEMA_VERSION,
        ADJUDICATION_SCHEMA_VERSION,
        RULES_VERSION,
        ORPHAN_INVENTORY_VERSION,
        VALIDATION_RECORD_FILENAMES,
        MATERIAL_INVENTORY_POLICY,
        COMPONENT_VERSIONS,
        INPUT_PROJECTION_VERSIONS,
    )

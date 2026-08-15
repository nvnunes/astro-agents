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
    GRAPH_CONTRACT_VERSION,
    INPUT_PROJECTION_VERSIONS,
)
from .contracts import (
    AdjudicationRecord,
    CanonicalRepositoryView,
    RenderCounts,
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
from .graph_store import SLICE_FILENAME
from .identities import (
    entry_validation_identity,
    summary_validation_identity,
)
from .incremental import (
    IncrementalOperations,
    current_check_dependency_contract,
    dependency_identity_snapshot,
    orphan_item_fingerprints,
)
from .inventory import (
    MaterialInventoryPolicy,
    display_path,
    file_identity,
    hash_file,
)
from .render import (
    RenderLifecyclePolicy,
    check_graph_slice,
)
from .render import (
    lint_records as render_lint_records,
)
from .render import (
    render_records as render_validation_records,
)
from .scan import (
    EntryScanPolicy,
    IdentityInspectionPolicy,
    ScanLifecyclePolicy,
    ScanRequest,
)
from .scan import (
    scan_log as run_scan,
)

SCAN_SCHEMA_VERSION = 18
ADJUDICATION_SCHEMA_VERSION = 8
STATE_SCHEMA_VERSION = 11
RULES_VERSION = "research-log-validation-v44"
ORPHAN_INVENTORY_VERSION = 7
VALIDATION_RECORD_FILENAMES = (
    "validation-decisions.json",
    "validation.md",
    "validation-failures.md",
    "validation-state.json",
    SLICE_FILENAME,
)
OWNED_INVENTORY_EXCLUDED_NAMES = frozenset(
    {
        "data.csv",
        "evidence.csv",
        "refs.bib",
        ".research-log-validation.lock",
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
        dependency_contract=current_check_dependency_contract,
        dependency_snapshot=dependency_identity_snapshot,
        graph_slice=check_graph_slice,
        orphan_fingerprints=orphan_item_fingerprints,
        report_identity=_content_identity,
    )


def scan_policy() -> ScanLifecyclePolicy:
    """Return the current complete scan contract."""

    return ScanLifecyclePolicy(
        SCAN_SCHEMA_VERSION,
        STATE_SCHEMA_VERSION,
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
        GRAPH_CONTRACT_VERSION,
    )


def scan_log(
    summary_path: Path,
    jobs: int = 8,
    prior_state: Optional[dict[str, Any]] = None,
    repository_index: Optional[CanonicalRepositoryView] = None,
    mode: str = "standard",
    prior_decisions: Optional[dict[str, Any]] = None,
) -> tuple[ScanRecord, ValidationMetrics]:
    """Run one scan through the current versioned policy."""

    return run_scan(
        ScanRequest(
            summary_path,
            jobs,
            prior_state,
            repository_index,
            RULES_VERSION,
            mode,
            scan_policy(),
            prior_decisions,
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
        STATE_SCHEMA_VERSION,
        RULES_VERSION,
        ORPHAN_INVENTORY_VERSION,
        VALIDATION_RECORD_FILENAMES,
        MATERIAL_INVENTORY_POLICY,
        COMPONENT_VERSIONS,
        INPUT_PROJECTION_VERSIONS,
        GRAPH_CONTRACT_VERSION,
    )


def render_records(
    adjudication: AdjudicationRecord,
    scan: ScanRecord,
    output_dir: Path,
) -> RenderCounts:
    """Render through the current canonical policy."""

    return render_validation_records(adjudication, scan, output_dir, render_policy())


def lint_records(
    output_dir: Path,
    expected_entry_order: Optional[list[str]] = None,
    expected_local_snapshot_identity: Optional[str] = None,
) -> dict[str, Any]:
    """Lint through the current canonical policy."""

    return render_lint_records(
        output_dir,
        render_policy(),
        expected_entry_order,
        expected_local_snapshot_identity,
    )

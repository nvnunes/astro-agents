"""Per-log migration from v43 bundles to local durable publication."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import ValidationToolError
from .decision_store import build_decision_store, decode_decision_store
from .graph import GraphContractError
from .graph_store import (
    SLICE_FILENAME,
    discover_repository_summaries,
    load_legacy_slice,
    repository_identity_path,
)
from .inventory import directory_membership_identity, find_project_root
from .records import (
    PublicationGuard,
    publish_record_bundle,
    record_bundle_identity,
    validation_lock,
)
from .report import install_status_summary, parse_markdown_rows, report_update_date
from .runtime import RULES_VERSION, lint_records
from .scan import legacy_local_snapshot_identity
from .state import decode_legacy_validation_state

LEGACY_GENERATED_FILES = frozenset(
    {
        "validation.md",
        "validation-failures.md",
        "validation-state.json",
        SLICE_FILENAME,
        "validation-decisions.json",
    }
)
_DURABLE_FILES = ("validation-decisions.json", "validation.md")


class LocalPublicationMigrationError(ValidationToolError):
    """Raised when one v43 publication cannot be migrated safely."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalPublicationMigrationError(
            f"could not read JSON {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise LocalPublicationMigrationError(f"expected JSON object: {path}")
    return value


def _remediation_from_failure_report(text: str) -> str:
    lines = text.rstrip().splitlines()
    if not lines or lines[0] != "# Validation Failures":
        raise LocalPublicationMigrationError(
            "validation-failures.md must start with '# Validation Failures'"
        )
    transformed = ["## Remediation"]
    for line in lines[1:]:
        if line.startswith("### "):
            transformed.append("#" + line)
        elif line.startswith("## "):
            transformed.append("#" + line)
        elif line.startswith("# "):
            raise LocalPublicationMigrationError(
                "validation-failures.md contains an unexpected H1"
            )
        else:
            transformed.append(line)
    return "\n".join(transformed).rstrip() + "\n"


def migrate_report_text(
    report_text: str,
    failure_text: str | None,
    snapshot_identity: str,
) -> str:
    """Transform one v43 report without changing its detailed result rows."""

    before_rows = parse_markdown_rows(report_text)
    before_date = report_update_date(report_text)
    lines = report_text.rstrip().splitlines()
    local_lines = [
        index
        for index, line in enumerate(lines)
        if line.startswith("- Local snapshot identity:")
    ]
    expected_local = f"- Local snapshot identity: `{snapshot_identity}`"
    if local_lines:
        if len(local_lines) != 1 or lines[local_lines[0]] != expected_local:
            raise LocalPublicationMigrationError(
                "report has a conflicting local snapshot identity"
            )
    else:
        rules = [
            index
            for index, line in enumerate(lines)
            if line.startswith("- Validation-rules version:")
        ]
        if len(rules) != 1:
            raise LocalPublicationMigrationError(
                "report must contain one validation-rules version"
            )
        lines.insert(rules[0] + 1, expected_local)
        lines.insert(
            rules[0] + 2,
            "- Cross-log slice provenance: unavailable in the v43 report",
        )

    lines = [line for line in lines if not line.startswith("- Failures: ")]
    remediation = [
        index for index, line in enumerate(lines) if line == "## Remediation"
    ]
    if failure_text is not None:
        if remediation:
            raise LocalPublicationMigrationError(
                "report already contains Remediation while a failure file remains"
            )
        lines.extend(["", *_remediation_from_failure_report(failure_text).splitlines()])
    elif not remediation and any(
        "`FAIL`" in row[2:]
        for row in [*before_rows["summary"], *before_rows["entries"]]
    ):
        raise LocalPublicationMigrationError(
            "failed report rows have no validation-failures.md to migrate"
        )

    migrated = install_status_summary("\n".join(lines).rstrip() + "\n")
    if parse_markdown_rows(migrated) != before_rows:
        raise LocalPublicationMigrationError(
            "report migration changed detailed validation rows"
        )
    if report_update_date(migrated) != before_date:
        raise LocalPublicationMigrationError("report migration changed its date")
    failed_rows = sum(
        "`FAIL`" in row[2:]
        for row in [*before_rows["summary"], *before_rows["entries"]]
    )
    if sum(line.startswith("#### ") for line in migrated.splitlines()) != failed_rows:
        raise LocalPublicationMigrationError(
            "migrated remediation headings do not match failed rows"
        )
    return migrated


def _semantic_source_count(state: Mapping[str, Any]) -> int:
    checks = sum(
        bool(check.get("resolution") or check.get("findings"))
        for check in state["completed_checks"]
    )
    orphans = sum(
        len(disposition["items"]) for disposition in state["orphan_dispositions"]
    )
    return checks + orphans


def _legacy_cache_currentness(
    project_root: Path,
    summary_identity: str,
    state: Mapping[str, Any],
    maintained_summaries: list[str],
) -> dict[str, Any]:
    """Check v43 local cache hints without hashing retained artifacts."""

    local_root = Path(summary_identity).with_suffix("").as_posix()
    foreign_roots = tuple(
        f"{Path(candidate).with_suffix('').as_posix()}/"
        for candidate in maintained_summaries
        if Path(candidate).with_suffix("").as_posix() != local_root
    )
    changed: list[str] = []
    checked = 0
    for identity, expected in state["input_files"].items():
        if identity.startswith(foreign_roots):
            continue
        checked += 1
        path = repository_identity_path(identity, project_root)
        if not isinstance(expected, Mapping) or not {
            "size",
            "mtime_ns",
            "ctime_ns",
        } <= set(expected):
            changed.append(identity)
            continue
        try:
            status = path.stat()
        except OSError:
            changed.append(identity)
            continue
        if (
            status.st_size != expected["size"]
            or status.st_mtime_ns != expected["mtime_ns"]
            or status.st_ctime_ns != expected["ctime_ns"]
        ):
            changed.append(identity)
    ignored = {
        repository_identity_path(f"{local_root}/{name}", project_root)
        for name in (*LEGACY_GENERATED_FILES, ".research-log-validation.lock")
    }
    for identity, expected in state["directory_memberships"].items():
        if identity.startswith(foreign_roots):
            continue
        checked += 1
        path = repository_identity_path(identity, project_root)
        try:
            current = directory_membership_identity(path, ignored)
        except (OSError, ValidationToolError):
            changed.append(identity)
            continue
        if current != expected:
            changed.append(identity)
    return {
        "status": (
            "cache-hints-match"
            if not changed
            else "content-currentness-not-checked"
        ),
        "identities_checked": checked,
        "changed_cache_hints": sorted(changed),
        "artifacts_rehashed": 0,
        "semantic_review_performed": False,
    }


def plan_log_migration(summary: Path) -> dict[str, Any]:
    """Return the exact no-write migration plan for one canonical v43 log."""

    summary = summary.resolve()
    output_dir = summary.with_suffix("")
    generated_names = {
        path.name
        for path in output_dir.iterdir()
        if path.name.startswith("validation")
    }
    unclassified = generated_names - LEGACY_GENERATED_FILES
    if unclassified:
        raise LocalPublicationMigrationError(
            "unclassified generated files block migration: "
            + ", ".join(sorted(unclassified))
        )
    staging_prefix = f".{output_dir.name}-validation-staging-"
    staging_paths = sorted(
        path
        for path in output_dir.parent.iterdir()
        if path.name.startswith(staging_prefix)
    )
    if any(path.is_symlink() or not path.is_dir() for path in staging_paths):
        raise LocalPublicationMigrationError(
            "obsolete validation staging path is not a regular directory"
        )
    report_path = output_dir / "validation.md"
    failure_path = output_dir / "validation-failures.md"
    state_path = output_dir / "validation-state.json"
    slice_path = output_dir / SLICE_FILENAME
    for path in (report_path, state_path, slice_path):
        if not path.is_file() or path.is_symlink():
            raise LocalPublicationMigrationError(f"legacy record is invalid: {path}")

    state_bytes = state_path.read_bytes()
    slice_bytes = slice_path.read_bytes()
    state = decode_legacy_validation_state(_read_json(state_path))
    try:
        slice_summary, _ = load_legacy_slice(_read_json(slice_path))
    except GraphContractError as exc:
        raise LocalPublicationMigrationError(
            f"legacy graph slice is invalid: {slice_path}: {exc}"
        ) from exc
    project_root = find_project_root(summary)
    expected_summary = summary.relative_to(project_root).as_posix()
    if slice_summary != expected_summary:
        raise LocalPublicationMigrationError(
            "legacy graph slice belongs to a different summary"
        )
    if state["validation_rules_version"] != RULES_VERSION:
        raise LocalPublicationMigrationError(
            "legacy state uses a different validation-rules version"
        )

    maintained_summaries = [
        candidate.relative_to(project_root).as_posix()
        for candidate in discover_repository_summaries(project_root)
    ]
    snapshot = legacy_local_snapshot_identity(
        expected_summary,
        state,
        maintained_summaries,
    )
    currentness = _legacy_cache_currentness(
        project_root,
        expected_summary,
        state,
        maintained_summaries,
    )
    report_text = report_path.read_text(encoding="utf-8")
    failure_text = (
        failure_path.read_text(encoding="utf-8") if failure_path.is_file() else None
    )
    migrated_report = migrate_report_text(report_text, failure_text, snapshot)
    store = build_decision_store(
        state["completed_checks"],
        state["orphan_dispositions"],
        validation_rules_version=state["validation_rules_version"],
        local_snapshot_identity=snapshot,
        report_date=state["result"]["date"],
    )
    decode_decision_store(store)
    source_count = _semantic_source_count(state)
    unavailable_rationale = sum(
        judgment["rationale_provenance"] == "unavailable-in-v43"
        for judgment in store["judgments"]
    )
    return {
        "summary": expected_summary,
        "output_dir": output_dir.as_posix(),
        "local_snapshot_identity": snapshot,
        "currentness": currentness,
        "semantic_review_performed": currentness["semantic_review_performed"],
        "artifacts_rehashed": currentness["artifacts_rehashed"],
        "state_bytes": state_bytes,
        "slice_bytes": slice_bytes,
        "migrated_report": migrated_report,
        "decision_store": store,
        "judgments": {
            "native_reviewed": 0,
            "legacy_attested": len(store["judgments"]),
            "non_reusable": source_count - len(store["judgments"]),
            "unavailable_rationale": unavailable_rationale,
        },
        "actions": [
            {"disposition": "preserve", "path": "validation.md"},
            {
                "disposition": "transform",
                "path": "validation.md",
                "detail": "add local identity and in-report remediation",
            },
            {
                "disposition": "transform",
                "path": "validation-decisions.json",
                "detail": f"write {len(store['judgments'])} compatible judgments",
            },
            {
                "disposition": "preserve",
                "path": "validation-state.json",
                "detail": "retain schema 9 byte-identically for Phase 5",
            },
            {
                "disposition": "preserve",
                "path": SLICE_FILENAME,
                "detail": "retain schema 6 byte-identically for Phase 5",
            },
            *(
                [
                    {
                        "disposition": "remove",
                        "path": "validation-failures.md",
                        "detail": "delete after equivalent remediation publishes",
                    }
                ]
                if failure_text is not None
                else []
            ),
            *(
                {
                    "disposition": "remove",
                    "path": path.as_posix(),
                    "detail": "obsolete interrupted publication staging directory",
                }
                for path in staging_paths
            ),
        ],
        "staging_paths": [path.as_posix() for path in staging_paths],
    }


def _public_plan(plan: Mapping[str, Any], *, applied: bool) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key
        not in {
            "state_bytes",
            "slice_bytes",
            "migrated_report",
            "decision_store",
            "staging_paths",
        }
    } | {"applied": applied}


def migrate_log(summary: Path, *, apply: bool = False) -> dict[str, Any]:
    """Dry-run or publish one independently locked local migration."""

    if not apply:
        return _public_plan(plan_log_migration(summary), applied=False)
    output_dir = summary.resolve().with_suffix("")
    with validation_lock(output_dir):
        plan = plan_log_migration(summary)
        expected = record_bundle_identity(output_dir, _DURABLE_FILES)
        with tempfile.TemporaryDirectory(
            prefix="research-log-local-migration-"
        ) as raw:
            staged = Path(raw)
            (staged / "validation-decisions.json").write_text(
                json.dumps(
                    plan["decision_store"],
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (staged / "validation.md").write_text(
                plan["migrated_report"], encoding="utf-8"
            )
            publish_record_bundle(
                staged,
                output_dir,
                _DURABLE_FILES,
                PublicationGuard(expected),
            )
        failure_path = output_dir / "validation-failures.md"
        failure_path.unlink(missing_ok=True)
        for raw_path in plan["staging_paths"]:
            staging_path = Path(raw_path)
            if (
                staging_path.parent != output_dir.parent
                or not staging_path.name.startswith(
                    f".{output_dir.name}-validation-staging-"
                )
                or staging_path.is_symlink()
            ):
                raise LocalPublicationMigrationError(
                    f"refusing unsafe staging cleanup: {staging_path}"
                )
            shutil.rmtree(staging_path)
        if (output_dir / "validation-state.json").read_bytes() != plan["state_bytes"]:
            raise LocalPublicationMigrationError(
                "schema-9 state changed during migration"
            )
        if (output_dir / SLICE_FILENAME).read_bytes() != plan["slice_bytes"]:
            raise LocalPublicationMigrationError(
                "schema-6 slice changed during migration"
            )
        lint = lint_records(
            output_dir,
            expected_local_snapshot_identity=plan["local_snapshot_identity"],
        )
        if not lint["durable_ok"]:
            raise LocalPublicationMigrationError(
                "migrated durable records failed lint: "
                + "; ".join(lint["durable_issues"])
            )
    result = _public_plan(plan, applied=True)
    result["lint"] = lint
    return result


def cleanup_repository_artifacts(
    project_root: Path, *, apply: bool = False
) -> dict[str, Any]:
    """Plan or remove the exact obsolete repository-owned publication artifacts."""

    project_root = project_root.resolve()
    aggregate_dir = project_root / ".research-log-validation-index"
    repository_lock = project_root / ".research-log-validation.lock"
    targets: list[Path] = []
    if aggregate_dir.exists():
        if aggregate_dir.is_symlink() or not aggregate_dir.is_dir():
            raise LocalPublicationMigrationError(
                "obsolete repository aggregate path is unsafe"
            )
        contents = {path.name for path in aggregate_dir.iterdir()}
        if contents - {"manifest.json", "incoming.json"}:
            raise LocalPublicationMigrationError(
                "repository aggregate contains unclassified artifacts"
            )
        targets.append(aggregate_dir)
    if repository_lock.exists():
        if repository_lock.is_symlink() or not repository_lock.is_file():
            raise LocalPublicationMigrationError(
                "obsolete repository lock path is unsafe"
            )
        targets.append(repository_lock)
    summaries = sorted(project_root.rglob("*.md"))
    maintained = [
        summary
        for summary in summaries
        if (summary.with_suffix("") / "entries").is_dir()
    ]
    incomplete = [
        summary.relative_to(project_root).as_posix()
        for summary in maintained
        if not (summary.with_suffix("") / "validation-decisions.json").is_file()
        or (summary.with_suffix("") / "validation-failures.md").exists()
    ]
    if apply and incomplete:
        raise LocalPublicationMigrationError(
            "repository cleanup requires every maintained log to finish migration: "
            + ", ".join(incomplete)
        )
    if apply:
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
    return {
        "project_root": project_root.as_posix(),
        "applied": apply,
        "incomplete_logs": incomplete,
        "actions": [
            {
                "disposition": "remove",
                "path": target.relative_to(project_root).as_posix(),
            }
            for target in targets
        ],
    }

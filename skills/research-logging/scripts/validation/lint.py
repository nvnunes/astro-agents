"""Canonical validation-record linting."""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import (
    Any,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    cast,
)

from .contracts import ValidationToolError
from .decision_store import decode_decision_store
from .graph import GraphContractError
from .graph_store import load_slice
from .report import (
    ReportContractError,
    install_status_summary,
    parse_markdown_rows,
    report_update_date,
)
from .state import (
    ValidationState,
    ValidationStateContractError,
    decode_validation_state,
)

SUCCESS_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class LintPolicy(NamedTuple):
    """Versioned rendering policy required to lint one canonical bundle."""

    state_schema_version: int
    orphan_inventory_version: int
    orphan_target: str
    slice_filename: str


class ReportLint(NamedTuple):
    """Parsed report rows and failure counts used by canonical linting."""

    text: str
    entry_order: list[str]
    summary_rows: list[list[str]]
    entry_rows: list[list[str]]
    summary_failed: int
    entry_failed: int
    local_snapshot_identity: str


class _ReportRowsInput(NamedTuple):
    text: str
    summary_rows: list[list[str]]
    entry_rows: list[list[str]]
    entry_order: list[str]
    expected_entry_order: Optional[Sequence[str]]
    policy: LintPolicy
    issues: List[str]


class StateLint(NamedTuple):
    """Completed-check counts and referenced files found in canonical state."""

    successful: int
    failed: int
    files: set[str]


def _success_date(value: Any) -> bool:
    if not isinstance(value, str) or SUCCESS_DATE_RE.fullmatch(value) is None:
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _content_identity(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _lint_outcome(check: Mapping[str, Any], issues: List[str]) -> bool:
    if re.fullmatch(r"[0-9a-f]{64}", check["dependency_signature"]) is None:
        issues.append("state completed check lacks a dependency signature")
    result = check["result"]
    if check["check"] not in {
        "Integrity",
        "Provenance",
        "Reproducibility",
    } or not (_success_date(result) or result == "FAIL"):
        issues.append("state contains an invalid completed-check result")
    findings = check.get("findings", [])
    if result == "FAIL":
        if not findings or not all(findings):
            issues.append("failed state result lacks focused findings")
        return False
    if findings:
        issues.append("successful state result has failure findings")
    return True


def _lint_dependencies(
    check: Mapping[str, Any], successful: bool, issues: List[str]
) -> Tuple[set[str], set[str]]:
    dependencies = set()
    successful_dependencies = set()
    for dependency in check["dependencies"]:
        identity = dependency["identity"]
        dependencies.add(dependency["path"])
        if successful:
            successful_dependencies.add(dependency["path"])
        if successful and isinstance(identity, dict) and (
            identity == {"missing": True} or set(identity) == {"error"}
        ):
            issues.append("successful state result has an unavailable dependency")
    return dependencies, successful_dependencies


def _lint_completed_checks(
    state: ValidationState, issues: List[str]
) -> Tuple[int, int, set[str], set[str]]:
    check_keys = []
    dependencies: set[str] = set()
    successful_dependencies: set[str] = set()
    successful = 0
    failed = 0
    for check in state["completed_checks"]:
        succeeded = _lint_outcome(check, issues)
        successful += succeeded
        failed += not succeeded
        check_keys.append((check["entry"], check["target"], check["check"]))
        check_dependencies, successful_check_dependencies = _lint_dependencies(
            check, succeeded, issues
        )
        dependencies.update(check_dependencies)
        successful_dependencies.update(successful_check_dependencies)
    if len(check_keys) != len(set(check_keys)):
        issues.append("state contains duplicate completed-check records")
    return successful, failed, dependencies, successful_dependencies


def _lint_material(
    state: ValidationState,
    dependencies: set[str],
    successful_dependencies: set[str],
    report_path: Path,
    issues: List[str],
) -> set[str]:
    files = set(state["files"])
    directory_dependencies = set(state["directory_memberships"])
    if files - dependencies or dependencies - files - directory_dependencies:
        issues.append(
            "state file identities do not exactly match completed-check dependencies"
        )
    if re.fullmatch(r"[0-9a-f]{64}", state["input_fingerprint"]) is None:
        issues.append("state contains an invalid input fingerprint")
    if re.fullmatch(r"[0-9a-f]{64}", state["local_snapshot_identity"]) is None:
        issues.append("state contains an invalid local snapshot identity")
    for dependency in list(successful_dependencies):
        membership = state["files"].get(dependency, {})
        successful_dependencies.update(
            (Path(dependency) / member).as_posix()
            for member in membership.get("members", [])
            if isinstance(member, str)
        )
    if set(state["report"]) != {"size", "sha256"} or state[
        "report"
    ] != _content_identity(report_path):
        issues.append("state report identity does not match validation.md")
    return files


def _lint_cached_result(
    state: ValidationState,
    report_text: str,
    expected_counts: Mapping[str, int],
    issues: List[str],
) -> None:
    required = {
        "date",
        "mode",
        "requested_scope",
        "scope",
        "summary_rows",
        "summary_failed",
        "entry_rows",
        "entry_failed",
        "entries",
        "failed_entries",
        "failure_rows",
        "failures",
    }
    result = state["result"]
    if set(result) != required:
        issues.append("state contains a malformed cached result")
        return
    try:
        date = report_update_date(report_text)
    except ReportContractError:
        issues.append("validation.md lacks a valid update date")
    else:
        if result["date"] != date:
            issues.append("cached result date differs from validation.md")
    for key, expected in expected_counts.items():
        if result[key] != expected:
            issues.append(f"cached result {key} differs from validation.md")
    if len(result.get("failures", [])) != expected_counts["failure_rows"]:
        issues.append("cached failure inventory differs from validation.md")


def _lint_orphans(
    state: ValidationState,
    successful_dependencies: set[str],
    policy: LintPolicy,
    issues: List[str],
) -> None:
    seen_entries: set[str] = set()
    unresolved: set[str] = set()
    for disposition in state["orphan_dispositions"]:
        if disposition["inventory_version"] != policy.orphan_inventory_version:
            issues.append("state contains a malformed orphan disposition")
            continue
        items = disposition["items"]
        if not items:
            issues.append("state contains malformed orphan item dispositions")
        elif len({item["identity"] for item in items}) != len(items):
            issues.append("state contains malformed orphan item dispositions")
        else:
            unresolved.update(
                item["identity"] for item in items if item["decision"] == "unresolved"
            )
        entry_id = disposition["entry"]
        if entry_id in seen_entries:
            issues.append("state contains duplicate orphan dispositions")
        seen_entries.add(entry_id)
    conflicts = sorted(successful_dependencies & unresolved)
    if conflicts:
        issues.append(
            "unresolved orphan is a dependency of a successful check: "
            + "; ".join(conflicts)
        )


def _read_utf8(path: Path, label: str, issues: List[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        issues.append(f"{label} is not readable UTF-8: {exc}")
        return ""


def _read_json(path: Path, label: str, issues: List[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(f"{label} is not readable JSON: {exc}")
        return {}


def _lint_summary_rows(rows: Sequence[Sequence[str]], issues: List[str]) -> None:
    for row in rows:
        if len(row) != 4 or not (_success_date(row[3]) or row[3] == "`FAIL`"):
            issues.append("validation.md contains an invalid Summary result row")


def _lint_entry_rows(
    rows: Sequence[Sequence[str]], policy: LintPolicy, issues: List[str]
) -> None:
    for row in rows:
        is_orphan = len(row) == 6 and row[0] == policy.orphan_target
        if is_orphan:
            valid_checked = row[1:5] == ["`-`", "`N/A`", "`FAIL`", "`N/A`"]
            valid_checked = valid_checked and re.fullmatch(
                r"\d+ unresolved items?", row[5]
            ) is not None
            valid_reproduction = valid_checked
        else:
            valid_checked = len(row) == 6 and all(
                _success_date(value) or value == "`FAIL`" for value in row[2:4]
            )
            valid_reproduction = len(row) == 6 and (
                _success_date(row[4]) or row[4] in {"`FAIL`", "`-`", "`N/A`"}
            )
        if not valid_checked or not valid_reproduction:
            issues.append("validation.md contains an invalid entry result row")


def _reported_counts(report_text: str) -> dict[str, Tuple[int, int]]:
    reported = {}
    for scope in ("Summary", "Entry targets"):
        match = re.search(
            rf"^\| {re.escape(scope)} \| (\d+) \| (\d+) \|$",
            report_text,
            re.MULTILINE,
        )
        if match:
            reported[scope] = (int(match.group(1)), int(match.group(2)))
    return reported


def _lint_report(
    report_path: Path,
    expected_entry_order: Optional[Sequence[str]],
    policy: LintPolicy,
    issues: List[str],
) -> ReportLint:
    text = _read_utf8(report_path, "validation.md", issues)
    snapshot = re.search(
        r"^- Local snapshot identity: `([0-9a-f]{64})`$", text, re.MULTILINE
    )
    if snapshot is None:
        issues.append("validation.md lacks a valid local snapshot identity")
    try:
        if install_status_summary(text) != text:
            issues.append("validation.md Status Summary is missing or inconsistent")
    except ReportContractError as exc:
        issues.append(f"validation.md Status Summary is invalid: {exc}")
    if re.search(r"\bPASS\b", text):
        issues.append("validation.md contains PASS")
    if "| - |" in text:
        issues.append("validation.md contains a plain hyphen table cell")
    parsed = parse_markdown_rows(text)
    summary_rows = parsed["summary"]
    entry_rows = parsed["entries"]
    entry_order = parsed["entry_order"]
    summary_failed, entry_failed = _lint_report_rows(
        _ReportRowsInput(
            text,
            summary_rows,
            entry_rows,
            entry_order,
            expected_entry_order,
            policy,
            issues,
        )
    )
    return ReportLint(
        text,
        entry_order,
        summary_rows,
        entry_rows,
        summary_failed,
        entry_failed,
        snapshot.group(1) if snapshot is not None else "",
    )


def _lint_report_rows(inputs: _ReportRowsInput) -> tuple[int, int]:
    text, summary_rows, entry_rows, entry_order = inputs[:4]
    expected_entry_order, policy, issues = inputs[4:]
    summary_failed = sum(len(row) == 4 and row[3] == "`FAIL`" for row in summary_rows)
    entry_failed = sum(len(row) == 6 and "`FAIL`" in row[2:5] for row in entry_rows)
    bad_notes = sum(
        len(row) == 6
        and "`FAIL`" in row[2:5]
        and row[5] != "`-`"
        and not (
            row[0] == policy.orphan_target
            and re.fullmatch(r"\d+ unresolved items?", row[5])
        )
        for row in entry_rows
    )
    if bad_notes:
        issues.append(f"{bad_notes} failed entry rows have non-placeholder Notes")
    if expected_entry_order is not None:
        expected = list(expected_entry_order)
        scoped = [entry_id for entry_id in expected if entry_id in entry_order]
        if scoped != entry_order or len(entry_order) != len(set(entry_order)):
            issues.append("entry order does not match the maintained summary")
    _lint_summary_rows(summary_rows, issues)
    _lint_entry_rows(entry_rows, policy, issues)
    reported = _reported_counts(text)
    if reported.get("Summary") != (len(summary_rows), summary_failed):
        issues.append("reported Summary counts do not match table rows")
    if reported.get("Entry targets") != (len(entry_rows), entry_failed):
        issues.append("reported entry-target counts do not match table rows")
    return summary_failed, entry_failed


def _decoded_state(
    state_path: Path, policy: LintPolicy, issues: List[str]
) -> ValidationState:
    try:
        return decode_validation_state(
            _read_json(state_path, "validation-state.json", issues),
            schema_version=policy.state_schema_version,
        )
    except ValidationStateContractError as exc:
        issues.append(f"validation-state.json violates its contract: {exc}")
        return cast(ValidationState, {})


def _lint_index(
    index_path: Path,
    state: ValidationState,
    policy: LintPolicy,
    issues: List[str],
) -> None:
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
        summary, graph = load_slice(value)
        if graph.identity != state.get("graph_identity"):
            issues.append("state graph identity differs from validation index")
        if value["local_snapshot_identity"] != state.get(
            "local_snapshot_identity"
        ):
            issues.append(
                "state local snapshot identity differs from validation index"
            )
        if not summary.endswith(".md"):
            issues.append("validation index summary is not a Markdown path")
    except (OSError, UnicodeError, json.JSONDecodeError, GraphContractError) as exc:
        issues.append(f"{policy.slice_filename} is invalid: {exc}")


def _lint_state(
    context: _StateLintInput,
) -> StateLint:
    state = _decoded_state(context.state_path, context.policy, context.issues)
    report_rules = re.search(
        r"^- Validation-rules version: `([^`]+)`$",
        context.report.text,
        re.MULTILINE,
    )
    if not report_rules or report_rules.group(1) != state.get(
        "validation_rules_version"
    ):
        context.issues.append("report and state validation-rules versions differ")
    if context.report.local_snapshot_identity != state.get(
        "local_snapshot_identity"
    ):
        context.issues.append("report and state local snapshot identities differ")
    if not state:
        return StateLint(0, 0, set())
    successful, failed, dependencies, successful_dependencies = (
        _lint_completed_checks(state, context.issues)
    )
    files = _lint_material(
        state,
        dependencies,
        successful_dependencies,
        context.report_path,
        context.issues,
    )
    expected = {
        "summary_rows": len(context.report.summary_rows),
        "summary_failed": context.report.summary_failed,
        "entry_rows": len(context.report.entry_rows),
        "entry_failed": context.report.entry_failed,
        "failure_rows": context.report.summary_failed + context.report.entry_failed,
    }
    _lint_cached_result(state, context.report.text, expected, context.issues)
    _lint_orphans(state, successful_dependencies, context.policy, context.issues)
    return StateLint(successful, failed, files)


class _StateLintInput(NamedTuple):
    state_path: Path
    report_path: Path
    report: ReportLint
    policy: LintPolicy
    issues: List[str]


class _CacheLintInput(NamedTuple):
    state_path: Path
    index_path: Path
    report_path: Path
    report: ReportLint
    policy: LintPolicy
    issues: List[str]


def _lint_failures(
    report_text: str,
    failure_path: Path,
    failed_rows: int,
    durable_issues: List[str],
    cache_issues: List[str],
) -> int:
    headings = sum(
        line.startswith("#### ") for line in report_text.splitlines()
    )
    if failed_rows and "## Remediation" not in report_text:
        durable_issues.append(
            "failed report rows lack an in-report Remediation section"
        )
    if not failed_rows and "## Remediation" in report_text:
        durable_issues.append(
            "validation.md has remediation detail without failed rows"
        )
    if failure_path.exists():
        cache_issues.append("obsolete validation-failures.md is present")
    if headings != failed_rows:
        durable_issues.append(
            f"failure heading count {headings} does not match failed rows {failed_rows}"
        )
    return headings


def _lint_decisions(path: Path, report_text: str, issues: List[str]) -> None:
    try:
        store = decode_decision_store(
            _read_json(path, "validation-decisions.json", issues)
        )
    except ValidationToolError as exc:
        issues.append(f"validation-decisions.json violates its contract: {exc}")
        return
    report_rules = re.search(
        r"^- Validation-rules version: `([^`]+)`$", report_text, re.MULTILINE
    )
    report_snapshot = re.search(
        r"^- Local snapshot identity: `([0-9a-f]{64})`$",
        report_text,
        re.MULTILINE,
    )
    if (
        report_rules is None
        or store["validation_rules_version"] != report_rules.group(1)
    ):
        issues.append("report and decision-store validation-rules versions differ")
    if (
        report_snapshot is None
        or store["local_snapshot_identity"] != report_snapshot.group(1)
    ):
        issues.append("report and decision-store local snapshot identities differ")


def _lint_caches(inputs: _CacheLintInput) -> StateLint:
    state = StateLint(0, 0, set())
    decoded_state: ValidationState = cast(ValidationState, {})
    if not inputs.state_path.is_file():
        inputs.issues.append("missing validation-state.json")
    else:
        state = _lint_state(
            _StateLintInput(
                inputs.state_path,
                inputs.report_path,
                inputs.report,
                inputs.policy,
                inputs.issues,
            )
        )
        decoded_state = _decoded_state(inputs.state_path, inputs.policy, [])
    if not inputs.index_path.is_file():
        inputs.issues.append(f"missing {inputs.policy.slice_filename}")
    elif decoded_state:
        _lint_index(
            inputs.index_path, decoded_state, inputs.policy, inputs.issues
        )
    else:
        try:
            load_slice(json.loads(inputs.index_path.read_text(encoding="utf-8")))
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            GraphContractError,
        ) as exc:
            inputs.issues.append(
                f"{inputs.policy.slice_filename} is invalid: {exc}"
            )
    return state


def lint_validation_records(
    output_dir: Path,
    expected_entry_order: Optional[Sequence[str]],
    policy: LintPolicy,
    expected_local_snapshot_identity: Optional[str] = None,
) -> dict[str, Any]:
    """Lint one generated canonical record bundle."""

    report_issues: List[str] = []
    decision_issues: List[str] = []
    currentness_issues: List[str] = []
    cache_issues: List[str] = []
    report_path = output_dir / "validation.md"
    state_path = output_dir / "validation-state.json"
    decisions_path = output_dir / "validation-decisions.json"
    failure_path = output_dir / "validation-failures.md"
    index_path = output_dir / policy.slice_filename
    if not report_path.is_file():
        report_issues.append("missing validation.md")
    if not decisions_path.is_file():
        decision_issues.append("missing validation-decisions.json")
    if report_issues:
        durable_issues = [*report_issues, *decision_issues]
        return {
            "ok": False,
            "durable_ok": False,
            "report_ok": False,
            "report_current": False,
            "decision_compatible": not decision_issues,
            "cache_usable": False,
            "issues": durable_issues,
            "durable_issues": durable_issues,
            "report_issues": report_issues,
            "decision_issues": decision_issues,
            "currentness_issues": currentness_issues,
            "cache_issues": cache_issues,
        }

    report = _lint_report(
        report_path, expected_entry_order, policy, report_issues
    )
    if (
        expected_local_snapshot_identity is not None
        and report.local_snapshot_identity != expected_local_snapshot_identity
    ):
        currentness_issues.append(
            "validation.md is historical for the current local research snapshot"
        )
    if decisions_path.is_file():
        _lint_decisions(decisions_path, report.text, decision_issues)
    state = _lint_caches(
        _CacheLintInput(
            state_path,
            index_path,
            report_path,
            report,
            policy,
            cache_issues,
        )
    )
    failed_rows = report.summary_failed + report.entry_failed
    headings = _lint_failures(
        report.text,
        failure_path,
        failed_rows,
        report_issues,
        cache_issues,
    )
    dates = sum(
        _success_date(row[3]) for row in report.summary_rows if len(row) == 4
    ) + sum(
        _success_date(value)
        for row in report.entry_rows
        if len(row) == 6
        for value in row[2:5]
    )
    if state_path.is_file() and dates != state.successful:
        cache_issues.append(
            f"successful report cells {dates} do not match state records "
            f"{state.successful}"
        )
    durable_issues = [*report_issues, *decision_issues]
    return {
        "ok": not durable_issues and not currentness_issues,
        "durable_ok": not durable_issues,
        "report_ok": not report_issues,
        "report_current": not currentness_issues,
        "decision_compatible": not decision_issues,
        "cache_usable": not cache_issues,
        "issues": [*durable_issues, *currentness_issues, *cache_issues],
        "durable_issues": durable_issues,
        "report_issues": report_issues,
        "decision_issues": decision_issues,
        "currentness_issues": currentness_issues,
        "cache_issues": cache_issues,
        "counts": {
            "summary_rows": len(report.summary_rows),
            "summary_failed": report.summary_failed,
            "entry_rows": len(report.entry_rows),
            "entry_failed": report.entry_failed,
            "failure_headings": headings,
            "successful_checks": state.successful,
            "failed_checks": state.failed,
            "completed_checks": state.successful + state.failed,
            "file_identities": len(state.files),
        },
        "entry_order": report.entry_order,
    }

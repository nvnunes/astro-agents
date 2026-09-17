from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import research_log_data as DATA
from research_log_cli_test_support import fixture_parameter_roles
from research_log_validation_test_support import mock, unittest, write

ENGINE = importlib.import_module("validation.engine")
DOMAIN = importlib.import_module("validation.domain")
LOCATOR = importlib.import_module("validation.locator")
PYRUN_STATE = importlib.import_module("validation.pyrun_state")
PRESENTATION = importlib.import_module("validation.presentation")
PROVENANCE = importlib.import_module("validation.provenance")
COMMANDS = importlib.import_module("validation.commands")
RESEARCH_GRAPH = importlib.import_module("validation.research_graph")
SNAPSHOT_REPORT = importlib.import_module("validation.snapshot_report")

_EVALUATE_MECHANICAL = ENGINE.evaluate_mechanical


def _without_material_production(graph: Any, material: str) -> Any:
    target = RESEARCH_GRAPH.ResearchNode(
        RESEARCH_GRAPH.NodeKind.MATERIAL,
        material,
    ).node_id
    return RESEARCH_GRAPH.ResearchGraph(
        graph.nodes,
        tuple(
            edge
            for edge in graph.edges
            if not (
                edge.kind is RESEARCH_GRAPH.EdgeKind.PRODUCTION
                and edge.target == target
            )
        ),
        graph.ambiguities,
        graph.limit_observation,
    )


def _evaluate_current_fixture(request: Any) -> Any:
    """Construct current authored comments for normalized engine fixtures.

    These tests mutate normalized state to exercise the engine, rather than
    authoring. Public sync tests independently use literal comment definitions.
    """

    log_root = Path(request.summary_path).with_suffix("")
    for entry_root in sorted(
        path for path in log_root.rglob("entries/*") if path.is_dir()
    ):
        counts: dict[str, int] = {}
        evidence_path = entry_root / "evidence.json"
        try:
            records = json.loads(evidence_path.read_text())["records"]
        except (OSError, ValueError, KeyError, TypeError):
            records = []
        for document in sorted(entry_root.glob("*.md")):
            text = document.read_text(encoding="utf-8")

            def add_cid(match: re.Match[str]) -> str:
                tail = text[match.end() : text.find("\n", match.end())]
                script = re.search(r"scripts/([A-Za-z0-9_-]+)\.py", tail)
                stem = script.group(1) if script is not None else "command"
                counts[stem] = counts.get(stem, 0) + 1
                cid = stem if counts[stem] == 1 else f"{stem}-{counts[stem]}"
                separator = "" if tail.lstrip().startswith("--") else "-- "
                return f"./pyrun --cid {cid} {separator}"

            current = re.sub(r"\./pyrun (?![^\n]*--cid\b)", add_cid, text)
            for record in records:
                if record.get("document") != document.relative_to(log_root).as_posix():
                    continue
                current = re.sub(
                    r"<!-- eid:" + re.escape(record["id"]) + r"(?: [^\r\n]*?)? -->",
                    lambda _: (
                        "<!-- eid:"
                        + record["id"]
                        + " "
                        + _fixture_definition(record)
                        + " -->"
                    ),
                    current,
                )
            if current != text:
                document.write_text(current, encoding="utf-8")
    evaluation = _EVALUATE_MECHANICAL(request)
    _assert_canonical_projection(evaluation)
    return evaluation


def _fixture_pointer(path: list[Any]) -> str:
    return "".join(
        "/" + str(item).replace("~", "~0").replace("/", "~1") for item in path
    )


def _fixture_definition(record: dict[str, Any]) -> str:
    """Spell the small current definition shapes used by engine fixtures."""

    transformation = record.get("transformation")
    fields = []
    if transformation:
        fields.extend(
            [
                "form=" + transformation["form"],
                "render=fixed:" + str(transformation.get("decimal_places", 1)),
            ]
        )
    tolerance = record.get("reproduction_tolerance")
    if tolerance:
        fields.append("reproduction_tolerance=" + tolerance["absolute"])
    sources = []
    for source in record.get("sources", []):
        clause = ["source=" + source["source"]]
        locator = source.get("locator") or {}
        if locator.get("path"):
            clause.append("path=" + _fixture_pointer(locator["path"]))
        for key in ("select", "identity"):
            clause.extend(
                key + "=" + _fixture_pointer(path) for path in locator.get(key, [])
            )
        for condition in locator.get("where", []):
            value = condition.get("value")
            kind = condition.get(
                "parse", "decimal" if isinstance(value, (float, int)) else "string"
            )
            clause.append(
                "where="
                + _fixture_pointer(condition["path"])
                + ":"
                + condition["op"]
                + ":"
                + kind
                + ":"
                + str(value)
            )
        clause.extend(
            key + "=" + value for key, value in locator.get("text", {}).items()
        )
        sources.append(clause)
    return "; ".join(
        " ".join(shlex.quote(token) for token in clause)
        for clause in [fields + sources[0], *sources[1:]]
    )


def _assert_canonical_projection(evaluation: Any) -> None:
    """Prove every finding check becomes exactly one finding."""

    canonical = {check.check_id: check for check in evaluation.attempt.checks}
    if len(canonical) != len(evaluation.attempt.checks):
        raise AssertionError("canonical checks are not unique")
    finding_checks = {
        check.check_id
        for check in canonical.values()
        if check.outcome is DOMAIN.CheckOutcome.FINDING
    }
    if {
        finding.finding_id for finding in evaluation.attempt.findings
    } != finding_checks:
        raise AssertionError("finding checks and projected findings differ")
    for finding in evaluation.attempt.findings:
        if not (
            finding.source_locations
            or finding.repair_keys
            or finding.context_nodes
            or finding.admission_owner is not None
        ):
            raise AssertionError(
                f"finding lacks typed repair context: {finding.finding_id}"
            )
    if evaluation.snapshot is None:
        raise AssertionError("completed evaluation did not produce a snapshot")


def _area_outcomes(attempt: Any) -> dict[Any, Any]:
    """Aggregate private checks by canonical rule area for focused assertions."""

    aggregated: dict[Any, Any] = {}
    for area in DOMAIN.RuleArea:
        outcomes = {check.outcome for check in attempt.checks if check.area is area}
        if not outcomes or outcomes == {DOMAIN.CheckOutcome.BLOCKED}:
            outcome = DOMAIN.CheckOutcome.BLOCKED
        elif DOMAIN.CheckOutcome.FAILED in outcomes:
            outcome = DOMAIN.CheckOutcome.FAILED
        elif DOMAIN.CheckOutcome.FINDING in outcomes:
            outcome = DOMAIN.CheckOutcome.FINDING
        else:
            outcome = DOMAIN.CheckOutcome.PASS
        aggregated[area] = outcome
    return aggregated


def _provenance_finding_checks(
    attempt: Any, entry_id: str, record_id: str
) -> tuple[Any, ...]:
    """Return canonical provenance finding checks behind one record consumer."""

    checks = {check.check_id: check for check in attempt.checks}
    pending = list(checks[f"provenance:{entry_id}:{record_id}"].dependencies)
    seen: set[str] = set()
    finding_checks: list[Any] = []
    while pending:
        identity = pending.pop()
        if identity in seen or identity not in checks:
            continue
        seen.add(identity)
        check = checks[identity]
        if (
            check.area is DOMAIN.RuleArea.PROVENANCE
            and check.outcome is DOMAIN.CheckOutcome.FINDING
        ):
            finding_checks.append(check)
        pending.extend(check.dependencies)
    return tuple(sorted(finding_checks, key=lambda check: check.check_id))


def _single_provenance_finding_check(
    attempt: Any, entry_id: str, record_id: str
) -> Any:
    finding_checks = _provenance_finding_checks(attempt, entry_id, record_id)
    if len(finding_checks) != 1:
        raise AssertionError(
            f"expected one provenance finding check for {entry_id}:{record_id}, "
            f"observed {[item.check_id for item in finding_checks]}"
        )
    return finding_checks[0]


def _single_currentness_blocker(
    evaluation: Any, entry_id: str, record_id: str
) -> Mapping[str, object]:
    """Return one Reproduce-owned currentness result outside validation."""

    del entry_id, record_id
    currentness = evaluation.context.currentness
    if len(currentness) != 1:
        raise AssertionError(f"expected one currentness result, observed {currentness}")
    return currentness[0].as_dict()


def _reproduce_currentness(evaluation: Any) -> tuple[Any, ...]:
    return evaluation.context.currentness


def _log(root: Path, *, output_option: str = "output-data") -> tuple[Path, Path]:
    (root / ".git").mkdir(exist_ok=True)
    summary = root / "docs" / "study.md"
    log_root = root / "docs" / "study"
    entry_root = log_root / "entries" / "2026-08-29-e001-study"
    entry = entry_root / "e001.md"
    write(
        summary,
        "# Study\n\n"
        "## Summary\n\n"
        "- Success rate: `67.6%`"
        "<!-- ref entry = e001; eid = success-rate -->.\n\n"
        "## Entries\n\n"
        "- [Study trial](study/entries/2026-08-29-e001-study/e001.md)\n",
    )
    write(entry_root / "scripts" / "model.py", "# retained model\n")
    write(entry_root / "data" / "catalog.csv", "id\n1\n")
    write(entry_root / "data" / "results.csv", "success_rate\n0.676\n")
    catalog_digest = hashlib.sha256(
        (entry_root / "data" / "catalog.csv").read_bytes()
    ).hexdigest()
    results_digest = hashlib.sha256(
        (entry_root / "data" / "results.csv").read_bytes()
    ).hexdigest()
    write(
        entry_root / "data.json",
        json.dumps(
            {
                "schema": "research-log-data/v6",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "data/catalog.csv",
                        "identity": {"algorithm": "sha256"},
                        "origin": True,
                    },
                    {
                        "name": "results",
                        "kind": "file",
                        "location": "data/results.csv",
                        "identity": {"algorithm": "sha256"},
                        "origin": False,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry_root / "evidence.json",
        """{
  "schema": "research-log-evidence/v5",
  "records": [
    {
      "id": "success-rate",
      "document": "entries/2026-08-29-e001-study/e001.md",
      "kind": "statistic",
      "sources": [
        {
          "source": "<results>",
          "locator": {"select": [["success_rate"]]}
        }
      ],
      "transformation": {
        "form": "percentage",
        "source": {"input": 0, "item": 0}
      }
    }
  ]
}
""",
    )
    write(
        entry_root / "pyrun-outputs.json",
        json.dumps(
            {
                "schema": "research-log-pyrun-outputs/v1",
                "outputs": {
                    "data/results.csv": {
                        "confirmed": True,
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": results_digest,
                        },
                        "inputs": {
                            "catalog": {
                                "algorithm": "sha256",
                                "digest": catalog_digest,
                            }
                        },
                        "code": {},
                        "parameters": [
                            "--input-catalog",
                            "<catalog>",
                            f"--{output_option}",
                            "data/results.csv",
                        ],
                        "script": {
                            "path": "scripts/model.py",
                            "fingerprint": {
                                "algorithm": "sha256",
                                "digest": hashlib.sha256(
                                    (entry_root / "scripts/model.py").read_bytes()
                                ).hexdigest(),
                            },
                        },
                    }
                },
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry,
        "# Entry e001\n\n"
        "## Trial\n\n"
        "`Background:`\n\nWhat is the success rate?\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/model.py --input-catalog '<catalog>' "
        f"--{output_option} '<results>'\n"
        "```\n\n"
        "`Results:`\n\n"
        "The success rate was `67.6%`<!-- eid:success-rate source=results "
        "select=/success_rate form=percentage render=fixed:1 -->.\n",
    )
    return summary, entry


def _replace_with_pyrun_state(
    entry_document: Path,
    parameters: tuple[str, ...],
    *,
    requires_reproduction: bool = False,
) -> str:
    """Replace the legacy fixture registry with one current execution."""

    entry = entry_document.parent
    (entry / "pyrun-outputs.json").unlink()
    recipe = PYRUN_STATE.ExecutionRecipe(
        "scripts/model.py",
        parameters,
        (),
        ("catalog",),
        (("data/results.csv", "file"),),
        parameter_roles=fixture_parameter_roles(
            parameters, ("catalog",), (("data/results.csv", "file"),)
        ),
    )
    observed = PYRUN_STATE.ObservedExecution(
        DATA.Fingerprint(
            "sha256",
            digest=hashlib.sha256(
                (entry / "scripts/model.py").read_bytes()
            ).hexdigest(),
        ),
        (
            (
                "catalog",
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256(
                        (entry / "data/catalog.csv").read_bytes()
                    ).hexdigest(),
                ),
            ),
        ),
        None,
        (
            (
                "data/results.csv",
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256(
                        (entry / "data/results.csv").read_bytes()
                    ).hexdigest(),
                ),
            ),
        ),
    )
    execution = PYRUN_STATE.PyrunExecution(
        requires_reproduction,
        True,
        "2030-01-01T00:00:00Z",
        PYRUN_STATE.PYRUN_RUNNER,
        PYRUN_STATE.PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_STATE.PYRUN_EXECUTION_CONTRACT,
        recipe,
        observed,
    )
    identity = PYRUN_STATE.execution_id(recipe)
    state = PYRUN_STATE.PyrunFile(
        entry / PYRUN_STATE.PYRUN_FILENAME,
        entry,
        {"model": PYRUN_STATE.PyrunCommand({identity: execution})},
    )
    write(entry / PYRUN_STATE.PYRUN_FILENAME, state.serialized())
    return identity


def _add_second_result_output(entry_document: Path) -> tuple[str, ...]:
    """Add a second evidence-consumed output to the fixture command."""

    entry = entry_document.parent
    second = entry / "data/second.csv"
    write(second, "success_rate\n0.500\n")
    data_path = entry / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["inputs"].append(
        {
            "name": "second",
            "kind": "file",
            "location": "data/second.csv",
            "identity": {"algorithm": "sha256"},
            "origin": False,
        }
    )
    write(data_path, json.dumps(data, indent=2) + "\n")
    evidence_path = entry / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    second_record = json.loads(json.dumps(evidence["records"][0]))
    second_record["id"] = "second-rate"
    second_record["sources"][0]["source"] = "<second>"
    evidence["records"].append(second_record)
    write(evidence_path, json.dumps(evidence, indent=2) + "\n")
    write(
        entry_document,
        entry_document.read_text(encoding="utf-8")
        .replace(
            "--output-data '<results>'",
            "--output-data '<results>' --output-data '<second>'",
        )
        .replace(
            "The success rate was `67.6%`<!-- eid:success-rate source=results "
            "select=/success_rate form=percentage render=fixed:1 -->.",
            "The success rate was `67.6%`<!-- eid:success-rate source=results "
            "select=/success_rate form=percentage render=fixed:1 -->.\n\n"
            "The second rate was `50.0%`<!-- eid:second-rate -->.",
        ),
    )
    return (
        "--input-catalog",
        "<catalog>",
        "--output-data",
        "data/results.csv",
        "--output-data",
        "data/second.csv",
    )


def _replace_two_outputs_with_pyrun_state(
    entry_document: Path, parameters: tuple[str, ...]
) -> str:
    """Replace legacy support with one current two-output execution."""

    entry = entry_document.parent
    (entry / "pyrun-outputs.json").unlink()
    outputs = (("data/results.csv", "file"), ("data/second.csv", "file"))
    recipe = PYRUN_STATE.ExecutionRecipe(
        "scripts/model.py",
        parameters,
        (),
        ("catalog",),
        outputs,
        parameter_roles=fixture_parameter_roles(parameters, ("catalog",), outputs),
    )
    observed = PYRUN_STATE.ObservedExecution(
        DATA.Fingerprint(
            "sha256",
            digest=hashlib.sha256(
                (entry / "scripts/model.py").read_bytes()
            ).hexdigest(),
        ),
        (
            (
                "catalog",
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256(
                        (entry / "data/catalog.csv").read_bytes()
                    ).hexdigest(),
                ),
            ),
        ),
        None,
        tuple(
            (
                output,
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256((entry / output).read_bytes()).hexdigest(),
                ),
            )
            for output, _kind in outputs
        ),
    )
    execution = PYRUN_STATE.PyrunExecution(
        True,
        True,
        "2030-01-01T00:00:00Z",
        PYRUN_STATE.PYRUN_RUNNER,
        PYRUN_STATE.PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_STATE.PYRUN_EXECUTION_CONTRACT,
        recipe,
        observed,
    )
    identity = PYRUN_STATE.execution_id(recipe)
    state = PYRUN_STATE.PyrunFile(
        entry / PYRUN_STATE.PYRUN_FILENAME,
        entry,
        {"model": PYRUN_STATE.PyrunCommand({identity: execution})},
    )
    write(entry / PYRUN_STATE.PYRUN_FILENAME, state.serialized())
    return identity


def _replace_bundle_with_pyrun_state(
    entry_document: Path, *, requires_reproduction: bool
) -> str:
    """Replace legacy bundle support with one current directory execution."""

    entry = entry_document.parent
    (entry / "pyrun-outputs.json").unlink()
    parameters = (
        "--input-catalog",
        "<catalog>",
        "--output-dir",
        "data/bundle",
    )
    recipe = PYRUN_STATE.ExecutionRecipe(
        "scripts/model.py",
        parameters,
        (),
        ("catalog",),
        (("data/bundle", "directory"),),
        parameter_roles=fixture_parameter_roles(
            parameters, ("catalog",), (("data/bundle", "directory"),)
        ),
    )
    bundle_resource = DATA.build_local_input(
        "bundle",
        "directory",
        "data/bundle",
        entry_root=entry,
        origin=False,
    )
    observed = PYRUN_STATE.ObservedExecution(
        DATA.Fingerprint(
            "sha256",
            digest=hashlib.sha256(
                (entry / "scripts/model.py").read_bytes()
            ).hexdigest(),
        ),
        (
            (
                "catalog",
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256(
                        (entry / "data/catalog.csv").read_bytes()
                    ).hexdigest(),
                ),
            ),
        ),
        None,
        (
            (
                "data/bundle",
                DATA.observe_fingerprint(bundle_resource).fingerprint,
            ),
        ),
    )
    execution = PYRUN_STATE.PyrunExecution(
        requires_reproduction,
        True,
        "2030-01-01T00:00:00Z",
        PYRUN_STATE.PYRUN_RUNNER,
        PYRUN_STATE.PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_STATE.PYRUN_EXECUTION_CONTRACT,
        recipe,
        observed,
    )
    identity = PYRUN_STATE.execution_id(recipe)
    state = PYRUN_STATE.PyrunFile(
        entry / PYRUN_STATE.PYRUN_FILENAME,
        entry,
        {"model": PYRUN_STATE.PyrunCommand({identity: execution})},
    )
    write(entry / PYRUN_STATE.PYRUN_FILENAME, state.serialized())
    return identity


def _evaluate(summary: Path) -> Any:
    evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
    return SimpleNamespace(
        attempt=evaluation.attempt,
        context=evaluation.context,
        snapshot=evaluation.snapshot,
        scan={
            "invocations": evaluation.context.invocations,
            "registries": evaluation.context.registries,
        },
        metrics=evaluation.metrics,
    )


def _set_code_support(
    entry: Path,
    code: dict[str, dict[str, str]],
    *,
    output: str = "data/results.csv",
) -> dict[str, Any]:
    path = entry.parent / "pyrun-outputs.json"
    support = json.loads(path.read_text(encoding="utf-8"))
    support["outputs"][output]["code"] = code
    write(path, json.dumps(support, indent=2) + "\n")
    return support


def _origin_data_json(entry_root: Path) -> str:
    source = entry_root / "data/catalog.csv"
    write(source, "id\n1\n")
    return (
        json.dumps(
            {
                "schema": "research-log-data/v6",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "data/catalog.csv",
                        "identity": {"algorithm": "sha256"},
                        "origin": True,
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )


def _convert_result_to_bundle(entry: Path) -> tuple[Path, Path, Path]:
    entry_root = entry.parent
    bundle = entry_root / "data/bundle"
    member = bundle / "results.csv"
    sibling = bundle / "model.pt"
    bundle.mkdir()
    (entry_root / "data/results.csv").replace(member)
    write(sibling, "model\n")

    data_path = entry_root / "data.json"
    data = json.loads(data_path.read_text())
    data["inputs"] = [item for item in data["inputs"] if item["name"] != "results"]
    resource = DATA.build_local_input(
        "results",
        "directory",
        "data/bundle",
        entry_root=entry_root,
        origin=False,
    )
    data["inputs"].append(resource.as_dict())
    data["inputs"].sort(key=lambda item: item["name"])
    write(data_path, json.dumps(data, indent=2) + "\n")

    evidence_path = entry_root / "evidence.json"
    write(
        evidence_path,
        evidence_path.read_text().replace(
            '"source": "<results>"',
            '"source": "<results>/results.csv"',
        ),
    )
    write(
        entry,
        entry.read_text().replace(
            "--output-data '<results>'",
            "--output-dir '<results>'",
        ),
    )
    support_path = entry_root / "pyrun-outputs.json"
    support = json.loads(support_path.read_text())
    record = support["outputs"].pop("data/results.csv")
    record["fingerprint"] = DATA.observe_fingerprint(resource).fingerprint.as_dict()
    record["parameters"] = [
        "--input-catalog",
        "<catalog>",
        "--output-dir",
        "data/bundle",
    ]
    support["outputs"]["data/bundle"] = record
    write(support_path, json.dumps(support, indent=2) + "\n")
    return bundle, member, sibling


class EngineV2EndToEndTests(unittest.TestCase):
    def test_entry_declaration_index_selects_exact_overlap_and_rejected_competitors(
        self,
    ) -> None:
        """Closure selection retains every declaration that can compete for a root."""

        index = ENGINE._LogDeclarationIndex(
            Path("/log"),
            (),
            (),
            {
                "/project/data/exact.csv": (
                    ENGINE._DeclarationCandidate(
                        "e001", "/project/data/exact.csv", "file", (0, 1, 1)
                    ),
                ),
                "/project/data/tree": (
                    ENGINE._DeclarationCandidate(
                        "e002", "/project/data/tree", "directory", (1, 1, 1)
                    ),
                ),
                "/project/data/tree/member.csv": (
                    ENGINE._DeclarationCandidate(
                        "e003", "/project/data/tree/member.csv", "file", (2, 1, 1)
                    ),
                ),
            },
            {
                "/project/data/tree/rejected.csv": (
                    ENGINE._DeclarationCandidate(
                        "e004", "/project/data/tree/rejected.csv", "unknown", (3, 1, 1)
                    ),
                ),
            },
            (),
        )
        owners = ENGINE._indexed_candidate_owners(
            index, (("/project/data/exact.csv", None), ("/project/data/tree", None))
        )
        self.assertEqual(owners, frozenset({"e001", "e002", "e003", "e004"}))

    def test_declaration_candidates_use_command_frontiers_not_document_order(self):
        """A prior fence can feed its document, but a later fence cannot."""

        candidate = ENGINE._DeclarationCandidate(
            "e001", "/project/data/shared.csv", "file", (0, 1, 1)
        )
        index = ENGINE._LogDeclarationIndex(
            Path("/log"), (), (), {candidate.path: (candidate,)}, {}, ()
        )
        self.assertEqual(
            ENGINE._indexed_candidate_owners(index, ((candidate.path, (0, 2, 1)),)),
            frozenset({"e001"}),
        )
        self.assertEqual(
            ENGINE._indexed_candidate_owners(index, ((candidate.path, (0, 1, 1)),)),
            frozenset(),
        )

    def test_selected_named_rejection_is_seeded_once_in_source_order(self):
        """Selected structural rejections do not re-enter producer closure."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "./pyrun scripts/model.py", "python scripts/model.py"
                ),
            )
            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e001", entry.parent),
                )
            )
            rejected = [
                check
                for check in result.attempt.checks
                if check.check_id == "entry:e001:command:1:1"
            ]
            self.assertEqual(len(rejected), 1)
            self.assertEqual(
                rejected[0].diagnostic.code, "invocation.command.unsupported"
            )

    def test_entry_unreadable_declaration_index_fails_then_recovers(
        self,
    ) -> None:
        """An unavailable declaration read is a localized retryable failure."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            scoped_request = ENGINE.EvaluationRequest(
                summary,
                ENGINE.EntryEvaluationTarget("e001", entry.parent),
            )
            self.assertIs(
                _evaluate_current_fixture(scoped_request).snapshot.outcome,
                DOMAIN.SnapshotOutcome.CLEAR,
            )
            original = Path.read_text

            def read_text(path: Path, *args: object, **kwargs: object) -> str:
                if path.name == "e001.md":
                    raise OSError("denied")
                return original(path, *args, **kwargs)

            for request in (ENGINE.EvaluationRequest(summary), scoped_request):
                with mock.patch.object(
                    Path, "read_text", autospec=True, side_effect=read_text
                ):
                    failed = _EVALUATE_MECHANICAL(request)
                    _assert_canonical_projection(failed)
                self.assertIs(failed.snapshot.outcome, DOMAIN.SnapshotOutcome.FAILED)
                self.assertEqual(len(failed.snapshot.failed_checks), 1)
                self.assertEqual(
                    failed.snapshot.failed_checks[0].code,
                    "association.document_unavailable",
                )
                self.assertGreaterEqual(len(failed.snapshot.blocked_checks), 2)
                self.assertFalse(failed.snapshot.findings)
                if isinstance(request.target, ENGINE.FullEvaluationTarget):
                    checks = {check.check_id: check for check in failed.attempt.checks}
                    self.assertIs(
                        checks["evidence:summary:5"].outcome,
                        DOMAIN.CheckOutcome.BLOCKED,
                    )
                    unmatched = [
                        check
                        for check in failed.attempt.checks
                        if check.check_id.startswith("orphan:unmatched-output:")
                    ]
                    self.assertTrue(unmatched)
                    self.assertTrue(
                        all(
                            check.outcome is DOMAIN.CheckOutcome.BLOCKED
                            for check in unmatched
                        )
                    )
            self.assertIs(
                _evaluate_current_fixture(scoped_request).snapshot.outcome,
                DOMAIN.SnapshotOutcome.CLEAR,
            )

    def test_entry_over_bound_declaration_index_fails_then_recovers(
        self,
    ) -> None:
        """A bounded declaration-index failure is localized and retryable."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = ENGINE.EvaluationRequest(
                summary,
                ENGINE.EntryEvaluationTarget("e001", entry.parent),
            )
            with mock.patch.object(
                ENGINE,
                "_index_log",
                side_effect=ENGINE.EngineV2Error(
                    "association.resource.too_large",
                    "index",
                    {"limit": 1},
                    "Recorded-Command Provenance And Material Graph",
                    outcome="unavailable",
                ),
            ):
                failed = _evaluate_current_fixture(request)
            self.assertIs(failed.snapshot.outcome, DOMAIN.SnapshotOutcome.FAILED)
            self.assertIs(
                _evaluate_current_fixture(request).snapshot.outcome,
                DOMAIN.SnapshotOutcome.CLEAR,
            )

    def test_command_declaration_index_never_loads_execution_state(self) -> None:
        """Namespace indexing is structural even when an entry has state to load."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            state = ENGINE._ScanState(summary, summary.with_suffix(""), root)
            with mock.patch.object(
                ENGINE,
                "load_pyrun_state",
                side_effect=AssertionError("indexing must not load execution state"),
            ):
                index = ENGINE._index_log(summary.read_text(encoding="utf-8"), state)
            self.assertEqual(
                tuple(entry.stable_id for entry in index.entries), ("e001",)
            )

    def test_pyrun_policy_mismatch_is_structure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry_document = _log(root)
            identity = _replace_with_pyrun_state(
                entry_document,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            entry = entry_document.parent
            path = entry / PYRUN_STATE.PYRUN_FILENAME
            state = PYRUN_STATE.load_pyrun_state(
                path, entry_root=entry, project_root=root
            )
            changed = dict(state.commands["model"].executions)
            changed[identity] = replace(changed[identity], auto_reproduce=False)
            write(
                path,
                PYRUN_STATE.PyrunFile(
                    path, entry, {"model": PYRUN_STATE.PyrunCommand(changed)}
                ).serialized(),
            )

            evaluation = _evaluate(summary)

            finding = next(
                check
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "pyrun.policy.mismatch"
            )
            self.assertEqual(finding.area, DOMAIN.RuleArea.CONFORMANCE)
            self.assertIn(identity, finding.check_id)

    def test_current_execution_requires_exact_command_association(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            entry.write_text(
                entry.read_text(encoding="utf-8").replace(
                    "--output-data '<results>'",
                    "--output-data '<results>' --mode exact",
                ),
                encoding="utf-8",
            )

            evaluation = _evaluate(summary)

            provenance = _single_provenance_finding_check(
                evaluation.attempt, "e001", "success-rate"
            )
            self.assertEqual(
                provenance.diagnostic.code,
                "provenance.output.execution_unassociated",
            )

    def test_current_execution_ignores_raw_only_script_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            (entry.parent / "scripts/model.py").write_text(
                "# changed model\n", encoding="utf-8"
            )

            evaluation = _evaluate(summary)

            self.assertEqual(_reproduce_currentness(evaluation), ())

    def test_pending_current_execution_accepts_unavailable_observations(self) -> None:
        cases = (
            ("empty", PYRUN_STATE.ObservedExecution(None, (), None, ())),
            ("script_missing", None),
        )
        for label, observed in cases:
            with (
                tempfile.TemporaryDirectory() as directory,
                self.subTest(observations=label),
            ):
                root = Path(directory)
                summary, entry = _log(root)
                identity = _replace_with_pyrun_state(
                    entry,
                    (
                        "--input-catalog",
                        "<catalog>",
                        "--output-data",
                        "data/results.csv",
                    ),
                    requires_reproduction=True,
                )
                path = entry.parent / PYRUN_STATE.PYRUN_FILENAME
                state = PYRUN_STATE.load_pyrun_state(
                    path, entry_root=entry.parent, project_root=root
                )
                execution = state.commands["model"].executions[identity]
                if observed is None:
                    observed = PYRUN_STATE.ObservedExecution(
                        None,
                        (),
                        None,
                        execution.observed.outputs,
                    )
                write(
                    path,
                    PYRUN_STATE.PyrunFile(
                        path,
                        entry.parent,
                        {
                            "model": PYRUN_STATE.PyrunCommand(
                                {identity: replace(execution, observed=observed)}
                            )
                        },
                    ).serialized(),
                )

                evaluation = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(summary)
                )

                assert evaluation.snapshot is not None
                self.assertIs(evaluation.snapshot.outcome, DOMAIN.SnapshotOutcome.CLEAR)
                self.assertFalse(evaluation.snapshot.failed_checks)
                self.assertFalse(
                    any(
                        finding.type is DOMAIN.RuleArea.PROVENANCE
                        for finding in evaluation.snapshot.findings
                    )
                )
                currentness = _single_currentness_blocker(
                    evaluation, "e001", "success-rate"
                )
                self.assertEqual(currentness["reason"], "required")
                self.assertEqual(
                    currentness["subject"],
                    (entry.parent / "data/results.csv").resolve().as_posix(),
                )
                scopes = _area_outcomes(evaluation.attempt)
                self.assertEqual(
                    scopes[DOMAIN.RuleArea.PROVENANCE], DOMAIN.CheckOutcome.PASS
                )
                self.assertEqual(
                    scopes[DOMAIN.RuleArea.EVIDENCE], DOMAIN.CheckOutcome.PASS
                )
                self.assertEqual(
                    scopes[DOMAIN.RuleArea.ORPHAN], DOMAIN.CheckOutcome.PASS
                )

    def test_current_execution_rejects_incomplete_confirmed_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            identity = _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            path = entry.parent / PYRUN_STATE.PYRUN_FILENAME
            state = PYRUN_STATE.load_pyrun_state(
                path, entry_root=entry.parent, project_root=root
            )
            execution = state.commands["model"].executions[identity]
            write(
                path,
                PYRUN_STATE.PyrunFile(
                    path,
                    entry.parent,
                    {
                        "model": PYRUN_STATE.PyrunCommand(
                            {
                                identity: replace(
                                    execution,
                                    observed=PYRUN_STATE.ObservedExecution(
                                        None, (), None, ()
                                    ),
                                )
                            }
                        )
                    },
                ).serialized(),
            )

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))

            self.assertTrue(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "pyrun.state.invalid"
                    for check in evaluation.attempt.checks
                )
            )
            self.assertFalse(_reproduce_currentness(evaluation))

    def test_reproduction_tolerance_requires_evidence_scoped_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["reproduction_tolerance"] = {"absolute": "0.01"}
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            failure = next(
                check
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code
                == "reproduction.comparison.tolerance_incompatible"
            )
            self.assertEqual(failure.area, DOMAIN.RuleArea.CONFORMANCE)

    def test_evidence_scoped_comparison_requires_applicable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            data["inputs"][1]["reproduction_comparison"] = {
                "contract": "research-log-evidence-scoped-comparison/1",
                "profile": "evidence",
            }
            write(data_path, json.dumps(data, indent=2) + "\n")

            valid = _evaluate(summary)

            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code.startswith("reproduction.comparison.")
                    for check in valid.attempt.checks
                )
            )

            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["locator"] = {"select": [["missing"]]}
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            incompatible = _evaluate(summary)
            self.assertTrue(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code
                    == "reproduction.comparison.evidence_incompatible"
                    and check.area is DOMAIN.RuleArea.CONFORMANCE
                    for check in incompatible.attempt.checks
                )
            )

            evidence["records"][0]["sources"][0]["locator"] = {
                "select": [["success_rate"]]
            }
            evidence["records"][0]["sources"][0]["source"] = "<catalog>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            invalid = _evaluate(summary)

            failure = next(
                check
                for check in invalid.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "reproduction.comparison.evidence_missing"
            )
            self.assertEqual(failure.area, DOMAIN.RuleArea.CONFORMANCE)

    def test_pyrun_binding_failure_is_execution_scoped_structure(self) -> None:
        cases = (
            (("--input-catalog", "<catalog>", "--mode", "exact"), "missing"),
            (
                (
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "data/results.csv",
                    "--reference",
                    "data/results.csv",
                ),
                "ambiguous",
            ),
            (
                (
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "./data/results.csv",
                ),
                "noncanonical",
            ),
        )
        for parameters, reason in cases:
            with (
                tempfile.TemporaryDirectory() as directory,
                self.subTest(reason=reason),
            ):
                summary, entry = _log(Path(directory))
                identity = _replace_with_pyrun_state(entry, parameters)

                evaluation = _evaluate(summary)

                binding = next(
                    check
                    for check in evaluation.attempt.checks
                    if check.diagnostic is not None
                    and check.diagnostic.code == "pyrun.output.binding_invalid"
                )
                self.assertEqual(binding.area, DOMAIN.RuleArea.CONFORMANCE)
                self.assertIn(identity, binding.check_id)
                self.assertEqual(binding.diagnostic.observed["reason"], reason)
                self.assertFalse(
                    any(
                        check.diagnostic is not None
                        and check.diagnostic.code == "pyrun.state.invalid"
                        for check in evaluation.attempt.checks
                    )
                )
                self.assertTrue(
                    any(
                        check.check_id == "evidence:e001:success-rate"
                        for check in evaluation.attempt.checks
                    )
                )

    def test_undeclared_generated_artifact_remains_an_orphan_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            unexpected = entry.parent / "data/unexpected.csv"
            write(unexpected, "value\n2\n")
            _replace_with_pyrun_state(
                entry,
                (
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "data/results.csv",
                ),
            )

            evaluation = _evaluate(summary)

            failures = {
                (check.diagnostic.code, check.diagnostic.subject)
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertIn(
                ("orphan.material.unused", unexpected.resolve().as_posix()),
                failures,
            )
            self.assertFalse(
                any(code == "pyrun.output.binding_invalid" for code, _ in failures)
            )

    def test_current_code_support_enters_provenance_and_suppresses_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            write(helper, "VALUE = 1\n")
            _set_code_support(
                entry,
                {
                    "scripts/helper.py": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    }
                },
            )

            result = _evaluate(summary).attempt
            provenance = next(
                check
                for check in result.checks
                if check.check_id == "provenance:e001:success-rate"
            )

            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in result.checks
                )
            )

    def test_changed_and_unconfirmed_code_support_stays_connected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            write(helper, "VALUE = 1\n")
            support = _set_code_support(
                entry,
                {
                    "scripts/helper.py": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    }
                },
            )
            support_path = entry.parent / "pyrun-outputs.json"

            write(helper, "VALUE = 2\n")
            changed = _evaluate(summary)
            currentness = _single_currentness_blocker(changed, "e001", "success-rate")
            self.assertEqual(currentness["reason"], "signature_mismatch")
            self.assertIn("code", currentness["observed"]["fields"])
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in changed.attempt.checks
                )
            )

            support["outputs"]["data/results.csv"]["confirmed"] = False
            write(support_path, json.dumps(support, indent=2) + "\n")
            unconfirmed = _evaluate(summary)
            currentness = _single_currentness_blocker(
                unconfirmed, "e001", "success-rate"
            )
            self.assertEqual(currentness["reason"], "required")
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in unconfirmed.attempt.checks
                )
            )

    def test_unavailable_and_duplicate_code_targets_fail_without_graph_edges(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            alias = entry.parent / "scripts/alias.py"
            write(helper, "VALUE = 1\n")
            alias.symlink_to(helper.name)
            fingerprint = {
                "algorithm": "sha256",
                "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
            }
            _set_code_support(
                entry,
                {
                    "scripts/helper.py": fingerprint,
                    "scripts/alias.py": fingerprint,
                },
            )

            duplicate = _evaluate(summary).attempt
            provenance = _single_provenance_finding_check(
                duplicate, "e001", "success-rate"
            )
            self.assertEqual(
                provenance.diagnostic.code, "provenance.output.code_invalid"
            )
            self.assertEqual(
                provenance.diagnostic.observed["reason"],
                "duplicate_resolved_identity",
            )
            self.assertTrue(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in duplicate.checks
                )
            )

            alias.unlink()
            helper.unlink()
            _set_code_support(entry, {"scripts/missing.py": fingerprint})
            missing = _evaluate(summary).attempt
            provenance = _single_provenance_finding_check(
                missing, "e001", "success-rate"
            )
            self.assertEqual(
                provenance.diagnostic.code, "provenance.output.code_invalid"
            )
            self.assertEqual(provenance.diagnostic.observed["reason"], "unavailable")

            code_directory = entry.parent / "scripts/directory.py"
            code_directory.mkdir()
            _set_code_support(entry, {"scripts/directory.py": fingerprint})
            wrong_kind = _evaluate(summary).attempt
            provenance = _single_provenance_finding_check(
                wrong_kind, "e001", "success-rate"
            )
            self.assertEqual(
                provenance.diagnostic.code, "provenance.output.code_invalid"
            )
            self.assertEqual(
                provenance.diagnostic.observed["reason"], "not_regular_file"
            )

    def test_unmatched_code_support_does_not_connect_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            stale = entry.parent / "data/stale.csv"
            write(helper, "VALUE = 1\n")
            write(stale, "stale\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            record = dict(support["outputs"]["data/results.csv"])
            record["fingerprint"] = {
                "algorithm": "sha256",
                "digest": hashlib.sha256(stale.read_bytes()).hexdigest(),
            }
            record["code"] = {
                "scripts/helper.py": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
                }
            }
            support["outputs"]["data/stale.csv"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).attempt
            failures = {
                (check.diagnostic.code, check.subject)
                for check in result.checks
                if check.diagnostic is not None
            }
            self.assertIn(
                ("orphan.output.unmatched", stale.resolve().as_posix()), failures
            )
            self.assertIn(
                ("orphan.material.unused", helper.resolve().as_posix()), failures
            )

    def test_code_observation_reuses_one_resolved_file_across_logical_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            entry_root = entry.parent
            helper = entry_root.parent.parent / "shared/helper.py"
            write(helper, "VALUE = 1\n")
            linked = entry_root / "scripts/linked"
            linked.symlink_to(helper.parent, target_is_directory=True)
            fingerprint = DATA.Fingerprint(
                "sha256", digest=hashlib.sha256(helper.read_bytes()).hexdigest()
            )
            support = ENGINE.load_pyrun_outputs(
                entry_root / "pyrun-outputs.json",
                entry_root=entry_root,
                project_root=root,
            )
            base = support.outputs["data/results.csv"]
            direct = replace(base, code=(("<log>/shared/helper.py", fingerprint),))
            alias = replace(base, code=(("scripts/linked/helper.py", fingerprint),))
            state = ENGINE._ScanState(summary, summary.with_suffix(""), root.resolve())

            with ENGINE.FingerprintCache(root, writable=False, reuse=False) as cache:
                state.fingerprint_cache = cache
                with mock.patch.object(
                    cache,
                    "observe_regular_file",
                    wraps=cache.observe_regular_file,
                ) as observe:
                    ENGINE._observe_output_code(
                        ENGINE.resolve_code_support(
                            direct,
                            entry_root=entry_root,
                            subject="data/results.csv",
                        ),
                        state,
                    )
                    ENGINE._observe_output_code(
                        ENGINE.resolve_code_support(
                            alias,
                            entry_root=entry_root,
                            subject="data/results.csv",
                        ),
                        state,
                    )

            helper_calls = [
                call
                for call in observe.call_args_list
                if call.args[0].resolve() == helper.resolve()
            ]
            self.assertEqual(len(helper_calls), 1)

    def test_support_without_code_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            del support["outputs"]["data/results.csv"]["code"]
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            failures = [
                finding
                for finding in evaluation.attempt.findings
                if finding.code == "pyrun.outputs.invalid"
            ]

            self.assertIsNotNone(evaluation.snapshot)
            self.assertEqual(len(failures), 1)
            self.assertEqual(
                failures[0].admission_owner,
                DOMAIN.AdmissionOwner.ENTRY,
            )
            consumer = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            self.assertEqual(consumer.outcome, DOMAIN.CheckOutcome.BLOCKED)
            self.assertIn(failures[0].finding_id, consumer.dependencies)
            assert evaluation.snapshot is not None
            owning_batches = [
                batch
                for batch in evaluation.snapshot.batches
                if failures[0].finding_id in batch.finding_ids
            ]
            self.assertEqual(len(owning_batches), 1)

    def test_invalid_directory_output_support_is_one_entry_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            _convert_result_to_bundle(entry)
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            del support["outputs"]["data/bundle"]["code"]
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            failures = [
                finding
                for finding in evaluation.attempt.findings
                if finding.code == "pyrun.outputs.invalid"
            ]

            self.assertEqual(len(failures), 1)
            consumer = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            self.assertEqual(consumer.outcome, DOMAIN.CheckOutcome.BLOCKED)
            self.assertIn(failures[0].finding_id, consumer.dependencies)
            assert evaluation.snapshot is not None
            self.assertEqual(
                sum(
                    failures[0].finding_id in batch.finding_ids
                    for batch in evaluation.snapshot.batches
                ),
                1,
            )

    def test_legacy_multi_output_currentness_blocks_validation_without_findings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            parameters = _add_second_result_output(entry)
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            first = support["outputs"]["data/results.csv"]
            first["confirmed"] = False
            first["parameters"] = list(parameters)
            support["outputs"]["data/second.csv"] = {
                **first,
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(
                        (entry.parent / "data/second.csv").read_bytes()
                    ).hexdigest(),
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            currentness = _reproduce_currentness(evaluation)

            self.assertEqual(len(currentness), 2)
            self.assertFalse(
                any(
                    finding.code.startswith("provenance.output.reproduction")
                    for finding in evaluation.attempt.findings
                )
            )
            assert evaluation.snapshot is not None
            self.assertFalse(evaluation.snapshot.blocked_checks)
            self.assertFalse(evaluation.snapshot.batches)
            self.assertEqual(
                evaluation.snapshot.outcome,
                DOMAIN.SnapshotOutcome.CLEAR,
            )

    def test_current_multi_output_currentness_is_not_a_repair_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            parameters = _add_second_result_output(entry)
            _replace_two_outputs_with_pyrun_state(entry, parameters)

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            currentness = _reproduce_currentness(evaluation)

            self.assertEqual(len(currentness), 2)
            assert evaluation.snapshot is not None
            self.assertFalse(evaluation.snapshot.blocked_checks)
            self.assertFalse(evaluation.snapshot.batches)

    def test_distinct_currentness_requirements_remain_separate_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            _add_second_result_output(entry)
            entry_root = entry.parent
            second_script = entry_root / "scripts/second.py"
            write(second_script, "# retained second model\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data '<results>' --output-data '<second>'",
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data '<results>'\n"
                    "./pyrun scripts/second.py --input-catalog '<catalog>' "
                    "--output-data '<second>'",
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            first = support["outputs"]["data/results.csv"]
            first["confirmed"] = False
            first["parameters"] = [
                "--input-catalog",
                "<catalog>",
                "--output-data",
                "data/results.csv",
            ]
            support["outputs"]["data/second.csv"] = {
                **json.loads(json.dumps(first)),
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(
                        (entry_root / "data/second.csv").read_bytes()
                    ).hexdigest(),
                },
                "parameters": [
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "data/second.csv",
                ],
                "script": {
                    "path": "scripts/second.py",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            second_script.read_bytes()
                        ).hexdigest(),
                    },
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            currentness = _reproduce_currentness(evaluation)

            self.assertEqual(len(currentness), 2)
            assert evaluation.snapshot is not None
            subjects = {item.subject for item in currentness}
            self.assertEqual(len(subjects), 2)
            self.assertFalse(
                any(
                    finding.code
                    in {
                        "provenance.output.reproduction_required",
                        "provenance.output.signature_mismatch",
                    }
                    for finding in evaluation.snapshot.findings
                )
            )

    def test_malformed_shared_support_does_not_scan_checks_per_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            del support["outputs"]["data/results.csv"]["code"]
            write(support_path, json.dumps(support, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            template = evidence["records"][0]
            consumer_count = 50
            evidence["records"] = []
            presentations = []
            for index in range(consumer_count):
                record = json.loads(json.dumps(template))
                record["id"] = "success-rate" if index == 0 else f"rate-{index}"
                evidence["records"].append(record)
                if index:
                    presentations.append(
                        f"Rate {index} was `67.6%`<!-- eid:rate-{index} -->."
                    )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(entry, entry.read_text(encoding="utf-8") + "\n".join(presentations))

            with mock.patch.object(
                ENGINE,
                "_checks_by_identity",
                wraps=ENGINE._checks_by_identity,
            ) as check_scans:
                evaluation = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(summary)
                )

            self.assertEqual(check_scans.call_count, 0)
            self.assertEqual(
                sum(
                    finding.code == "pyrun.outputs.invalid"
                    for finding in evaluation.attempt.findings
                ),
                1,
            )
            self.assertEqual(evaluation.metrics["provenance_traversals"], 1)
            self.assertEqual(
                evaluation.metrics["provenance_traversals_reused"],
                consumer_count - 1,
            )

    def test_bundle_member_uses_root_support_and_atomic_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            bundle, member, sibling = _convert_result_to_bundle(entry)

            complete = _evaluate(summary).attempt

            provenance = next(
                check
                for check in complete.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            artifact = provenance.dependency_evidence[0]
            self.assertEqual(artifact["artifacts"], (member.resolve().as_posix(),))
            self.assertEqual(
                provenance.dependency_evidence[1]["material"],
                member.resolve().as_posix(),
            )

            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/bundle"]["parameters"].append("--stale")
            write(support_path, json.dumps(support, indent=2) + "\n")
            stale = _evaluate(summary)
            currentness = _single_currentness_blocker(stale, "e001", "success-rate")
            self.assertEqual(currentness["reason"], "signature_mismatch")
            self.assertEqual(currentness["subject"], bundle.resolve().as_posix())

            support["outputs"]["data/bundle"]["parameters"].pop()
            support["outputs"]["data/bundle"]["confirmed"] = False
            write(support_path, json.dumps(support, indent=2) + "\n")
            unconfirmed = _evaluate(summary)
            currentness = _single_currentness_blocker(
                unconfirmed, "e001", "success-rate"
            )
            self.assertEqual(currentness["reason"], "required")
            self.assertEqual(currentness["subject"], bundle.resolve().as_posix())
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.material.unused"
                    and check.subject
                    in {
                        member.resolve().as_posix(),
                        sibling.resolve().as_posix(),
                    }
                    for check in unconfirmed.attempt.checks
                )
            )

            support["outputs"]["data/bundle"]["confirmed"] = True
            write(support_path, json.dumps(support, indent=2) + "\n")
            write(sibling, "changed model\n")
            modified = _evaluate(summary)
            modified_currentness = _single_currentness_blocker(
                modified, "e001", "success-rate"
            )
            self.assertEqual(modified_currentness["reason"], "signature_mismatch")
            self.assertEqual(
                modified_currentness["subject"],
                bundle.resolve().as_posix(),
            )
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.material.unused"
                    and check.subject.startswith(bundle.resolve().as_posix() + "/")
                    for check in modified.attempt.checks
                )
            )

            shutil.rmtree(bundle)
            deleted = _evaluate(summary).attempt
            missing = [
                check
                for check in deleted.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "provenance.output.missing"
                and check.subject == bundle.resolve().as_posix()
            ]
            self.assertEqual(len(missing), 1)

    def test_directory_output_support_is_owned_by_the_declared_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            bundle, first, _ = _convert_result_to_bundle(entry)
            second = bundle / "second.csv"
            write(second, "success_rate\n0.676\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            second_record = dict(evidence["records"][0])
            second_record["id"] = "second-success-rate"
            second_record["sources"] = [
                {
                    "source": "<results>/second.csv",
                    "locator": {"select": [["success_rate"]]},
                }
            ]
            evidence["records"].append(second_record)
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was `67.6%`<!-- eid:success-rate source=results "
                    "select=/success_rate form=percentage render=fixed:1 -->.",
                    "The success rate was `67.6%`<!-- eid:success-rate source=results "
                    "select=/success_rate form=percentage render=fixed:1 -->.\n\n"
                    "The second rate was `67.6%`<!-- eid:second-success-rate -->.",
                ),
            )
            _replace_bundle_with_pyrun_state(entry, requires_reproduction=True)

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            currentness = _reproduce_currentness(evaluation)
            bundle_id = bundle.resolve().as_posix()

            self.assertEqual(len(currentness), 1)
            self.assertEqual(
                {item.subject for item in currentness},
                {bundle_id},
            )
            graph = evaluation.context.graph
            material_ids = {
                node.identity
                for node in graph.nodes
                if node.kind is RESEARCH_GRAPH.NodeKind.MATERIAL
            }
            self.assertIn(first.resolve().as_posix(), material_ids)
            self.assertIn(second.resolve().as_posix(), material_ids)
            evidence_material_edges = [
                edge
                for edge in graph.edges
                if edge.kind is RESEARCH_GRAPH.EdgeKind.DECLARATION
                and (
                    graph.node(edge.source).kind
                    is RESEARCH_GRAPH.NodeKind.EVIDENCE_RECORD
                )
                and (graph.node(edge.target).kind is RESEARCH_GRAPH.NodeKind.MATERIAL)
            ]
            self.assertGreaterEqual(len(evidence_material_edges), 2)
            assert evaluation.snapshot is not None
            self.assertFalse(
                any(
                    finding.code
                    in {
                        "provenance.output.reproduction_required",
                        "provenance.output.signature_mismatch",
                    }
                    for finding in evaluation.snapshot.findings
                )
            )

    def test_unreached_output_only_bundle_is_one_root_orphan_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            bundle = entry_root / "data/bundle"
            members = (bundle / "one.csv", bundle / "two.csv")
            for index, member in enumerate(members, 1):
                write(member, f"value\n{index}\n")
            unrelated = entry_root / "data/unrelated.csv"
            write(unrelated, "value\n3\n")
            write(entry_root / "scripts/bundle.py", "# bundle\n")
            resource = DATA.build_local_input(
                "bundle",
                "directory",
                "data/bundle",
                entry_root=entry_root,
                origin=False,
            )
            write(
                entry,
                entry.read_text().replace(
                    "```\n\n`Results:`",
                    "./pyrun scripts/bundle.py --output-dir data/bundle\n"
                    "```\n\n`Results:`",
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/bundle"] = {
                "confirmed": False,
                "fingerprint": DATA.observe_fingerprint(resource).fingerprint.as_dict(),
                "inputs": {},
                "code": {},
                "parameters": ["--output-dir", "data/bundle"],
                "script": {
                    "path": "scripts/bundle.py",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            (entry_root / "scripts/bundle.py").read_bytes()
                        ).hexdigest(),
                    },
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).attempt
            bundle_root = bundle.resolve().as_posix()
            bundle_findings = [
                check
                for check in result.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "orphan.material.unused"
                and (
                    check.subject == bundle_root
                    or check.subject.startswith(bundle_root + "/")
                )
            ]

            self.assertEqual(len(bundle_findings), 1)
            self.assertEqual(bundle_findings[0].subject, bundle_root)

            support["outputs"]["data/bundle"]["parameters"].append("changed")
            write(support_path, json.dumps(support, indent=2) + "\n")
            mismatched_evaluation = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary)
            )
            mismatched = mismatched_evaluation.attempt
            mismatched_subjects = {
                check.subject
                for check in mismatched.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "orphan.material.unused"
            }
            self.assertNotIn(bundle_root, mismatched_subjects)
            self.assertTrue(
                {member.resolve().as_posix() for member in members}.issubset(
                    mismatched_subjects
                )
            )
            orphan_findings = tuple(
                finding
                for finding in mismatched.findings
                if finding.code == "orphan.material.unused"
                and finding.subject
                in {member.resolve().as_posix() for member in members}
            )
            self.assertEqual(len(orphan_findings), 2)
            assert mismatched_evaluation.snapshot is not None
            batches = [
                batch
                for batch in mismatched_evaluation.snapshot.batches
                if set(batch.finding_ids)
                & {finding.finding_id for finding in orphan_findings}
            ]
            self.assertEqual(len(batches), 1)
            self.assertEqual(
                set(batches[0].finding_ids),
                {finding.finding_id for finding in orphan_findings},
            )
            self.assertIn(
                DOMAIN.RepairKey(
                    DOMAIN.RepairKeyKind.MATERIAL,
                    bundle_root,
                ),
                batches[0].repair_keys,
            )
            unrelated_finding = next(
                finding
                for finding in mismatched.findings
                if finding.code == "orphan.material.unused"
                and finding.subject == unrelated.resolve().as_posix()
            )
            unrelated_batch = next(
                batch
                for batch in mismatched_evaluation.snapshot.batches
                if unrelated_finding.finding_id in batch.finding_ids
            )
            self.assertNotEqual(unrelated_batch.batch_id, batches[0].batch_id)
            residual_ids = {
                finding.finding_id
                for finding in mismatched.findings
                if finding.code == "orphan.material.unused"
                and finding.finding_id not in batches[0].finding_ids
            }
            self.assertEqual(
                set(unrelated_batch.finding_ids),
                residual_ids,
            )

    def test_unmatched_directory_support_suppresses_descendant_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            stale = entry_root / "data/stale"
            members = (stale / "one.csv", stale / "two.csv")
            for index, member in enumerate(members, 1):
                write(member, f"value\n{index}\n")
            resource = DATA.build_local_input(
                "stale",
                "directory",
                "data/stale",
                entry_root=entry_root,
                origin=False,
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            record = dict(support["outputs"]["data/results.csv"])
            record["fingerprint"] = DATA.observe_fingerprint(
                resource
            ).fingerprint.as_dict()
            support["outputs"]["data/stale"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).attempt
            root = stale.resolve().as_posix()
            findings = [
                check
                for check in result.checks
                if check.diagnostic is not None
                and (check.subject == root or check.subject.startswith(root + "/"))
            ]

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].diagnostic.code, "orphan.output.unmatched")
            self.assertEqual(findings[0].subject, root)

    def test_project_output_supports_generated_input_without_entering_orphan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            entry_root = entry.parent
            project_output = root / "artifacts/results.csv"
            project_output.parent.mkdir()
            (entry_root / "data/results.csv").replace(project_output)
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text())
            results = next(item for item in data["inputs"] if item["name"] == "results")
            results["location"] = os.path.relpath(project_output, entry_root)
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text().replace(
                    "data/results.csv", "'<project>/artifacts/results.csv'"
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            record = support["outputs"].pop("data/results.csv")
            record["parameters"][-1] = "<project>/artifacts/results.csv"
            support["outputs"]["<project>/artifacts/results.csv"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")

            complete = _evaluate(summary).attempt

            self.assertFalse(
                any(
                    check.subject == project_output.as_posix()
                    for check in complete.checks
                )
            )

            support["outputs"]["<project>/artifacts/stale.csv"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")
            self.assertIsNotNone(_evaluate(summary).snapshot)

            record["parameters"].append("changed")
            write(support_path, json.dumps(support, indent=2) + "\n")
            drift = _evaluate(summary)
            currentness = _single_currentness_blocker(drift, "e001", "success-rate")
            self.assertEqual(currentness["reason"], "signature_mismatch")

            record["parameters"].pop()
            write(support_path, json.dumps(support, indent=2) + "\n")
            project_output.unlink()
            missing = _evaluate(summary).attempt
            provenance = _single_provenance_finding_check(
                missing, "e001", "success-rate"
            )
            self.assertEqual(provenance.diagnostic.code, "provenance.output.missing")

    def test_validation_builds_one_shared_producer_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            builder = ENGINE.build_producer_index

            with mock.patch.object(
                ENGINE,
                "build_producer_index",
                wraps=builder,
            ) as indexed:
                _evaluate(summary)

            self.assertEqual(indexed.call_count, 1)

    def test_output_support_conclusions_are_reused_within_one_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ENGINE._ScanState(root / "study.md", root, root)
            invocation = mock.Mock(identity="entry:e001:execution:one", collections=())
            support = {"output": "data/result.csv"}

            with mock.patch.object(
                ENGINE, "_evaluate_output_support", return_value=(support, None)
            ) as evaluate:
                first = ENGINE._validate_output_support(invocation, "result", state)
                second = ENGINE._validate_output_support(invocation, "result", state)

            self.assertIs(first, support)
            self.assertIs(second, support)
            self.assertEqual(evaluate.call_count, 1)

            failure = ENGINE.EngineV2Error(
                "provenance.output.reproduction_required",
                "failed",
                {"producer": invocation.identity},
                "Pyrun Output Support Records",
            )
            with mock.patch.object(
                ENGINE, "_evaluate_output_support", side_effect=failure
            ) as evaluate:
                for _ in range(2):
                    with self.assertRaises(ENGINE.MechanicalContractError) as raised:
                        ENGINE._validate_output_support(invocation, "failed", state)
                    self.assertEqual(raised.exception.code, failure.code)

            self.assertEqual(evaluate.call_count, 1)

    def test_distinct_directory_output_bindings_remain_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = ENGINE._ScanState(root / "study.md", root, root)
            first_root = root / "first/shared"
            second_root = root / "second/shared"
            first = SimpleNamespace(
                identity="producer:first",
                collections=(
                    COMMANDS.MaterialCollection(
                        "output",
                        "directory",
                        "first",
                        ((first_root / "one.csv").as_posix(),),
                        first_root.as_posix(),
                    ),
                ),
            )
            second = SimpleNamespace(
                identity="producer:second",
                collections=(
                    COMMANDS.MaterialCollection(
                        "output",
                        "directory",
                        "second",
                        ((second_root / "one.csv").as_posix(),),
                        second_root.as_posix(),
                    ),
                ),
            )

            def current(invocation: Any, subject: str, scan: Any) -> Any:
                del scan
                return (
                    {"output": subject},
                    PROVENANCE.ProducerCurrentness(
                        "required",
                        subject,
                        {"producer": invocation.identity},
                        PROVENANCE.ProvenanceAnchor(
                            "material", subject, invocation.identity
                        ),
                    ),
                )

            with mock.patch.object(
                ENGINE, "_evaluate_output_support", side_effect=current
            ) as evaluate:
                for invocation, member in (
                    (first, first_root / "one.csv"),
                    (second, second_root / "one.csv"),
                ):
                    ENGINE._validate_output_support(
                        invocation, member.as_posix(), state
                    )

            self.assertEqual(evaluate.call_count, 2)
            self.assertEqual(
                set(state.output_support_conclusions),
                {
                    (first.identity, first_root.as_posix()),
                    (second.identity, second_root.as_posix()),
                },
            )
            self.assertEqual(
                {
                    conclusion.currentness.anchor.identity
                    for conclusion in state.output_support_conclusions.values()
                    if conclusion.currentness is not None
                },
                {first_root.as_posix(), second_root.as_posix()},
            )

    def test_provenance_findings_prepare_ordering_and_blockers_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subject = (root / "missing.csv").as_posix()
            state = ENGINE._ScanState(root / "study.md", root, root)
            state.command_blocker_candidates = (
                (root / "missing.csv", False, ("entry:e001:command:1:1",)),
            )
            findings = (
                PROVENANCE.ProvenanceFinding(
                    "lineage.missing",
                    subject,
                    {"consumer": "two"},
                    "Lineage",
                    PROVENANCE.ProvenanceAnchor("material", subject),
                ),
                PROVENANCE.ProvenanceFinding(
                    "provenance.output.reproduction_required",
                    subject,
                    {"producer": "one"},
                    "Output Support",
                    PROVENANCE.ProvenanceAnchor("material", subject),
                ),
            )
            encoder = ENGINE.canonical_json

            with (
                mock.patch.object(ENGINE, "canonical_json", wraps=encoder) as canonical,
                mock.patch.object(
                    ENGINE,
                    "_indexed_command_blockers",
                    wraps=ENGINE._indexed_command_blockers,
                ) as blocker_index,
            ):
                prepared = ENGINE._ordered_provenance_findings(findings, state)
                self.assertEqual(
                    ENGINE._command_blockers(subject, state),
                    ("entry:e001:command:1:1",),
                )

            self.assertEqual(canonical.call_count, len(findings))
            self.assertEqual(blocker_index.call_count, 1)
            self.assertEqual(
                [item.finding.code for item in prepared],
                ["provenance.output.reproduction_required", "lineage.missing"],
            )
            with mock.patch.object(
                ENGINE,
                "_command_blockers",
                side_effect=AssertionError("prepared check must reuse blockers"),
            ):
                check = ENGINE._provenance_finding_check(
                    "provenance:e001:test", prepared[-1], dependencies=()
                )
            self.assertEqual(check.outcome, DOMAIN.CheckOutcome.BLOCKED)

    def test_output_support_parameter_change_breaks_provenance_until_replaced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text().replace(
                    "--input-catalog '<catalog>' ",
                    "--input-catalog '<catalog>' --mode revised ",
                ),
            )

            changed = _evaluate(summary)

            currentness = _single_currentness_blocker(changed, "e001", "success-rate")
            self.assertEqual(currentness["reason"], "signature_mismatch")
            self.assertEqual(currentness["observed"]["fields"], ["parameters"])

            output_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(output_path.read_text())
            support["outputs"]["data/results.csv"]["parameters"] = [
                "--input-catalog",
                "<catalog>",
                "--mode",
                "revised",
                "--output-data",
                "data/results.csv",
            ]
            write(output_path, json.dumps(support, indent=2) + "\n")
            self.assertIsNotNone(_evaluate(summary).snapshot)

    def test_recursive_chain_requires_each_link_and_input_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            intermediate = entry_root / "data" / "intermediate.csv"
            write(intermediate, "id\n1\n")
            write(entry_root / "scripts" / "preprocess.py", "# preprocess\n")
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text())
            intermediate_digest = hashlib.sha256(intermediate.read_bytes()).hexdigest()
            data["inputs"].append(
                {
                    "name": "intermediate",
                    "kind": "file",
                    "location": "data/intermediate.csv",
                    "identity": {"algorithm": "sha256"},
                    "origin": False,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text().replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' ",
                    "./pyrun --cid preprocess -- scripts/preprocess.py "
                    "--input-data '<catalog>' --output-data '<intermediate>'\n"
                    "```\n\n```bash\n"
                    "./pyrun --cid model -- scripts/model.py "
                    "--input-data '<intermediate>' ",
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            result_record = support["outputs"]["data/results.csv"]
            result_record["inputs"] = {
                "intermediate": {
                    "algorithm": "sha256",
                    "digest": intermediate_digest,
                }
            }
            result_record["parameters"] = [
                "--input-data",
                "<intermediate>",
                "--output-data",
                "data/results.csv",
            ]
            catalog = entry_root / "data" / "catalog.csv"
            support["outputs"]["data/intermediate.csv"] = {
                "confirmed": True,
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": intermediate_digest,
                },
                "inputs": {
                    "catalog": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                    }
                },
                "code": {},
                "parameters": [
                    "--input-data",
                    "<catalog>",
                    "--output-data",
                    "data/intermediate.csv",
                ],
                "script": {
                    "path": "scripts/preprocess.py",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            (entry_root / "scripts" / "preprocess.py").read_bytes()
                        ).hexdigest(),
                    },
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            self.assertIsNotNone(_evaluate(summary).snapshot)
            complete_document = entry.read_text(encoding="utf-8")
            result_record["confirmed"] = False
            write(support_path, json.dumps(support, indent=2) + "\n")
            write(
                entry,
                complete_document.replace(
                    "./pyrun --cid preprocess -- scripts/preprocess.py "
                    "--input-data '<catalog>' --output-data '<intermediate>'\n"
                    "```\n\n```bash\n",
                    "",
                ),
            )

            collected = _evaluate(summary).attempt
            provenance = _provenance_finding_checks(collected, "e001", "success-rate")
            self.assertEqual(
                {check.diagnostic.code for check in provenance if check.diagnostic},
                {"lineage.missing"},
            )
            self.assertTrue(_reproduce_currentness(_evaluate(summary)))
            canonical = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            self.assertEqual(
                {
                    finding.code
                    for finding in canonical.attempt.findings
                    if finding.type is DOMAIN.RuleArea.PROVENANCE
                },
                {"lineage.missing"},
            )

            result_record["confirmed"] = True
            write(support_path, json.dumps(support, indent=2) + "\n")
            confirmed = _evaluate(summary).attempt
            self.assertEqual(
                {
                    check.diagnostic.code
                    for check in confirmed.checks
                    if check
                    in _provenance_finding_checks(confirmed, "e001", "success-rate")
                    and check.diagnostic
                },
                {"lineage.missing"},
            )
            write(entry, complete_document)
            write(support_path, json.dumps(support, indent=2) + "\n")

            original = catalog.read_bytes()
            write(catalog, "id\n2\n")
            self.assertTrue(_reproduce_currentness(_evaluate(summary)))
            catalog.write_bytes(original)
            self.assertFalse(_evaluate(summary).attempt.findings)
            write(entry_root / "scripts" / "preprocess.py", "# changed\n")
            self.assertFalse(_reproduce_currentness(_evaluate(summary)))

    def test_unconfirmed_and_missing_output_records_fail_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            output_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(output_path.read_text())
            support["outputs"]["data/results.csv"]["confirmed"] = False
            write(output_path, json.dumps(support) + "\n")

            unconfirmed = _evaluate(summary)
            currentness = _single_currentness_blocker(
                unconfirmed, "e001", "success-rate"
            )
            self.assertEqual(currentness["reason"], "required")

            output_path.unlink()
            unrecorded = _evaluate(summary).attempt
            check = _single_provenance_finding_check(unrecorded, "e001", "success-rate")
            self.assertEqual(check.diagnostic.code, "provenance.output.unrecorded")

    def test_origin_support_ignores_reproduce_currentness(self) -> None:
        invocation = SimpleNamespace(material_owner="entry-owner")
        state = ENGINE._ScanState(
            Path("/project/study.md"), Path("/project/study"), Path("/project")
        )
        state.execution_states["entry-owner"] = object()
        state.execution_output_owners["entry-owner"] = object()
        association = SimpleNamespace(
            execution=SimpleNamespace(requires_reproduction=True)
        )
        with (
            mock.patch.object(ENGINE, "associate_execution", return_value=object()),
            mock.patch.object(
                ENGINE,
                "resolve_execution_output",
                return_value=SimpleNamespace(association=association),
            ),
        ):
            self.assertTrue(
                ENGINE._has_structural_output_record(
                    invocation, "/project/result.csv", state
                )
            )

        state.execution_states.clear()
        state.output_files["entry-owner"] = object()
        for confirmed in (True, False):
            with (
                self.subTest(legacy_confirmed=confirmed),
                mock.patch.object(
                    ENGINE,
                    "resolve_output_support",
                    return_value=SimpleNamespace(
                        record=SimpleNamespace(confirmed=confirmed)
                    ),
                ),
                mock.patch.object(
                    ENGINE,
                    "_entry_root_for_owner",
                    return_value=Path("/project/study/entries/entry-owner"),
                ),
            ):
                self.assertTrue(
                    ENGINE._has_structural_output_record(
                        invocation, "/project/result.csv", state
                    )
                )

    def test_missing_output_takes_precedence_with_or_without_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            output = entry.parent / "data/results.csv"
            output.unlink()

            recorded = _evaluate(summary).attempt
            check = _single_provenance_finding_check(recorded, "e001", "success-rate")
            self.assertEqual(check.diagnostic.code, "provenance.output.missing")

            (entry.parent / "pyrun-outputs.json").unlink()
            unrecorded = _evaluate(summary).attempt
            check = _single_provenance_finding_check(unrecorded, "e001", "success-rate")
            self.assertEqual(check.diagnostic.code, "provenance.output.missing")

    def test_missing_graph_output_outside_evidence_closure_fails_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            text = entry.read_text(encoding="utf-8")
            write(
                entry,
                text.replace(
                    "```\n\n`Results:`",
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data data/missing.csv\n"
                    "```\n\n`Results:`",
                ),
            )

            result = _evaluate(summary).attempt

            missing = [
                check
                for check in result.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "provenance.output.missing"
                and check.subject.endswith("/data/missing.csv")
            ]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0].area, DOMAIN.RuleArea.PROVENANCE)

    def test_missing_output_conclusion_uses_authoritative_production_edge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text().replace(
                    "```\n\n`Results:",
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data data/missing.csv\n```\n\n`Results:",
                ),
            )
            missing = (entry.parent / "data/missing.csv").resolve().as_posix()
            original = ENGINE.build_evaluation_graph

            def without_edge(inputs: Any, *, bounds: Any) -> Any:
                return _without_material_production(
                    original(inputs, bounds=bounds),
                    missing,
                )

            baseline = _evaluate(summary).attempt
            with mock.patch.object(
                ENGINE,
                "build_evaluation_graph",
                side_effect=without_edge,
            ):
                disconnected = _evaluate(summary).attempt

            self.assertTrue(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "provenance.output.missing"
                    and check.subject == missing
                    for check in baseline.checks
                )
            )
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "provenance.output.missing"
                    and check.subject == missing
                    for check in disconnected.checks
                )
            )

    def test_unmatched_output_conclusion_uses_authoritative_production_edge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            output = (entry.parent / "data/results.csv").resolve().as_posix()
            original = ENGINE.build_evaluation_graph

            def without_edge(inputs: Any, *, bounds: Any) -> Any:
                return _without_material_production(
                    original(inputs, bounds=bounds),
                    output,
                )

            baseline = _evaluate(summary).attempt
            with mock.patch.object(
                ENGINE,
                "build_evaluation_graph",
                side_effect=without_edge,
            ):
                disconnected = _evaluate(summary).attempt

            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.output.unmatched"
                    and check.subject == output
                    for check in baseline.checks
                )
            )
            self.assertTrue(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "orphan.output.unmatched"
                    and check.subject == output
                    for check in disconnected.checks
                )
            )

    def test_unmatched_record_is_one_orphan_finding_not_an_orphan_duplicate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            stale = entry.parent / "data/stale.csv"
            write(stale, "stale\n")
            output_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(output_path.read_text())
            support["outputs"]["data/stale.csv"] = {
                **support["outputs"]["data/results.csv"],
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(stale.read_bytes()).hexdigest(),
                },
            }
            write(output_path, json.dumps(support) + "\n")

            result = _evaluate(summary).attempt
            stale_findings = [
                check
                for check in result.checks
                if check.subject == stale.resolve().as_posix()
                and check.diagnostic is not None
            ]
            self.assertEqual(len(stale_findings), 1)
            self.assertEqual(
                stale_findings[0].diagnostic.code, "orphan.output.unmatched"
            )

    def test_input_verification_dependencies_include_the_entry_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "source.csv", "value\n1\n")
            resource = DATA.build_local_input(
                "source", "file", str(root / "source.csv"), entry_root=root
            )

            self.assertNotEqual(
                ENGINE._input_declaration_key("entries/first", resource),
                ENGINE._input_declaration_key("entries/second", resource),
            )

    def test_material_input_prerequisites_use_precomputed_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            exact = (root / "exact.csv").as_posix()
            managed = (root / "managed").as_posix()
            exact_check = ENGINE._pass_check(
                "entry:e001:input:exact-declaration", DOMAIN.RuleArea.PROVENANCE
            )
            directory_check = ENGINE._pass_check(
                "entry:e001:input:managed-declaration", DOMAIN.RuleArea.PROVENANCE
            )
            state = ENGINE._ScanState(root / "study.md", root, root)
            state.input_prerequisite_files[exact] = [exact_check]
            state.input_prerequisite_directories[managed] = [directory_check]
            state.logical_material_roots = (
                (root, "entries/2026-08-29-e001-study", ""),
            )
            state.command_blocker_candidates = (
                (
                    root / "managed",
                    True,
                    ("entry:e001:command:1:1",),
                ),
            )
            state.owner_surface_prerequisite_checks["entries/2026-08-29-e001-study"] = (
                exact_check,
            )

            with mock.patch.object(
                Path,
                "resolve",
                side_effect=AssertionError("lookup must not access the filesystem"),
            ):
                self.assertEqual(
                    ENGINE._material_input_prerequisites(exact, state), (exact_check,)
                )
                self.assertEqual(
                    ENGINE._material_input_prerequisites(
                        f"{managed}/nested/result.csv", state
                    ),
                    (directory_check,),
                )
                self.assertEqual(
                    ENGINE._material_input_prerequisites(
                        (root / "unrelated.csv").as_posix(), state
                    ),
                    (),
                )
                self.assertEqual(
                    ENGINE._logical_entry_material(exact, state),
                    ("entries/2026-08-29-e001-study", "exact.csv"),
                )
                self.assertEqual(
                    ENGINE._command_blockers(f"{managed}/nested/result.csv", state),
                    ("entry:e001:command:1:1",),
                )
                self.assertEqual(
                    ENGINE._owner_surface_prerequisites(
                        "entries/2026-08-29-e001-study", state
                    ),
                    (exact_check,),
                )

    def test_shared_input_observation_checks_every_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            write(source, "value\n1\n")
            correct = DATA.build_local_input(
                "first", "file", str(source), entry_root=root
            )
            wrong = replace(
                correct,
                name="second",
                origin=False,
            )
            state = ENGINE._ScanState(root / "study.md", root, root)

            ENGINE._verify_input(correct, state)

            ENGINE._verify_input(wrong, state)

    def test_cross_log_summary_link_is_not_an_owned_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            external_entry = (
                root / "docs/other/entries/2026-08-29-e005-other-study/e005.md"
            )
            write(external_entry, "# External entry\n")
            write(external_entry.parent / "evidence.json", "{}\n")
            write(
                summary,
                summary.read_text().replace(
                    "## Entries",
                    "See [external evidence]"
                    "(other/entries/2026-08-29-e005-other-study/e005.md).\n\n"
                    "## Entries",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertFalse(
                any("e005" in check.check_id for check in evaluation.attempt.checks)
            )

    def test_symlinked_entry_root_is_rejected_lexically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            lexical_root = entry.parent
            physical_root = lexical_root.with_name(f"{lexical_root.name}-physical")
            lexical_root.rename(physical_root)
            lexical_root.symlink_to(physical_root.name, target_is_directory=True)

            evaluation = _evaluate(summary)

            failure = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e001:declaration"
            )
            self.assertEqual(failure.outcome, DOMAIN.CheckOutcome.FINDING)
            assert failure.diagnostic is not None
            self.assertEqual(failure.diagnostic.code, "evidence.declaration.invalid")

    def test_invalid_entry_evidence_does_not_block_valid_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            bad_root = root / "docs/study/entries/2026-08-30-e002-invalid"
            bad_entry = bad_root / "e002.md"
            write(bad_entry, "# Invalid entry\n")
            write(bad_root / "evidence.json", "{\n")
            write(
                summary,
                summary.read_text().replace(
                    "- [Study trial]",
                    "- [Invalid entry]"
                    "(study/entries/2026-08-30-e002-invalid/e002.md)\n"
                    "- [Study trial]",
                ),
            )

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            invalid = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e002:evidence-declaration"
            )
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(invalid.area, DOMAIN.RuleArea.CONFORMANCE)
            self.assertEqual(invalid.diagnostic.code, "evidence.json.schema_invalid")
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "association.declaration_missing"
                    and "e002" in check.check_id
                    for check in evaluation.attempt.checks
                )
            )

    def test_invalid_evidence_does_not_block_unmatched_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            extra = entry.parent / "data/extra.csv"
            write(extra, "value\n1\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            recorded = json.loads(json.dumps(support["outputs"]["data/results.csv"]))
            recorded["fingerprint"]["digest"] = hashlib.sha256(
                extra.read_bytes()
            ).hexdigest()
            support["outputs"]["data/extra.csv"] = recorded
            write(support_path, json.dumps(support, indent=2) + "\n")
            write(entry.parent / "evidence.json", "{\n")

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e001:evidence-declaration"
            )
            unmatched = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.endswith(":data/extra.csv")
                and check.check_id.startswith("orphan:unmatched-output:")
            )
            self.assertIs(evidence.outcome, DOMAIN.CheckOutcome.FINDING)
            self.assertIs(unmatched.outcome, DOMAIN.CheckOutcome.FINDING)
            self.assertEqual(unmatched.diagnostic.code, "orphan.output.unmatched")

    def test_invalid_retention_preserves_commands_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "retention.json", "{\n")

            evaluation = _evaluate(summary)

            invalid = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e001:retention-declaration"
            )
            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertEqual(invalid.diagnostic.code, "retention.declaration.invalid")
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 1)

    def test_redundant_retention_is_a_contextualized_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            retention_path = entry.parent / "retention.json"
            write(
                retention_path,
                json.dumps(
                    {
                        "schema": "research-log-retention/v1",
                        "records": [{"id": "result", "paths": ["data/results.csv"]}],
                    }
                )
                + "\n",
            )

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            finding = next(
                item
                for item in evaluation.attempt.findings
                if item.code == "retention.declaration.invalid"
            )

            self.assertIsNotNone(evaluation.snapshot)
            self.assertEqual(
                finding.source_locations,
                (DOMAIN.SourceLocation(retention_path.resolve().as_posix()),),
            )
            self.assertTrue(finding.context_nodes)

    def test_command_discovery_exception_has_explicit_entry_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            error = ENGINE.EngineV2Error(
                "invocation.command.unsupported",
                entry.as_posix(),
                {"reason": "test_failure"},
                "Recorded-Command Provenance And Material Graph",
            )

            with mock.patch.object(
                ENGINE,
                "_discover_entry_invocations",
                side_effect=error,
            ):
                evaluation = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(summary)
                )

            finding = next(
                item
                for item in evaluation.attempt.findings
                if item.finding_id == "entry:e001:command"
            )
            self.assertEqual(finding.entry, "e001")
            self.assertEqual(
                finding.source_locations,
                (DOMAIN.SourceLocation(entry.resolve().as_posix()),),
            )
            self.assertEqual(
                finding.context_nodes,
                (
                    DOMAIN.GraphReference(
                        "document",
                        entry.resolve().as_posix(),
                        "e001",
                    ),
                ),
            )

    def test_origin_ignores_confirmed_producer_from_another_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            source = root / "output/logs/other/e001/data/catalog.csv"
            write(source, "id\n1\n")
            (entry.parent / "data/catalog.csv").unlink()
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog["location"] = source.as_posix()
            write(data_path, json.dumps(data, indent=2) + "\n")

            other_entry = root / "docs/other/entries/2026-08-29-e001-other"
            output_key = "<project>/output/logs/other/e001/data/catalog.csv"
            script = other_entry / "scripts/build.py"
            write(root / "docs/other.md", "# Other\n\n## Entries\n")
            write(script, "# retained producer\n")
            write(
                other_entry / "e001.md",
                "# Other entry\n\n"
                "## Build catalog\n\n"
                "`Steps:`\n\n"
                "```bash\n"
                "./pyrun scripts/build.py "
                f"--output-data '{output_key}'\n"
                "```\n\n"
                "`Results:`\n\n"
                "The catalog was generated.\n",
            )
            recipe = PYRUN_STATE.ExecutionRecipe(
                "scripts/build.py",
                ("--output-data", output_key),
                (),
                (),
                ((output_key, "file"),),
                parameter_roles=fixture_parameter_roles(
                    ("--output-data", output_key), (), ((output_key, "file"),)
                ),
            )
            observed = PYRUN_STATE.ObservedExecution(
                DATA.Fingerprint(
                    "sha256", digest=hashlib.sha256(script.read_bytes()).hexdigest()
                ),
                (),
                None,
                (
                    (
                        output_key,
                        DATA.Fingerprint(
                            "sha256",
                            digest=hashlib.sha256(source.read_bytes()).hexdigest(),
                        ),
                    ),
                ),
            )
            execution = PYRUN_STATE.PyrunExecution(
                False,
                True,
                "2030-01-01T00:00:00Z",
                PYRUN_STATE.PYRUN_RUNNER,
                PYRUN_STATE.PYRUN_ENVIRONMENT_PROFILE,
                PYRUN_STATE.PYRUN_EXECUTION_CONTRACT,
                recipe,
                observed,
            )
            identity = PYRUN_STATE.execution_id(recipe)
            other_state = PYRUN_STATE.PyrunFile(
                other_entry / PYRUN_STATE.PYRUN_FILENAME,
                other_entry,
                {"build": PYRUN_STATE.PyrunCommand({identity: execution})},
            )
            write(
                other_state.path,
                PYRUN_STATE.validated_pyrun_serialization(
                    other_state, project_root=root
                ),
            )

            evaluation = _evaluate(summary)

            provenance = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)

    def test_invalid_input_preserves_unrelated_inputs_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            bad_source = entry.parent / "data" / "bad.csv"
            write(bad_source, "value\n1\n")
            data_path = entry.parent / "data.json"
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            payload["inputs"].append(
                {
                    "name": "unused-bad",
                    "kind": "file",
                    "location": "data/bad.csv",
                    "identity": {"algorithm": "sha256"},
                    "origin": True,
                }
            )
            write(data_path, json.dumps(payload, indent=2) + "\n")

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 1)

    def test_invalid_command_input_blocks_its_provenance_without_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            catalog_path = entry.parent / "data/catalog.csv"
            write(catalog_path, "value\n1\n")

            evaluation = _evaluate(summary)

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            command = checks["entry:e001:command:1:1:output:1"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(command.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(
                checks["evidence:e001:success-rate"].outcome,
                DOMAIN.CheckOutcome.PASS,
            )
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)
            failure_codes = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("producer.missing", failure_codes)

    def test_invalid_data_file_blocks_dependent_checks_without_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            write(data_path, "{\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            declaration = checks["entry:e001:data-declaration"]
            for identity in (
                "entry:e001:command:1:1",
                "evidence:e001:success-rate",
                "provenance:e001:success-rate",
            ):
                self.assertEqual(checks[identity].outcome, DOMAIN.CheckOutcome.BLOCKED)
                self.assertIn(
                    {"dependency": declaration.check_id},
                    checks[identity].dependency_evidence,
                )
            failure_codes = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("data.input.undeclared", failure_codes)
            self.assertNotIn("orphan.material.unused", failure_codes)
            finding = next(
                item
                for item in evaluation.attempt.findings
                if item.finding_id == "entry:e001:data-declaration"
            )
            self.assertEqual(finding.context_nodes, ())
            self.assertEqual(
                finding.source_locations,
                (DOMAIN.SourceLocation(data_path.resolve().as_posix()),),
            )

            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e001", entry.parent),
                )
            )
            self.assertFalse(scoped.snapshot.failed_checks)
            self.assertEqual(
                {(item.finding_id, item.code) for item in evaluation.snapshot.findings},
                {(item.finding_id, item.code) for item in scoped.snapshot.findings},
            )
            self.assertEqual(
                {item.code for item in scoped.snapshot.findings},
                {"data.declaration.invalid"},
            )
            unmatched = [
                check
                for check in evaluation.attempt.checks
                if check.check_id.startswith("orphan:unmatched-output:")
            ]
            self.assertTrue(unmatched)
            self.assertTrue(
                all(check.outcome is DOMAIN.CheckOutcome.BLOCKED for check in unmatched)
            )

    def test_invalid_evidence_file_blocks_owner_orphan_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "data/results.csv"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            declaration = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e001:evidence-declaration"
            )
            orphan_checks = [
                check
                for check in evaluation.attempt.checks
                if check.area is DOMAIN.RuleArea.ORPHAN
            ]
            self.assertTrue(orphan_checks)
            for check in orphan_checks:
                self.assertEqual(check.outcome, DOMAIN.CheckOutcome.BLOCKED)
                self.assertIn(
                    {"dependency": declaration.check_id}, check.dependency_evidence
                )
            self.assertFalse(
                any(check.diagnostic is not None for check in orphan_checks)
            )

    def test_conflicted_input_blocks_consumers_without_undeclared_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            shared = root / "inputs/catalog.csv"
            write(shared, "success_rate\n0.676\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog.update(
                {
                    "location": shared.as_posix(),
                    "identity": {"algorithm": "sha256"},
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "<catalog>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            second_root = root / "docs/study/entries/2026-08-30-e002-conflict"
            write(second_root / "e002.md", "# Entry e002\n")
            write(
                second_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared-catalog",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                summary,
                summary.read_text(encoding="utf-8") + "\n- [Conflict]"
                "(study/entries/2026-08-30-e002-conflict/e002.md)\n",
            )

            evaluation = _evaluate(summary)

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            conflict = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.startswith("conformance:data-conflict:")
            )
            for identity in (
                "entry:e001:command:1:1",
                "evidence:e001:success-rate",
                "provenance:e001:success-rate",
            ):
                self.assertEqual(checks[identity].outcome, DOMAIN.CheckOutcome.BLOCKED)
                self.assertIn(
                    {"dependency": conflict.check_id},
                    checks[identity].dependency_evidence,
                )
            failure_codes = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("data.input.undeclared", failure_codes)
            self.assertNotIn("orphan.input.unused", failure_codes)
            unmatched = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.startswith("orphan:unmatched-output:")
            )
            self.assertIs(unmatched.outcome, DOMAIN.CheckOutcome.BLOCKED)
            self.assertIn(
                {"dependency": conflict.check_id}, unmatched.dependency_evidence
            )

    def test_cross_entry_data_conflict_does_not_block_unrelated_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            conflict_path = root / "shared-conflict.csv"
            write(conflict_path, "conflict\n")
            links: list[str] = []
            for entry_id, date, identity in (
                ("e002", "2026-08-30", "conflict/v1"),
                ("e003", "2026-08-31", "conflict/v2"),
            ):
                entry_root = root / f"docs/study/entries/{date}-{entry_id}-conflict"
                write(entry_root / "scripts/model.py", "# fixture\n")
                safe_path = entry_root / f"data/{entry_id}.csv"
                write(safe_path, f"safe/{entry_id}")
                write(
                    entry_root / f"{entry_id}.md",
                    f"# Entry {entry_id}\n\n## Trial\n\n`Steps:`\n\n"
                    "```bash\n"
                    f"./pyrun scripts/model.py --input-data '<safe-{entry_id}>' "
                    "--output-data data/result.csv\n"
                    "```\n\n`Results:`\n\nDone.\n",
                )
                write(
                    entry_root / "data.json",
                    json.dumps(
                        {
                            "schema": "research-log-data/v6",
                            "inputs": [
                                {
                                    "name": "conflict",
                                    "kind": "file",
                                    "location": conflict_path.as_posix(),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": entry_id == "e002",
                                },
                                {
                                    "name": f"safe-{entry_id}",
                                    "kind": "file",
                                    "location": (f"data/{entry_id}.csv"),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": True,
                                },
                            ],
                        }
                    )
                    + "\n",
                )
                links.append(
                    f"- [{entry_id}]"
                    f"(study/entries/{date}-{entry_id}-conflict/{entry_id}.md)"
                )
            write(summary, summary.read_text() + "\n" + "\n".join(links) + "\n")

            evaluation = _evaluate(summary)

            conflict = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.startswith("conformance:data-conflict:")
            )
            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertEqual(conflict.outcome, DOMAIN.CheckOutcome.FINDING)
            assert conflict.diagnostic is not None
            self.assertEqual(conflict.diagnostic.code, "data.declaration.conflict")
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 3)

    def test_invalid_entry_command_does_not_block_valid_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            bad_root = root / "docs/study/entries/2026-08-30-e002-invalid"
            bad_entry = bad_root / "e002.md"
            write(
                bad_entry,
                "# Invalid entry\n\n## Trial\n\n`Steps:`\n\n"
                "```bash\npython scripts/run.py\n```\n\n`Results:`\n\nDone.\n",
            )
            write(
                summary,
                summary.read_text().replace(
                    "- [Study trial]",
                    "- [Invalid entry]"
                    "(study/entries/2026-08-30-e002-invalid/e002.md)\n"
                    "- [Study trial]",
                ),
            )

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            invalid = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.startswith("entry:e002:command")
            )
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(invalid.area, DOMAIN.RuleArea.CONFORMANCE)
            self.assertEqual(invalid.diagnostic.code, "invocation.command.unsupported")

    def test_split_entry_loads_shared_root_surfaces_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            summary = project_root / "docs" / "study.md"
            log_root = project_root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            first = entry_root / "e001a.md"
            second = entry_root / "e001b.md"
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [First](study/entries/2026-08-29-e001-study/e001a.md)\n"
                "- [Second](study/entries/2026-08-29-e001-study/e001b.md)\n",
            )
            write(first, "# First\n")
            write(second, "# Second\n")
            write(entry_root / "evidence.json", "{}\n")
            write(entry_root / "data.json", _origin_data_json(entry_root))
            state = ENGINE._ScanState(summary, log_root, project_root)
            evidence = object()

            with (
                mock.patch.object(
                    ENGINE, "load_evidence_file", return_value=evidence
                ) as evidence_loader,
                mock.patch.object(
                    ENGINE, "load_data_file", wraps=ENGINE.load_data_file
                ) as data_loader,
            ):
                entries = ENGINE._entries(summary.read_text(), state)

            evidence_loader.assert_called_once()
            data_loader.assert_called_once()
            self.assertIs(entries[0].evidence_file, entries[1].evidence_file)
            self.assertIs(entries[0].data_file, entries[1].data_file)

    def test_split_document_summary_reference_uses_authored_document_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            split = entry.with_name("e001a.md")
            entry.rename(split)
            write(
                summary,
                summary.read_text(encoding="utf-8")
                .replace("e001.md", "e001a.md")
                .replace("ref entry = e001;", "ref entry = e001a;"),
            )
            evidence_path = split.parent / "evidence.json"
            write(
                evidence_path,
                evidence_path.read_text(encoding="utf-8").replace(
                    "e001.md", "e001a.md"
                ),
            )

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            summary_failures = [
                finding
                for finding in evaluation.attempt.findings
                if finding.code.startswith("summary.reference.")
            ]

            self.assertEqual(summary_failures, [])
            self.assertEqual(evaluation.context.materials[0].entry_id, "e001")
            self.assertTrue(
                any(
                    check.check_id == "evidence:e001:success-rate"
                    for check in evaluation.attempt.checks
                )
            )

    def test_invalid_split_document_summary_reference_remains_one_finding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            split = entry.with_name("e001a.md")
            entry.rename(split)
            write(
                summary,
                summary.read_text(encoding="utf-8")
                .replace("e001.md", "e001a.md")
                .replace("ref entry = e001;", "ref entry = e001b;"),
            )
            evidence_path = split.parent / "evidence.json"
            write(
                evidence_path,
                evidence_path.read_text(encoding="utf-8").replace(
                    "e001.md", "e001a.md"
                ),
            )

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            failures = [
                finding
                for finding in evaluation.attempt.findings
                if finding.code == "summary.reference.target_invalid"
            ]

            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].subject, "summary:5")

    def test_split_entry_commands_are_discovered_once_per_listed_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            log_root = project_root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            first = entry_root / "e001a.md"
            second = entry_root / "e001b.md"
            command = (
                "## Trial\n\n"
                "`Steps:`\n\n"
                "```bash\n./pyrun --cid run -- scripts/run.py "
                "--output-data data/result.csv\n```\n\n"
                "`Results:`\n\nDone.\n"
            )
            write(first, command)
            write(second, command)
            entries = [
                ENGINE._Entry("e001a", first, entry_root, None, None, None),
                ENGINE._Entry("e001b", second, entry_root, None, None, None),
            ]
            state = ENGINE._ScanState(
                project_root / "docs" / "study.md", log_root, project_root
            )
            state.entries = entries

            invocations = ENGINE._discover_invocations(state)

            self.assertEqual(len(invocations), 2)
            self.assertEqual([item.entry for item in invocations], ["e001a", "e001b"])
            self.assertEqual(
                [item.document for item in invocations],
                [
                    "entries/2026-08-29-e001-study/e001a.md",
                    "entries/2026-08-29-e001-study/e001b.md",
                ],
            )
            self.assertEqual(
                {item.material_owner for item in invocations},
                {"entries/2026-08-29-e001-study"},
            )

    def test_split_entry_shares_data_index_usage_without_false_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "study.md"
            log_root = root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            first = entry_root / "e001a.md"
            second = entry_root / "e001b.md"
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [First](study/entries/2026-08-29-e001-study/e001a.md)\n"
                "- [Second](study/entries/2026-08-29-e001-study/e001b.md)\n",
            )
            write(entry_root / "data.json", _origin_data_json(entry_root))
            write(
                first,
                "## Trial\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/run.py --label baseline\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            write(
                second,
                "## Trial\n\n`Steps:`\n\n"
                "```bash\n./pyrun scripts/run.py --input-catalog '<catalog>'\n```\n\n"
                "`Results:`\n\nDone.\n",
            )

            evaluation = _evaluate(summary)

            failures = [
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            ]
            self.assertNotIn("orphan.input.unused", failures)
            self.assertEqual(evaluation.metrics["invocations"], 2)

    def test_unlisted_split_document_evidence_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "study.md"
            log_root = root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            listed = entry_root / "e001a.md"
            unlisted = entry_root / "e001b.md"
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Listed](study/entries/2026-08-29-e001-study/e001a.md)\n",
            )
            write(
                listed,
                "## Trial\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/run.py --label baseline\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            write(
                unlisted,
                "## Trial\n\n`Steps:`\n\nNo command.\n\n`Results:`\n\n"
                "Value: `1`<!-- eid:unlisted-value -->.\n",
            )
            write(entry_root / "data" / "result.csv", "value\n1\n")
            write(
                entry_root / "evidence.json",
                """{
  "schema": "research-log-evidence/v5",
  "records": [
    {
      "id": "unlisted-value",
      "document": "entries/2026-08-29-e001-study/e001b.md",
      "kind": "statistic",
      "sources": [
        {"source": "<result>", "locator": {"select": [["value"]]}}
      ],
      "transformation": null
    }
  ]
}
""",
            )

            evaluation = _evaluate(summary)

            failures = [
                check.diagnostic
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "association.presentation_missing"
            ]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].observed["ids"], ("unlisted-value",))

    def test_complete_log_is_mechanically_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            scopes = _area_outcomes(evaluation.attempt)
            self.assertEqual(scopes[DOMAIN.RuleArea.EVIDENCE], DOMAIN.CheckOutcome.PASS)
            self.assertEqual(
                scopes[DOMAIN.RuleArea.PROVENANCE], DOMAIN.CheckOutcome.PASS
            )
            self.assertEqual(scopes[DOMAIN.RuleArea.ORPHAN], DOMAIN.CheckOutcome.PASS)
            self.assertEqual(evaluation.metrics["source_evaluations"], 1)
            self.assertEqual(evaluation.metrics["source_reads"], 1)
            self.assertEqual(evaluation.metrics["script_hashes"], 1)
            self.assertEqual(evaluation.metrics["markdown_reads"], 2)
            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertIn(
                {
                    "context": {
                        "classification": "experimental",
                        "classifier_version": "entry-section-labels/1",
                        "under_results": True,
                    }
                },
                evidence.dependency_evidence,
            )

    def test_repeated_evidence_source_reuses_provenance_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            duplicate = json.loads(json.dumps(evidence["records"][0]))
            duplicate["id"] = "success-rate-copy"
            evidence["records"].append(duplicate)
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8") + "\nThe copied rate was `67.6%`"
                "<!-- eid:success-rate-copy -->.\n",
            )

            with mock.patch.object(
                ENGINE,
                "evaluate_complete_provenance",
                wraps=ENGINE.evaluate_complete_provenance,
            ) as evaluate:
                evaluation = _evaluate(summary)

            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(evaluation.metrics["provenance_traversals"], 1)
            self.assertEqual(evaluation.metrics["provenance_traversals_reused"], 1)

    def test_repeated_evidence_source_reuses_one_currentness_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            duplicate = json.loads(json.dumps(evidence["records"][0]))
            duplicate["id"] = "success-rate-copy"
            evidence["records"].append(duplicate)
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8") + "\nThe copied rate was `67.6%`"
                "<!-- eid:success-rate-copy -->.\n",
            )
            write(entry.parent / "data/catalog.csv", "id\n2\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))

            failed_checks = [
                check
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "provenance.output.signature_mismatch"
            ]
            findings = [
                finding
                for finding in evaluation.attempt.findings
                if finding.code == "provenance.output.signature_mismatch"
            ]
            self.assertFalse(failed_checks)
            self.assertFalse(findings)
            checks = {check.check_id: check for check in evaluation.attempt.checks}
            for record_id in ("success-rate", "success-rate-copy"):
                consumer = checks[f"provenance:e001:{record_id}"]
                self.assertEqual(consumer.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(len(evaluation.context.currentness), 1)

            result_path = (entry.parent / "data/results.csv").resolve().as_posix()
            material_node = f"material:{result_path}"
            declaration_sources = {
                edge.source
                for edge in evaluation.context.graph.edges
                if edge.kind is RESEARCH_GRAPH.EdgeKind.DECLARATION
                and edge.target == material_node
            }
            self.assertTrue(
                {
                    "evidence_record:e001:e001:success-rate",
                    "evidence_record:e001:e001:success-rate-copy",
                }
                <= declaration_sources,
                declaration_sources,
            )

    def test_restricted_provenance_scales_with_material_not_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            write(entry_root / "data/catalog.csv", "success_rate\n0.676\n")
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog["origin"] = False
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry_root / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            template = evidence["records"][0]
            records = []
            presentations = []
            consumer_count = 25
            for index in range(consumer_count):
                record = json.loads(json.dumps(template))
                record["id"] = "success-rate" if index == 0 else f"catalog-rate-{index}"
                record["sources"][0]["source"] = "<catalog>"
                records.append(record)
                if index:
                    presentations.append(
                        f"The catalog rate was `67.6%`"
                        f"<!-- eid:catalog-rate-{index} -->."
                    )
            evidence["records"] = records
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(entry, entry.read_text(encoding="utf-8") + "\n".join(presentations))
            builder = ENGINE.build_producer_index

            with (
                mock.patch.object(
                    ENGINE, "build_producer_index", wraps=builder
                ) as indexed,
                mock.patch.object(
                    ENGINE,
                    "evaluate_complete_provenance",
                    wraps=ENGINE.evaluate_complete_provenance,
                ) as evaluate,
            ):
                evaluation = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(summary)
                )

            failures = [
                finding
                for finding in evaluation.attempt.findings
                if finding.code == "producer.missing"
                and finding.subject
                == (entry_root / "data/catalog.csv").resolve().as_posix()
            ]
            self.assertEqual(
                len(failures),
                1,
                [
                    (finding.code, finding.subject)
                    for finding in evaluation.attempt.findings
                ],
            )
            self.assertEqual(indexed.call_count, 2)
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(evaluation.metrics["provenance_traversals"], 1)
            self.assertEqual(
                evaluation.metrics["provenance_traversals_reused"],
                consumer_count - 1,
            )

    def test_provenance_check_identity_preserves_distinct_finding_conditions(
        self,
    ) -> None:
        root = Path("/project")
        state = ENGINE._ScanState(root / "study.md", root / "study", root)
        findings = (
            PROVENANCE.ProvenanceFinding(
                "producer.missing",
                "/project/one.csv",
                {},
                "Producer",
                PROVENANCE.ProvenanceAnchor("material", "/project/one.csv"),
            ),
            PROVENANCE.ProvenanceFinding(
                "producer.missing",
                "/project/two.csv",
                {},
                "Producer",
                PROVENANCE.ProvenanceAnchor("material", "/project/two.csv"),
            ),
            PROVENANCE.ProvenanceFinding(
                "lineage.missing",
                "/project/one.csv",
                {},
                "Lineage",
                PROVENANCE.ProvenanceAnchor("material", "/project/one.csv"),
            ),
        )
        prepared = tuple(
            ENGINE._PreparedProvenanceFinding(
                finding,
                ENGINE.canonical_json(finding.identity_dict()),
                (),
            )
            for finding in findings
        )

        checks = ENGINE._register_provenance_checks(prepared, state)

        self.assertEqual(len(checks), 3)
        self.assertEqual(len({check.check_id for check in checks}), 3)
        self.assertEqual(
            {(check.diagnostic.code, check.subject) for check in checks},
            {
                ("producer.missing", "/project/one.csv"),
                ("producer.missing", "/project/two.csv"),
                ("lineage.missing", "/project/one.csv"),
            },
        )

    def test_same_named_provenance_findings_keep_natural_material_owners(
        self,
    ) -> None:
        root = Path("/project")
        first = "/project/entry-a/catalog.csv"
        second = "/project/entry-b/catalog.csv"
        state = ENGINE._ScanState(root / "study.md", root / "study", root)
        state.invocations = (
            SimpleNamespace(
                inputs=(SimpleNamespace(path=first),),
                outputs=(),
            ),
            SimpleNamespace(
                inputs=(SimpleNamespace(path=second),),
                outputs=(),
            ),
        )
        findings = tuple(
            PROVENANCE.ProvenanceFinding(
                "data.origin.invalid",
                "catalog",
                {"producer": "producer"},
                "Origin boundary",
                PROVENANCE.ProvenanceAnchor("material", material),
            )
            for material in (first, second)
        )

        prepared = ENGINE._ordered_provenance_findings(findings, state)
        checks = ENGINE._register_provenance_checks(prepared, state)
        attempt = DOMAIN.ValidationAttempt.build(
            target=DOMAIN.ValidationTarget(DOMAIN.TargetKind.LOG, "/project/study.md"),
            source_identity="source",
            rules_version="rules",
            started_at="start",
            finished_at="finish",
            checks=checks,
        )

        self.assertEqual(len(attempt.findings), 2)
        self.assertEqual(len(DOMAIN.build_batches(attempt.findings)), 2)
        self.assertEqual(
            {
                (
                    finding.repair_keys[0].value,
                    finding.context_nodes[0].node_id,
                )
                for finding in attempt.findings
            },
            {
                (f"material:{first}", f"material:{first}"),
                (f"material:{second}", f"material:{second}"),
            },
        )

    def test_named_output_rejects_an_origin_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            results = next(item for item in data["inputs"] if item["name"] == "results")
            results["origin"] = True
            write(data_path, json.dumps(data, indent=2) + "\n")

            evaluation = _evaluate(summary)

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            command = checks["entry:e001:command:1:1"]
            evidence = checks["evidence:e001:success-rate"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(command.outcome, DOMAIN.CheckOutcome.FINDING)
            self.assertEqual(command.diagnostic.code, "data.output.declaration_invalid")
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)

    def test_origin_evidence_is_valid_but_unrelated_output_is_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog_path = entry.parent / "data/catalog.csv"
            write(catalog_path, "success_rate\n0.676\n")
            data["inputs"] = [
                item for item in data["inputs"] if item["name"] == "catalog"
            ]
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence_data = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence_data["records"][0]["sources"][0]["source"] = "<catalog>"
            write(evidence_path, json.dumps(evidence_data, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    " --input-catalog '<catalog>'", ""
                ),
            )

            evaluation = _evaluate(summary)

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            self.assertIsNotNone(evaluation.snapshot)
            self.assertEqual(
                checks["evidence:e001:success-rate"].outcome,
                DOMAIN.CheckOutcome.PASS,
            )
            self.assertEqual(
                checks["provenance:e001:success-rate"].outcome,
                DOMAIN.CheckOutcome.PASS,
            )
            failure_codes = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("orphan.input.unused", failure_codes)

    def test_changed_generated_evidence_fails_provenance_and_blocks_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data/results.csv", "success_rate\n0.675\n")

            evaluation = _evaluate(summary)

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            evidence = checks["evidence:e001:success-rate"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.FINDING)
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertTrue(evaluation.context.currentness)
            currentness = _single_currentness_blocker(
                evaluation, "e001", "success-rate"
            )
            self.assertEqual(currentness["reason"], "signature_mismatch")
            failure_codes = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("data.input.undeclared", failure_codes)
            self.assertNotIn("orphan.input.unused", failure_codes)

    def test_decimal_locator_is_retained_exactly_in_record_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["locator"]["where"] = [
                {
                    "op": "eq",
                    "parse": "decimal",
                    "path": ["threshold"],
                    "value": 0.676,
                }
            ]
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(
                entry.parent / "data" / "results.csv",
                "threshold,success_rate\n0.676,0.676\n",
            )
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            write(data_path, json.dumps(data, indent=2) + "\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/results.csv"]["fingerprint"]["digest"] = (
                hashlib.sha256(
                    (entry.parent / "data" / "results.csv").read_bytes()
                ).hexdigest()
            )
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            evidence_check = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            record = evidence_check.dependency_evidence[0]["record"]
            self.assertIsInstance(record, str)
            self.assertIn('"value":0.676', record)

    def test_invalid_section_is_reported_while_valid_section_evaluates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8")
                + "\n## Incomplete appendix\n\n`Steps:`\n\nNo result was retained.\n",
            )

            evaluation = _evaluate(summary)

            invalid = [
                check
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "association.context_invalid"
            ]
            self.assertEqual(len(invalid), 1)
            self.assertEqual(invalid[0].area, DOMAIN.RuleArea.CONFORMANCE)
            self.assertEqual(
                invalid[0].diagnostic.observed["heading"], "Incomplete appendix"
            )
            evidence = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)

    def test_association_syntax_and_context_failures_are_conformance(self) -> None:
        for code in (
            "association.context_invalid",
            "association.presentation.syntax_invalid",
        ):
            with self.subTest(code=code):
                error = ENGINE.EngineV2Error(code, "entry", {}, "rule")
                self.assertEqual(
                    ENGINE._error_scope(error, DOMAIN.RuleArea.EVIDENCE),
                    DOMAIN.RuleArea.CONFORMANCE,
                )

    def test_symlinked_entry_material_root_is_mechanically_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            retained = root / "output" / "logs" / "study" / "e001" / "data"
            retained.parent.mkdir(parents=True)
            (entry.parent / "data").rename(retained)
            relative_target = os.path.relpath(retained, entry.parent)
            (entry.parent / "data").symlink_to(
                relative_target, target_is_directory=True
            )

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            scopes = _area_outcomes(evaluation.attempt)
            self.assertEqual(
                scopes[DOMAIN.RuleArea.ORPHAN],
                DOMAIN.CheckOutcome.PASS,
            )

    def test_cross_entry_source_uses_the_consuming_entry_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            split_entry = entry.with_name("e001a.md")
            entry.rename(split_entry)
            entry = split_entry
            evidence_path = entry.parent / "evidence.json"
            evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence_payload["records"][0]["document"] = (
                "entries/2026-08-29-e001-study/e001a.md"
            )
            write(evidence_path, json.dumps(evidence_payload, indent=2) + "\n")
            write(
                summary,
                summary.read_text(encoding="utf-8")
                .replace("e001.md", "e001a.md")
                .replace("ref entry = e001;", "ref entry = e001a;"),
            )
            retained = root / "output" / "logs" / "study" / "e001" / "data"
            retained.parent.mkdir(parents=True)
            (entry.parent / "data").rename(retained)
            relative_target = os.path.relpath(retained, entry.parent)
            (entry.parent / "data").symlink_to(
                relative_target, target_is_directory=True
            )

            second_root = root / "docs/study/entries/2026-08-29-e002-cross-entry-study"
            second_entry = second_root / "e002.md"
            write(
                second_entry,
                "## Trial\n\n`Steps:`\n\nNo command.\n\n`Results:`\n\n"
                "The prior success rate was `67.6%`"
                "<!-- eid:prior-success-rate -->.\n",
            )
            write(
                second_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "prior-results",
                                "kind": "file",
                                "location": os.path.relpath(
                                    retained / "results.csv", second_root
                                ),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                second_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "prior-success-rate",
                                "document": (
                                    "entries/2026-08-29-e002-cross-entry-study/e002.md"
                                ),
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<prior-results>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "- [Cross-entry study](study/entries/"
                "2026-08-29-e002-cross-entry-study/e002.md)\n",
            )

            evaluation = _evaluate(summary)

            checks = {check.check_id: check for check in evaluation.attempt.checks}
            self.assertIn(
                "evidence:e002:prior-success-rate",
                checks,
                [
                    (check.check_id, check.diagnostic)
                    for check in evaluation.attempt.checks
                    if "e002" in check.check_id
                ],
            )
            evidence = checks["evidence:e002:prior-success-rate"]
            self.assertEqual(evidence.outcome, DOMAIN.CheckOutcome.PASS)
            provenance = checks["provenance:e002:prior-success-rate"]
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)

            second_evidence_path = second_root / "evidence.json"
            second_payload = json.loads(
                second_evidence_path.read_text(encoding="utf-8")
            )
            second_payload["records"][0]["sources"][0]["source"] = (
                "<log>/entries/2026-08-29-e001-study/data/results.csv"
            )
            write(
                second_evidence_path,
                json.dumps(second_payload, indent=2) + "\n",
            )

            log_relative_evaluation = _evaluate(summary)
            declaration = next(
                check
                for check in log_relative_evaluation.attempt.checks
                if check.check_id == "entry:e002:evidence-declaration"
            )
            self.assertEqual(declaration.area, DOMAIN.RuleArea.CONFORMANCE)
            assert declaration.diagnostic is not None
            self.assertEqual(
                declaration.diagnostic.code, "evidence.declaration.invalid"
            )

    def test_evidence_directory_requires_one_exact_regular_file_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            evidence_directory = entry_root / "data/evidence"
            evidence_directory.mkdir()
            (entry_root / "data/results.csv").replace(
                evidence_directory / "results.csv"
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "data/results.csv", "data/evidence/results.csv"
                ),
            )
            data_path = entry_root / "data.json"
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            payload["inputs"] = [
                DATA.build_local_input(
                    "results-dir",
                    "directory",
                    "data/evidence",
                    entry_root=entry_root,
                ).as_dict()
            ]
            write(data_path, json.dumps(payload, indent=2) + "\n")
            evidence_path = entry_root / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "<results-dir>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            bare = _evaluate(summary)

            declaration = next(
                check
                for check in bare.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            self.assertEqual(declaration.outcome, DOMAIN.CheckOutcome.FINDING)
            assert declaration.diagnostic is not None
            self.assertEqual(
                declaration.diagnostic.code, "evidence.declaration.invalid"
            )

            evidence["records"][0]["sources"][0]["source"] = "<results-dir>/results.csv"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            member = _evaluate(summary)

            checks = {check.check_id: check for check in member.attempt.checks}
            self.assertEqual(
                checks["evidence:e001:success-rate"].outcome,
                DOMAIN.CheckOutcome.PASS,
            )
            failure_codes = {
                check.diagnostic.code
                for check in member.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("orphan.input.unused", failure_codes)

    def test_distinct_locators_share_one_stable_source_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            second = dict(payload["records"][0])
            second["id"] = "success-rate-checked"
            second["sources"] = [dict(second["sources"][0])]
            second["sources"][0]["locator"] = {
                "expect": {"items": 1},
                "select": [["success_rate"]],
            }
            payload["records"].append(second)
            write(evidence_path, json.dumps(payload, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was `67.6%`<!-- eid:success-rate source=results "
                    "select=/success_rate form=percentage render=fixed:1 -->.",
                    "The success rate was `67.6%`<!-- eid:success-rate source=results "
                    "select=/success_rate form=percentage render=fixed:1 -->.\n\n"
                    "The checked rate was `67.6%`<!-- eid:success-rate-checked -->.",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            self.assertEqual(evaluation.metrics["source_evaluations"], 2)
            self.assertEqual(evaluation.metrics["source_reads"], 1)

    def test_generated_artifact_uses_evidence_and_enters_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data" / "report.txt", "retained report\n")
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace(
                    "--output-data '<results>'",
                    "--output-data '<results>' --output-report '<report>'",
                )
                .replace(
                    "The success rate was",
                    "<!-- eid:retained-report -->\n"
                    "```diff\nretained report\n```\n\nThe success rate was",
                ),
            )
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            data["inputs"].append(
                {
                    "name": "report",
                    "kind": "file",
                    "location": "data/report.txt",
                    "identity": {"algorithm": "sha256"},
                    "origin": False,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "retained-report",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<report>", "locator": None}],
                    "transformation": None,
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            parameters = [
                "--input-catalog",
                "<catalog>",
                "--output-data",
                "data/results.csv",
                "--output-report",
                "data/report.txt",
            ]
            support["outputs"]["data/results.csv"]["parameters"] = parameters
            support["outputs"]["data/report.txt"] = {
                **support["outputs"]["data/results.csv"],
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(
                        (entry.parent / "data/report.txt").read_bytes()
                    ).hexdigest(),
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            checks = {check.check_id: check for check in evaluation.attempt.checks}
            self.assertEqual(
                checks["evidence:e001:retained-report"].outcome,
                DOMAIN.CheckOutcome.PASS,
            )
            self.assertEqual(
                checks["provenance:e001:retained-report"].outcome,
                DOMAIN.CheckOutcome.PASS,
            )
            evidence["records"][-1]["artifact_fingerprint"] = None
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            inline_baseline = _evaluate(summary)
            inline_check = next(
                check
                for check in inline_baseline.attempt.checks
                if check.check_id == "evidence:e001:retained-report"
            )
            self.assertEqual(inline_check.area, DOMAIN.RuleArea.CONFORMANCE)
            assert inline_check.diagnostic is not None
            self.assertEqual(
                inline_check.diagnostic.code, "evidence.declaration.invalid"
            )
            self.assertEqual(
                inline_check.diagnostic.observed,
                {
                    "actual": {
                        "artifact_fingerprint_present": True,
                        "presentation_form": "inline-text",
                    },
                    "expected": {"artifact_fingerprint_present": False},
                    "reason": "An inline artifact is validated from its displayed "
                    "content and must not record an artifact fingerprint.",
                },
            )

    def test_unmarked_artifact_requires_an_evidence_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data" / "report.txt", "retained report\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "[Retained report](data/report.txt)\n\nThe success rate was",
                ),
            )

            evaluation = _evaluate(summary)

            failures = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertIn("association.declaration_missing", failures)

    def test_artifact_evidence_may_use_an_explicit_origin_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            report = entry.parent / "data" / "historical-report.txt"
            write(report, "retained historical report\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            data["inputs"].append(
                {
                    "name": "historical_report",
                    "kind": "file",
                    "location": "data/historical-report.txt",
                    "identity": {"algorithm": "sha256"},
                    "origin": True,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "[Historical report](data/historical-report.txt)"
                    "<!-- eid:historical-report -->\n\nThe success rate was",
                ),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "historical-report",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<historical_report>", "locator": None}],
                    "transformation": None,
                    "artifact_fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(report.read_bytes()).hexdigest(),
                    },
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            failures = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertNotIn("producer.missing", failures)
            self.assertNotIn("orphan.input.unused", failures)

            write(report, "replacement bytes\n")
            replacement = _evaluate(summary)
            artifact = next(
                check
                for check in replacement.attempt.checks
                if check.check_id == "evidence:e001:historical-report"
            )
            self.assertEqual(artifact.outcome, DOMAIN.CheckOutcome.FINDING)
            assert artifact.diagnostic is not None
            self.assertEqual(
                artifact.diagnostic.code, "association.artifact.fingerprint_mismatch"
            )
            provenance = next(
                check
                for check in replacement.attempt.checks
                if check.check_id == "provenance:e001:historical-report"
            )
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.PASS)

            evidence["records"][-1].pop("artifact_fingerprint")
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            missing = _evaluate(summary)
            missing_check = next(
                check
                for check in missing.attempt.checks
                if check.check_id == "evidence:e001:historical-report"
            )
            self.assertEqual(missing_check.area, DOMAIN.RuleArea.CONFORMANCE)
            assert missing_check.diagnostic is not None
            self.assertEqual(
                missing_check.diagnostic.code, "evidence.declaration.invalid"
            )
            self.assertEqual(
                missing_check.diagnostic.observed,
                {
                    "actual": {
                        "artifact_fingerprint_present": False,
                        "presentation_form": "link",
                    },
                    "expected": {"artifact_fingerprint_present": True},
                    "reason": "A linked or image artifact must record an artifact "
                    "fingerprint.",
                },
            )

            evidence["records"][-1]["artifact_fingerprint"] = None
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            unrecorded = _evaluate(summary)
            unrecorded_check = next(
                check
                for check in unrecorded.attempt.checks
                if check.check_id == "evidence:e001:historical-report"
            )
            self.assertEqual(unrecorded_check.area, DOMAIN.RuleArea.EVIDENCE)
            assert unrecorded_check.diagnostic is not None
            self.assertEqual(
                unrecorded_check.diagnostic.code,
                "association.artifact.fingerprint_unrecorded",
            )

    def test_artifact_association_uses_path_not_equal_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            first = entry.parent / "data" / "first.png"
            second = entry.parent / "data" / "second.png"
            write(first, "same bytes\n")
            write(second, "same bytes\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            for name, path in (("first", first), ("second", second)):
                data["inputs"].append(
                    {
                        "name": name,
                        "kind": "file",
                        "location": f"data/{path.name}",
                        "identity": {"algorithm": "sha256"},
                        "origin": True,
                    }
                )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "![First](data/first.png)<!-- eid:first-image -->\n\n"
                    "The success rate was",
                ),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "first-image",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<second>", "locator": None}],
                    "transformation": None,
                    "artifact_fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(second.read_bytes()).hexdigest(),
                    },
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            check = next(
                item
                for item in evaluation.attempt.checks
                if item.check_id == "evidence:e001:first-image"
            )
            self.assertEqual(check.outcome, DOMAIN.CheckOutcome.FINDING)
            assert check.diagnostic is not None
            self.assertEqual(
                check.diagnostic.code, "association.artifact.source_mismatch"
            )

    def test_inline_artifact_source_obeys_the_presentation_byte_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "<!-- eid:results-diff -->\n"
                    "```diff\nsuccess_rate\n0.676\n```\n\nThe success rate was",
                ),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "results-diff",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<results>", "locator": None}],
                    "transformation": None,
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            with mock.patch.object(PRESENTATION, "MAX_PRESENTATION_BYTES", 4):
                evaluation = _evaluate(summary)

            check = next(
                item
                for item in evaluation.attempt.checks
                if item.check_id == "evidence:e001:results-diff"
            )
            self.assertEqual(check.area, DOMAIN.RuleArea.CONFORMANCE)
            assert check.diagnostic is not None
            self.assertEqual(check.diagnostic.code, "association.resource.too_large")

    def test_unmarked_diff_fence_requires_an_evidence_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "```diff\n-old\n+new\n```\n\nThe success rate was",
                ),
            )

            evaluation = _evaluate(summary)

            failures = [
                check.diagnostic
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "association.declaration_missing"
            ]
            self.assertTrue(failures)
            self.assertEqual(failures[0].observed["kind"], "artifact")

    def test_unavailable_and_resource_limit_completion_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            unavailable = LOCATOR.LocatorV2Error(
                "locator.reader.unavailable",
                "data/results.csv",
                {"error": "temporarily unavailable"},
                "V2: Expanded Mechanical Locator Language",
                outcome="unavailable",
            )
            with mock.patch.object(
                ENGINE, "observe_source_identity", side_effect=unavailable
            ):
                evaluation = _evaluate(summary)

            self.assertIs(evaluation.snapshot.outcome, DOMAIN.SnapshotOutcome.FAILED)
            self.assertIn(
                "locator.reader.unavailable",
                [
                    check.diagnostic.code
                    for check in evaluation.attempt.checks
                    if check.diagnostic is not None
                ],
            )

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            with mock.patch.object(LOCATOR, "MAX_TEXT_OR_JSON_BYTES", 4):
                evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)
            self.assertIn(
                "locator.source.too_large",
                [
                    check.diagnostic.code
                    for check in evaluation.attempt.checks
                    if check.diagnostic is not None
                ],
            )

    def test_log_level_record_marker_and_summary_bounds_are_composed(self) -> None:
        limits = (
            ("MAX_RECORDS_PER_LOG", DOMAIN.RuleArea.CONFORMANCE),
            ("MAX_PRESENTATIONS_PER_LOG", DOMAIN.RuleArea.CONFORMANCE),
            ("MAX_SUMMARY_REFERENCES_PER_LOG", DOMAIN.RuleArea.CONFORMANCE),
        )
        for constant, scope in limits:
            with self.subTest(constant=constant):
                with tempfile.TemporaryDirectory() as directory:
                    summary, _ = _log(Path(directory))
                    with mock.patch.object(ENGINE, constant, 0):
                        evaluation = _evaluate(summary)
                failures = [
                    check
                    for check in evaluation.attempt.checks
                    if check.diagnostic is not None
                    and check.diagnostic.code == "association.resource.too_large"
                ]
                self.assertTrue(failures)
                self.assertEqual(failures[0].area, scope)

    def test_evidence_passes_while_missing_producer_fails_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text().replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data '<results>'",
                    "true",
                ),
            )

            evaluation = _evaluate(summary)

            scopes = _area_outcomes(evaluation.attempt)
            self.assertEqual(scopes[DOMAIN.RuleArea.EVIDENCE], DOMAIN.CheckOutcome.PASS)
            self.assertEqual(
                scopes[DOMAIN.RuleArea.PROVENANCE], DOMAIN.CheckOutcome.FINDING
            )
            failures = [
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            ]
            self.assertIn("producer.missing", failures)

    def test_repeated_evidence_source_builds_unique_repair_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"].append(
                {
                    "source": "<results>",
                    "locator": {"select": [["success_rate"]]},
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(entry.parent / "data/results.csv", "success_rate\n0.700\n")

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))

            self.assertIsNotNone(evaluation.snapshot)
            for finding in evaluation.attempt.findings:
                self.assertEqual(
                    len(finding.context_nodes),
                    len(set(finding.context_nodes)),
                )

    def test_rejected_command_candidates_block_dependent_graph_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            scratch = entry.parent / "data/scratch.csv"
            write(scratch, "value\n1\n")
            write(
                entry,
                entry.read_text().replace(
                    "--results '<results>'",
                    "--results '<results>' --scratch data/scratch.csv",
                ),
            )

            evaluation = _evaluate(summary)

            command = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e001:command:1:1"
            )
            provenance = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            scratch_orphan = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.endswith("data/scratch.csv")
                and check.area is DOMAIN.RuleArea.ORPHAN
            )
            unused_input = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id.endswith(":catalog")
            )
            self.assertEqual(command.outcome, DOMAIN.CheckOutcome.FINDING)
            self.assertEqual(command.diagnostic.code, "material.candidate.unresolved")
            for dependent in (scratch_orphan, unused_input):
                self.assertEqual(dependent.outcome, DOMAIN.CheckOutcome.BLOCKED)
                self.assertIn(
                    {"dependency": command.check_id}, dependent.dependency_evidence
                )
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.BLOCKED)
            by_id = {check.check_id: check for check in evaluation.attempt.checks}
            provenance_rule = by_id[provenance.dependencies[0]]
            self.assertEqual(provenance_rule.outcome, DOMAIN.CheckOutcome.BLOCKED)
            self.assertIn(
                {"dependency": command.check_id}, provenance_rule.dependency_evidence
            )

    def test_repair_context_keeps_complete_rejected_command_neighborhood(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            (entry.parent / "data/catalog.csv").unlink()

            evaluation = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))

            assert evaluation.snapshot is not None
            context = evaluation.snapshot.repair_context
            nodes = tuple(context["nodes"])
            node_ids = {str(node["node_id"]) for node in nodes}
            rejected = [
                node
                for node in nodes
                if node["kind"] == "command"
                and node["attributes"].get("rejected") is True
            ]
            self.assertEqual(len(rejected), 1)
            command_id = str(rejected[0]["node_id"])
            script = (entry.parent / "scripts/model.py").resolve().as_posix()
            script_id = DOMAIN.GraphReference("script", script, "e001").node_id
            self.assertIn(script_id, node_ids)
            self.assertIn(
                {
                    "kind": "script_use",
                    "source": script_id,
                    "target": command_id,
                },
                context["relationships"],
            )
            rejected_ambiguities = [
                item
                for item in context["ambiguities"]
                if item["kind"] == "rejected_command"
                and command_id in item["candidates"]
            ]
            self.assertTrue(rejected_ambiguities)
            for ambiguity in context["ambiguities"]:
                self.assertIn(ambiguity["subject"], node_ids)
                self.assertLessEqual(set(ambiguity["candidates"]), node_ids)

    def test_invalid_command_makes_the_whole_fence_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```bash\n./pyrun",
                    "```bash\npython scripts/run.py\n./pyrun",
                ),
            )

            evaluation = _evaluate(summary)

            command_failure = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "entry:e001:command:1:1"
            )
            provenance = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            self.assertEqual(command_failure.outcome, DOMAIN.CheckOutcome.FINDING)
            self.assertEqual(
                command_failure.diagnostic.code, "invocation.command.unsupported"
            )
            self.assertEqual(provenance.outcome, DOMAIN.CheckOutcome.BLOCKED)

    def test_adjacent_comment_cannot_supply_a_material_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```\n\n`Results:`",
                    "```\n<!-- results are retained -->\n\n`Results:`",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)

    def test_pyrun_other_roles_are_shared_with_static_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--results '<results>'",
                    "./pyrun --other-inputs input-catalog --other-outputs results -- "
                    "scripts/model.py --input-catalog '<catalog>' "
                    "--results '<results>'",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertIsNotNone(evaluation.snapshot)

    def test_missing_summary_reference_is_precise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            write(
                summary,
                summary.read_text(encoding="utf-8").replace(
                    "<!-- ref entry = e001; eid = success-rate -->", ""
                ),
            )

            missing = _evaluate(summary)

            failures = [
                (check.area, check.diagnostic.code)
                for check in missing.attempt.checks
                if check.diagnostic is not None
            ]
            self.assertIn(
                (DOMAIN.RuleArea.EVIDENCE, "summary.reference.missing"), failures
            )

    def test_raw_script_change_keeps_execution_linked_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            first = _evaluate(summary)
            write(entry.parent / "scripts" / "model.py", "raise RuntimeError\n")
            second = _evaluate(summary)

            first_provenance = [
                check.outcome
                for check in first.attempt.checks
                if check.area is DOMAIN.RuleArea.PROVENANCE
            ]
            second_provenance = [
                check.outcome
                for check in second.attempt.checks
                if check.area is DOMAIN.RuleArea.PROVENANCE
            ]
            self.assertTrue(
                all(status is DOMAIN.CheckOutcome.PASS for status in first_provenance)
            )
            self.assertTrue(
                all(status is DOMAIN.CheckOutcome.PASS for status in second_provenance)
            )
            self.assertFalse(_reproduce_currentness(second))

    def test_input_changed_during_validation_is_unavailable_without_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            catalog = entry.parent / "data" / "catalog.csv"
            original_compose = ENGINE._compose_graph

            def change_after_graph(state: Any, request: Any) -> None:
                original_compose(state, request)
                write(catalog, "id\n2\n")

            with mock.patch.object(
                ENGINE, "_compose_graph", side_effect=change_after_graph
            ):
                evaluation = _evaluate(summary)

            failures = {
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            }
            self.assertIn("provenance.observation.unavailable", failures)

    def test_automatic_simulation_and_untyped_terminal_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            script = entry.parent / "scripts" / "model.py"
            simulation = entry.parent / "scripts" / "simulate_trials.py"
            script.rename(simulation)
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace("scripts/model.py", "scripts/simulate_trials.py")
                .replace("\n<!-- command type = model -->", ""),
            )
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            record = support["outputs"]["data/results.csv"]
            record["script"]["path"] = "scripts/simulate_trials.py"
            record["script"]["fingerprint"]["digest"] = hashlib.sha256(
                simulation.read_bytes()
            ).hexdigest()
            write(support_path, json.dumps(support, indent=2) + "\n")

            recognized = _evaluate(summary)

            self.assertIsNotNone(recognized.snapshot)

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace(" --input-catalog '<catalog>'", "")
                .replace("\n<!-- command type = model -->", ""),
            )

            untyped = _evaluate(summary)

            codes = [
                check.diagnostic.code
                for check in untyped.attempt.checks
                if check.diagnostic is not None
            ]
            self.assertNotIn("provenance.root.missing", codes)
            self.assertIn("orphan.input.unused", codes)

    def test_typed_command_does_not_hide_unrooted_visible_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data" / "unrooted.csv", "value\n1\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--input-catalog '<catalog>' ",
                    "--input-catalog '<catalog>' --input-data data/unrooted.csv ",
                ),
            )
            evaluation = _evaluate(summary)

            codes = [
                check.diagnostic.code
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
            ]
            self.assertIn("data.input.undeclared", codes)

    def test_cross_log_evidence_reads_only_the_external_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            external_root = root / "docs" / "other"
            external_source = external_root / "entries/e001/data/results.csv"
            external_state = external_root / "validation/manifest.json"
            write(external_source, "success_rate\n0.676\n")
            write(external_state, "not valid validation state\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            data["inputs"].append(
                {
                    "name": "external-results",
                    "kind": "file",
                    "location": os.path.relpath(external_source, entry.parent),
                    "identity": {"algorithm": "sha256"},
                    "origin": True,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"][0]["sources"][0]["source"] = "<external-results>"
            write(evidence_path, json.dumps(evidence) + "\n")
            original_read_bytes = Path.read_bytes
            original_read_text = Path.read_text

            def guarded_read_bytes(path: Path) -> bytes:
                if path.resolve() == external_state.resolve():
                    raise AssertionError("external validation state was read")
                return original_read_bytes(path)

            def guarded_read_text(
                path: Path, encoding: str | None = None, errors: str | None = None
            ) -> str:
                if path.resolve() == external_state.resolve():
                    raise AssertionError("external validation state was read")
                return original_read_text(path, encoding=encoding, errors=errors)

            with mock.patch.object(Path, "read_bytes", guarded_read_bytes):
                with mock.patch.object(Path, "read_text", guarded_read_text):
                    evaluation = _evaluate(summary)

            evidence_check = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "evidence:e001:success-rate"
            )
            provenance_check = next(
                check
                for check in evaluation.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            self.assertEqual(evidence_check.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(provenance_check.outcome, DOMAIN.CheckOutcome.PASS)
            self.assertEqual(evaluation.metrics["source_reads"], 1)

    def test_unsupported_command_has_complete_precise_failure_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```bash\n./pyrun",
                    "```bash\npython scripts/run.py\n./pyrun",
                ),
            )

            evaluation = _evaluate(summary)

            failure = next(
                check.diagnostic
                for check in evaluation.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "invocation.command.unsupported"
            )
            self.assertTrue(failure.subject)
            self.assertTrue(failure.observed)
            self.assertEqual(
                failure.rule, "Recorded-Command Provenance And Material Graph"
            )

    def test_entry_evaluation_does_not_observe_unrelated_entry_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            unrelated_root = root / "docs/study/entries/2026-08-30-e002-unrelated"
            unrelated = unrelated_root / "e002.md"
            write(unrelated_root / "scripts/unrelated.py", "# must stay unopened\n")
            write(unrelated_root / "data/input.csv", "unrelated\n")
            write(
                unrelated_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "input",
                                "kind": "file",
                                "location": "data/input.csv",
                                "identity": {"algorithm": "sha256"},
                                "origin": True,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                unrelated,
                "# Unrelated\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/unrelated.py --input-data '<input>' "
                "--output-data data/unrelated.csv\n```\n",
            )
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "- [Unrelated](study/entries/2026-08-30-e002-unrelated/e002.md)\n",
            )

            with mock.patch.object(
                ENGINE,
                "_observe_script_identity",
                wraps=ENGINE._observe_script_identity,
            ) as observe:
                result = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(
                        summary,
                        ENGINE.EntryEvaluationTarget("e001", entry.parent),
                    )
                )

            self.assertEqual(result.context.selected_documents, ("e001",))
            self.assertEqual(result.context.dependency_entries, ())
            self.assertEqual(
                tuple(invocation.entry for invocation in result.context.invocations),
                ("e001",),
            )
            self.assertFalse(
                any(
                    "unrelated.py" in str(call.args[0])
                    for call in observe.call_args_list
                )
            )

    def test_entry_checks_match_the_full_evaluation_for_its_selected_entry(
        self,
    ) -> None:
        """A scoped evaluation preserves all applicable selected-entry checks."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            full = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e001", entry.parent),
                )
            )

            full_checks = {
                check.check_id: check
                for check in full.attempt.checks
                if check.check_id
                not in {
                    "evidence:summary:5",
                    "orphan:log",
                    "provenance:summary:5",
                }
            }
            self.assertEqual(
                {check.check_id: check for check in scoped.attempt.checks},
                full_checks,
            )

    def test_fixed_clock_full_attempt_snapshot_and_report_are_invariant(
        self,
    ) -> None:
        """Fixed time leaves the full attempt, snapshot, and report reproducible."""

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = ENGINE.EvaluationRequest(summary)
            with (
                mock.patch(
                    "validation.engine._utc_timestamp",
                    return_value="2026-08-29T00:00:00.000000+00:00",
                ),
                mock.patch("validation.engine.time.perf_counter", return_value=1.0),
            ):
                first = _evaluate_current_fixture(request)
                second = _evaluate_current_fixture(request)
            self.assertEqual(first.attempt, second.attempt)
            self.assertEqual(first.metrics, second.metrics)
            self.assertEqual(first.attempt, second.attempt)
            self.assertEqual(first.snapshot, second.snapshot)
            self.assertIsNotNone(first.snapshot)
            self.assertIsNotNone(second.snapshot)
            self.assertEqual(
                SNAPSHOT_REPORT.compose_snapshot_report(first.snapshot),
                SNAPSHOT_REPORT.compose_snapshot_report(second.snapshot),
            )

    def test_entry_closure_reaches_a_producer_listed_after_its_presentation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, target = _log(root)
            producer_root = target.parent
            shared = root / "shared.csv"
            write(shared, "value\n1\n")
            write(
                producer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": str(shared),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                target,
                "# Producer\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/model.py --output-data '<shared>'\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            consumer = consumer_root / "e002.md"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": str(shared),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "shared-value",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["value"]]},
                                    }
                                ],
                                "transformation": None,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer,
                "# Consumer\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/result.csv\n```\n\n`Results:`\n\n"
                "Value `1`<!-- eid:shared-value -->.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n",
            )

            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )

            self.assertEqual(result.context.selected_documents, ("e002",))
            self.assertEqual(result.context.dependency_entries, ("e001",))
            self.assertEqual(
                tuple(invocation.entry for invocation in result.context.invocations),
                ("e002", "e001"),
            )

    def test_entry_exposes_stale_producer_after_evidence_passes(
        self,
    ) -> None:
        """A matching consumer presentation cannot certify its stale producer."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            shared = producer_root / "data/results.csv"
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n"
                "```\n\n`Results:`\n\nValue `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n",
            )
            # Keep the producer output and evidence byte intact, but invalidate its
            # recorded input fingerprint after its support record was written.
            write(producer_root / "data/catalog.csv", "id\n2\n")

            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            checks = {check.check_id: check for check in result.attempt.checks}
            self.assertEqual(
                checks["evidence:e002:shared"].outcome, DOMAIN.CheckOutcome.PASS
            )
            self.assertEqual(result.context.dependency_entries, ("e001",))
            blockers = _reproduce_currentness(result)
            self.assertTrue(blockers)
            self.assertTrue(
                any(item.reason == "signature_mismatch" for item in blockers)
            )

    def test_entry_data_conflict_prerequisites_match_full_when_relevant_only(
        self,
    ) -> None:
        """Scoped conflict checks include only conflicts touching the selected entry."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, selected = _log(root)
            selected_root = selected.parent
            shared = root / "shared.csv"
            unrelated = root / "unrelated.csv"
            write(shared, "shared\n")
            write(unrelated, "unrelated\n")
            selected_data = json.loads((selected_root / "data.json").read_text())
            catalog = next(
                item for item in selected_data["inputs"] if item["name"] == "catalog"
            )
            catalog["location"] = shared.as_posix()
            write(
                selected_root / "data.json", json.dumps(selected_data, indent=2) + "\n"
            )
            links = []
            for entry_id, path, comparison in (
                ("e002", shared, "comparison/a"),
                ("e003", unrelated, "comparison/b"),
                ("e004", unrelated, "comparison/c"),
            ):
                entry_root = (
                    root
                    / f"docs/study/entries/2026-08-3{int(entry_id[-1]) - 1}-{entry_id}"
                )
                write(entry_root / f"{entry_id}.md", f"# {entry_id}\n")
                write(
                    entry_root / "data.json",
                    json.dumps(
                        {
                            "schema": "research-log-data/v6",
                            "inputs": [
                                {
                                    "name": "input",
                                    "kind": "file",
                                    "location": path.as_posix(),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": entry_id == "e003",
                                }
                            ],
                        }
                    )
                    + "\n",
                )
                links.append(
                    f"- [{entry_id}](study/entries/{entry_root.name}/{entry_id}.md)"
                )
            write(summary, summary.read_text() + "\n" + "\n".join(links) + "\n")

            full = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e001", selected_root),
                )
            )
            full_conflicts = {
                check.check_id
                for check in full.attempt.checks
                if check.check_id.startswith("conformance:data-conflict:")
            }
            scoped_conflicts = {
                check.check_id
                for check in scoped.attempt.checks
                if check.check_id.startswith("conformance:data-conflict:")
            }
            self.assertTrue(scoped_conflicts <= full_conflicts)
            self.assertEqual(len(scoped_conflicts), 1)
            self.assertEqual(len(full_conflicts), 2)
            full_checks = {check.check_id: check for check in full.attempt.checks}
            scoped_checks = {check.check_id: check for check in scoped.attempt.checks}
            conflict_id = next(iter(scoped_conflicts))
            self.assertEqual(scoped_checks[conflict_id], full_checks[conflict_id])
            selected_prerequisites = {
                check.check_id
                for check in scoped.attempt.checks
                if check.check_id.startswith("evidence:e001:")
            }
            self.assertIn("evidence:e001:success-rate", selected_prerequisites)
            for identity in selected_prerequisites:
                self.assertEqual(
                    scoped_checks[identity].dependency_evidence,
                    full_checks[identity].dependency_evidence,
                )

    def test_reached_conflict_prerequisite_matches_full_producer_blocking(self):
        """A later reached owner receives an already-emitted conflict prerequisite."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            shared = root / "shared.csv"
            write(shared, "id\n1\n")
            producer_data = json.loads((producer_root / "data.json").read_text())
            catalog = next(
                item for item in producer_data["inputs"] if item["name"] == "catalog"
            )
            catalog["location"] = shared.as_posix()
            write(
                producer_root / "data.json",
                json.dumps(producer_data, indent=2) + "\n",
            )
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            results = producer_root / "data/results.csv"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "catalog",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                                "reproduction_comparison": {
                                    "contract": DATA.EVIDENCE_COMPARISON_CONTRACT,
                                    "profile": "evidence",
                                },
                            },
                            {
                                "name": "results",
                                "kind": "file",
                                "location": results.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<results>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<results>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n",
            )
            full = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            full_checks = {check.check_id: check for check in full.attempt.checks}
            scoped_checks = {check.check_id: check for check in scoped.attempt.checks}
            producer_command = "entry:e001:command:1:1"
            self.assertEqual(
                scoped_checks[producer_command], full_checks[producer_command]
            )
            self.assertEqual(
                scoped_checks[producer_command].outcome,
                DOMAIN.CheckOutcome.BLOCKED,
            )
            self.assertEqual(
                scoped_checks["provenance:e002:shared"],
                full_checks["provenance:e002:shared"],
            )

    def test_entry_competing_producers_match_full_relevant_ambiguity(self) -> None:
        """Exact and overlapping owners stay in scoped closure and match full output."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, first = _log(root)
            shared = first.parent / "data/results.csv"
            third_root = root / "docs/study/entries/2026-08-30-e003-third"
            write(third_root / "scripts/model.py", "# producer\n")
            write(shared.parent / "catalog.csv", "id\n1\n")
            write(
                third_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            DATA.build_declared_generated(
                                "bundle",
                                "directory",
                                shared.parent.as_posix(),
                                entry_root=third_root,
                                identity=("catalog.csv",),
                            ).as_dict(),
                        ],
                    }
                )
                + "\n",
            )
            write(
                third_root / "e003.md",
                "# Second\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/model.py --output-dir '<bundle>'\n"
                "```\n\n`Results:`\n\nDone.\n",
            )
            consumer_root = root / "docs/study/entries/2026-08-31-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-31-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            fourth_root = root / "docs/study/entries/2026-08-30-e004-fourth"
            write(fourth_root / "scripts/model.py", "# producer\n")
            write(
                fourth_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            DATA.build_local_input(
                                "results",
                                "file",
                                shared.as_posix(),
                                entry_root=fourth_root,
                                origin=False,
                            ).as_dict(),
                        ],
                    }
                )
                + "\n",
            )
            write(
                fourth_root / "e004.md",
                "# Third\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/model.py --output-data '<results>'\n"
                "```\n\n`Results:`\n\nDone.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [First](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Third](study/entries/2026-08-30-e003-third/e003.md)\n"
                "- [Fourth](study/entries/2026-08-30-e004-fourth/e004.md)\n"
                "- [Consumer](study/entries/2026-08-31-e002-consumer/e002.md)\n",
            )
            full = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            self.assertEqual(
                scoped.context.dependency_entries, ("e001", "e003", "e004")
            )
            full_check = _single_provenance_finding_check(
                full.attempt, "e002", "shared"
            )
            scoped_check = _single_provenance_finding_check(
                scoped.attempt, "e002", "shared"
            )
            self.assertEqual(scoped_check, full_check)
            self.assertEqual(scoped_check.diagnostic.code, "producer.ambiguous")
            producer_entries = {
                item.identity: item.entry for item in scoped.context.invocations
            }
            ambiguous_producers = scoped_check.diagnostic.observed["producers"]
            self.assertEqual(
                [producer_entries[item] for item in ambiguous_producers],
                ["e001", "e004"],
            )
            self.assertTrue(
                any(item.entry == "e003" for item in scoped.context.invocations)
            )
            self.assertFalse(
                any(
                    check.diagnostic is not None
                    and check.diagnostic.code == "directory.producer.conflict"
                    for check in scoped.attempt.checks
                )
            )

    def test_index_log_preserves_summary_order_across_split_documents(self) -> None:
        """Declaration order follows summary order, not physical-root order."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            first = entry.with_name("e001a.md")
            second = entry.with_name("e001b.md")
            write(first, "# First\n")
            write(second, "# Second\n")
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Second](study/entries/2026-08-29-e001-study/e001b.md)\n"
                "- [Main](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [First](study/entries/2026-08-29-e001-study/e001a.md)\n",
            )
            state = ENGINE._ScanState(summary, summary.with_suffix(""), root)
            index = ENGINE._index_log(summary.read_text(encoding="utf-8"), state)
            self.assertEqual(
                tuple(path.name for path in index.document_order),
                ("e001b.md", "e001.md", "e001a.md"),
            )

    def test_interleaved_split_producer_admits_only_earlier_document(self) -> None:
        """A later split producer cannot enter a consumer's scoped frontier."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            earlier = producer.with_name("e001a.md")
            later = producer.with_name("e001b.md")
            producer.replace(earlier)
            write(later, earlier.read_text(encoding="utf-8"))
            shared = producer_root / "data/results.csv"
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Earlier](study/entries/2026-08-29-e001-study/e001a.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n"
                "- [Later](study/entries/2026-08-29-e001-study/e001b.md)\n",
            )
            full = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            self.assertEqual(scoped.context.dependency_entries, ("e001",))
            self.assertEqual(
                tuple(
                    item.document.rsplit("/", 1)[-1]
                    for item in scoped.context.invocations
                ),
                ("e001a.md", "e002.md"),
            )
            self.assertEqual(
                tuple(
                    item.document.rsplit("/", 1)[-1]
                    for item in full.context.invocations
                ),
                ("e001a.md", "e002.md", "e001b.md"),
            )
            full_check = next(
                check
                for check in full.attempt.checks
                if check.check_id == "provenance:e002:shared"
            )
            scoped_check = next(
                check
                for check in scoped.attempt.checks
                if check.check_id == "provenance:e002:shared"
            )
            self.assertEqual(scoped_check, full_check)

    def test_reached_dependency_document_omits_unrelated_broken_command(
        self,
    ) -> None:
        """Closure observation excludes an unrelated command in a reached document."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            shared = producer_root / "data/results.csv"
            write(producer_root / "scripts/unrelated.py", "# must remain unopened\n")
            producer.write_text(
                producer.read_text() + "\n```bash\n"
                "./pyrun scripts/unrelated.py --input-data data/missing.csv "
                "--output-data data/unrelated.csv\n```\n",
                encoding="utf-8",
            )
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            unrelated_root = root / "docs/study/entries/2026-08-31-e003-unrelated"
            write(
                unrelated_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "name": "missing",
                                "kind": "file",
                                "location": "data/missing.csv",
                                "identity": {"algorithm": "sha256"},
                                "origin": True,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                unrelated_root / "e003.md",
                "# Unrelated\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/missing.py --input-data '<missing>' "
                "--output-data data/unrelated.csv\n```\n\n`Results:`\n\n"
                "Broken `1`<!-- eid:broken -->.\n",
            )
            write(unrelated_root / "evidence.json", "{ broken evidence\n")
            write(unrelated_root / "retention.json", "{ broken retention\n")
            write(unrelated_root / "pyrun.json", "{ broken state\n")
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n"
                "- [Unrelated](study/entries/2026-08-31-e003-unrelated/e003.md)\n",
            )
            full = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))
            with (
                mock.patch.object(
                    ENGINE,
                    "_observe_script_identity",
                    wraps=ENGINE._observe_script_identity,
                ) as observe,
                mock.patch.object(
                    ENGINE, "load_evidence_file", wraps=ENGINE.load_evidence_file
                ) as evidence_loader,
                mock.patch.object(
                    ENGINE, "load_retention_file", wraps=ENGINE.load_retention_file
                ) as retention_loader,
                mock.patch.object(
                    ENGINE, "load_pyrun_state", wraps=ENGINE.load_pyrun_state
                ) as state_loader,
                mock.patch.object(
                    COMMANDS, "_observe_script", wraps=COMMANDS._observe_script
                ) as command_script,
                mock.patch.object(
                    ENGINE, "observe_fingerprint", wraps=ENGINE.observe_fingerprint
                ) as fingerprint,
                mock.patch.object(
                    ENGINE, "_entry_presentations", wraps=ENGINE._entry_presentations
                ) as presentations,
            ):
                result = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(
                        summary,
                        ENGINE.EntryEvaluationTarget("e002", consumer_root),
                    )
                )
            self.assertFalse(
                any(
                    "unrelated.py" in str(call.args[0])
                    for call in observe.call_args_list
                )
            )
            self.assertFalse(
                any("unrelated" in check.check_id for check in result.attempt.checks)
            )
            self.assertEqual(result.context.selected_documents, ("e002",))
            self.assertEqual(result.context.dependency_entries, ("e001",))
            full_check = next(
                check
                for check in full.attempt.checks
                if check.check_id == "evidence:e002:shared"
            )
            scoped_check = next(
                check
                for check in result.attempt.checks
                if check.check_id == "evidence:e002:shared"
            )
            self.assertEqual(scoped_check, full_check)
            for name, loader in (
                ("evidence", evidence_loader),
                ("retention", retention_loader),
                ("state", state_loader),
                ("command", command_script),
                ("fingerprint", fingerprint),
                ("presentations", presentations),
            ):
                calls = loader.call_args_list
                if name == "presentations":
                    self.assertFalse(
                        any(call.args[0].root == unrelated_root for call in calls),
                        name,
                    )
                    continue
                self.assertFalse(
                    any(unrelated_root.as_posix() in str(call.args) for call in calls),
                    name,
                )
            self.assertEqual(
                tuple(item.entry for item in result.context.invocations),
                ("e001", "e002"),
            )
            self.assertEqual(
                tuple(entry for entry, _ in result.context.registries),
                ("e002", "e001"),
            )
            self.assertEqual(
                next(
                    check
                    for check in result.attempt.checks
                    if check.check_id == "provenance:e002:shared"
                ),
                next(
                    check
                    for check in full.attempt.checks
                    if check.check_id == "provenance:e002:shared"
                ),
            )

    def test_entry_producer_closure_covers_exact_reverse_and_rejected_declarations(
        self,
    ) -> None:
        """Entry closure admits each declaration shape without full evaluation."""

        for shape in ("exact", "reverse", "rejected"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                summary, producer = _log(root)
                producer_root = producer.parent
                if shape == "reverse":
                    member = producer_root / "data/bundle/member.csv"
                    write(member, "value\n1\n")
                    producer.write_text(
                        producer.read_text(encoding="utf-8").replace(
                            "--output-data '<results>'", "--output-dir data/bundle"
                        ),
                        encoding="utf-8",
                    )
                    material = member
                else:
                    material = producer_root / "data/results.csv"
                if shape == "rejected":
                    producer.write_text(
                        producer.read_text(encoding="utf-8").replace(
                            "./pyrun scripts/model.py", "python scripts/model.py"
                        ),
                        encoding="utf-8",
                    )
                consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
                write(consumer_root / "scripts/use.py", "# consumer\n")
                write(
                    consumer_root / "data.json",
                    json.dumps(
                        {
                            "schema": "research-log-data/v6",
                            "inputs": [
                                {
                                    "name": "shared",
                                    "kind": "file",
                                    "location": material.as_posix(),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": False,
                                }
                            ],
                        }
                    )
                    + "\n",
                )
                write(
                    consumer_root / "e002.md",
                    "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                    "./pyrun scripts/use.py --input-data '<shared>' "
                    "--output-data data/local.csv\n"
                    "```\n\n`Results:`\n\nDone.\n",
                )
                write(
                    summary,
                    "# Study\n\n## Entries\n\n"
                    "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                    "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n",
                )

                if shape == "rejected":
                    state = ENGINE._ScanState(summary, summary.with_suffix(""), root)
                    index = ENGINE._index_log(
                        summary.read_text(encoding="utf-8"), state
                    )
                    self.assertIn(
                        material.resolve().as_posix(), index.rejected_candidates
                    )

                result = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(
                        summary,
                        ENGINE.EntryEvaluationTarget("e002", consumer_root),
                    )
                )

                self.assertEqual(result.context.dependency_entries, ("e001",))
                if shape == "rejected":
                    self.assertIn(
                        "invocation.command.unsupported",
                        [
                            check.diagnostic.code
                            for check in result.attempt.checks
                            if check.diagnostic
                        ],
                    )
                else:
                    self.assertTrue(
                        any(
                            invocation.entry == "e001"
                            for invocation in result.context.invocations
                        )
                    )

    def test_evaluation_exposes_loaded_entry_material_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))

            result = _evaluate_current_fixture(ENGINE.EvaluationRequest(summary))

            self.assertEqual(len(result.context.materials), 1)
            material = result.context.materials[0]
            self.assertEqual(material.entry_id, "e001")
            self.assertEqual(material.entry_root, entry.parent.resolve())
            self.assertEqual(material.document, entry.resolve())
            self.assertIsNotNone(material.data)
            self.assertIsNotNone(material.evidence)

    def test_engine_has_no_semantic_review_or_reproduction_import(self) -> None:
        source = Path(ENGINE.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "adjudication",
            "decisions",
            "review_exchange",
            "review_reuse",
            "reproduction",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"validation.{forbidden}", source)
                self.assertNotIn(f"from .{forbidden}", source)

        self.assertNotIn("from .discovery", source)

    def test_runtime_has_no_parallel_mechanical_record_model(self) -> None:
        validation_root = Path(ENGINE.__file__).parent
        planner = validation_root.parent / "log_commands/reproduction_planner.py"
        for path in (
            Path(ENGINE.__file__),
            validation_root / "controller.py",
            planner,
        ):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("mechanical_results", source)
                self.assertNotIn("MechanicalGeneratedRecord", source)
        self.assertFalse((validation_root / "mechanical_results.py").exists())


if __name__ == "__main__":
    unittest.main()

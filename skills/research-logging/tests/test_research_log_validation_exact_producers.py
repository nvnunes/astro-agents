from __future__ import annotations

import copy
import importlib
import tempfile
import unittest
from pathlib import Path

import research_log_validation_test_support  # noqa: F401

ADJUDICATION = importlib.import_module("validation.adjudication")
PRODUCER_BINDINGS = importlib.import_module("validation.producer_bindings")


def exact_fixture(root: Path) -> tuple[dict, dict, str]:
    script = root / "entries" / "run" / "scripts" / "produce.py"
    source = root / "inputs" / "source.csv"
    target = root / "entries" / "run" / "data" / "result.csv"
    script.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    source.write_text("value\n1\n", encoding="utf-8")
    target.write_text("value\n1\n", encoding="utf-8")
    identity = "docs/log/entries/run/data/result.csv"
    command = {
        "line": 12,
        "section": "Results",
        "command": "python produce.py --input source.csv --output result.csv",
        "script": str(script),
        "unknown_options": [],
        "data_tokens": [],
        "path_arguments": [
            {
                "path": str(source),
                "role_hint": "input",
                "exists": True,
            },
            {
                "path": str(target),
                "role_hint": "output",
                "exists": True,
            },
        ],
    }
    scan = {
        "project_root": str(root),
        "entries": [
            {
                "id": "producer",
                "path": "docs/log/entries/run/e001.md",
                "commands": [command],
            }
        ],
        "resolved_paths": {
            "docs/log/entries/run/scripts/produce.py": str(script),
            "inputs/source.csv": str(source),
            identity: str(target),
        },
        "mechanical_checks": {
            "docs/log/entries/run/scripts/produce.py": {
                "status": "ok",
                "type": "python",
            },
            identity: {"status": "ok", "type": "table"},
        },
    }
    return scan, command, identity


class ExactMechanicalProducerTests(unittest.TestCase):
    def test_unique_exact_output_resolves_with_complete_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scan, command, target = exact_fixture(Path(directory))
            invocation = PRODUCER_BINDINGS.invocation_identities("producer", [command])[
                0
            ]

            resolution = PRODUCER_BINDINGS.exact_mechanical_producer(
                scan, target, [(invocation, command)]
            )

            self.assertIsNotNone(resolution)
            assert resolution is not None
            self.assertEqual(resolution.invocation_identity, invocation)
            self.assertEqual(
                resolution.dependencies,
                [
                    {
                        "path": "docs/log/entries/run/scripts/produce.py",
                        "role": "producer",
                    },
                    {"path": "inputs/source.csv", "role": "input"},
                ],
            )

    def test_ambiguous_or_incomplete_candidates_remain_semantic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scan, command, target = exact_fixture(Path(directory))
            invocation = PRODUCER_BINDINGS.invocation_identities("producer", [command])[
                0
            ]
            cases = {}

            unknown_direction = copy.deepcopy(command)
            unknown_direction["path_arguments"][-1]["role_hint"] = "unknown"
            cases["ambiguous output direction"] = (
                scan,
                [(invocation, unknown_direction)],
            )

            unresolved_input = copy.deepcopy(command)
            unresolved_input["path_arguments"][0]["exists"] = False
            cases["unresolved dependency"] = (
                scan,
                [(invocation, unresolved_input)],
            )

            uncertain_command = copy.deepcopy(command)
            uncertain_command["unknown_options"] = ["--mystery"]
            cases["unresolved command option"] = (
                scan,
                [(invocation, uncertain_command)],
            )

            copied_target = copy.deepcopy(command)
            copied_target["path_arguments"].insert(
                0,
                {
                    "path": command["path_arguments"][-1]["path"],
                    "role_hint": "input",
                    "exists": True,
                },
            )
            cases["copied or in-place target"] = (
                scan,
                [(invocation, copied_target)],
            )

            second_command = copy.deepcopy(command)
            second_command["command"] += " --variant second"
            second_invocation = PRODUCER_BINDINGS.invocation_identities(
                "producer", [command, second_command]
            )[1]
            cases["competing exact invocations"] = (
                scan,
                [(invocation, command), (second_invocation, second_command)],
            )

            duplicate_scan = copy.deepcopy(scan)
            duplicate_scan["entries"][0]["commands"] = [command, copy.deepcopy(command)]
            cases["duplicate recorded command"] = (
                duplicate_scan,
                [(invocation, command)],
            )

            for name, (candidate_scan, candidates) in cases.items():
                with self.subTest(name=name):
                    self.assertIsNone(
                        PRODUCER_BINDINGS.exact_mechanical_producer(
                            candidate_scan, target, candidates
                        )
                    )

    def test_unique_output_directory_resolves_one_exact_member_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scan, command, target = exact_fixture(Path(directory))
            output_directory = Path(directory) / "entries" / "run" / "data"
            command["path_arguments"][-1]["path"] = str(output_directory)
            collection = "docs/log/entries/run/data"
            scan["resolved_paths"][collection] = str(output_directory)
            scan["mechanical_checks"][collection] = {
                "status": "ok",
                "type": "directory",
            }
            invocation = PRODUCER_BINDINGS.invocation_identities(
                "producer", [command]
            )[0]

            self.assertIsNone(
                PRODUCER_BINDINGS.exact_mechanical_producer(
                    scan, target, [(invocation, command)]
                )
            )
            resolution = PRODUCER_BINDINGS.exact_mechanical_producer(
                scan,
                target,
                [(invocation, command)],
                allow_scoped_collection=True,
            )

            self.assertIsNotNone(resolution)
            assert resolution is not None
            self.assertEqual(
                resolution.dependencies[-1],
                {
                    "path": collection,
                    "role": "producer",
                    "members": ["result.csv"],
                },
            )


class ExactProducerAdjudicationTests(unittest.TestCase):
    def _prepare(self, root: Path, support_status: str):
        scan, command, target = exact_fixture(root)
        consumer = {
            "id": "consumer",
            "path": "docs/log/entries/report/e002.md",
            "commands": [],
            "evidence_record": {"identity": "", "rows": []},
        }
        scan["entries"].append(consumer)
        source = {
            "identity": target,
            "path": scan["resolved_paths"][target],
            "status": "resolved",
            "source": target,
            "locator": "field=value",
        }
        grouped = {
            "source": source,
            "associations": [
                {
                    "row": {
                        "kind": "statistic",
                        "evidence": "`1`",
                        "transformation": "",
                        "presented_item": {"context": "value 1"},
                    },
                    "source": source,
                }
            ],
            "sections": ["Results"],
        }
        context = ADJUDICATION.TargetPreparationContext(
            scan,
            {},
            "2026-08-26",
            "standard",
            lambda _row, _source: {
                "status": support_status,
                "detail": "matched" if support_status == "pass" else "uncertain",
            },
        )
        prepared = ADJUDICATION.prepare_evidence_target(
            consumer, target, grouped, context
        )
        return prepared, command

    def test_unique_exact_owner_producer_skips_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, command = self._prepare(Path(directory), "pass")
            invocation = PRODUCER_BINDINGS.invocation_identities("producer", [command])[
                0
            ]

            self.assertEqual(prepared.review_items, [])
            self.assertEqual(prepared.targets[0]["provenance"], "2026-08-26")
            self.assertEqual(prepared.targets[0]["producer_invocation"], invocation)

    def test_unresolved_evidence_still_requires_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, _command = self._prepare(Path(directory), "unresolved")

            self.assertIsNone(prepared.targets[0]["provenance"])
            self.assertEqual(len(prepared.review_items), 1)
            self.assertEqual(prepared.review_items[0]["kind"], "semantic_fallback")

    def test_presented_output_directory_member_needs_no_evidence_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, command, target = exact_fixture(root)
            output_directory = root / "entries" / "run" / "data"
            command["path_arguments"][-1]["path"] = str(output_directory)
            collection = "docs/log/entries/run/data"
            scan["resolved_paths"][collection] = str(output_directory)
            scan["mechanical_checks"][collection] = {
                "status": "ok",
                "type": "directory",
            }
            consumer = {
                "id": "consumer",
                "path": "docs/log/entries/report/e002.md",
                "commands": [],
                "evidence_record": {"identity": "", "rows": []},
            }
            scan["entries"].append(consumer)
            grouped = {
                "source": {
                    "identity": target,
                    "path": scan["resolved_paths"][target],
                    "status": "resolved",
                    "source": target,
                    "locator": "",
                },
                "associations": [],
                "sections": ["Results"],
            }
            context = ADJUDICATION.TargetPreparationContext(
                scan,
                {},
                "2026-08-26",
                "standard",
                lambda _row, _source: {"status": "unresolved"},
            )

            prepared = ADJUDICATION.prepare_evidence_target(
                consumer, target, grouped, context
            )

            self.assertEqual(prepared.review_items, [])
            self.assertEqual(prepared.targets[0]["provenance"], "2026-08-26")
            self.assertEqual(
                prepared.targets[0]["dependencies"][-1],
                {
                    "path": collection,
                    "role": "producer",
                    "members": ["result.csv"],
                },
            )


if __name__ == "__main__":
    unittest.main()

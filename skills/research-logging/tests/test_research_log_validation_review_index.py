from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

BENCHMARK = importlib.import_module("benchmark_validation_review")
REVIEW_INDEX = importlib.import_module("validation.review_index")


class ReviewIndexTests(unittest.TestCase):
    def test_indexed_candidates_match_the_simple_v43_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scan, adjudication, _ = BENCHMARK.generated_workload(
                Path(directory), orphan_count=12, command_count=4
            )
            session = REVIEW_INDEX.ReviewQuerySession(
                REVIEW_INDEX.ReviewContextIndex.build(scan)
            )
            counters = {
                "entry_scans": 0,
                "relationship_evaluations": 0,
                "producer_source_reads": 0,
            }
            for candidate in adjudication["review_queue"][0]["candidates"]:
                identity = candidate["identity"]
                reference = BENCHMARK._legacy_candidate_commands(
                    scan, "e001", identity, counters
                )
                indexed = session.candidate_commands(
                    "e001", identity, ["Benchmark Results"]
                )
                self.assertEqual(indexed, reference)

    def test_index_prepares_each_invocation_and_source_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scan, adjudication, _ = BENCHMARK.generated_workload(
                Path(directory), orphan_count=3, command_count=8
            )
            index = REVIEW_INDEX.ReviewContextIndex.build(scan)
            session = REVIEW_INDEX.ReviewQuerySession(index)
            identity = adjudication["review_queue"][0]["candidates"][0]["identity"]

            first = session.candidate_invocations("e001", identity, [])
            evaluations = session.metrics()["relationship_evaluations"]
            second = session.candidate_invocations("e001", identity, [])

            self.assertEqual(first, second)
            self.assertEqual(index.build_metrics["entry_scans"], 1)
            self.assertEqual(index.build_metrics["static_invocation_preparations"], 8)
            self.assertEqual(index.build_metrics["producer_source_reads"], 1)
            self.assertEqual(session.metrics()["relationship_evaluations"], evaluations)
            self.assertEqual(session.metrics()["candidate_cache_hits"], 1)

    def test_source_context_is_extracted_once_per_invocation_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "produce.py"
            target = root / "result.csv"
            script.write_text(
                "def produce(args):\n    return args.output\n", encoding="utf-8"
            )
            target.write_text("value\n1\n", encoding="utf-8")
            command = {
                "line": 8,
                "section": "Results",
                "command": "python produce.py --output result.csv",
                "script": script.as_posix(),
                "path_arguments": [
                    {
                        "path": target.as_posix(),
                        "role_hint": "output",
                        "option": "--output",
                    }
                ],
            }
            scan = {
                "project_root": root.as_posix(),
                "resolved_paths": {
                    "produce.py": script.as_posix(),
                    "result.csv": target.as_posix(),
                },
                "mechanical_checks": {
                    "produce.py": {"status": "ok", "type": "python"}
                },
                "entries": [
                    {
                        "id": "e001",
                        "path": "entries/e001/e001.md",
                        "commands": [command],
                    }
                ],
            }
            session = REVIEW_INDEX.ReviewQuerySession(
                REVIEW_INDEX.ReviewContextIndex.build(scan)
            )
            invocation = session.candidate_invocations(
                "e001", "result.csv", ["Results"]
            )[0]

            first = session.source_context(invocation, "result.csv")
            second = session.source_context(invocation, "result.csv")

            self.assertEqual(first, ("2: return args.output",))
            self.assertEqual(first, second)
            self.assertEqual(session.metrics()["source_context_extractions"], 1)
            self.assertEqual(session.metrics()["source_context_cache_hits"], 1)

    def test_review_invocation_key_ignores_unrelated_command_insertion(self) -> None:
        original = REVIEW_INDEX.review_invocation_key(
            "e001", "Results", "python run.py --output result.csv", 1
        )
        after_insertion = REVIEW_INDEX.review_invocation_key(
            "e001", "Results", "python run.py --output result.csv", 1
        )
        duplicate = REVIEW_INDEX.review_invocation_key(
            "e001", "Results", "python run.py --output result.csv", 2
        )

        self.assertEqual(original, after_insertion)
        self.assertNotEqual(original, duplicate)

    def test_normalized_section_duplicates_receive_distinct_ordinals(self) -> None:
        command = "python run.py --output result.csv"
        scan = {
            "project_root": "/tmp",
            "resolved_paths": {},
            "mechanical_checks": {},
            "entries": [
                {
                    "id": "e001",
                    "path": "entries/e001/e001.md",
                    "commands": [
                        {"section": "Results", "command": command},
                        {"section": " results ", "command": command},
                    ],
                }
            ],
        }

        index = REVIEW_INDEX.ReviewContextIndex.build(scan)

        self.assertEqual(len(index.invocations), 2)
        self.assertEqual(
            tuple(index.invocations),
            (
                REVIEW_INDEX.review_invocation_key(
                    "e001", "Results", command, 1
                ),
                REVIEW_INDEX.review_invocation_key(
                    "e001", "Results", command, 2
                ),
            ),
        )

    def test_orphan_growth_does_not_repeat_static_index_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            small, _, _ = BENCHMARK.generated_workload(
                root / "small", orphan_count=10, command_count=6
            )
            large, _, _ = BENCHMARK.generated_workload(
                root / "large", orphan_count=1_000, command_count=6
            )

            small_metrics = REVIEW_INDEX.ReviewContextIndex.build(
                small
            ).build_metrics
            large_metrics = REVIEW_INDEX.ReviewContextIndex.build(
                large
            ).build_metrics

            for key in (
                "entry_scans",
                "static_invocation_preparations",
                "unique_scripts",
                "producer_source_reads",
                "filesystem_probes",
            ):
                self.assertEqual(small_metrics[key], large_metrics[key])

    def test_eligible_candidates_are_not_lost_to_the_diagnostic_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scan, adjudication, _ = BENCHMARK.generated_workload(
                Path(directory), orphan_count=1, command_count=8
            )
            session = REVIEW_INDEX.ReviewQuerySession(
                REVIEW_INDEX.ReviewContextIndex.build(scan)
            )
            identity = adjudication["review_queue"][0]["candidates"][0]["identity"]

            candidates = session.candidate_invocations(
                "e001", identity, ["Benchmark Results"]
            )

            self.assertEqual(len(candidates), 8)


if __name__ == "__main__":
    unittest.main()

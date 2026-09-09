from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_comparison import ArtifactComparison, ExecutionComparison
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_planner import (
    ReproductionCommandInventory,
    ReproductionStateProjection,
)
from log_commands.reproduction_publication import (
    CompletedPublication,
    _artifact_results,
    _command_outcomes,
    publish_completed_reproduction,
)
from log_commands.reproduction_results import ReproductionResults
from validation.engine import RULES_VERSION
from validation.mechanical_results import MechanicalGeneratedRecord


def _case(
    execution_id: str,
    disposition: str,
    reason: str | None,
    *,
    artifact: str = "data/result.csv",
) -> dict[str, object]:
    return {
        "artifact": artifact,
        "disposition": disposition,
        "entry": "e001",
        "execution_id": execution_id,
        "reason": reason,
    }


class ReproductionPublicationTests(unittest.TestCase):
    def test_command_outcomes_are_exhaustive_and_not_artifact_counts(self) -> None:
        identities = {
            name: "pyrun-exec/v1:" + digit * 64
            for name, digit in zip(
                ("manual", "reused", "success", "failure", "dependency", "blocked"),
                "123456",
                strict=True,
            )
        }
        cases = (
            _case(identities["manual"], "skipped", "non_automatic"),
            _case(identities["reused"], "current", None),
            _case(identities["success"], "run", None, artifact="data/one.csv"),
            _case(identities["success"], "run", None, artifact="data/two.csv"),
            _case(identities["failure"], "run", None),
            _case(identities["dependency"], "run", None),
            _case(identities["blocked"], "failed", "validation_blocked"),
        )
        planned = tuple(
            {
                "auto_reproduce": True,
                "entry": "e001",
                "execution_id": identities[name],
            }
            for name in ("success", "failure", "dependency")
        )
        plan = ReproductionPlan(
            "docs/study.md",
            {"entry": None, "kind": "log"},
            False,
            {},
            {},
            cases,
            planned,
            (),
            (),
        )
        comparisons = (
            ExecutionComparison(
                "e001",
                identities["success"],
                (
                    ArtifactComparison(
                        "data/one.csv", "matched", None, None, None, None
                    ),
                    ArtifactComparison(
                        "data/two.csv", "matched", None, None, None, None
                    ),
                ),
                None,
                True,
            ),
            ExecutionComparison(
                "e001",
                identities["failure"],
                (
                    ArtifactComparison(
                        "data/failure.csv",
                        "failed",
                        "execution_failed",
                        None,
                        None,
                        None,
                    ),
                ),
                None,
                False,
            ),
        )
        request = CompletedPublication(
            plan,
            comparisons,
            "reproduce-20300101t000000z-accounting",
            "2030-01-01T00:00:00Z",
            "2030-01-01T00:01:00Z",
            Path("/tmp/reproduction-run"),
            (
                {
                    "depends_on": [f"e001:{identities['failure']}"],
                    "entry": "e001",
                    "execution_id": identities["dependency"],
                    "reason": "dependency_failed",
                },
            ),
        )

        self.assertEqual(
            _command_outcomes(plan, request, ReproductionCommandInventory(8, 2)),
            {
                "blocked": 1,
                "failed": 1,
                "not_automatic": 2,
                "reused": 3,
                "succeeded": 1,
                "total": 8,
            },
        )

    def test_publication_accepts_run_beneath_intentional_tmp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = temporary / "project"
            root.mkdir()
            (root / ".git").mkdir()
            external_tmp = temporary / "scratch"
            external_tmp.mkdir()
            (root / "tmp").symlink_to(external_tmp, target_is_directory=True)
            log_root = root / "docs" / "study"
            log_root.mkdir(parents=True)
            summary = root / "docs" / "study.md"
            summary.write_text(
                "# Study\n\n## Entries\n\n"
                "- [Example](study/entries/2030-01-01-e001-example/e001.md)\n",
                encoding="utf-8",
            )
            validation = MechanicalGeneratedRecord.build(
                summary.resolve().as_posix(), RULES_VERSION, "2030-01-01", ()
            )
            validation_path = log_root / ".cache" / "validation" / "results.json"
            validation_path.parent.mkdir(parents=True)
            validation_path.write_text(
                validation.canonical_json() + "\n", encoding="utf-8"
            )
            run_id = "reproduce-20300101t000000z-symlink"
            run_folder = (
                external_tmp
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-study-{run_id}"
            )
            run_folder.mkdir(parents=True)
            plan = ReproductionPlan(
                "docs/study.md",
                {"entry": "e001", "kind": "entry"},
                False,
                {},
                {},
                (
                    {
                        "artifact": "data/result.csv",
                        "disposition": "failed",
                        "entry": "e001",
                        "execution_id": None,
                        "reason": "graph_limit",
                    },
                ),
                (),
                (),
                (),
            )
            with (
                mock.patch(
                    "log_commands.reproduction_publication."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_publication.project_reproduction_state",
                    return_value=ReproductionStateProjection(
                        frozenset({("e001", "data/result.csv")}), {}, {}
                    ),
                ),
                mock.patch(
                    "log_commands.reproduction_publication."
                    "project_reproduction_command_inventory",
                    return_value=ReproductionCommandInventory(0, 0),
                ),
            ):
                published = publish_completed_reproduction(
                    LogContext(summary, log_root),
                    CompletedPublication(
                        plan,
                        (),
                        run_id,
                        "2030-01-01T00:00:00Z",
                        "2030-01-01T00:01:00Z",
                        run_folder,
                    ),
                )

            self.assertEqual(
                published.results.runs[0].folder.path,
                f"tmp/reproduction/2030-01-01/reproduce-study-{run_id}",
            )

    def test_distinct_entry_publications_merge_in_both_orders(self) -> None:
        for order in (("e001", "e002"), ("e002", "e001")):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".git").mkdir()
                log_root = root / "docs" / "study"
                log_root.mkdir(parents=True)
                summary = root / "docs" / "study.md"
                summary.write_text(
                    "# Study\n\n"
                    "## Entries\n\n"
                    "- [First](study/entries/2030-01-01-e001-first/e001.md)\n"
                    "- [Second](study/entries/2030-01-02-e002-second/e002.md)\n",
                    encoding="utf-8",
                )
                validation = MechanicalGeneratedRecord.build(
                    summary.resolve().as_posix(), RULES_VERSION, "2030-01-01", ()
                )
                validation_path = log_root / ".cache" / "validation" / "results.json"
                validation_path.parent.mkdir(parents=True)
                validation_path.write_text(
                    validation.canonical_json() + "\n", encoding="utf-8"
                )
                state = ReproductionStateProjection(
                    frozenset(
                        {
                            ("e001", "data/e001.csv"),
                            ("e002", "data/e002.csv"),
                        }
                    ),
                    {},
                    {},
                )
                with (
                    mock.patch(
                        "log_commands.reproduction_publication."
                        "verify_reproduction_runtime_snapshot"
                    ),
                    mock.patch(
                        "log_commands.reproduction_publication."
                        "project_reproduction_state",
                        return_value=state,
                    ),
                    mock.patch(
                        "log_commands.reproduction_publication."
                        "project_reproduction_command_inventory",
                        return_value=ReproductionCommandInventory(0, 0),
                    ),
                ):
                    for index, entry in enumerate(order, 1):
                        run_id = f"reproduce-2030010{index}t000000z-{entry}"
                        run_folder = (
                            root
                            / "tmp"
                            / "reproduction"
                            / f"2030-01-0{index}"
                            / f"reproduce-study-{run_id}"
                        )
                        run_folder.mkdir(parents=True)
                        plan = ReproductionPlan(
                            "docs/study.md",
                            {"entry": entry, "kind": "entry"},
                            False,
                            {},
                            {},
                            (
                                {
                                    "artifact": f"data/{entry}.csv",
                                    "disposition": "failed",
                                    "entry": entry,
                                    "execution_id": None,
                                    "reason": "graph_limit",
                                },
                            ),
                            (),
                            (),
                            (),
                        )
                        publish_completed_reproduction(
                            LogContext(summary, log_root),
                            CompletedPublication(
                                plan,
                                (),
                                run_id,
                                f"2030-01-0{index}T00:00:00Z",
                                f"2030-01-0{index}T00:01:00Z",
                                run_folder,
                            ),
                        )

                result = ReproductionResults.from_json(
                    (log_root / ".cache" / "reproduction" / "results.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    {(item.entry, item.artifact) for item in result.artifacts},
                    {("e001", "data/e001.csv"), ("e002", "data/e002.csv")},
                )
                self.assertEqual(
                    {item.run_id for item in result.runs},
                    {
                        f"reproduce-2030010{index}t000000z-{entry}"
                        for index, entry in enumerate(order, 1)
                    },
                )

    def test_completed_failure_is_published_without_touching_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            log_root = root / "docs" / "study"
            log_root.mkdir(parents=True)
            summary = root / "docs" / "study.md"
            summary.write_text(
                "# Study\n\n## Entries\n\n"
                "- [Example](study/entries/2030-01-01-e001-example/e001.md)\n",
                encoding="utf-8",
            )
            validation = MechanicalGeneratedRecord.build(
                summary.resolve().as_posix(), RULES_VERSION, "2030-01-01", ()
            )
            validation_path = log_root / ".cache" / "validation" / "results.json"
            validation_path.parent.mkdir(parents=True)
            validation_text = validation.canonical_json() + "\n"
            validation_path.write_text(validation_text, encoding="utf-8")
            run_id = "reproduce-20300101t000000z-publication"
            run_folder = (
                root
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-study-{run_id}"
            )
            run_folder.mkdir(parents=True)
            plan = ReproductionPlan(
                "docs/study.md",
                {"entry": "e001", "kind": "entry"},
                False,
                {},
                {},
                (
                    {
                        "artifact": "data/result.csv",
                        "disposition": "failed",
                        "entry": "e001",
                        "execution_id": None,
                        "reason": "graph_limit",
                    },
                ),
                (),
                (),
                (),
            )
            with (
                mock.patch(
                    "log_commands.reproduction_publication."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_publication.project_reproduction_state",
                    return_value=ReproductionStateProjection(
                        frozenset({("e001", "data/result.csv")}), {}, {}
                    ),
                ),
                mock.patch(
                    "log_commands.reproduction_publication."
                    "project_reproduction_command_inventory",
                    return_value=ReproductionCommandInventory(0, 0),
                ),
            ):
                published = publish_completed_reproduction(
                    LogContext(summary, log_root),
                    CompletedPublication(
                        plan,
                        (),
                        run_id,
                        "2030-01-01T00:00:00Z",
                        "2030-01-01T00:01:00Z",
                        run_folder,
                    ),
                )

            result_path = log_root / ".cache" / "reproduction" / "results.json"
            decoded = ReproductionResults.from_json(
                result_path.read_text(encoding="utf-8")
            )
            self.assertEqual(decoded, published.results)
            self.assertEqual(decoded.artifacts[0].outcome, "failed")
            self.assertIn("| `data/result.csv` | **failed** |", published.report)
            self.assertEqual(
                validation_path.read_text(encoding="utf-8"), validation_text
            )
            self.assertFalse((log_root / "validation.md").exists())

    def test_generated_failure_reasons_are_publishable_artifact_failures(self) -> None:
        execution_id = "pyrun-exec/v1:" + "1" * 64
        execution_reasons = (
            "capture_failed",
            "execution_exception",
            "output_materialization_failed",
            "reproduction.run.invalid",
        )
        for reason in (*execution_reasons, "validation_blocked"):
            planned_failure = reason == "validation_blocked"
            plan = ReproductionPlan(
                "docs/study.md",
                {"entry": None, "kind": "log"},
                False,
                {},
                {},
                (
                    {
                        "artifact": "data/result.csv",
                        "disposition": "failed" if planned_failure else "run",
                        "entry": "e001",
                        "execution_id": execution_id,
                        "reason": reason if planned_failure else None,
                    },
                ),
                (),
                (),
                (),
            )
            artifacts = _artifact_results(
                CompletedPublication(
                    plan,
                    (
                        ()
                        if planned_failure
                        else (
                            ExecutionComparison(
                                "e001",
                                execution_id,
                                (
                                    ArtifactComparison(
                                        "data/result.csv",
                                        "failed",
                                        reason,
                                        None,
                                        None,
                                        None,
                                    ),
                                ),
                                None,
                                False,
                            ),
                        )
                    ),
                    "reproduce-20300101t000000z-execution-failure",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:01:00Z",
                    Path("/tmp/reproduction-run"),
                )
            )

            with self.subTest(reason=reason):
                self.assertEqual(
                    (
                        artifacts[0].execution_id,
                        artifacts[0].outcome,
                        artifacts[0].reason,
                    ),
                    (execution_id, "failed", reason),
                )

    def test_unknown_artifact_reason_is_a_retriable_publication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            log_root = root / "docs" / "study"
            log_root.mkdir(parents=True)
            summary = root / "docs" / "study.md"
            summary.write_text("# Study\n", encoding="utf-8")
            plan = ReproductionPlan(
                "docs/study.md",
                {"entry": None, "kind": "log"},
                False,
                {},
                {},
                (
                    {
                        "artifact": "data/result.csv",
                        "disposition": "failed",
                        "entry": "e001",
                        "execution_id": None,
                        "reason": "unknown_reason",
                    },
                ),
                (),
                (),
                (),
            )

            with self.assertRaises(ActionError) as caught:
                publish_completed_reproduction(
                    LogContext(summary, log_root),
                    CompletedPublication(
                        plan,
                        (),
                        "reproduce-20300101t000000z-unknown-reason",
                        "2030-01-01T00:00:00Z",
                        "2030-01-01T00:01:00Z",
                        root / "tmp" / "reproduction-run",
                    ),
                )

            self.assertEqual(caught.exception.code, "reproduction.publication.failed")


if __name__ == "__main__":
    unittest.main()

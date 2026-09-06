from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.context import LogContext
from log_commands.reproduction_comparison import ConfirmationUpdates
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_planner import ReproductionStateProjection
from log_commands.reproduction_publication import (
    CompletedPublication,
    publish_completed_reproduction,
)
from log_commands.reproduction_results import ReproductionResults
from validation.engine import RULES_VERSION
from validation.mechanical_results import MechanicalGeneratedRecord


class ReproductionPublicationTests(unittest.TestCase):
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
            validation_path = log_root / "validation" / "results.json"
            validation_path.parent.mkdir()
            validation_text = validation.canonical_json() + "\n"
            validation_path.write_text(validation_text, encoding="utf-8")
            run_id = "reproduce-20300101t000000z-publication"
            run_folder = root / "tmp" / f"reproduce-study-{run_id}"
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
            no_confirmations = ConfirmationUpdates({}, {}, {})

            with (
                mock.patch(
                    "log_commands.reproduction_publication."
                    "verify_reproduction_publication_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_publication."
                    "prepare_confirmation_updates_locked",
                    return_value=no_confirmations,
                ),
                mock.patch(
                    "log_commands.reproduction_publication.project_reproduction_state",
                    return_value=ReproductionStateProjection(
                        frozenset({("e001", "data/result.csv")}), {}, {}
                    ),
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

            result_path = log_root / "reproduction" / "results.json"
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


if __name__ == "__main__":
    unittest.main()

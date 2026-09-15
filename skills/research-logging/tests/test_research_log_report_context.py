from __future__ import annotations

import ast
import importlib
import re
import tempfile
import unittest
from pathlib import Path

import research_log_validation_test_support  # noqa: F401

REPORT_CONTEXT = importlib.import_module("validation.report_context")


class PresentationTests(unittest.TestCase):
    def test_catalog_covers_the_approved_emitted_code_inventory(self) -> None:
        self.assertEqual(len(REPORT_CONTEXT.CATALOG), 141)
        self.assertTrue(
            {
                "orphan.generated.residue",
                "pyrun.command.missing",
                "pyrun.command.recipe_changed",
                "pyrun.command.stale",
            }
            <= set(REPORT_CONTEXT.CATALOG)
        )
        prefixes = {code.split(".", 1)[0] for code in REPORT_CONTEXT.CATALOG}
        candidates = set()
        scripts = Path(__file__).parents[1] / "scripts" / "validation"
        pattern = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}\Z")
        for path in scripts.glob("*.py"):
            if path.name == "presentation.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and pattern.fullmatch(node.value)
                    and node.value.split(".", 1)[0] in prefixes
                ):
                    candidates.add(node.value)
        data_contract = scripts.parent / "research_log_data.py"
        tree = ast.parse(data_contract.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_fail"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                candidates.add(node.args[0].value)
        non_codes = {
            "locator.expect.identities",
            "locator.expect.shape",
            "provenance.output.reproduction_required",
            "provenance.output.signature_mismatch",
        }
        self.assertEqual(candidates - non_codes - set(REPORT_CONTEXT.CATALOG), set())

    def test_catalog_preserves_repair_wording_for_representative_findings(self) -> None:
        cases = {
            "data.fingerprint.unobserved": (
                "Unobserved Generated Fingerprint",
                "The generated material lacks the required retained execution "
                "observation.",
            ),
            "association.artifact.fingerprint_mismatch": (
                "Artifact Fingerprint Mismatch",
                "The linked artifact bytes differ from the baseline accepted in its "
                "evidence record.",
            ),
            "provenance.output.execution_unassociated": (
                "Execution Association Missing",
                "The recorded execution no longer matches the current producing "
                "command.",
            ),
        }

        for code, expected in cases.items():
            with self.subTest(code=code):
                value = REPORT_CONTEXT.CATALOG[code]
                self.assertEqual((value.name, value.sentence), expected)

    def test_report_context_reads_direct_entry_title_without_group_projection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "study.md"
            summary.write_text(
                "# Study Title\n\n- [First result](study/entries/e001.md)\n",
                encoding="utf-8",
            )

            context = REPORT_CONTEXT.load_report_context(summary)

            self.assertEqual(context.title, "Study Title")
            self.assertEqual(context.entries["e001"].title, "First result")
            self.assertEqual(context.entries["e001"].document, "entries/e001.md")
            self.assertFalse(hasattr(REPORT_CONTEXT, "FindingGroup"))


if __name__ == "__main__":
    unittest.main()

"""Literal public evidence-sync fixtures, independent of production renderers."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from log_commands import dispatcher, evidence_sync, storage
from research_log_cli_test_support import run_log, run_log_process
from test_log_command_sync import fixture, sync


def evidence(logical: Path, action: str, *extra: str):
    return run_log(
        logical.parent,
        "evidence",
        action,
        "--path",
        str(logical),
        "--entry",
        "e001",
        *extra,
    )


def set_results(document: Path, results: str) -> str:
    text = document.read_text().replace("Pending.", results)
    document.write_text(text)
    return text


def retained_files(logical: Path) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in logical.rglob("*")
        if path.is_file() and ".cache" not in path.parts
    }


class EvidenceSyncTests(unittest.TestCase):
    def test_target_change_rejects_unselected_markdown_only_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/first.json").write_text('{"value":7}')
            (entry / "data/second.json").write_text('{"value":8}')
            set_results(document, "``<!-- eid:selected source=values select=/value -->")
            created = evidence(
                logical,
                "sync",
                "--id",
                "selected",
                "--add-origin",
                "values=data/first.json",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            document.write_text(
                document.read_text()
                + "\n``<!-- eid:unsynced source=values select=/value -->\n"
            )
            before = retained_files(logical)
            rejected = evidence(
                logical,
                "sync",
                "--id",
                "selected",
                "--change-target",
                "values=data/second.json",
            )
            self.assertEqual(rejected.returncode, 2, rejected.stderr)
            self.assertIn("evidence.sync.target.shared", rejected.stderr)
            self.assertIn("unsynced", rejected.stderr)
            self.assertEqual(retained_files(logical), before)

    def test_delete_ignores_unrelated_malformed_eid_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            set_results(document, "``<!-- eid:selected source=values select=/value -->")
            created = evidence(
                logical,
                "sync",
                "--id",
                "selected",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            document.write_text(
                document.read_text().replace(
                    "`7`<!-- eid:selected source=values select=/value -->",
                    "Retired.",
                )
                + "\n``<!-- eid:broken source='unterminated -->\n"
            )
            deleted = evidence(logical, "sync", "--delete", "selected")
            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            self.assertFalse((entry / "evidence.json").exists())

    def test_target_change_requires_every_evidence_consumer_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/first.json").write_text('{"value":7}')
            (entry / "data/second.json").write_text('{"value":8}')
            set_results(
                document,
                "``<!-- eid:first source=values select=/value -->\n"
                "``<!-- eid:second source=values select=/value -->",
            )
            created = evidence(
                logical,
                "sync",
                "--id",
                "first",
                "--id",
                "second",
                "--add-origin",
                "values=data/first.json",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            before = retained_files(logical)
            rejected = evidence(
                logical,
                "sync",
                "--id",
                "first",
                "--change-target",
                "values=data/second.json",
            )
            self.assertEqual(rejected.returncode, 2, rejected.stderr)
            self.assertIn("evidence.sync.target.shared", rejected.stderr)
            self.assertEqual(retained_files(logical), before)
            accepted = evidence(
                logical,
                "sync",
                "--id",
                "first",
                "--id",
                "second",
                "--change-target",
                "values=data/second.json",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(document.read_text().count("`8`<!-- eid:"), 2)
            resources = json.loads((entry / "data.json").read_text())["inputs"]
            self.assertEqual(resources[0]["location"], "data/second.json")

    def test_rename_reevaluates_new_definition_and_updates_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/first.json").write_text('{"value":7}')
            (entry / "data/second.json").write_text('{"value":8.25}')
            set_results(
                document,
                "``<!-- eid:old source=first select=/value -->",
            )
            first = evidence(
                logical, "sync", "--id", "old", "--add-origin", "first=data/first.json"
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text()
                + "\n## Summary\n\n`7`<!-- ref entry = e001; eid = old -->\n"
            )
            document.write_text(
                document.read_text().replace(
                    "`7`<!-- eid:old source=first select=/value -->",
                    "``<!-- eid:new source=second select=/value render=fixed:1 -->",
                )
            )
            summary.write_text(summary.read_text().replace("eid = old", "eid = new"))
            arguments = (
                "--rename",
                "old=new",
                "--add-origin",
                "second=data/second.json",
            )
            before = retained_files(logical)
            preview = evidence(logical, "sync", *arguments, "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(retained_files(logical), before)
            applied = evidence(logical, "sync", *arguments)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("`8.2`<!-- eid:new", document.read_text())
            self.assertIn(
                "`8.2`<!-- ref entry = e001; eid = new -->", summary.read_text()
            )
            records = json.loads((entry / "evidence.json").read_text())["records"]
            self.assertEqual([record["id"] for record in records], ["new"])
            self.assertEqual(records[0]["sources"][0]["source"], "<second>")
            after = retained_files(logical)
            repeated = evidence(logical, "sync", *arguments)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(retained_files(logical), after)

    def test_invalid_member_prevents_batch_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            set_results(
                document,
                "``<!-- eid:good source=values select=/value -->\n"
                "``<!-- eid:bad source=values select=/missing -->",
            )
            before = retained_files(logical)
            result = evidence(
                logical,
                "sync",
                "--id",
                "good",
                "--id",
                "bad",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(retained_files(logical), before)
            self.assertFalse((entry / "evidence.json").exists())

    def test_delete_only_is_idempotent_and_reports_unused_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            set_results(document, "``<!-- eid:old source=values select=/value -->")
            created = evidence(
                logical, "sync", "--id", "old", "--add-origin", "values=data/value.json"
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            document.write_text(
                document.read_text().replace(
                    "`7`<!-- eid:old source=values select=/value -->", "Retired."
                )
            )
            before = retained_files(logical)
            preview = evidence(logical, "sync", "--delete", "old", "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(retained_files(logical), before)
            self.assertIn(
                {"unused_data": "values"}, json.loads(preview.stdout)["records"]
            )
            deleted = evidence(logical, "sync", "--delete", "old")
            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            self.assertFalse((entry / "evidence.json").exists())
            self.assertTrue((entry / "data.json").exists())
            after = retained_files(logical)
            repeated = evidence(logical, "sync", "--delete", "old")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(retained_files(logical), after)

    def test_conflicting_batch_selection_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            set_results(document, "``<!-- eid:new source=values select=/value -->")
            before = retained_files(logical)
            for arguments in (
                ("--id", "new", "--delete", "new"),
                ("--rename", "old=new", "--rename", "another=new"),
                ("--rename", "old=new", "--rename", "new=third"),
                ("--delete", "old", "--add-origin", "values=data/value.json"),
            ):
                with self.subTest(arguments=arguments):
                    rejected = evidence(logical, "sync", *arguments)
                    self.assertEqual(rejected.returncode, 2, rejected.stderr)
                    self.assertEqual(retained_files(logical), before)

    def test_replaces_six_records_with_one_in_one_public_change_set(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            old = "\n".join(
                f"``<!-- eid:old-{index} source=values select=/value -->"
                for index in range(6)
            )
            set_results(document, old)
            for index in range(6):
                arguments = (
                    ("--add-origin", "values=data/value.json") if index == 0 else ()
                )
                result = evidence(logical, "sync", "--id", f"old-{index}", *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
            document.write_text(
                document.read_text().replace(
                    "\n".join(
                        f"`7`<!-- eid:old-{index} source=values select=/value -->"
                        for index in range(6)
                    ),
                    "``<!-- eid:new source=values select=/value -->",
                )
            )
            arguments = (
                "--id",
                "new",
                *(item for index in range(6) for item in ("--delete", f"old-{index}")),
            )
            before = retained_files(logical)
            preview = evidence(logical, "sync", *arguments, "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(retained_files(logical), before)
            self.assertEqual(
                [record["id"] for record in json.loads(preview.stdout)["records"]],
                ["new", *(f"old-{index}" for index in range(6))],
            )
            applied = evidence(logical, "sync", *arguments)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("`7`<!-- eid:new", document.read_text())
            self.assertEqual(
                [
                    item["id"]
                    for item in json.loads((entry / "evidence.json").read_text())[
                        "records"
                    ]
                ],
                ["new"],
            )
            self.assertEqual((entry / "data/value.json").read_text(), '{"value":7}')
            after = retained_files(logical)
            repeated = evidence(logical, "sync", *arguments)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(retained_files(logical), after)

    def test_numeric_comment_filters_accept_matching_native_types_only(self):
        for kind, value in (("integer", 6), ("decimal", 7.5)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                document.write_text(
                    document.read_text().replace(
                        "./pyrun scripts/build.py", "# Read the evidence-owned origin."
                    )
                )
                (entry / "scripts/build.py").unlink()
                source = entry / "data/native.json"
                source.write_text(json.dumps({"level": value, "value": 3}))
                set_results(
                    document,
                    "`3`<!-- eid:native source=values select=/value "
                    f"where=/level:eq:{kind}:{value} -->",
                )
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "native",
                    "--add-origin",
                    "values=data/native.json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("`3`<!-- eid:native", document.read_text())
                before = retained_files(logical)
                result = evidence(logical, "sync", "--id", "native")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(retained_files(logical), before)
                result = run_log(
                    logical.parent,
                    "validate",
                    "run",
                    "--path",
                    str(logical),
                    "--format",
                    "json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["outcome"], "clear")
                wrong_numeric_kind = 6.5 if kind == "integer" else 7
                for wrong in (True, wrong_numeric_kind):
                    source.write_text(json.dumps({"level": wrong, "value": 3}))
                    before = retained_files(logical)
                    result = evidence(logical, "sync", "--id", "native")
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("locator.type.mismatch", result.stderr)
                    self.assertEqual(retained_files(logical), before)

    def test_observed_identity_literals_round_trip_through_full_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            document.write_text(
                document.read_text().replace(
                    "./pyrun scripts/build.py", "# Read the evidence-owned origin."
                )
            )
            (entry / "scripts/build.py").unlink()
            identities = ["status/state", 10, 1.5, True, None]
            (entry / "data/identities.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {"identity": value, "value": i}
                            for i, value in enumerate(identities)
                        ]
                    }
                )
            )
            set_results(
                document,
                "<!-- eid:identities source=values path=/rows/* identity=/identity; "
                "column=/value render=integer -->\n| Value |\n| ---: |\n",
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "identities",
                "--add-origin",
                "values=data/identities.json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(
                record["sources"][0]["locator"]["expect"]["identities"],
                [[value] for value in identities],
            )
            # Do not derive this expectation through the production projector.
            self.assertIn("| 0 |\n| 1 |\n| 2 |\n| 3 |\n| 4 |\n", document.read_text())
            before = retained_files(logical)
            result = evidence(logical, "sync", "--id", "identities")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(retained_files(logical), before)
            result = run_log(
                logical.parent,
                "validate",
                "run",
                "--path",
                str(logical),
                "--format",
                "json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["outcome"], "clear", result.stdout
            )

    def test_range_uses_observed_rows_and_preserves_existing_spelling(self):
        from validation.controller import ValidationRequest, validate

        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.csv").write_text(
                "name,value\nlow,1.25\nskip,99\nhigh,2.75\n"
            )
            original = set_results(
                document,
                "`1.2–2.8 nm`<!-- eid:range form=range unit=nm source=values "
                "select=/value identity=/name where=/name:in:string:low,high "
                "parse=decimal render=fixed:1 -->",
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "range",
                "--add-origin",
                "values=data/value.csv",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(document.read_text(), original)
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(record["sources"][0]["locator"]["expect"]["items"], 2)
            self.assertEqual(
                [value["source"] for value in record["transformation"]["values"]],
                [{"input": 0, "item": 0}, {"input": 0, "item": 1}],
            )
            checked = validate(
                ValidationRequest(logical.with_suffix(".md"), publish=False)
            )
            self.assertNotIn(
                "evidence.definition.unsynchronized",
                [finding.code for finding in checked.snapshot.findings],
            )

    def test_compound_source_can_format_selected_fields_differently(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"mean":1.234,"error":0.567}')
            original = set_results(
                document,
                "``<!-- eid:value form=plus_minus source=values select=/mean "
                "select=/error render=fixed:2 render=fixed:1 -->",
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                document.read_text(), original.replace("``<!--", "`1.23 ± 0.6`<!--")
            )
            (entry / "data/value.json").write_text(
                '{"mean":1.234,"error":0.567,"extra":2}'
            )
            document.write_text(
                document.read_text().replace(
                    "select=/error", "select=/error select=/extra"
                )
            )
            before = retained_files(logical)
            rejected = evidence(logical, "sync", "--id", "value")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("evidence.render.invalid", rejected.stderr)
            self.assertEqual(retained_files(logical), before)

    def test_unsynchronized_decimal_definition_returns_a_finding(self):
        from validation.controller import ValidationRequest, validate

        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.csv").write_text("threshold,value\n0.676,7\n")
            set_results(
                document,
                "``<!-- eid:value source=values select=/value "
                "where=/threshold:eq:decimal:0.676 -->",
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.csv",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            document.write_text(document.read_text().replace("0.676", "0.675"))
            checked = validate(
                ValidationRequest(logical.with_suffix(".md"), publish=False)
            )
            findings = [
                finding
                for finding in checked.snapshot.findings
                if finding.code == "evidence.definition.unsynchronized"
            ]
            self.assertEqual(len(findings), 1)
            self.assertIn("0.676", str(findings[0].observed))

    def test_bounded_numeric_rendering_is_declared_not_inferred(self):
        for options, expected in (
            ("render=fixed:2 scale=100 unit=%", "`-123450.00%`"),
            ("render=grouped_integer magnitude=true", "`1,234`"),
            ("render=scientific:3 sign=always", "`-1.23e3`"),
        ):
            with (
                self.subTest(options=options),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.csv").write_text(
                    "value\n-1234.5\n" if "grouped" not in options else "value\n-1234\n"
                )
                original = set_results(
                    document,
                    "Before ``<!-- eid:value source=values "
                    f"select=/value parse=decimal {options} --> after.",
                )
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.csv",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    document.read_text(), original.replace("``<!--", expected + "<!--")
                )

    def test_validation_rejects_an_unsynchronized_comment_edit(self):
        from validation.controller import ValidationRequest, validate

        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7,"other":7}')
            set_results(document, "``<!-- eid:value source=values select=/value -->")
            result = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            baseline = validate(
                ValidationRequest(logical.with_suffix(".md"), publish=False)
            )
            self.assertNotIn(
                "evidence.definition.unsynchronized",
                [finding.code for finding in baseline.snapshot.findings],
            )
            document.write_text(
                document.read_text().replace("select=/value", "select=/other")
            )
            changed = validate(
                ValidationRequest(logical.with_suffix(".md"), publish=False)
            )
            findings = [
                finding
                for finding in changed.snapshot.findings
                if finding.code == "evidence.definition.unsynchronized"
            ]
            self.assertEqual(len(findings), 1)
            self.assertIn("log evidence sync --id value", str(findings[0].observed))

    def test_text_excerpt_treats_literal_eid_comments_as_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            source = "literal <!-- eid:text source=not-a-declaration -->\n"
            (entry / "data/value.txt").write_text(source)
            original = set_results(
                document, "<!-- eid:text source=values line=1 -->\n```text\n```\n"
            )
            result = evidence(
                logical, "sync", "--id", "text", "--add-origin", "values=data/value.txt"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                document.read_text(),
                original.replace("```text\n", "```text\n" + source),
            )
            repeated = evidence(logical, "sync", "--id", "text")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertFalse(json.loads(repeated.stdout)["changed"])

    def test_short_boolean_value_in_an_ordinary_summary_table_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.csv").write_text("name,ok\nalpha,True\n")
            original = set_results(
                document,
                "| Case | Status |\n| --- | --- |\n"
                "| Alpha | ``<!-- eid:ok source=values select=/ok "
                "identity=/name parse=boolean render=boolean:pass_fail --> |",
            )
            result = evidence(
                logical, "sync", "--id", "ok", "--add-origin", "values=data/value.csv"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                document.read_text(), original.replace("``<!--", "`Pass`<!--")
            )

    def test_table_filters_boolean_and_percentage_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.csv").write_text(
                "name,rate,ok\nalpha,0.1234,True\nbeta,0.8,False\ngamma,0.9999,True\n"
            )
            original = set_results(
                document,
                "<!-- eid:table source=values identity=/name "
                "where=/name:in:string:alpha,gamma; "
                "column=/ok parse=boolean render=boolean:yes_no; "
                "column=/rate render=percentage:2 -->\n"
                "| Valid | Rate |\n| --- | ---: |\n",
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "table",
                "--add-origin",
                "values=data/value.csv",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                document.read_text(),
                original.replace(
                    "| --- | ---: |\n",
                    "| --- | ---: |\n| yes | 12.34% |\n| yes | 99.99% |\n",
                ),
            )

    def test_inline_whole_diff_artifact_is_filled_and_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            source = entry / "data/value.diff"
            source.write_text("--- old\n+++ new\n@@ -1 +1 @@\n-a\n+b\n\n")
            original = set_results(
                document,
                "Above.\n\n<!-- eid:diff source=diff -->\n```diff\n```\n\nBelow.",
            )
            result = evidence(
                logical, "sync", "--id", "diff", "--add-origin", "diff=data/value.diff"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                document.read_text(),
                original.replace("```diff\n", "```diff\n" + source.read_text()),
            )
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertNotIn("artifact_fingerprint", record)
            source.write_text(source.read_text().replace("+b", "+c"))
            refreshed = evidence(logical, "sync", "--id", "diff")
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertIn("+c\n\n```", document.read_text())

    def test_v4_registry_and_removed_sync_options_are_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            set_results(document, "``<!-- eid:value source=values select=/value -->")
            (entry / "evidence.json").write_text(
                '{"schema":"research-log-evidence/v4","records":[]}'
            )
            before = retained_files(logical)
            rejected = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("direct Repair", rejected.stderr)
            self.assertEqual(retained_files(logical), before)
            for flag in ("--definition", "--comparison-id", "--artifact-fingerprint"):
                result = evidence(logical, "sync", "--id", "value", flag, "x")
                self.assertEqual(result.returncode, 2)
                self.assertIn("unrecognized arguments", result.stderr)
                self.assertEqual(retained_files(logical), before)

    def test_scalar_placeholder_compare_then_sync_preserves_neighbors(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":1.2345}')
            original = set_results(
                document,
                "Before ``<!-- eid:value source=values select=/value "
                "render=fixed:2 --> after.\n",
            )
            compared = evidence(logical, "compare", "--id", "value")
            self.assertEqual(compared.returncode, 2)
            self.assertIn("producer", compared.stderr)
            synced = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(synced.returncode, 0, synced.stderr)
            self.assertEqual(
                document.read_text(), original.replace("``<!--", "`1.23`<!--")
            )
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(
                record["sources"][0]["locator"]["expect"], {"items": 1, "matches": 1}
            )
            before = retained_files(logical)
            repeated = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertFalse(json.loads(repeated.stdout)["changed"])
            self.assertEqual(retained_files(logical), before)
            compared = evidence(logical, "compare", "--id", "value")
            self.assertEqual(compared.returncode, 0, compared.stderr)
            self.assertEqual(
                json.loads(compared.stdout)["records"],
                [{"id": "value", "before": "`1.23`", "after": "`1.23`"}],
            )

    def test_changed_aspect_needs_only_the_changed_comment_and_call(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":0.12345}')
            set_results(
                document,
                "``<!-- eid:value source=values select=/value render=fixed:2 -->",
            )
            first = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            data_before = (entry / "data.json").read_bytes()
            document.write_text(
                document.read_text().replace(
                    "render=fixed:2", "form=percentage render=fixed:1"
                )
            )
            result = evidence(logical, "sync", "--id", "value")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("`12.3%`", document.read_text())
            self.assertEqual((entry / "data.json").read_bytes(), data_before)

    def test_closed_compounds_have_exact_literal_spellings(self):
        for form, expected, fields in (
            ("range", "`1.2–2.3 nm`", ["a", "b"]),
            ("tuple", "`(1.2, 2.3) nm`", ["a", "b"]),
            ("interval", "`1.2 [2.3, 3.4] nm`", ["a", "b", "c"]),
            ("plus_minus", "`1.2 ± 2.3 nm`", ["a", "b"]),
        ):
            with self.subTest(form=form), tempfile.TemporaryDirectory() as directory:
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.json").write_text('{"a":1.2,"b":2.3,"c":3.4}')
                clauses = "; ".join(
                    f"source=values select=/{field} render=fixed:1" for field in fields
                )
                original = set_results(
                    document,
                    f"Before ``<!-- eid:value form={form} unit=nm; "
                    f"{clauses} --> after.",
                )
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    document.read_text(), original.replace("``<!--", expected + "<!--")
                )

    def test_direct_table_preserves_header_alignment_and_formats_subset_order(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.csv").write_text(
                "name,value,unused\nalpha,1.234,9\nbeta,2.345,8\n"
            )
            declaration = (
                "<!-- eid:table source=values identity=/name; "
                "column=/value parse=decimal render=fixed:2; "
                "column=/name render=text -->\n"
            )
            header = "  | Reading | Label |\n  | ---: | :--- |\n"
            original = set_results(
                document, "Above.\n\n" + declaration + header + "\nBelow."
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "table",
                "--add-origin",
                "values=data/value.csv",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = original.replace(
                header, header + "  | 1.23 | alpha |\n  | 2.34 | beta |\n"
            )
            self.assertEqual(document.read_text(), expected)
            compared = evidence(logical, "compare", "--id", "table")
            self.assertEqual(compared.returncode, 0, compared.stderr)
            self.assertEqual(
                json.loads(compared.stdout)["records"],
                [
                    {
                        "id": "table",
                        "before": header + "  | 1.23 | alpha |\n  | 2.34 | beta |\n",
                        "after": header + "  | 1.23 | alpha |\n  | 2.34 | beta |\n",
                    }
                ],
            )

    def test_text_slices_use_inclusive_unicode_positions_and_preserve_fences(self):
        for selector, expected in (
            ("line=2", "aβcd\n"),
            ("lines=1:2", "first\naβcd\n"),
            ("line=2 chars=2:3", "βc\n"),
        ):
            with (
                self.subTest(selector=selector),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.txt").write_text("first\r\naβcd\r\nlast\r\n")
                original = set_results(
                    document,
                    f"Above.\n\n<!-- eid:text source=values {selector} -->\n"
                    "````text\n````\n\nBelow.",
                )
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "text",
                    "--add-origin",
                    "values=data/value.txt",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    document.read_text(),
                    original.replace("````text\n", "````text\n" + expected),
                )

    def test_directory_origin_reads_only_explicit_member(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/collection").mkdir()
            (entry / "data/collection/value.json").write_text('{"value":7}')
            set_results(
                document, "``<!-- eid:value source=values/value.json select=/value -->"
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin-directory",
                "values=data/collection",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("`7`", document.read_text())

    def test_conflicting_and_unused_additions_are_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            (entry / "data/other.json").write_text('{"value":8}')
            set_results(document, "``<!-- eid:value source=values select=/value -->")
            first = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            for extra, code in (
                ("values=data/other.json", "declaration.conflict"),
                ("other=data/other.json", "declaration.unused"),
            ):
                before = retained_files(logical)
                result = evidence(
                    logical, "sync", "--id", "value", "--add-origin", extra
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(code, result.stderr)
                self.assertEqual(retained_files(logical), before)

    def test_invalid_definitions_fail_before_publishing(self):
        for definition in (
            "source=values join=other",
            "source=values line=0",
            "source=values lines=2:1",
            "source=values line=2 chars=1:99",
            "source=values select=/missing",
            "source=values select=/value render=fixed:no",
            "source=values select=/value artifact_fingerprint=anything",
        ):
            with (
                self.subTest(definition=definition),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.json").write_text('{"value":7}')
                set_results(document, f"``<!-- eid:value {definition} -->")
                before = retained_files(logical)
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.json",
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(retained_files(logical), before)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value":7}')
            set_results(document, "``<!-- eid:value source=values select=/value -->")
            before = retained_files(logical)
            result = evidence(
                logical,
                "sync",
                "--id",
                "value",
                "--add-origin",
                "values=data/value.json",
                "--dry-run",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["changed"])
            self.assertEqual(retained_files(logical), before)
            self.assertFalse((logical / ".cache").exists())

    def test_artifact_bytes_refresh_fingerprint_without_touching_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "images").mkdir()
            source = entry / "images/figure.png"
            source.write_bytes(b"old")
            original = set_results(
                document, "![Label](images/figure.png)<!-- eid:figure source=figure -->"
            )
            first = evidence(
                logical,
                "sync",
                "--id",
                "figure",
                "--add-origin",
                "figure=images/figure.png",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            old = (entry / "evidence.json").read_bytes()
            source.write_bytes(b"new")
            result = evidence(logical, "sync", "--id", "figure")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(document.read_text(), original)
            self.assertNotEqual((entry / "evidence.json").read_bytes(), old)


def related_fixture(root: Path):
    logical, entry, document = fixture(
        root, './pyrun scripts/build.py --output "<values>"'
    )
    (entry / "data/value.json").write_text('{"value":1.234}')
    owner = sync(logical, "--add-generated", "values=data/value.json")
    if owner.returncode:
        raise AssertionError(owner.stderr)
    set_results(
        document, "``<!-- eid:owner source=values select=/value render=fixed:2 -->"
    )
    first = evidence(logical, "sync", "--id", "owner")
    if first.returncode:
        raise AssertionError(first.stderr)
    referenced = logical / "entries/2030-01-02-e002-reference"
    referenced.mkdir()
    ref_document = referenced / "e002.md"
    ref_document.write_text(
        "# Reference\n\n## Execution\n\n`Steps:`\n\nRecorded input.\n\n"
        "`Results:`\n\n``<!-- eid:forward source=values select=/value "
        "render=fixed:1 -->\n"
    )
    summary = logical.with_suffix(".md")
    summary.write_text(
        summary.read_text() + "- `2030-01-02` [Reference](study/entries/"
        "2030-01-02-e002-reference/e002.md)\n"
        "\n## Summary\n\n`1.23`<!-- ref entry = e001; eid = owner --> "
        "and `1.2`<!-- ref entry = e002; eid = forward -->\n"
    )
    result = run_log(
        logical.parent,
        "evidence",
        "sync",
        "--path",
        str(logical),
        "--entry",
        "e002",
        "--id",
        "forward",
        "--add-from-entry",
        "values=e001",
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return logical, entry, document, ref_document, summary


class SourceEvidenceSyncTests(unittest.TestCase):
    def test_source_scope_refreshes_all_member_artifact_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document = fixture(
                root, './pyrun scripts/build.py --output "<bundle>"'
            )
            bundle = entry / "data/bundle"
            bundle.mkdir()
            for name in ("first.png", "second.png"):
                (bundle / name).write_bytes(b"old")
            owner = sync(logical, "--add-generated-directory", "bundle=data/bundle")
            self.assertEqual(owner.returncode, 0, owner.stderr)
            original = set_results(
                document,
                "![First](data/bundle/first.png)"
                "<!-- eid:first source=bundle/first.png -->\n"
                "![Second](data/bundle/second.png)"
                "<!-- eid:second source=bundle/second.png -->",
            )
            for record_id in ("first", "second"):
                result = evidence(logical, "sync", "--id", record_id)
                self.assertEqual(result.returncode, 0, result.stderr)
            before = json.loads((entry / "evidence.json").read_text())["records"]
            for name in ("first.png", "second.png"):
                (bundle / name).write_bytes(b"new")
            result = evidence(logical, "sync", "--source", "bundle")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(document.read_text(), original)
            after = json.loads((entry / "evidence.json").read_text())["records"]
            for left, right in zip(before, after):
                self.assertNotEqual(
                    left["artifact_fingerprint"], right["artifact_fingerprint"]
                )
            self.assertEqual(
                [record["id"] for record in json.loads(result.stdout)["records"]],
                ["first", "second"],
            )

    def test_source_scope_updates_related_entries_and_forwarded_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document, ref_document, summary = related_fixture(
                Path(directory)
            )
            (entry / "data/value.json").write_text('{"value":2.345}')
            before = retained_files(logical)
            result = evidence(logical, "compare", "--source", "values")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["records"],
                [
                    {"id": "owner", "before": "`1.23`", "after": "`2.34`"},
                    {"id": "forward", "before": "`1.2`", "after": "`2.3`"},
                ],
            )
            self.assertEqual(retained_files(logical), before)
            result = evidence(logical, "sync", "--source", "values")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("`2.34`", document.read_text())
            self.assertIn("`2.3`", ref_document.read_text())
            self.assertIn(
                "`2.34`<!-- ref entry = e001; eid = owner --> "
                "and `2.3`<!-- ref entry = e002; eid = forward -->",
                summary.read_text(),
            )

    def test_invalid_related_record_prevents_every_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _, ref_document, _ = related_fixture(Path(directory))
            (entry / "data/value.json").write_text('{"value":2.345}')
            ref_document.write_text(
                ref_document.read_text().replace("select=/value", "select=/missing")
            )
            before = retained_files(logical)
            result = evidence(logical, "sync", "--source", "values")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(retained_files(logical), before)

    def test_changed_source_observation_prevents_every_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _, _, _ = related_fixture(Path(directory))
            source = entry / "data/value.json"
            source.write_text('{"value":2.345}')
            before = retained_files(logical)
            original = evidence_sync._recheck_sources

            def change_then_recheck(edits, observations):
                source.write_text('{"value":9.999}')
                return original(edits, observations)

            with (
                mock.patch.object(
                    evidence_sync, "_recheck_sources", side_effect=change_then_recheck
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = dispatcher.main(
                    [
                        "evidence",
                        "sync",
                        "--path",
                        str(logical),
                        "--entry",
                        "e001",
                        "--source",
                        "values",
                    ]
                )
            self.assertEqual(status, 2)
            # The external update belongs to the source, not this publication.
            before[source] = source.read_bytes()
            self.assertEqual(retained_files(logical), before)

    def test_late_publication_failure_restores_all_owned_files(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _, _, _ = related_fixture(Path(directory))
            (entry / "data/value.json").write_text('{"value":2.345}')
            before = retained_files(logical)
            original = storage.remove_or_write
            calls = []

            def fail_once(path, value):
                calls.append(path)
                if len(calls) == 3:
                    raise OSError("injected third publication")
                return original(path, value)

            output = io.StringIO()
            with (
                mock.patch.object(storage, "remove_or_write", side_effect=fail_once),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = dispatcher.main(
                    [
                        "evidence",
                        "sync",
                        "--path",
                        str(logical),
                        "--entry",
                        "e001",
                        "--source",
                        "values",
                    ]
                )
            self.assertEqual(status, 2)
            self.assertGreaterEqual(len(calls), 3)
            self.assertEqual(retained_files(logical), before)

    def test_overlapping_source_syncs_serialize_under_bounded_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document, ref_document, _ = related_fixture(Path(directory))
            (entry / "data/value.json").write_text('{"value":2.345}')
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [
                    workers.submit(
                        run_log_process,
                        logical.parent,
                        "evidence",
                        "sync",
                        "--path",
                        str(logical),
                        "--entry",
                        "e001",
                        "--source",
                        "values",
                    )
                    for _ in range(2)
                ]
                results = [future.result(timeout=45) for future in futures]
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("`2.34`", document.read_text())
            self.assertIn("`2.3`", ref_document.read_text())


if __name__ == "__main__":
    unittest.main()

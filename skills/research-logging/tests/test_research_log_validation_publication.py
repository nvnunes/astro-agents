from research_log_validation_test_support import (
    CLI,
    GRAPH,
    GRAPH_STORE,
    RECORDS,
    RUNTIME,
    SCRIPT,
    Path,
    adjudication_for,
    json,
    make_log,
    mock,
    subprocess,
    sys,
    tempfile,
    unittest,
    write,
)


class PublicationTests(unittest.TestCase):
    def _lock_probe(self, root: Path, exit_code: int) -> subprocess.CompletedProcess:
        code = f"""
import os
import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
from pathlib import Path
from validation.records import validation_lock
with validation_lock(Path({str(root)!r})):
    os._exit({exit_code})
"""
        return subprocess.run(
            [sys.executable, "-c", code],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_per_log_lock_rejects_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with RECORDS.validation_lock(root):
                completed = self._lock_probe(root, 0)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("another validation writer", completed.stderr)

    def test_per_log_lock_is_released_when_process_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = self._lock_probe(root, 23)

            self.assertEqual(completed.returncode, 23)
            with RECORDS.validation_lock(root):
                pass

    def test_different_logs_have_independent_publication_locks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            with RECORDS.validation_lock(first), RECORDS.validation_lock(second):
                pass

    def test_interrupted_bundle_is_rejected_and_rebuilt_without_rollback(
        self,
    ) -> None:
        for interrupt_after in range(1, 7):
            with self.subTest(interrupt_after=interrupt_after):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    summary, entry = make_log(root)
                    scan, _ = RUNTIME.scan_log(summary, jobs=1)
                    output = summary.with_suffix("")
                    RUNTIME.render_records(
                        adjudication_for(scan, entry), scan, output
                    )
                    prior_state = output / "validation-state.json"
                    original_publish = RECORDS._publish_one
                    calls = 0
                    write(
                        entry.parent / "scripts" / "extra.py",
                        "value = 1\n",
                    )

                    def interrupt(staged: Path, destination: Path) -> None:
                        nonlocal calls
                        calls += 1
                        original_publish(staged, destination)
                        if calls == interrupt_after:
                            raise RuntimeError("simulated interruption")

                    changed_scan, _ = RUNTIME.scan_log(summary, jobs=1)
                    changed_adjudication = adjudication_for(changed_scan, entry)
                    changed_adjudication["date"] = "2026-08-12"
                    with mock.patch.object(
                        RECORDS, "_publish_one", side_effect=interrupt
                    ):
                        with self.assertRaisesRegex(
                            RECORDS.RecordPublicationError,
                            "next validation must rebuild",
                        ):
                            RUNTIME.render_records(
                                changed_adjudication, changed_scan, output
                            )

                    interrupted_lint = RUNTIME.lint_records(output)
                    self.assertTrue(interrupted_lint["report_ok"])
                    self.assertEqual(
                        interrupted_lint["ok"], interrupt_after in {1, 3, 4, 5, 6}
                    )
                    self.assertEqual(
                        interrupted_lint["cache_usable"], interrupt_after in {1, 6}
                    )
                    fresh_scan_path = root / "fresh-scan.json"
                    result = CLI.main(
                        [
                            "scan",
                            "--summary",
                            str(summary),
                            "--state",
                            str(prior_state),
                            "--output",
                            str(fresh_scan_path),
                        ]
                    )
                    self.assertEqual(result, 0)
                    fresh_scan = json.loads(
                        fresh_scan_path.read_text(encoding="utf-8")
                    )
                    self.assertIn("incremental", fresh_scan)

                    if fresh_scan.get("incremental", {}).get("status") != "unchanged":
                        RUNTIME.render_records(
                            adjudication_for(fresh_scan, entry), fresh_scan, output
                        )
                    self.assertTrue(RUNTIME.lint_records(output)["ok"])

    def test_interruption_after_durable_files_leaves_only_cache_misses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            output = summary.with_suffix("")
            first_scan, _ = RUNTIME.scan_log(summary, jobs=1)
            RUNTIME.render_records(
                adjudication_for(first_scan, entry), first_scan, output
            )
            names = RUNTIME.VALIDATION_RECORD_FILENAMES
            first = {
                name: (
                    (output / name).read_bytes()
                    if (output / name).is_file()
                    else None
                )
                for name in names
            }

            text = entry.read_text(encoding="utf-8")
            entry.write_text(
                text.replace(
                    "python <log>/scripts/shared.py --flag\n",
                    "python <log>/scripts/shared.py --flag\n"
                    "python scripts/extra.py\n",
                ),
                encoding="utf-8",
            )
            write(entry.parent / "scripts" / "extra.py", "value = 1\n")
            second_scan, _ = RUNTIME.scan_log(summary, jobs=1)
            RUNTIME.render_records(
                adjudication_for(second_scan, entry), second_scan, output
            )
            with tempfile.TemporaryDirectory() as staged_directory:
                staged = Path(staged_directory)
                for name in names:
                    source = output / name
                    if source.is_file():
                        write(staged / name, source.read_text(encoding="utf-8"))
                builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
                origin = GRAPH.FactOrigin(
                    kind=GRAPH.OriginKind.MECHANICAL,
                    resolver="publication-boundary-test",
                    inputs=(GRAPH.OriginInput(second_scan["summary"], "fixture"),),
                    rules_version=RUNTIME.RULES_VERSION,
                )
                invocation = GRAPH.NodeKey(
                    "docs/mini",
                    GRAPH.NodeKind.INVOCATION,
                    "e001:command:99",
                )
                artifact = GRAPH.NodeKey(
                    "docs/other",
                    GRAPH.NodeKind.ARTIFACT,
                    "docs/other/data/shared.csv",
                )
                builder.add_node(invocation, origin)
                builder.add_node(artifact, origin)
                builder.add_edge(
                    GRAPH.EdgeKind.CROSS_LOG_USE,
                    invocation,
                    artifact,
                    "docs/mini",
                    origin,
                )
                builder.add_root(
                    invocation, GRAPH.RootPolicy.RECORDED_COMMAND, origin
                )
                replacement_slice = GRAPH_STORE.slice_record(
                    builder.build(),
                    second_scan["summary"],
                    second_scan["files"],
                    second_scan["repository_material_owners"],
                )
                write(
                    staged / GRAPH_STORE.SLICE_FILENAME,
                    json.dumps(replacement_slice, indent=2) + "\n",
                )
                state_path = staged / "validation-state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["graph_identity"] = replacement_slice["graph_identity"]
                write(state_path, json.dumps(state, indent=2) + "\n")
                self.assertTrue(RUNTIME.lint_records(staged)["ok"])
                prior_state = json.loads(
                    first["validation-state.json"].decode("utf-8")
                )
                self.assertNotEqual(
                    prior_state["graph_identity"], state["graph_identity"]
                )
                for name, payload in first.items():
                    destination = output / name
                    if payload is None:
                        destination.unlink(missing_ok=True)
                    else:
                        destination.write_bytes(payload)
                expected = RECORDS.record_bundle_identity(output, names)
                original_publish = RECORDS._publish_one
                calls = 0

                def interrupt(source: Path, destination: Path) -> None:
                    nonlocal calls
                    calls += 1
                    original_publish(source, destination)
                    if calls == 2:
                        raise RuntimeError("after durable files")

                with mock.patch.object(
                    RECORDS, "_publish_one", side_effect=interrupt
                ):
                    with self.assertRaises(RECORDS.RecordPublicationError):
                        RECORDS.publish_record_bundle(
                            staged,
                            output,
                            names,
                            RECORDS.PublicationGuard(expected),
                        )

            lint = RUNTIME.lint_records(output)
            self.assertTrue(lint["ok"], lint["durable_issues"])
            self.assertFalse(lint["cache_usable"])
            self.assertIn("state report identity", "; ".join(lint["cache_issues"]))

    def test_lock_file_is_ignored_by_owned_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            write(summary.with_suffix("") / RECORDS.LOCK_FILENAME, "")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)

            identities = {
                item["identity"]
                for entry in scan["entries"]
                for item in entry.get("orphan_inventory", [])
            }
            self.assertNotIn(RECORDS.LOCK_FILENAME, identities)


if __name__ == "__main__":
    unittest.main()

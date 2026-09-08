from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from research_log_cli_test_support import run_log
from research_log_validation_test_support import mechanical_log, write

RESULTS = importlib.import_module("validation.mechanical_results")
BATCH_VALIDATION = importlib.import_module("log_commands.batch_validation")
OPERATION_STATE = importlib.import_module("validation.operation_state")


class FindingsCliTests(unittest.TestCase):
    def test_validate_batch_retries_one_changed_snapshot_then_completes(self) -> None:
        old = {
            "chain_id": "old",
            "commands": [{"document": "entries/e001.md", "fence": 1, "ordinal": 1}],
            "entry": "e001",
        }
        current = {**old, "chain_id": "current", "findings": []}
        evaluation = SimpleNamespace(
            result=SimpleNamespace(
                checks=(),
                completion=RESULTS.CompletionState.COMPLETE_CLEAR,
                as_dict=lambda: {"checks": []},
            ),
            scan={"graph": None, "invocations": (), "registries": ()},
        )
        published = {
            "batch": old,
            "projection_id": "projection",
            "result_date": "2026-09-08",
        }
        projection = {"chains": [current], "unresolved": []}
        log = SimpleNamespace(summary=Path("/project/study.md"))

        with (
            mock.patch.object(
                BATCH_VALIDATION, "batch_findings", return_value=published
            ),
            mock.patch.object(
                BATCH_VALIDATION,
                "load_batch_projection",
                return_value={"chains": [old]},
            ),
            mock.patch.object(BATCH_VALIDATION, "resolve_entry", return_value=object()),
            mock.patch.object(
                BATCH_VALIDATION, "evaluate_entry_record", return_value=evaluation
            ) as evaluated,
            mock.patch.object(
                BATCH_VALIDATION, "build_batch_projection", return_value=projection
            ),
            mock.patch.object(
                BATCH_VALIDATION,
                "_source_snapshot",
                side_effect=[("before",), ("changed",), ("stable",), ("stable",)],
            ),
        ):
            result, complete = BATCH_VALIDATION.validate_batch(
                log, projection_id="projection", entry="e001", chain_id="old"
            )

        self.assertTrue(complete)
        self.assertEqual(result["status"], "complete_clear")
        self.assertEqual(evaluated.call_count, 2)

    def test_validate_batch_reports_repeated_source_change_incomplete(self) -> None:
        old = {
            "chain_id": "old",
            "commands": [{"document": "entries/e001.md", "fence": 1, "ordinal": 1}],
            "entry": "e001",
        }
        evaluation = SimpleNamespace(
            result=SimpleNamespace(
                checks=(),
                completion=RESULTS.CompletionState.COMPLETE_CLEAR,
                as_dict=lambda: {"checks": []},
            ),
            scan={"graph": None, "invocations": (), "registries": ()},
        )
        published = {
            "batch": old,
            "projection_id": "projection",
            "result_date": "2026-09-08",
        }
        log = SimpleNamespace(summary=Path("/project/study.md"))
        with (
            mock.patch.object(
                BATCH_VALIDATION, "batch_findings", return_value=published
            ),
            mock.patch.object(
                BATCH_VALIDATION,
                "load_batch_projection",
                return_value={"chains": [old]},
            ),
            mock.patch.object(BATCH_VALIDATION, "resolve_entry", return_value=object()),
            mock.patch.object(
                BATCH_VALIDATION, "evaluate_entry_record", return_value=evaluation
            ),
            mock.patch.object(
                BATCH_VALIDATION,
                "build_batch_projection",
                return_value={"chains": [], "unresolved": []},
            ),
            mock.patch.object(
                BATCH_VALIDATION,
                "_source_snapshot",
                side_effect=[("a",), ("b",), ("c",), ("d",)],
            ),
        ):
            result, complete = BATCH_VALIDATION.validate_batch(
                log, projection_id="projection", entry="e001", chain_id="old"
            )

        self.assertFalse(complete)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["reason"], "source_changed")

    def test_projection_groups_only_unique_same_entry_producer_edges(self) -> None:
        from validation.batch_projection import build_batch_projection
        from validation.commands import Invocation, MaterialRelationship

        def invocation(
            identity: str,
            *,
            entry: str = "e001",
            inputs: tuple[str, ...] = (),
            outputs: tuple[str, ...] = (),
            ordinal: int,
        ) -> Invocation:
            return Invocation(
                identity=identity,
                document=f"entries/{entry}.md",
                entry=entry,
                fence=1,
                ordinal=ordinal,
                sequence=ordinal,
                tokens=("./pyrun",),
                executable="./pyrun",
                script_argument="scripts/run.py",
                parameters=(),
                script="scripts/run.py",
                script_identity=None,
                inputs=tuple(
                    MaterialRelationship(path, "input", "option")
                    for path in inputs
                ),
                outputs=tuple(
                    MaterialRelationship(path, "output", "option")
                    for path in outputs
                ),
                collections=(),
                candidates=(),
                material_owner=entry,
            )

        invocations = (
            invocation("producer", outputs=("/x",), ordinal=1),
            invocation("consumer", inputs=("/x",), outputs=("/y",), ordinal=2),
            invocation("writer-a", outputs=("/shared",), ordinal=3),
            invocation("writer-b", outputs=("/shared",), ordinal=4),
            invocation("cross-entry", entry="e002", inputs=("/y",), ordinal=1),
        )
        record = RESULTS.MechanicalGeneratedRecord.build(
            "/project/study.md", "test-rules", "2026-09-08", ()
        )

        projection = build_batch_projection(
            record,
            invocations=invocations,
            registries=(),
            source_identity="source",
        )
        reversed_projection = build_batch_projection(
            record,
            invocations=tuple(reversed(invocations)),
            registries=(),
            source_identity="source",
        )

        memberships = {
            (chain["entry"], tuple(item["identity"] for item in chain["commands"]))
            for chain in projection["chains"]
        }
        self.assertEqual(
            memberships,
            {
                ("e001", ("consumer", "producer")),
                ("e001", ("writer-a",)),
                ("e001", ("writer-b",)),
                ("e002", ("cross-entry",)),
            },
        )
        self.assertEqual(
            projection["projection_id"], reversed_projection["projection_id"]
        )

    def test_projection_preserves_directory_root_without_false_fan_out(self) -> None:
        from validation.batch_projection import build_batch_projection
        from validation.commands import (
            Invocation,
            MaterialCollection,
            MaterialRelationship,
        )

        producer = Invocation(
            identity="producer",
            document="entries/e001.md",
            entry="e001",
            fence=1,
            ordinal=1,
            sequence=1,
            tokens=("./pyrun",),
            executable="./pyrun",
            script_argument="scripts/run.py",
            parameters=(),
            script="scripts/run.py",
            script_identity=None,
            inputs=(),
            outputs=(
                MaterialRelationship("/out/a.csv", "output", "directory"),
                MaterialRelationship("/out/b.csv", "output", "directory"),
            ),
            collections=(
                MaterialCollection(
                    "output",
                    "directory",
                    "--output-dir",
                    ("/out/a.csv", "/out/b.csv"),
                    "/out",
                ),
            ),
            candidates=(),
            material_owner="e001",
        )
        consumer = Invocation(
            identity="consumer",
            document="entries/e001.md",
            entry="e001",
            fence=1,
            ordinal=2,
            sequence=2,
            tokens=("./pyrun",),
            executable="./pyrun",
            script_argument="scripts/use.py",
            parameters=(),
            script="scripts/use.py",
            script_identity=None,
            inputs=(MaterialRelationship("/out", "input", "directory"),),
            outputs=(),
            collections=(),
            candidates=(),
            material_owner="e001",
        )
        record = RESULTS.MechanicalGeneratedRecord.build(
            "/project/study.md", "test-rules", "2026-09-08", ()
        )

        projection = build_batch_projection(
            record,
            invocations=(producer, consumer),
            registries=(),
            source_identity="source",
        )

        self.assertEqual(len(projection["chains"]), 1)
        chain = projection["chains"][0]
        self.assertEqual(chain["artifacts"], ["/out", "/out/a.csv", "/out/b.csv"])
        self.assertEqual(chain["edges"][0]["artifact"], "/out")
        self.assertIn("directory", chain["signals"])
        self.assertNotIn("fan_out", chain["signals"])

    def test_related_unresolved_uses_artifact_context_not_generic_values(self) -> None:
        old = {
            "artifacts": ["/selected.csv"],
            "chain_id": "selected",
            "commands": [
                {
                    "document": "entries/e001.md",
                    "entry": "e001",
                    "fence": 1,
                    "identity": "selected-command",
                    "inputs": [],
                    "ordinal": 1,
                    "outputs": [{"path": "/selected.csv"}],
                }
            ],
            "entry": "e001",
        }
        related = {
            "code": "lineage.missing",
            "dependencies": [],
            "identity": "provenance:e001:selected",
            "observed": {},
            "rule": "Provenance",
            "scope": "provenance",
            "status": "fail",
            "subject": "/selected.csv",
        }
        unrelated = {
            "code": "lineage.missing",
            "dependencies": [],
            "identity": "provenance:e001:other",
            "observed": {"owner": "e001"},
            "rule": "Provenance",
            "scope": "provenance",
            "status": "fail",
            "subject": "/other.csv",
        }
        projection = {
            "unresolved": [
                {
                    "entry": "e001",
                    "findings": [unrelated, related],
                }
            ]
        }

        selected = BATCH_VALIDATION._related_unresolved(old, [old], projection)

        self.assertEqual(
            [value["identity"] for value in selected], [related["identity"]]
        )

    def test_projection_marks_cross_entry_registry_context_read_only(self) -> None:
        from research_log_data import DataFile, Fingerprint, InputResource
        from validation.batch_projection import build_batch_projection
        from validation.commands import Invocation, MaterialRelationship

        resource = InputResource(
            "shared",
            "file",
            "../producer/data/shared.csv",
            Fingerprint("sha256", digest="0" * 64),
            False,
            "/project/shared.csv",
            reference_entry="e001",
        )
        invocation = Invocation(
            identity="consumer",
            document="entries/e002.md",
            entry="e002",
            fence=1,
            ordinal=1,
            sequence=1,
            tokens=("./pyrun",),
            executable="./pyrun",
            script_argument="scripts/use.py",
            parameters=(),
            script="scripts/use.py",
            script_identity=None,
            inputs=(
                MaterialRelationship(
                    "/project/shared.csv",
                    "input",
                    "named-input",
                    named_input="shared",
                    input_resource=resource,
                ),
            ),
            outputs=(),
            collections=(),
            candidates=(),
            material_owner="e002",
        )
        record = RESULTS.MechanicalGeneratedRecord.build(
            "/project/study.md", "test-rules", "2026-09-08", ()
        )
        data = DataFile(
            Path("/project/e002/data.json"), Path("/project/e002"), (resource,)
        )

        projection = build_batch_projection(
            record,
            invocations=(invocation,),
            registries=(("e002", data),),
            source_identity="source",
        )

        registry = projection["chains"][0]["registry"]
        self.assertEqual(registry[0]["from_entry"], "e001")
        self.assertIs(registry[0]["read_only"], True)

    def test_batch_snapshot_includes_external_directory_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_root = root / "log"
            entry_root = log_root / "entries" / "e001"
            external = root / "external"
            entry_root.mkdir(parents=True)
            external.mkdir()
            summary = root / "study.md"
            summary.write_text("# Study\n", encoding="utf-8")
            source = external / "source.txt"
            source.write_text("before", encoding="utf-8")
            context = SimpleNamespace(
                root=entry_root,
                log=SimpleNamespace(summary=summary, root=log_root),
            )
            batch = {
                "registry": [
                    {"kind": "directory", "path": external.as_posix()}
                ]
            }

            before = BATCH_VALIDATION._source_snapshot(context, batch)
            source.write_text("after!", encoding="utf-8")
            after = BATCH_VALIDATION._source_snapshot(context, batch)

            self.assertNotEqual(before, after)

    def test_batch_membership_handles_replacement_split_and_join(self) -> None:
        from log_commands.batch_validation import _current_groups

        command_a = {"document": "entries/e001.md", "fence": 1, "ordinal": 1}
        command_b = {"document": "entries/e001.md", "fence": 1, "ordinal": 2}
        old = {
            "commands": [command_a, command_b],
            "entry": "e001",
        }
        split = {
            "chains": [
                {
                    "chain_id": "a",
                    "commands": [command_a],
                    "entry": "e001",
                },
                {
                    "chain_id": "b",
                    "commands": [command_b],
                    "entry": "e001",
                },
            ]
        }
        self.assertEqual(
            [value["chain_id"] for value in _current_groups(old, split)],
            ["a", "b"],
        )
        joined = {
            "chains": [
                {
                    "chain_id": "joined",
                    "commands": [command_a, command_b],
                    "entry": "e001",
                }
            ]
        }
        self.assertEqual(
            [value["chain_id"] for value in _current_groups(old, joined)],
            ["joined"],
        )
        replacement = {
            "commands": [
                {
                    "document": "entries/e001.md",
                    "fence": 2,
                    "ordinal": 1,
                    "outputs": [{"path": "/artifact.csv"}],
                }
            ],
            "entry": "e001",
        }
        replaced_old = {
            "commands": [{"outputs": [{"path": "/artifact.csv"}]}],
            "entry": "e001",
        }
        self.assertEqual(
            _current_groups(replaced_old, {"chains": [replacement]}),
            [replacement],
        )

    def test_validate_batch_only_caches_inspection_and_reconciles_renamed_command(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = mechanical_log(root)
            completed = run_log(
                root,
                "validate",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_root = summary.with_suffix("")
            projection = json.loads(
                (log_root / "validation/batches.json").read_text(encoding="utf-8")
            )
            selected = projection["chains"][0]
            old_identity = selected["commands"][0]["identity"]
            before = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in log_root.rglob("*")
                if path.is_file()
                and not path.name.startswith("research-log-inspection.sqlite3")
                and ("validation" in path.parts or ".cache" in path.parts)
            }

            with OPERATION_STATE.operation_lock(log_root, "log.lock", mode="exclusive"):
                checked = run_log(
                    root,
                    "validate-batch",
                    "--format",
                    "json",
                    "--path",
                    str(log_root),
                    "--projection",
                    projection["projection_id"],
                    "--entry",
                    selected["entry"],
                    "--chain",
                    selected["chain_id"],
                )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)["status"], "complete_clear")
            self.assertEqual(
                before,
                {
                    path: (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in log_root.rglob("*")
                    if path.is_file()
                    and not path.name.startswith("research-log-inspection.sqlite3")
                    and ("validation" in path.parts or ".cache" in path.parts)
                },
            )

            entry.write_text(
                entry.read_text(encoding="utf-8").replace(
                    "--input-catalog", "--input-source"
                ),
                encoding="utf-8",
            )
            renamed = run_log(
                root,
                "validate-batch",
                "--format",
                "json",
                "--path",
                str(log_root),
                "--projection",
                projection["projection_id"],
                "--entry",
                selected["entry"],
                "--chain",
                selected["chain_id"],
            )
            self.assertEqual(renamed.returncode, 0, renamed.stderr)
            renamed_payload = json.loads(renamed.stdout)
            self.assertNotEqual(
                renamed_payload["current_membership"][0]["commands"][0]["identity"],
                old_identity,
            )

    def test_validate_batch_accepts_split_entry_document_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = mechanical_log(root)
            split_entry = entry.with_name("e001a.md")
            entry.rename(split_entry)
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("e001.md", "e001a.md"),
                encoding="utf-8",
            )
            evidence = entry.parent / "evidence.json"
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace("e001.md", "e001a.md"),
                encoding="utf-8",
            )
            completed = run_log(
                root,
                "validate",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            projection = json.loads(
                (summary.with_suffix("") / "validation" / "batches.json").read_text(
                    encoding="utf-8"
                )
            )
            selected = next(
                value for value in projection["chains"] if value["entry"] == "e001a"
            )

            checked = run_log(
                root,
                "validate-batch",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--projection",
                projection["projection_id"],
                "--entry",
                "e001a",
                "--chain",
                selected["chain_id"],
            )

            self.assertEqual(checked.returncode, 0, checked.stderr)
            payload = json.loads(checked.stdout)
            self.assertEqual(payload["status"], "complete_clear")
            self.assertEqual(payload["current_membership"][0]["entry"], "e001a")

    def test_validate_batch_rejects_unplaceable_and_superseded_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = mechanical_log(root, output_option="results")
            completed = run_log(
                root,
                "validate",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with OPERATION_STATE.operation_lock(
                summary.with_suffix(""), "log.lock", mode="exclusive"
            ):
                listed = run_log(
                    root,
                    "findings",
                    "list",
                    "--format",
                    "json",
                    "--path",
                    str(summary.with_suffix("")),
                )
            payload = json.loads(listed.stdout)
            selected = next(
                value for value in payload["chains"] if value["entry"] == "e001"
            )
            incomplete = run_log(
                root,
                "validate-batch",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--projection",
                payload["projection_id"],
                "--entry",
                selected["entry"],
                "--chain",
                selected["chain_id"],
            )
            self.assertEqual(incomplete.returncode, 2)
            self.assertEqual(json.loads(incomplete.stdout)["status"], "incomplete")

            superseded = run_log(
                root,
                "validate-batch",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--projection",
                "superseded",
                "--entry",
                selected["entry"],
                "--chain",
                selected["chain_id"],
            )
            self.assertEqual(superseded.returncode, 2)
            self.assertIn("findings.projection_superseded", superseded.stderr)

    def test_list_and_show_read_one_published_finding_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root, output_option="results")
            completed = run_log(
                root,
                "validate",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = summary.with_suffix("") / "validation/results.json"
            before = result_path.read_bytes()

            listed = run_log(
                root,
                "findings",
                "list",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )

            self.assertEqual(listed.returncode, 0, listed.stderr)
            payload = json.loads(listed.stdout)
            self.assertEqual(payload["schema"], "research-log-findings-list/2")
            self.assertGreater(payload["matched_chains"], 0)
            selected = payload["chains"][0]
            narrowed = run_log(
                root,
                "findings",
                "list",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--entry",
                selected["entry"],
                "--subject",
                selected["subjects"][0],
            )
            self.assertEqual(narrowed.returncode, 0, narrowed.stderr)
            narrowed_payload = json.loads(narrowed.stdout)
            self.assertTrue(narrowed_payload["chains"])
            self.assertTrue(
                all(
                    item["entry"] == selected["entry"]
                    for item in narrowed_payload["chains"]
                )
            )

            batched = run_log(
                root,
                "findings",
                "batch",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--projection",
                payload["projection_id"],
                "--entry",
                selected["entry"],
                "--chain",
                selected["chain_id"],
            )
            self.assertEqual(batched.returncode, 0, batched.stderr)
            batch_payload = json.loads(batched.stdout)
            self.assertEqual(batch_payload["schema"], "research-log-findings-batch/1")
            check_id = batch_payload["batch"]["findings"][0]["identity"]

            shown = run_log(
                root,
                "findings",
                "show",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--id",
                check_id,
            )

            self.assertEqual(shown.returncode, 0, shown.stderr)
            finding = json.loads(shown.stdout)
            self.assertEqual(finding["schema"], "research-log-finding/1")
            self.assertEqual(finding["finding"]["identity"], check_id)
            self.assertEqual(result_path.read_bytes(), before)

    def test_list_returns_every_matching_chain_without_a_fifty_group_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            checks = []
            for number in range(51):
                subject = f"data/item-{number:03}.csv"
                checks.append(
                    RESULTS.MechanicalCheck(
                        f"orphan:e001:{number:03}",
                        RESULTS.CheckScope.ORPHAN,
                        RESULTS.CheckStatus.FAIL,
                        subject,
                        failure=RESULTS.FailurePayload(
                            "orphan.material.unused", subject, {}, "Hygiene"
                        ),
                    )
                )
            checks.append(
                RESULTS.MechanicalCheck(
                    "orphan:e001:duplicate",
                    RESULTS.CheckScope.ORPHAN,
                    RESULTS.CheckStatus.FAIL,
                    "data/item-000.csv",
                    failure=RESULTS.FailurePayload(
                        "orphan.material.unused",
                        "data/item-000.csv",
                        {},
                        "Hygiene",
                    ),
                )
            )
            record = RESULTS.MechanicalGeneratedRecord.build(
                summary.resolve().as_posix(),
                "test-rules",
                "2026-09-05",
                checks,
            )
            result_path = summary.with_suffix("") / "validation/results.json"
            write(result_path, record.canonical_json() + "\n")
            unresolved = [
                {
                    "chain_id": f"unresolved-{number:03}",
                    "entry": "e001",
                    "findings": [
                        {
                            "code": "orphan.material.unused",
                            "dependencies": [],
                            "identity": f"orphan:e001:{number:03}",
                            "observed": {},
                            "rule": "Hygiene",
                            "scope": "orphan",
                            "status": "fail",
                            "subject": f"data/item-{number:03}.csv",
                        }
                    ],
                    "reason": "finding_scope_unresolved",
                }
                for number in range(51)
            ]
            body = {
                "chains": [],
                "record_identity": hashlib.sha256(
                    record.canonical_json().encode("utf-8")
                ).hexdigest(),
                "result_date": record.result_date,
                "rules_version": record.rules_version,
                "schema": "research-log-batch-projection/1",
                "source_identity": "source",
                "summary": record.summary,
                "unresolved": unresolved,
            }
            body["projection_id"] = hashlib.sha256(
                json.dumps(
                    body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            write(
                summary.with_suffix("") / "validation/batches.json",
                json.dumps(body) + "\n",
            )

            completed = run_log(
                root,
                "findings",
                "list",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["matched_chains"], 51)
            self.assertEqual(len(payload["chains"]), 51)
            self.assertEqual(payload["chains"][0]["subjects"], ["data/item-000.csv"])

    def test_expected_query_failures_use_precise_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))

            missing = run_log(
                root, "findings", "list", "--format", "json", "--path", log_path
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("findings.result.missing", missing.stderr)

            result_path = summary.with_suffix("") / "validation/results.json"
            write(result_path, '{"schema":"research-log-mechanical/2"}\n')
            unsupported = run_log(
                root, "findings", "list", "--format", "json", "--path", log_path
            )
            self.assertEqual(unsupported.returncode, 2)
            self.assertIn("findings.result.schema_unsupported", unsupported.stderr)

            write(result_path, "{not json}\n")
            malformed = run_log(
                root, "findings", "list", "--format", "json", "--path", log_path
            )
            self.assertEqual(malformed.returncode, 2)
            self.assertIn("findings.result.malformed", malformed.stderr)

            record = RESULTS.MechanicalGeneratedRecord.build(
                summary.resolve().as_posix(), "test-rules", "2026-09-05", ()
            )
            write(result_path, record.canonical_json() + "\n")
            unavailable = run_log(
                root, "findings", "list", "--format", "json", "--path", log_path
            )
            self.assertEqual(unavailable.returncode, 2)
            self.assertIn("findings.projection_unavailable", unavailable.stderr)

    def test_query_rejects_malformed_nested_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))
            completed = run_log(
                root, "validate", "--format", "json", "--path", log_path
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            projection_path = summary.with_suffix("") / "validation/batches.json"
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["chains"][0]["commands"] = ["malformed"]
            body = {
                key: value
                for key, value in projection.items()
                if key != "projection_id"
            }
            projection["projection_id"] = hashlib.sha256(
                json.dumps(
                    body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            write(projection_path, json.dumps(projection) + "\n")

            queried = run_log(
                root, "findings", "list", "--format", "json", "--path", log_path
            )

            self.assertEqual(queried.returncode, 2)
            self.assertIn("findings.projection.malformed", queried.stderr)

    def test_show_distinguishes_duplicate_unknown_and_nonfinding_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))
            completed = run_log(
                root, "validate", "--format", "json", "--path", log_path
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = summary.with_suffix("") / "validation/results.json"
            payload = json.loads(result_path.read_text())
            passing = next(
                check["identity"]
                for check in payload["checks"]
                if check["status"] == "pass"
            )

            unknown = run_log(
                root,
                "findings",
                "show",
                "--format",
                "json",
                "--path",
                log_path,
                "--id",
                "absent",
            )
            self.assertEqual(unknown.returncode, 2)
            self.assertIn("findings.id.unknown", unknown.stderr)
            not_finding = run_log(
                root,
                "findings",
                "show",
                "--format",
                "json",
                "--path",
                log_path,
                "--id",
                passing,
            )
            self.assertEqual(not_finding.returncode, 2)
            self.assertIn("findings.id.not_finding", not_finding.stderr)

            payload["checks"].append(payload["checks"][0])
            write(result_path, json.dumps(payload) + "\n")
            duplicate = run_log(
                root,
                "findings",
                "show",
                "--format",
                "json",
                "--path",
                log_path,
                "--id",
                payload["checks"][0]["identity"],
            )
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("findings.id.duplicate", duplicate.stderr)


if __name__ == "__main__":
    unittest.main()

"""Tests for the retained opt-in agent behavior evaluation tool."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import agent_behavior_eval as agent_eval

FIXTURES = Path(__file__).parent / "fixtures" / "agent_behavior_eval"


class AgentBehaviorEvalTests(unittest.TestCase):
    def test_event_fixture_exposes_thread_and_usage(self) -> None:
        summary = agent_eval.parse_event_lines(
            (FIXTURES / "events.jsonl").read_text(encoding="utf-8")
        )
        agent_eval.require_event_contract(summary, first_turn=True)
        self.assertEqual(summary["thread_id"], "trial-001")
        self.assertEqual(summary["usages"][0]["input_tokens"], 42)
        self.assertFalse(summary["compacted"])

    def test_session_fixture_reports_actual_peak(self) -> None:
        summary = agent_eval.parse_session_trace(
            (FIXTURES / "session.jsonl").read_text(encoding="utf-8")
        )
        agent_eval.require_session_contract(summary)
        self.assertEqual(summary["peak_input_tokens"], 84)
        self.assertEqual(summary["model_context_window"], 258400)
        self.assertAlmostEqual(summary["peak_context_fraction"], 84 / 258400)

    def test_compaction_fixture_reports_both_signals(self) -> None:
        summary = agent_eval.parse_session_trace(
            (FIXTURES / "session-compacted.jsonl").read_text(encoding="utf-8")
        )
        self.assertTrue(summary["compacted"])
        self.assertEqual(summary["compacted_records"], 1)
        self.assertEqual(summary["context_compacted_events"], 1)

    def test_session_observation_marks_compaction_boundary_once(self) -> None:
        ordinary = agent_eval.parse_session_trace(
            (FIXTURES / "session.jsonl").read_text(encoding="utf-8")
        )
        compacted = agent_eval.parse_session_trace(
            (FIXTURES / "session-compacted.jsonl").read_text(encoding="utf-8")
        )
        before, observed = agent_eval.session_observation(ordinary, 0)
        boundary, observed = agent_eval.session_observation(compacted, observed)
        after, observed = agent_eval.session_observation(compacted, observed)
        self.assertFalse(before["new_compaction"])
        self.assertTrue(boundary["new_compaction"])
        self.assertFalse(after["new_compaction"])
        self.assertEqual(observed, 1)

    def test_first_post_compaction_turn_supports_older_artifacts(self) -> None:
        turns = [
            {
                "id": "before",
                "index": 1,
                "session_observation": {"compacted": False},
            },
            {
                "id": "boundary",
                "index": 2,
                "session_observation": {"compacted": True},
            },
            {
                "id": "after",
                "index": 3,
                "session_observation": {"compacted": True},
            },
        ]
        self.assertEqual(
            agent_eval.find_first_post_compaction_turn(turns),
            {"id": "boundary", "index": 2},
        )

    def test_inspection_reports_legacy_compaction_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            turns = [
                {
                    "id": "before",
                    "index": 1,
                    "session_observation": {"compacted": False},
                },
                {
                    "id": "boundary",
                    "index": 2,
                    "session_observation": {"compacted": True},
                },
            ]
            (artifacts / "sequence-summary.json").write_text(
                json.dumps(
                    {
                        "artifact_schema_version": 1,
                        "name": "legacy",
                        "turns": turns,
                        "session": {
                            "compacted": True,
                            "peak_input_tokens": 100,
                            "model_context_window": 200,
                            "peak_context_fraction": 0.5,
                        },
                    }
                ),
                encoding="utf-8",
            )
            for index, turn in enumerate(turns, start=1):
                turn_dir = artifacts / f"turn-{index:02d}-{turn['id']}"
                (turn_dir / "state-before").mkdir(parents=True)
                (turn_dir / "state-after").mkdir()
                (turn_dir / "summary.json").write_text(
                    json.dumps(turn), encoding="utf-8"
                )
            report = agent_eval.inspect_artifacts(artifacts)
            self.assertEqual(
                report["first_post_compaction_turn"],
                {"id": "boundary", "index": 2},
            )

    def test_discovery_requires_one_snapshot_and_no_disabled_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / "snapshot" / "SKILL.md"
            disabled = root / "live" / "SKILL.md"
            prompt_input = f"- example: description (file: {expected.resolve()})"
            valid = agent_eval.verify_discovery(
                prompt_input, "example", expected, [disabled]
            )
            self.assertTrue(valid["verified"])
            quoted = prompt_input + f"\nUser text quotes {disabled.resolve()}"
            quoted_valid = agent_eval.verify_discovery(
                quoted, "example", expected, [disabled]
            )
            self.assertTrue(quoted_valid["verified"])
            encoded = json.dumps(
                [
                    {"content": prompt_input},
                    {"content": f"User text quotes {disabled.resolve()}"},
                ]
            )
            encoded_valid = agent_eval.verify_discovery(
                encoded, "example", expected, [disabled]
            )
            self.assertTrue(encoded_valid["verified"])
            aliased = (
                f"### Skill roots\n- `r7` = `{expected.parent.parent}`\n"
                "### Available skills\n"
                "- example: description (file: r7/snapshot/SKILL.md)"
            )
            aliased_valid = agent_eval.verify_discovery(
                aliased, "example", expected, [disabled]
            )
            self.assertTrue(aliased_valid["verified"])
            duplicate = prompt_input + f"\n- example: live (file: {disabled.resolve()})"
            invalid = agent_eval.verify_discovery(
                duplicate, "example", expected, [disabled]
            )
            self.assertFalse(invalid["verified"])

    def test_sequence_contract_resolves_prompts_and_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "prompt.txt").write_text("Proceed.", encoding="utf-8")
            sequence = root / "sequence.json"
            sequence.write_text(
                json.dumps(
                    {
                        "name": "example",
                        "turns": [{"id": "record", "prompt": "prompt.txt"}],
                    }
                ),
                encoding="utf-8",
            )
            loaded = agent_eval.load_sequence(sequence)
            self.assertEqual(
                loaded.turns[0].prompt_path, (root / "prompt.txt").resolve()
            )
            sequence.write_text(
                json.dumps(
                    {
                        "name": "duplicate",
                        "turns": [
                            {"id": "record", "prompt": "prompt.txt"},
                            {"id": "record", "prompt": "prompt.txt"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate turn id"):
                agent_eval.load_sequence(sequence)

    def test_state_comparison_includes_files_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = root / "before"
            after = root / "after"
            before.mkdir()
            after.mkdir()
            (before / "changed.txt").write_text("old", encoding="utf-8")
            (after / "changed.txt").write_text("new", encoding="utf-8")
            (before / "deleted.txt").write_text("gone", encoding="utf-8")
            (after / "added.txt").write_text("new", encoding="utf-8")
            (before / "link").symlink_to("old-target")
            (after / "link").symlink_to("new-target")
            self.assertEqual(
                agent_eval.compare_states(before, after),
                {
                    "changed": ["changed.txt", "link"],
                    "added": ["added.txt"],
                    "deleted": ["deleted.txt"],
                },
            )

    def test_snapshot_is_hashed_and_excludes_runtime_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "snapshot" / "example"
            source.mkdir()
            (source / "SKILL.md").write_text("# Example\n", encoding="utf-8")
            (source / ".DS_Store").write_bytes(b"noise")
            result = agent_eval.create_snapshot(source, destination)
            self.assertEqual(result["sha256"], agent_eval.tree_hash(destination))
            self.assertFalse((destination / ".DS_Store").exists())
            self.assertTrue((destination.parent / "example.snapshot.json").is_file())


if __name__ == "__main__":
    unittest.main()

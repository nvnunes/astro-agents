from __future__ import annotations

import copy
import unittest

from validation.reproduction_admission import repair_verification_exempt_findings


def command(identity, inputs, outputs):
    return {
        "identity": identity,
        "inputs": [{"path": path} for path in inputs],
        "outputs": [{"path": path} for path in outputs],
        "collections": [],
    }


def finding(producer, output, **changes):
    return {
        "identity": "finding:" + producer,
        "admission_effect": "chain",
        "affected_entries": ["e001"],
        "affected_chains": ["chain"],
        "status": "fail",
        "scope": "provenance",
        "code": "provenance.output.signature_mismatch",
        "subject": output,
        "observed": {"fields": ["code"], "producer": producer},
        **changes,
    }


class RepairAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.chain = {
            "commands": [
                command("upstream", [], ["/data/raw"]),
                command("middle", ["/data/raw"], ["/data/input"]),
                command("selected", ["/data/input"], ["/data/a", "/data/b"]),
                command("sibling", [], ["/data/sibling"]),
                command("downstream", ["/data/a", "/data/sibling"], ["/data/table"]),
            ],
            "findings": [
                finding(identity, path)
                for identity, path in (
                    ("upstream", "/data/raw"),
                    ("middle", "/data/input"),
                    ("selected", "/data/a"),
                    ("sibling", "/data/sibling"),
                    ("downstream", "/data/table"),
                )
            ],
        }

    def exempt(self, inputs=()):
        return repair_verification_exempt_findings(
            self.chain, outputs={"/data/a", "/data/b"}, retained_inputs=set(inputs)
        )

    def test_only_sibling_and_downstream_code_findings_are_exempt(self):
        before = copy.deepcopy(self.chain)
        self.assertEqual(self.exempt(), {"finding:sibling", "finding:downstream"})
        self.assertEqual(self.chain, before)

    def test_additional_recorded_inputs_protect_their_producer(self):
        self.assertEqual(self.exempt(["/data/sibling"]), {"finding:downstream"})

    def test_collection_members_and_directory_inputs_protect_producers(self):
        self.chain["commands"][2]["collections"] = [
            {
                "direction": "input",
                "root": "/collection",
                "members": ["/data/sibling"],
            }
        ]
        self.assertEqual(self.exempt(), {"finding:downstream"})
        self.assertEqual(self.exempt(["/data"]), set())

    def test_mixed_fields_and_other_validation_constraints_remain_blocking(self):
        for changes in (
            {"observed": {"fields": ["code", "inputs"], "producer": "sibling"}},
            {"observed": {"fields": ["inputs"], "producer": "sibling"}},
            {"code": "provenance.output.missing"},
            {"scope": "structure"},
            {"status": "unavailable"},
            {"admission_effect": "entry"},
            {"subject": "/unknown"},
            {"observed": {"fields": ["code"], "producer": "unknown"}},
        ):
            with self.subTest(changes=changes):
                self.chain["findings"] = [
                    finding("sibling", "/data/sibling", **changes)
                ]
                self.assertEqual(self.exempt(), set())

    def test_missing_or_ambiguous_ownership_fails_closed(self):
        self.chain["commands"].append(command("competitor", [], ["/data/sibling"]))
        self.assertEqual(self.exempt(), {"finding:downstream"})
        del self.chain["commands"][0]["inputs"]
        self.assertEqual(self.exempt(), set())


if __name__ == "__main__":
    unittest.main()

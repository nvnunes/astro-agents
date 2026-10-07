"""Retained concerns can be external while dated runs remain confined."""

import tempfile
import unittest
from pathlib import Path

from log_commands.reproduction_paths import (
    canonical_run_root,
    iter_canonical_run_roots,
    project_tmp_relative,
    resolve_reproduction_root,
    resolve_verification_root,
)


class RetainedConcernPathsTests(unittest.TestCase):
    def setUp(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        self.base = Path(owner.name).resolve()
        self.project = self.base / "project"
        (self.project / "tmp").mkdir(parents=True)

    def test_external_reproduction_keeps_logical_identity_and_bounded_scan(self):
        external = self.base / "scratch" / "reproduction"
        run = external / "2026-10-07" / "reproduce-study-run1"
        run.mkdir(parents=True)
        (self.project / "tmp" / "reproduction").symlink_to(external)
        logical = self.project / "tmp/reproduction/2026-10-07/reproduce-study-run1"
        self.assertEqual(
            canonical_run_root(logical, self.project, require_exists=True), run
        )
        self.assertEqual(
            project_tmp_relative(run, self.project),
            "tmp/reproduction/2026-10-07/reproduce-study-run1",
        )
        self.assertEqual(iter_canonical_run_roots(self.project, max_entries=2), (run,))
        with self.assertRaisesRegex(OSError, "scan limit"):
            iter_canonical_run_roots(self.project, max_entries=1)

    def test_broken_concern_links_never_create_external_targets(self):
        for name, resolver in (
            ("reproduction", resolve_reproduction_root),
            ("verification", resolve_verification_root),
        ):
            with self.subTest(name=name):
                missing = self.base / "disconnected" / name
                (self.project / "tmp" / name).symlink_to(missing)
                with self.assertRaisesRegex(OSError, "unavailable"):
                    resolver(self.project)
                self.assertFalse(missing.parent.exists())

    def test_missing_regular_concerns_remain_creatable_without_read_side_effects(self):
        self.assertEqual(
            resolve_reproduction_root(self.project), self.project / "tmp/reproduction"
        )
        self.assertEqual(
            resolve_verification_root(self.project), self.project / "tmp/verification"
        )
        self.assertEqual(iter_canonical_run_roots(self.project, max_entries=2), ())
        self.assertEqual(list((self.project / "tmp").iterdir()), [])

    def test_date_and_run_links_are_not_canonical_or_scanned(self):
        root = self.project / "tmp/reproduction"
        real = root / "2026-10-06" / "reproduce-study-run1"
        real.mkdir(parents=True)
        date_link = root / "2026-10-07"
        date_link.symlink_to(real.parent)
        run_link = real.parent / "reproduce-study-alias"
        run_link.symlink_to(real)
        for path in (date_link / real.name, run_link):
            with self.subTest(path=path):
                with self.assertRaisesRegex(OSError, "not a canonical"):
                    canonical_run_root(path, self.project, require_exists=True)
        self.assertEqual(
            iter_canonical_run_roots(self.project, max_entries=10), (real,)
        )

    def test_run_outside_reproduction_is_rejected_even_inside_tmp(self):
        path = self.project / "tmp/experiments/2026-10-07/reproduce-study-run1"
        path.mkdir(parents=True)
        with self.assertRaisesRegex(OSError, "outside the reproduction"):
            canonical_run_root(path, self.project, require_exists=True)

"""Comparison predicates retain bounded locations from already loaded values."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
from log_commands.reproduction_comparison import compare_artifacts
from PIL import Image
from scipy.io import savemat


class ComparisonDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def paths(self, suffix):
        return self.root / ("expected" + suffix), self.root / ("regenerated" + suffix)

    def difference(self, expected, regenerated, location):
        result = compare_artifacts(expected, regenerated)
        self.assertEqual(result.outcome, "changed")
        self.assertEqual(result.observed["difference"]["location"], location)
        return result.observed["difference"]

    def test_numpy_signed_zero_retains_exact_flat_index_and_scalar_spellings(self):
        expected, regenerated = self.paths(".npy")
        np.save(expected, np.array([np.nan, 0.0, 3.0]))
        np.save(regenerated, np.array([np.nan, -0.0, 3.0]))
        difference = self.difference(expected, regenerated, "array flat index 1")
        self.assertEqual(difference["expected"], "0.0")
        self.assertEqual(difference["regenerated"], "-0.0")

    def test_numpy_chunk_offset_and_structured_field_are_retained(self):
        from log_commands.reproduction_comparison import ARRAY_CHUNK_MEMBERS

        expected, regenerated = self.paths(".npy")
        first = np.zeros(ARRAY_CHUNK_MEMBERS + 2, dtype=[("value", "i4")])
        second = first.copy()
        second["value"][-1] = 7
        np.save(expected, first)
        np.save(regenerated, second)
        difference = self.difference(
            expected, regenerated, f"array.value flat index {ARRAY_CHUNK_MEMBERS + 1}"
        )
        self.assertEqual(difference["expected"], 0)
        self.assertEqual(difference["regenerated"], 7)

    def test_numpy_archive_retains_member_and_shape_difference(self):
        expected, regenerated = self.paths(".npz")
        np.savez(expected, signal=np.ones((2, 3)))
        np.savez(regenerated, signal=np.ones((3, 2)))
        difference = self.difference(expected, regenerated, "signal dtype/shape")
        self.assertEqual(difference["expected"]["items"]["shape"]["items"], [2, 3])
        self.assertEqual(difference["regenerated"]["items"]["shape"]["items"], [3, 2])

    def test_hdf5_retains_dataset_row_region_and_attribute_name(self):
        expected, regenerated = self.paths(".h5")
        with h5py.File(expected, "w") as db:
            db.create_dataset("signal", data=np.array([[1, 2], [3, 4]]))
            db.attrs["calibration"] = 1
        with h5py.File(regenerated, "w") as db:
            db.create_dataset("signal", data=np.array([[1, 2], [3, 9]]))
            db.attrs["calibration"] = 1
        difference = self.difference(
            expected, regenerated, "signal rows 0:2 flat index 3"
        )
        self.assertEqual(difference["regenerated"], 9)
        with h5py.File(regenerated, "a") as db:
            db.attrs["calibration"] = 2
        difference = self.difference(
            expected, regenerated, "/@calibration flat index 0"
        )
        self.assertEqual(difference["expected"], 1)
        self.assertEqual(difference["regenerated"], 2)

    def test_mat_retains_variable_and_scalar_location(self):
        expected, regenerated = self.paths(".mat")
        savemat(expected, {"signal": np.array([[1.0, 2.0]])})
        savemat(regenerated, {"signal": np.array([[1.0, 5.0]])})
        self.difference(expected, regenerated, "signal flat index 1")

    def test_image_retains_frame_region_and_changed_decoded_byte(self):
        expected, regenerated = self.paths(".png")
        first = Image.new("RGB", (2, 2), (0, 0, 0))
        second = first.copy()
        second.putpixel((1, 1), (0, 4, 0))
        first.save(expected)
        second.save(regenerated)
        difference = self.difference(expected, regenerated, "frame 0 rows 0:2 byte 10")
        self.assertEqual(difference["expected"], 0)
        self.assertEqual(difference["regenerated"], 4)

    def test_equal_nan_arrays_and_different_image_encodings_still_match(self):
        expected, regenerated = self.paths(".npy")
        np.save(expected, np.array([np.nan, 0.0]))
        np.save(regenerated, np.array([np.nan, 0.0]))
        self.assertEqual(compare_artifacts(expected, regenerated).outcome, "matched")
        expected, regenerated = self.paths(".png")
        image = Image.new("RGB", (2, 2), (1, 2, 3))
        image.save(expected, compress_level=0)
        image.save(regenerated, compress_level=9)
        result = compare_artifacts(expected, regenerated)
        self.assertEqual(result.outcome, "matched")
        self.assertNotIn("difference", result.observed)

    def test_malformed_array_retains_original_comparison_error(self):
        expected, regenerated = self.paths(".npy")
        np.save(expected, np.array([1, 2]))
        regenerated.write_bytes(b"not an array")
        result = compare_artifacts(expected, regenerated)
        self.assertEqual(result.outcome, "comparison_failed")
        self.assertEqual(result.reason, "comparator_error")
        self.assertEqual(result.observed["error"]["type"], "ValueError")
        self.assertTrue(result.observed["error"]["message"])

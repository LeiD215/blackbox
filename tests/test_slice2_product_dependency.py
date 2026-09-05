from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackbox_vnext.slice2 import dependency_pin
from blackbox_vnext.slice2.checkpoint import generate_checkpoint


class Slice2ProductDependencyTests(unittest.TestCase):
    def test_shipped_product_namespace_passes_without_bbx_package(self):
        result = dependency_pin.verify_slice1_dependency()
        self.assertTrue(result.ok, result.to_dict())
        self.assertNotIn("bbx", __import__("sys").modules)

    def test_one_byte_product_file_drift_fails_closed_and_blocks_checkpoint(self):
        package_root = Path(dependency_pin.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            copied = Path(td) / "product"
            shutil.copytree(package_root / "slice1", copied / "slice1")
            target = copied / "slice1" / "canonical.py"
            target.write_bytes(target.read_bytes() + b"\n# drift\n")
            with patch("blackbox_vnext.slice2._slice1_path.slice1_root", return_value=copied):
                result = dependency_pin.verify_slice1_dependency()
                self.assertFalse(result.ok)
                self.assertTrue(any(x["file"] == "slice1/canonical.py" for x in result.mismatches))
                with self.assertRaises(dependency_pin.DependencyDrift):
                    dependency_pin.require_pinned_slice1()

    def test_checkpoint_uses_green_guard(self):
        with tempfile.TemporaryDirectory() as td:
            events = Path(td) / ".blackbox" / "events"
            events.mkdir(parents=True)
            checkpoint = generate_checkpoint(events)
            self.assertTrue(checkpoint.checkpoint_id.startswith("cp-"))

    def test_missing_required_api_symbol_fails_closed(self):
        profile = dict(dependency_pin.SLICE1_API_PROFILE)
        profile["slice1/canonical.py"] = ["canonical_bytes", "missing_required_symbol"]
        with patch.object(dependency_pin, "SLICE1_API_PROFILE", profile):
            result = dependency_pin.verify_slice1_dependency()
            self.assertFalse(result.ok)
            self.assertIn("blackbox_vnext.slice1.canonical", result.api_mismatches)
            with self.assertRaises(dependency_pin.DependencyDrift):
                dependency_pin.require_pinned_slice1()

    def test_checkpoint_is_blocked_by_injected_product_drift(self):
        package_root = Path(dependency_pin.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            copied = Path(td) / "product"
            shutil.copytree(package_root / "slice1", copied / "slice1")
            target = copied / "slice1" / "canonical.py"
            target.write_bytes(target.read_bytes() + b"\n# drift\n")
            events = Path(td) / ".blackbox" / "events"
            events.mkdir(parents=True)
            with patch("blackbox_vnext.slice2._slice1_path.slice1_root", return_value=copied):
                with self.assertRaises(dependency_pin.DependencyDrift):
                    generate_checkpoint(events)
            self.assertEqual(list(events.glob("checkpoint*")), [])


if __name__ == "__main__":
    unittest.main()

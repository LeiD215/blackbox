from __future__ import annotations

import shutil
import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

from blackbox_vnext.slice4 import dependency_pin
from blackbox_vnext.slice4 import dispatch


class Slice4ProductDependencyTests(unittest.TestCase):
    def test_shipped_product_guard_passes_without_bbx_namespace(self):
        self.assertTrue(dependency_pin.verify_dependency().ok)

    def test_packaged_resource_is_committed_lf_image(self):
        package = Path(dependency_pin.__file__).resolve().parents[1]
        stage = package.parent.parent
        attributes = (stage / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("src/blackbox_vnext/data/*.json text eol=lf", attributes)
        self.assertIn("src/blackbox_vnext/**/*.py text eol=lf", attributes)
        resource = package / "data" / "subtypes.json"
        raw = resource.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertIn(
            hashlib.sha256(raw).hexdigest(),
            dependency_pin.SLICE0_FILE_SHA256.values(),
        )

    def test_crlf_resource_mutation_fails_closed(self):
        package = Path(dependency_pin.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            copied = Path(td) / "data"
            shutil.copytree(package / "data", copied)
            path = copied / "subtypes.json"
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            with patch("blackbox_vnext.slice4._slice1_path.slice0_root", return_value=copied):
                self.assertFalse(dependency_pin.verify_dependency().ok)
                with self.assertRaises(dependency_pin.DependencyDrift):
                    dependency_pin.require_pinned_dependency()

    def test_required_api_symbol_fails_closed(self):
        profile = dict(dependency_pin.SLICE1_API_PROFILE)
        profile["slice1/canonical.py"] = ["canonical_bytes", "missing_required_symbol"]
        with patch.object(dependency_pin, "SLICE1_API_PROFILE", profile):
            result = dependency_pin.verify_dependency()
            self.assertFalse(result.ok)
            self.assertTrue(result.api_mismatches)
            with self.assertRaises(dependency_pin.DependencyDrift):
                dependency_pin.require_pinned_dependency()

    def test_slice1_byte_drift_blocks_guard_and_public_entrypoint(self):
        package = Path(dependency_pin.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            copied = Path(td) / "package"
            shutil.copytree(package / "slice1", copied / "slice1")
            path = copied / "slice1" / "canonical.py"
            path.write_bytes(path.read_bytes() + b"\n# drift\n")
            events = Path(td) / "events"
            events.mkdir()
            with patch("blackbox_vnext.slice4._slice1_path.slice1_root", return_value=copied):
                self.assertFalse(dependency_pin.verify_dependency().ok)
                with self.assertRaises(dependency_pin.DependencyDrift):
                    dependency_pin.require_pinned_dependency()
                with self.assertRaises(dependency_pin.DependencyDrift):
                    dispatch.validate_authority(events)

    def test_schema_byte_drift_blocks_guard(self):
        package = Path(dependency_pin.__file__).resolve().parents[1]
        stage = package.parents[1]
        with tempfile.TemporaryDirectory() as td:
            schemas = Path(td) / "schemas"
            shutil.copytree(stage / "schemas", schemas)
            path = schemas / "subtypes.json"
            path.write_bytes(path.read_bytes() + b"\n")
            with patch("blackbox_vnext.slice4._slice1_path.slice0_root", return_value=schemas):
                self.assertFalse(dependency_pin.verify_dependency().ok)
                with self.assertRaises(dependency_pin.DependencyDrift):
                    dependency_pin.require_pinned_dependency()


if __name__ == "__main__":
    unittest.main()

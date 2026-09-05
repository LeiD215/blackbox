from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import blackbox_vnext
from blackbox_vnext import integrity
from blackbox_vnext.sidecar import render_status
from blackbox_vnext.subtypes import SubtypeRegistry


class ProductContractTests(unittest.TestCase):
    def test_legacy_fixed_paths_are_present(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "SKILL.md").is_file())
        self.assertTrue((root / "assets" / "STATUS_TEMPLATE.md").is_file())

    def test_package_is_self_contained(self):
        package = Path(blackbox_vnext.__file__).resolve().parent
        self.assertTrue((package / "data" / "subtypes.json").is_file())
        forbidden = ("hermes" + "-nas-experiment", "reference-implementation" + "-slice")
        for path in package.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertFalse(any(item in text for item in forbidden), path)

    def test_registry_loads_from_package_data(self):
        registry = SubtypeRegistry.load()
        self.assertTrue(registry.is_registered("dispatch"))

    def test_empty_offline_recovery_needs_no_git(self):
        with tempfile.TemporaryDirectory() as td:
            result = blackbox_vnext.recover(Path(td), SubtypeRegistry.load())
            self.assertEqual(result.state, "TRUSTED")
            self.assertFalse(result.git_required)
            self.assertFalse((Path(td) / ".git").exists())

    def test_sidecar_is_explicitly_non_authority(self):
        result = render_status("UNKNOWN", ("B", "A"))
        self.assertTrue(result.derived_non_authority)
        self.assertEqual(result.text, "state=UNKNOWN; blockers=A,B")

    def test_integrity_guard(self):
        self.assertEqual(integrity.verify(), {"ok": True, "files_checked": 1})

    def test_schemas_are_valid_json(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "schemas").glob("*.json"):
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from blackbox_vnext import dependency_pin as slice5_dependency_pin
from blackbox_vnext.slice2 import dependency_pin as slice2_dependency_pin
from blackbox_vnext.slice4 import dependency_pin as slice4_dependency_pin


MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "migration-manifest.json"
)


class LFCheckoutDomainTests(unittest.TestCase):
    def test_all_byte_pinned_python_sources_are_lf(self):
        stage = Path(__file__).resolve().parents[1]
        pinned_relative_paths = {
            *slice2_dependency_pin.SLICE1_FILE_SHA256,
            *slice4_dependency_pin.SLICE1_FILE_SHA256,
            *slice5_dependency_pin.FILE_SHA256,
        }
        python_paths = sorted(
            path for path in pinned_relative_paths if path.endswith(".py")
        )
        staging_paths = [
            f"src/blackbox_vnext/{path}" for path in python_paths
        ]
        self.assertEqual(len(python_paths), 22)
        completed = subprocess.run(
            [
                "git",
                "check-attr",
                "text",
                "eol",
                "--",
                *staging_paths,
            ],
            cwd=stage,
            check=True,
            capture_output=True,
            text=True,
        )
        attributes: dict[str, dict[str, str]] = {}
        for line in completed.stdout.splitlines():
            relative_path, attribute, value = line.split(": ")
            attributes.setdefault(relative_path, {})[attribute] = value
        for relative_path in python_paths:
            staging_path = f"src/blackbox_vnext/{relative_path}"
            self.assertEqual(attributes[staging_path]["text"], "set")
            self.assertEqual(attributes[staging_path]["eol"], "lf")

    def test_manifest_domain_files_are_pinned_to_lf(self):
        stage = Path(__file__).resolve().parents[1]
        paths = ("CHANGELOG.md", "assets/STATUS_TEMPLATE.md")
        completed = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", *paths],
            cwd=stage,
            check=True,
            capture_output=True,
            text=True,
        )
        attributes: dict[str, dict[str, str]] = {}
        for line in completed.stdout.splitlines():
            relative_path, attribute, value = line.split(": ")
            attributes.setdefault(relative_path, {})[attribute] = value
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        expected = {entry["target_path"]: entry["staged_sha256"] for entry in manifest["entries"]}
        for relative_path in paths:
            self.assertEqual(attributes[relative_path]["text"], "unset")
            self.assertEqual(attributes[relative_path]["eol"], "unspecified")
            data = (stage / relative_path).read_bytes()
            normalized = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
            self.assertEqual(normalized, expected[relative_path])


if __name__ == "__main__":
    unittest.main()

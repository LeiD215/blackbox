"""Mechanical guard: a manifest `preserve` entry must be the pinned Git blob."""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "migration-manifest.json"
)


class LegacyPreservationTests(unittest.TestCase):
    def test_manifest_is_source_staging_only(self):
        stage = Path(__file__).resolve().parents[1]
        self.assertFalse((stage / "MIGRATION-MANIFEST.json").exists())

    def test_all_declared_preserves_match_pinned_source_blobs(self):
        stage = Path(__file__).resolve().parents[1]
        if stage.parent.name != "productization-prep":
            self.skipTest("source-staging manifest is not packaged in product")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        preserved = [entry for entry in manifest["entries"] if entry["action"] == "preserve"]
        self.assertGreater(len(preserved), 0)
        for entry in preserved:
            with self.subTest(path=entry["target_path"]):
                working = stage / entry["target_path"]
                output = subprocess.check_output(["git", "ls-files", "-s", "--", str(working)], text=True)
                actual = output.split()[1]
                self.assertEqual(actual, entry["source_blob_sha"])

    def test_preservation_classification_is_independent_of_crlf_checkout(self):
        stage = Path(__file__).resolve().parents[1]
        if stage.parent.name != "productization-prep":
            self.skipTest("source-staging manifest is not packaged in product")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for entry in manifest["entries"]:
            with self.subTest(path=entry["target_path"]):
                working = (stage / entry["target_path"]).read_bytes()
                if b"\0" in working:
                    continue
                self.assertEqual(hashlib.sha256(working.replace(b"\r\n", b"\n")).hexdigest(), entry["staged_sha256"])


if __name__ == "__main__":
    unittest.main()

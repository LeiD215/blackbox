from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


STAGE = Path(__file__).resolve().parents[1]
PREP = STAGE.parent
EXCLUDED_PARTS = {
    ".git", "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv",
}


def _release_paths_from(root: Path) -> set[str]:
    paths: set[str] = set()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not any(
            part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in rel.parts
        ):
            paths.add(rel.as_posix())
    return paths


def _release_paths() -> set[str]:
    return _release_paths_from(STAGE)


def _validate_targets(targets: list[str], release_paths: set[str]) -> None:
    assert len(targets) == len(set(targets))
    assert set(targets) == release_paths
    for target in targets:
        assert (STAGE / target).is_file(), target
        parts = Path(target).parts
        assert not any(
            part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in parts
        ), target


class ManifestCoverageTests(unittest.TestCase):
    def test_manifest_targets_exist_once_and_cover_release_tree_exactly(self):
        manifest = json.loads((PREP / "MIGRATION-MANIFEST.json").read_text(encoding="utf-8"))
        targets = [entry["target_path"] for entry in manifest["entries"]]
        _validate_targets(targets, _release_paths())

    def test_git_metadata_is_not_release_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
            (root / "PRODUCT.md").write_bytes(b"release payload\n")
            self.assertEqual(_release_paths_from(root), {"PRODUCT.md"})

    def test_git_metadata_manifest_target_fails_closed(self):
        with self.assertRaises(AssertionError) as caught:
            _validate_targets([".git/HEAD"], {".git/HEAD"})
        self.assertIn(".git/HEAD", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from blackbox_vnext.canonical import sha256_canonical
from blackbox_vnext.cli import main, parser
from blackbox_vnext.slice1.observe import compute_workspace_baseline
from blackbox_vnext.receipt import validate_receipt_event


NOW = "2026-09-05T00:10:00Z"


class RuntimeCorrectionTests(unittest.TestCase):
    def test_independent_verify_requires_explicit_timestamps(self):
        with self.assertRaises(SystemExit):
            parser().parse_args(["independent-verify"])

    def test_independent_verify_emits_valid_receipt_and_rejects_bad_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            read_root = Path(td) / "read"
            read_root.mkdir()
            payload = b"independent payload\n"
            (read_root / "artifact.bin").write_bytes(payload)
            identity = "sha256:" + hashlib.sha256(payload).hexdigest()
            argv = [
                "independent-verify", "--root", str(root), "--read-root", str(read_root),
                "--read-path", "artifact.bin", "--target", "artifact:test", "--subject", "task:test",
                "--artifact-identity", "artifact:test", "--input-scope", "project:reference",
                "--expected-identity", identity, "--expected-algorithm", "sha256",
                "--executor", "agent:executor", "--claim-source", "workspace:claim",
                "--verifier", "agent:reviewer", "--source-id", "filesystem:independent",
                "--event-id", "evt-000102030405060708090a0b0c0d0e99",
                "--observed-at", NOW, "--now", NOW, "--recorded-at", NOW,
            ]
            # Capture the emitted canonical receipt without relying on a store write.
            from unittest.mock import patch
            with patch("builtins.print") as output:
                self.assertEqual(main(argv), 0)
            event = json.loads(output.call_args.args[0])
            self.assertTrue(validate_receipt_event(event).valid)
            bad = argv.copy(); bad[bad.index(NOW)] = "not-a-time"
            with patch("builtins.print") as output:
                self.assertEqual(main(bad), 2)
            self.assertIn("INVALID-INDEPENDENT-VERIFY-TIMESTAMP", output.call_args.args[0])

    def test_nested_governed_paths_are_posix_and_blackbox_is_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "nested").mkdir(parents=True)
            (root / "src" / "nested" / "unit.py").write_text("x = 1\n", encoding="utf-8")
            (root / ".blackbox").mkdir()
            (root / ".blackbox" / "ignored.py").write_text("x = 2\n", encoding="utf-8")
            profile = {"governed_paths": ["src/**/*.py", ".blackbox/**"], "fixed_exclusions": [".blackbox/**"]}
            baseline = compute_workspace_baseline(root, profile)
            self.assertEqual(["src/nested/unit.py"], sorted(baseline))

    def test_top_level_observe_forwards_recovery_flags(self):
        from unittest.mock import patch
        with patch("blackbox_vnext.cli._slice1", return_value=0) as invoke:
            self.assertEqual(main(["observe", "--root", "project", "--ack", "--rebaseline"]), 0)
        self.assertEqual(invoke.call_args.args[0], ["observe", str(Path("project").resolve()), "--ack", "--rebaseline"])

    def test_persisted_as_of_input_identity_has_exactly_one_prefix(self):
        event = {
            "schema_version": "0.3.2",
            "event_id": "evt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "type": "VERIFICATION",
            "subtype": "self-verification",
            "receipt_class": "self-verification",
            "actor": "agent:executor",
            "subject": "task:identity-prefix",
            "recorded_at": NOW,
            "prior_refs": {"parent": [], "supports": []},
            "extensions": {},
            "body": "explicit self evidence",
        }
        event["content_hash"] = sha256_canonical({k: v for k, v in event.items() if k != "content_hash"})
        for flag in ("--ack", "--rebaseline"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                source = root / "self.json"
                source.write_text(json.dumps(event), encoding="utf-8")
                self.assertEqual(main(["init", "--root", str(root)]), 0)
                self.assertEqual(main(["self-verify", "--root", str(root), "--event", str(source)]), 0)
                self.assertEqual(main(["observe", "--root", str(root), flag]), 0)
                baseline = json.loads((root / ".blackbox" / "baseline.json").read_text(encoding="utf-8"))
                identity = baseline["as_of_input_identity"]
                self.assertRegex(identity, r"^sha256:[0-9a-f]{64}$")
                self.assertEqual(identity, sha256_canonical(event["event_id"]))


if __name__ == "__main__":
    unittest.main()

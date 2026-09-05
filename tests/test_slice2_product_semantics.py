from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from blackbox_vnext.slice1.canonical import canonical_bytes, sha256_canonical
from blackbox_vnext.slice1.store import write_event
from blackbox_vnext.slice1.subtypes import SubtypeRegistry
from blackbox_vnext.slice2.checkpoint import checkpoint_to_bytes, generate_checkpoint
from blackbox_vnext.slice2.frontier import compute_frontier
from blackbox_vnext.slice2.manifest import build_manifest, recompute_integrity
from blackbox_vnext.slice2.projector import project
from blackbox_vnext.slice2.recovery import (
    FULL_CANONICAL_REPLAY_REQUIRED,
    PARTIAL_RECOVERY_PLAN,
    build_recovery_plan,
)
from blackbox_vnext.slice2.replay import replay
from blackbox_vnext.slice2.staleness import (
    STATE_CURRENT,
    STATE_STALE_ADDED,
    STATE_UNKNOWN,
    compute_staleness,
)
from blackbox_vnext.slice2.validate import validate
from blackbox_vnext.slice1.authority import CaseSAuthorityProfile
from blackbox_vnext.slice2 import checkpoint as checkpoint_module
from blackbox_vnext.slice2 import dependency_pin as dependency_pin_module


def _event_id(seed: str) -> str:
    return "evt-" + (seed.lower() + "0" * 32)[:32]


def _event(event_id: str, subject: str, subtype: str, type_: str,
           receipt_class: str, *, parent: list[str] | None = None,
           supports: list[str] | None = None, actor: str = "executor") -> dict:
    event = {
        "schema_version": "0.3.2",
        "event_id": event_id,
        "type": type_,
        "subtype": subtype,
        "receipt_class": receipt_class,
        "actor": actor,
        "subject": subject,
        "recorded_at": "2026-09-04T00:00:00Z",
        "prior_refs": {"parent": parent or [], "supports": supports or []},
        "extensions": {},
        "body": "product Slice2 semantic fixture",
    }
    event["content_hash"] = sha256_canonical(
        {key: value for key, value in event.items() if key != "content_hash"}
    )
    return event


class Slice2ProductSemanticsTests(unittest.TestCase):
    """Closure tests use only the shipped blackbox_vnext namespace."""

    def _corpus(self, root: Path, reverse: bool = False) -> tuple[Path, list[dict]]:
        events_dir = root / ".blackbox" / "events"
        events_dir.mkdir(parents=True)
        subject = "task:product-s2"
        dispatch = _event(
            _event_id("a1"), subject, "dispatch", "TASK", "claim",
            actor="human:owner",
        )
        executed = _event(
            _event_id("b2"), subject, "execution-result", "TASK", "claim",
            parent=[dispatch["event_id"]],
        )
        self_verified = _event(
            _event_id("c3"), subject, "self-verification", "VERIFICATION",
            "self-verification", supports=[executed["event_id"]],
        )
        completed = _event(
            _event_id("d4"), subject, "completion-claim", "TASK", "claim",
            parent=[executed["event_id"]], supports=[self_verified["event_id"]],
        )
        events = [dispatch, executed, self_verified, completed]
        for event in reversed(events) if reverse else events:
            write_event(json.dumps(event), events_dir=events_dir)
        return events_dir, events

    def test_deterministic_projection_frontier_manifest_checkpoint_and_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_dir, events = self._corpus(root)
            self.assertFalse((root / ".git").exists())
            for module in (checkpoint_module, dependency_pin_module):
                source = Path(module.__file__).read_text(encoding="utf-8")
                self.assertNotIn("hermes-nas-experiment", source)
                self.assertNotIn("reference-implementation-slice", source)

            first = project(events_dir).projection
            second = project(events_dir).projection
            self.assertEqual(canonical_bytes(first), canonical_bytes(second))
            self.assertEqual(first["header"]["kind"], "DERIVED/NON-AUTHORITY_PROJECTION")

            registry = SubtypeRegistry.load()
            authority = CaseSAuthorityProfile(registry=registry)
            frontier = compute_frontier(events, registry, authority)
            self.assertEqual(frontier.frontier, {
                "task:product-s2": events[-1]["event_id"],
            })

            manifest = build_manifest(events_dir, reg=registry)
            self.assertEqual(len(manifest.manifest), 4)
            self.assertEqual(manifest.integrity_sha256, recompute_integrity(manifest.manifest))
            checkpoint = generate_checkpoint(events_dir, reg=registry).checkpoint
            self.assertTrue(validate(checkpoint).ok)
            self.assertEqual(checkpoint["frontier"], frontier.frontier)
            self.assertTrue(replay(checkpoint, events_dir, reg=registry).is_reproducible)

    def test_checkpoint_and_projection_are_input_order_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_dir, _ = self._corpus(root / "first")
            second_dir, _ = self._corpus(root / "second", reverse=True)
            first = generate_checkpoint(first_dir).checkpoint
            second = generate_checkpoint(second_dir).checkpoint
            self.assertEqual(checkpoint_to_bytes(first), checkpoint_to_bytes(second))
            self.assertEqual(
                canonical_bytes(project(first_dir).projection),
                canonical_bytes(project(second_dir).projection),
            )

    def test_staleness_is_pair_set_based_and_detects_divergence(self):
        baseline = [{"event_id": _event_id("a"), "content_hash": "sha256:" + "1" * 64}]
        added = baseline + [{"event_id": _event_id("b"), "content_hash": "sha256:" + "2" * 64}]
        self.assertEqual(compute_staleness(baseline, baseline).state, STATE_CURRENT)
        self.assertEqual(compute_staleness(baseline, added).state, STATE_STALE_ADDED)
        divergent = [{"event_id": _event_id("a"), "content_hash": "sha256:" + "3" * 64}]
        self.assertEqual(compute_staleness(baseline, divergent).state, STATE_UNKNOWN)

    def test_recovery_uses_valid_checkpoint_and_missing_checkpoint_requires_full_replay(self):
        with tempfile.TemporaryDirectory() as td:
            events_dir, _ = self._corpus(Path(td))
            checkpoint = generate_checkpoint(events_dir).checkpoint
            recovered = build_recovery_plan(events_dir, checkpoint)
            missing = build_recovery_plan(events_dir, None)
            self.assertEqual(recovered.recommendation, PARTIAL_RECOVERY_PLAN)
            self.assertEqual(recovered.checkpoint_validity, "VALID")
            self.assertEqual(missing.recommendation, FULL_CANONICAL_REPLAY_REQUIRED)
            self.assertEqual(missing.checkpoint_validity, "MISSING")
            self.assertEqual(recovered.current_frontier, missing.full_canonical_replay_frontier)

    def test_checkpoint_input_manifest_hash_is_single_prefixed_and_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            events_dir, events = self._corpus(Path(td))
            projection = project(events_dir).projection
            expected_pairs = [
                {"event_id": event_id, "content_hash": content_hash}
                for event_id, content_hash in sorted(
                    (event["event_id"], event["content_hash"]) for event in events
                )
            ]
            expected_hash = sha256_canonical(expected_pairs)
            self.assertRegex(
                projection["input_identity"]["event_manifest_hash"],
                r"^sha256:[0-9a-f]{64}$",
            )
            self.assertEqual(expected_hash, projection["input_identity"]["event_manifest_hash"])

            checkpoint = generate_checkpoint(events_dir).checkpoint
            self.assertRegex(checkpoint["header"]["input_manifest_hash"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(expected_hash, checkpoint["header"]["input_manifest_hash"])
            self.assertTrue(validate(checkpoint).ok)
            self.assertTrue(replay(checkpoint, events_dir).is_reproducible)
            recovered = build_recovery_plan(events_dir, checkpoint)
            self.assertEqual(recovered.recommendation, PARTIAL_RECOVERY_PLAN)
            self.assertEqual(recovered.checkpoint_validity, "VALID")

            tampered = json.loads(json.dumps(checkpoint))
            tampered["input_event_manifest"][0]["content_hash"] = "sha256:" + "f" * 64
            self.assertFalse(validate(tampered).ok)


if __name__ == "__main__":
    unittest.main()

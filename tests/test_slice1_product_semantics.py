"""Focused F4 Slice1 closure tests against the shipped product namespace.

These use only ``blackbox_vnext`` package APIs and a temporary project-root
event store: they deliberately do not import a historical ``bbx*`` package or
consult Git.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from blackbox_vnext.slice1.canonical import canonical_bytes
from blackbox_vnext.slice1.fold import fold_subject
from blackbox_vnext.slice1.observe import (
    COVERED,
    ORPHAN,
    ObservedChange,
    classify_change,
    index_receipt_effect_bindings,
)
from blackbox_vnext.slice1.reingest import V_VALID, reingest_events_dir
from blackbox_vnext.slice1.store import WriteError, write_event
from blackbox_vnext.slice1.subtypes import SubtypeRegistry


def event(event_id: str, subtype: str, *, type_: str = "TASK",
          receipt_class: str = "claim", parent: list[str] | None = None,
          subject: str = "task:product-s1", extensions: dict | None = None) -> dict:
    """Make a fully canonical shipped-Slice1 event payload."""
    obj = {
        "schema_version": "0.3.2",
        "event_id": event_id,
        "content_hash": "sha256:" + "0" * 64,
        "actor": "agent:product-test",
        "type": type_,
        "subtype": subtype,
        "receipt_class": receipt_class,
        "subject": subject,
        "recorded_at": "2026-09-04T00:00:00Z",
        "prior_refs": {"parent": parent or [], "supports": []},
    }
    if extensions is not None:
        obj["extensions"] = extensions
    obj["content_hash"] = "sha256:" + hashlib.sha256(
        canonical_bytes({k: v for k, v in obj.items() if k != "content_hash"})
    ).hexdigest()
    return obj


class Slice1ProductSemanticTests(unittest.TestCase):
    def setUp(self):
        self.registry = SubtypeRegistry.load()

    def test_subtype_registry_accepts_frozen_combinations_and_rejects_invalid(self):
        valid = event("evt-00000000000000000000000000000001", "dispatch")
        invalid = {**valid, "type": "CHANGE"}
        self.assertTrue(self.registry.is_valid_combination("TASK", "dispatch", "claim"))
        self.assertIsNone(self.registry.classify_event(valid))
        label, reason = self.registry.classify_event(invalid)
        self.assertEqual("INVALID", label)
        self.assertIn("allowed", reason)

    def test_canonical_ingest_fold_and_reingest_are_idempotent_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_dir = Path(tmp) / ".blackbox" / "events"
            first = event("evt-00000000000000000000000000000001", "dispatch")
            second = event(
                "evt-00000000000000000000000000000002", "execution-result",
                parent=[first["event_id"]],
            )
            written_first = write_event(first, registry=self.registry, events_dir=events_dir)
            write_event(second, registry=self.registry, events_dir=events_dir)
            # An exact replay is a no-rewrite idempotent ingest, not a collision.
            self.assertEqual(written_first, write_event(first, registry=self.registry, events_dir=events_dir))
            result = reingest_events_dir(events_dir, self.registry)
            self.assertFalse(result.corpus_blocked)
            self.assertEqual([V_VALID, V_VALID], [v.status for v in result.verdicts])
            head, conflicts = fold_subject(
                [v.event for v in result.verdicts if v.event], self.registry, "task:product-s1"
            )
            self.assertEqual(second["event_id"], head)
            self.assertEqual([], conflicts)
            with self.assertRaisesRegex(WriteError, "EVENT_ID_COLLISION"):
                write_event(
                    event(first["event_id"], "dispatch", subject="task:other"),
                    registry=self.registry,
                    events_dir=events_dir,
                )

    def test_observation_receipt_boundary_distinguishes_covered_from_orphan(self):
        post = "sha256:" + "a" * 64
        receipt = event(
            "evt-00000000000000000000000000000003", "execution-result",
            parent=["evt-00000000000000000000000000000001"],
            extensions={"effect": [{"path": "governed.txt", "post_identity": post}]},
        )
        bindings = index_receipt_effect_bindings([receipt])
        covered = classify_change(
            ObservedChange("governed.txt", post), {}, bindings,
            {receipt["event_id"]: True}, {receipt["event_id"]: "verified-independent"},
        )
        orphan = classify_change(
            ObservedChange("unbound.txt", post), {}, bindings,
            {receipt["event_id"]: True}, {receipt["event_id"]: "verified-independent"},
        )
        self.assertEqual(COVERED, covered.category)
        self.assertEqual(receipt["event_id"], covered.matching_receipt_event_id)
        self.assertEqual(ORPHAN, orphan.category)
        self.assertIsNone(orphan.matching_receipt_event_id)

"""Focused product-namespace closure for the frozen Slice 4 profile.

The fixtures deliberately go through the shipped canonical writer and the
path-only Slice 4 entry points.  They must never import the historical bbx4
package or a parent Hermes checkout.
"""

from __future__ import annotations

import tempfile
import unittest
import hashlib
from pathlib import Path

from blackbox_vnext.canonical import sha256_canonical, stored_bytes
from blackbox_vnext.slice4 import dispatch, state
from blackbox_vnext.slice4.authority_profile import (
    ACTIONS, SURFACES, DEFAULT_HUMAN_PRINCIPALS, bootstrap_capabilities,
)


ROOT = DEFAULT_HUMAN_PRINCIPALS[0]
SURFACE = "project:reference"


def _event(*, event_id: str, type: str, subtype: str, actor: str,
           subject: str, extension: dict, parents: list[str] | None = None,
           supports: list[str] | None = None) -> dict:
    # Slice 0's canonical registry requires an opaque evt-<32-hex> identity.
    # Labels keep the test readable without weakening the on-disk corpus path.
    event_id = "evt-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:32]
    event = {
        "schema_version": "0.3.2", "event_id": event_id, "type": type,
        "subtype": subtype, "receipt_class": "claim", "actor": actor,
        "subject": subject, "recorded_at": "2026-09-04T00:00:00Z",
        "prior_refs": {"parent": list(parents or []), "supports": list(supports or [])},
        "extensions": extension, "body": "product Slice4 semantic fixture",
    }
    event["content_hash"] = sha256_canonical(event)
    return event


def _change(eid: str, actor: str, beneficiary: str, action: str, op: str = "grant",
            parents: list[str] | None = None, supports: list[str] | None = None) -> dict:
    return _event(event_id=eid, type="CHANGE", subtype="authority-change", actor=actor,
                  subject=f"authority:{beneficiary}", parents=parents, supports=supports,
                  extension={"authority_change": {"op": op, "beneficiary": beneficiary,
                                                    "action": action, "surface": SURFACE}})


def _dispatch(eid: str, actor: str, supports: list[str] | None = None) -> dict:
    return _event(event_id=eid, type="TASK", subtype="dispatch", actor=actor,
                  subject=f"task:{eid}", supports=supports,
                  extension={"dispatch": {"assignee": actor, "surface": SURFACE}})


def _resolution(eid: str, beneficiary: str, parents: list[str], selected: str,
                actor: str = ROOT, supports: list[str] | None = None) -> dict:
    return _event(event_id=eid, type="CHANGE", subtype="resolution", actor=actor,
                  subject=f"authority:{beneficiary}", parents=parents, supports=supports,
                  extension={"resolution": {"op": "select", "selected_head_event_id": selected}})


class Slice4ProductSemanticsTests(unittest.TestCase):
    def corpus(self, events: list[dict]):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name) / "events"
        directory.mkdir()
        for event in events:
            (directory / f"{event['event_id']}.json").write_bytes(stored_bytes(event))
        return directory

    def test_frozen_profile_is_exact_actions_surface_and_bootstrap_root(self):
        self.assertEqual(ACTIONS, frozenset({"task.dispatch", "authority.change"}))
        self.assertEqual(SURFACES, frozenset({SURFACE}))
        self.assertEqual(DEFAULT_HUMAN_PRINCIPALS, (ROOT,))
        self.assertEqual({(c.action, c.surface) for c in bootstrap_capabilities()},
                         {("task.dispatch", SURFACE), ("authority.change", SURFACE)})
        bootstrap = _dispatch("evt-bootstrap", ROOT)
        decision = dispatch.validate_dispatch(bootstrap["event_id"], self.corpus([bootstrap]))
        self.assertTrue(decision.authorized, decision)

    def test_delegation_requires_authority_change_but_can_exercise_exact_task_capability(self):
        grant_change = _change("evt-grant-change", ROOT, "executor:a", "authority.change")
        grant_task = _change("evt-grant-task", "executor:a", "executor:b", "task.dispatch",
                             supports=[grant_change["event_id"]])
        target = _dispatch("evt-delegated-dispatch", "executor:b", [grant_task["event_id"]])
        events = [grant_change, grant_task, target]
        result = state.derive_authority_state(self.corpus(events))
        self.assertTrue(result.has_capability("executor:b", "task.dispatch", SURFACE))
        self.assertFalse(result.has_capability("executor:b", "authority.change", SURFACE))
        self.assertTrue(dispatch.validate_dispatch(target["event_id"], self.corpus(events)).authorized)

    def test_historical_support_is_at_causal_cut_not_current_aggregate(self):
        grant = _change("evt-historical-grant", ROOT, "executor:worker", "task.dispatch")
        target = _dispatch("evt-historical-dispatch", "executor:worker", [grant["event_id"]])
        revoke = _change("evt-later-revoke", ROOT, "executor:worker", "task.dispatch", "revoke",
                         [grant["event_id"]])
        corpus = self.corpus([grant, target, revoke])
        self.assertTrue(dispatch.validate_dispatch(target["event_id"], corpus).authorized)
        self.assertFalse(state.derive_authority_state(corpus).has_capability(
            "executor:worker", "task.dispatch", SURFACE))

    def test_exact_current_parent_and_only_eligible_siblings_create_conflict(self):
        genesis = _change("evt-parent-genesis", ROOT, "executor:w", "task.dispatch")
        current = _change("evt-parent-current", ROOT, "executor:w", "task.dispatch", "revoke",
                          [genesis["event_id"]])
        stale = _change("evt-parent-stale", ROOT, "executor:w", "task.dispatch",
                        parents=[genesis["event_id"]])
        stale_state = state.derive_authority_state(self.corpus([genesis, current, stale]))
        self.assertFalse(stale_state.has_capability("executor:w", "task.dispatch", SURFACE))
        # Two eligible children at one immutable pre-mutation frontier conflict;
        # an unrelated stream never joins that conflict set.
        fork_root = _change("evt-fork-root", ROOT, "executor:f", "task.dispatch")
        left = _change("evt-fork-left", ROOT, "executor:f", "task.dispatch", "revoke", [fork_root["event_id"]])
        right = _change("evt-fork-right", ROOT, "executor:f", "authority.change", parents=[fork_root["event_id"]])
        unrelated = _change("evt-unrelated", ROOT, "executor:other", "task.dispatch")
        folded = state.derive_authority_state(self.corpus([fork_root, left, right, unrelated]))
        self.assertTrue(folded.is_conflict("executor:f"))
        self.assertFalse(folded.is_conflict("executor:other"))

    def test_resolution_requires_exact_conflict_set_and_becomes_historical_head(self):
        resolver_grant = _change("evt-resolution-resolver", ROOT, "executor:a", "authority.change")
        left = _change("evt-resolution-left", "executor:a", "executor:r", "task.dispatch",
                       supports=[resolver_grant["event_id"]])
        right = _change("evt-resolution-right", "executor:a", "executor:r", "authority.change",
                        supports=[resolver_grant["event_id"]])
        invalid = _resolution("evt-resolution-invalid", "executor:r", [left["event_id"]], left["event_id"])
        invalid_state = state.derive_authority_state(self.corpus([resolver_grant, left, right, invalid]))
        self.assertTrue(invalid_state.is_conflict("executor:r"))
        resolution = _resolution("evt-resolution-valid", "executor:r",
                                 [left["event_id"], right["event_id"]], left["event_id"],
                                 actor="executor:a", supports=[resolver_grant["event_id"]])
        corpus = self.corpus([resolver_grant, left, right, resolution])
        folded = state.derive_authority_state(corpus)
        self.assertFalse(folded.is_conflict("executor:r"))
        self.assertEqual(folded.streams["executor:r"].head_event_id, resolution["event_id"])
        self.assertTrue(folded.has_capability("executor:r", "task.dispatch", SURFACE))

    def test_j_registry_lifecycle_and_lexical_order_are_not_authority(self):
        unknown_action = _change("aaa-unknown-action", ROOT, "executor:j", "unknown.action")
        unknown_surface = _change("zzz-unknown-surface", ROOT, "executor:k", "task.dispatch")
        unknown_surface["extensions"]["authority_change"]["surface"] = "project:other"
        unknown_surface["content_hash"] = sha256_canonical({k: v for k, v in unknown_surface.items() if k != "content_hash"})
        folded = state.derive_authority_state(self.corpus([unknown_action, unknown_surface]))
        self.assertFalse(folded.has_capability("executor:j", "task.dispatch", SURFACE))
        self.assertFalse(folded.has_capability("executor:k", "task.dispatch", SURFACE))
        # A lexically earlier unrelated grant cannot authorize this actor; only
        # the causally cited, effective support can do so.
        unrelated = _change("aaa-unrelated", ROOT, "executor:else", "task.dispatch")
        unauthorized = _dispatch("zzz-no-lexical-authority", "executor:lex", [])
        self.assertFalse(dispatch.validate_dispatch(unauthorized["event_id"], self.corpus([
            unrelated, unauthorized,
        ])).authorized)


if __name__ == "__main__":
    unittest.main()

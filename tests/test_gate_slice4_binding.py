from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackbox_vnext import authority, canonical, gate
from blackbox_vnext.slice4 import dependency_pin, dispatch


def _green_dispatch_event() -> dict:
    event = {
        "schema_version": "0.3.2",
        "event_id": "evt-d10000000000000000000000000000a1",
        "type": "TASK",
        "subtype": "dispatch",
        "receipt_class": "claim",
        "actor": "human:owner",
        "subject": "task:evt-d10000000000000000000000000000a1",
        "recorded_at": "2026-08-31T00:00:00Z",
        "prior_refs": {"parent": [], "supports": []},
        "extensions": {
            "dispatch": {
                "assignee": "executor:worker",
                "surface": "project:reference",
            },
        },
        "body": "gate binding fixture",
    }
    event["content_hash"] = canonical.sha256_canonical(event)
    return event


def _issued_green_decision() -> dispatch.DispatchDecision:
    event = _green_dispatch_event()
    with tempfile.TemporaryDirectory() as td:
        events = Path(td) / "corpus"
        events.mkdir()
        (events / f"{event['event_id']}.json").write_bytes(canonical.stored_bytes(event))
        decision = dispatch.validate_dispatch(event["event_id"], events)
    assert decision.authorized and decision.reason == "OK"
    return decision


class GateSlice4BindingTests(unittest.TestCase):
    def test_actual_slice4_validation_can_authorize_gate(self):
        self.assertTrue(gate.authorization_from_slice4(_issued_green_decision()))

    def test_root_slice1_and_caller_constructed_values_are_rejected(self):
        self.assertFalse(gate.authorization_from_slice4(
            authority.DispatchDecision("evt", True, "OK")))
        self.assertFalse(gate.authorization_from_slice4(
            dispatch.DispatchDecision("evt", True, "OK")))
        for reason in ("STALE", "CONFLICT", "AUTHORITY-MISSING", "NON-GREEN"):
            self.assertFalse(gate.authorization_from_slice4(
                dispatch.DispatchDecision("evt", False, reason)))

    def test_slice4_dependency_drift_blocks_previously_issued_green_result(self):
        green = _issued_green_decision()
        package = Path(dependency_pin.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            copied = Path(td) / "package"
            shutil.copytree(package / "slice1", copied / "slice1")
            drifted = copied / "slice1" / "canonical.py"
            drifted.write_bytes(drifted.read_bytes() + b"\n# drift\n")
            with patch("blackbox_vnext.slice4._slice1_path.slice1_root", return_value=copied):
                with self.assertRaises(dependency_pin.DependencyDrift):
                    gate.authorization_from_slice4(green)


if __name__ == "__main__":
    unittest.main()

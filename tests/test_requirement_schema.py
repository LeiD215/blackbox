from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from blackbox_vnext import CanonicalRequirement, pre_release_check
from blackbox_vnext.cli import main


class RequirementSchemaTests(unittest.TestCase):
    def requirement(self, **changes) -> CanonicalRequirement:
        return replace(
            CanonicalRequirement(
                "effect:release-1", "project:reference", "release.bin",
                "task:release-1", "artifact:release-1", "project:reference",
                "text:green", "text", "agent:executor", "workspace:executor",
                "fixture://release.bin", "fixture:independent",
                "agent:reviewer", "release", True, True, True,
            ),
            **changes,
        )

    def blocker(self, result, code):
        self.assertEqual(result.verdict, "NON-GREEN")
        self.assertIn(code, {item.code for item in result.blockers})

    def test_unknown_and_malformed_effect_classes_fail_closed_before_authorization(self):
        for effect_class in ("RELEASE", "release ", "iam-admin", 7, None):
            with self.subTest(effect_class=effect_class):
                result = pre_release_check((self.requirement(effect_class=effect_class),), ())
                self.blocker(result, "INVALID-REQUIREMENT")
                self.assertNotIn("AUTHORIZATION-MISSING", {item.code for item in result.blockers})

    def test_local_is_still_the_low_assurance_profile_member(self):
        result = pre_release_check((self.requirement(effect_class="local"),), ())
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.blockers, ())

    def test_declared_string_and_boolean_field_types_fail_closed(self):
        invalid = [
            {"field": "effect_id", "value": 7},
            {"field": "scope", "value": None},
            {"field": "prior_authorized", "value": "false"},
            {"field": "lifecycle_ready", "value": 0},
            {"field": "lifecycle_ready", "value": 1},
            {"field": "completion_claimed", "value": None},
        ]
        for case in invalid:
            with self.subTest(**case):
                result = pre_release_check((self.requirement(**{case["field"]: case["value"]}),), ())
                self.blocker(result, "INVALID-REQUIREMENT")

    def test_boolean_controls_are_retained_without_schema_blockers(self):
        for value in (True, False):
            result = pre_release_check((self.requirement(lifecycle_ready=value),), ())
            self.blocker(result, "ASSURANCE-GAP")
            self.assertNotIn("INVALID-REQUIREMENT", {item.code for item in result.blockers})

    def test_cli_rejects_null_and_mixed_requirements_but_keeps_empty_scope_valid(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            empty = root / "empty.json"
            null = root / "null.json"
            mixed = root / "mixed.json"
            empty.write_text("[]", encoding="utf-8")
            null.write_text("[null]", encoding="utf-8")
            valid = asdict(self.requirement())
            mixed.write_text(json.dumps([valid, None]), encoding="utf-8")
            self.assertEqual(main(["pre-release-check", "--root", str(root), "--requirements", str(empty)]), 0)
            for path in (null, mixed):
                self.assertEqual(main(["pre-release-check", "--root", str(root), "--requirements", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()

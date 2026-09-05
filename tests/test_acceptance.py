from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from blackbox_vnext import (
    CanonicalRequirement,
    CorpusBlocker,
    FilesystemReadbackAdapter,
    FixtureReadbackAdapter,
    IdentityBinding,
    OtherEvidence,
    ReadbackError,
    authorization_from_slice4,
    make_receipt_event,
    pre_release_check,
    validate_receipt_event,
    verify_independent,
)
from blackbox_vnext import integrity as dependency_pin
from blackbox_vnext.cli import parser as _parser


NOW = "2026-09-03T08:00:00Z"
OLD = "2026-09-03T07:00:00Z"


class MandatoryScenarioTests(unittest.TestCase):
    def setUp(self):
        self.binding = IdentityBinding(
            target="release.bin", subject="task:release-1",
            artifact_identity="artifact:release-1", input_scope="project:reference",
            expected_identity="text:green", expected_algorithm="text",
            executor="agent:executor",
            claim_source="workspace:executor",
        )

    def observation(self, value="text:green", **changes):
        obs = FixtureReadbackAdapter(
            {"release.bin": value}, verifier="agent:reviewer",
            source_id="fixture:independent",
        ).read(self.binding, observed_at=NOW)
        return replace(obs, **changes)

    def receipt(self, observation=None, binding=None, **kwargs):
        return verify_independent(
            binding or self.binding, observation or self.observation(), now=NOW, **kwargs
        )

    def requirement(self, **changes):
        base = CanonicalRequirement(
            effect_id="effect:release-1", scope="project:reference",
            target=self.binding.target,
            subject=self.binding.subject,
            artifact_identity=self.binding.artifact_identity,
            input_scope=self.binding.input_scope,
            expected_identity=self.binding.expected_identity,
            expected_algorithm=self.binding.expected_algorithm,
            executor=self.binding.executor,
            claim_source=self.binding.claim_source,
            required_read_path="fixture://release.bin",
            required_source_id="fixture:independent",
            required_verifier="agent:reviewer",
            effect_class="release", prior_authorized=True,
            lifecycle_ready=True, completion_claimed=True,
        )
        return replace(base, **changes)

    def gate(self, evidence=(), requirement=None, **kwargs):
        canonical = tuple(
            make_receipt_event(item, event_id="evt-" + "a" * 32, recorded_at=NOW)
            if hasattr(item, "extension") else item
            for item in evidence
        )
        return pre_release_check((requirement or self.requirement(),), canonical, **kwargs)

    def assert_blocked(self, result, code=None):
        self.assertEqual(result.verdict, "NON-GREEN")
        if code:
            self.assertIn(code, {item.code for item in result.blockers})

    def test_s01_deterministic_independent_fixture_observation(self):
        a = self.observation()
        b = self.observation()
        self.assertEqual(a, b)
        self.assertTrue(a.transport_ok)

    def test_s02_transport_success_without_identity_match_not_pass(self):
        receipt = self.receipt(self.observation("wrong"))
        self.assertEqual(receipt.result, "FAIL")

    def test_s03_exact_match_is_eligible_independent_evidence(self):
        self.assertTrue(self.receipt().verified_independent)

    def test_s04_identity_mismatch_blocks_gate(self):
        self.assert_blocked(self.gate((self.receipt(self.observation("wrong")),)), "IDENTITY-MISMATCH")

    def test_s05_uncomparable_algorithm_is_unknown(self):
        receipt = self.receipt(self.observation(algorithm="sha512"))
        self.assertEqual((receipt.result, receipt.reason), ("UNKNOWN", "UNCOMPARABLE-IDENTITY"))

    def test_s06_narrative_only_cannot_verify(self):
        self.assert_blocked(self.gate((OtherEvidence("narrative", self.binding.subject),)), "ASSURANCE-GAP")

    def test_s07_self_verification_cannot_upgrade(self):
        receipt = self.receipt(self.observation(verifier=self.binding.executor))
        self.assertEqual(receipt.reason, "SELF-VERIFICATION")

    def test_s08_issuer_not_executor_is_insufficient(self):
        evidence = OtherEvidence("claim", self.binding.subject)
        self.assert_blocked(self.gate((evidence,)), "ASSURANCE-GAP")

    def test_s09_same_executor_and_path_fails_independence(self):
        obs = self.observation(
            verifier=self.binding.executor, read_path=self.binding.claim_source,
            source_id=self.binding.claim_source, independent_source=False,
        )
        self.assertFalse(self.receipt(obs).verified_independent)

    def test_s10_distinct_verifier_common_source_fails(self):
        obs = self.observation(source_id=self.binding.claim_source, independent_source=False)
        self.assertEqual(self.receipt(obs).reason, "COMMON-SOURCE")

    def test_s11_valid_independent_source_and_binding_succeeds(self):
        self.assertEqual(self.gate((self.receipt(),)).verdict, "PASS")

    def test_s12_wrong_subject_does_not_support_target(self):
        receipt = self.receipt(self.observation(subject="task:other"))
        self.assert_blocked(self.gate((receipt,)), "ASSURANCE-GAP")

    def test_s13_wrong_artifact_does_not_support_target(self):
        receipt = self.receipt(self.observation(artifact_identity="artifact:other"))
        self.assert_blocked(self.gate((receipt,)), "ASSURANCE-GAP")

    def test_s14_wrong_input_scope_does_not_support_target(self):
        receipt = self.receipt(self.observation(input_scope="project:other"))
        self.assert_blocked(self.gate((receipt,)), "ASSURANCE-GAP")

    def test_s15_stale_readback_blocks_green(self):
        receipt = self.receipt(self.observation(observed_at=OLD), max_age_seconds=300)
        self.assert_blocked(self.gate((receipt,)), "STALE")

    def test_s16_unavailable_adapter_is_assurance_gap(self):
        obs = FixtureReadbackAdapter({}, "agent:reviewer", available=False).read(
            self.binding, observed_at=NOW
        )
        self.assert_blocked(self.gate((self.receipt(obs),)), "ASSURANCE-GAP")

    def test_s17_production_self_only_is_non_green(self):
        req = self.requirement(effect_class="production")
        self.assert_blocked(self.gate((OtherEvidence("self-verification", req.subject),), req), "ASSURANCE-GAP")

    def test_s18_high_risk_requires_prior_authorization_and_independence(self):
        req = self.requirement(effect_class="high-risk", prior_authorized=False)
        self.assert_blocked(self.gate((self.receipt(),), req), "AUTHORIZATION-MISSING")

    def test_s19_security_boundary_requires_independence(self):
        req = self.requirement(effect_class="security")
        self.assert_blocked(self.gate((), req), "ASSURANCE-GAP")

    def test_s20_release_requires_exact_scope_and_artifact(self):
        wrong = self.receipt(self.observation(artifact_identity="artifact:other"))
        self.assert_blocked(self.gate((wrong,)), "ASSURANCE-GAP")

    def test_s21_gate_pass_is_derived_and_creates_no_receipt(self):
        result = self.gate((self.receipt(),))
        self.assertTrue(result.derived_non_authority)
        self.assertFalse(result.creates_acceptance_receipt)

    def test_s22_gate_result_observation_cannot_substitute(self):
        self.assert_blocked(self.gate((OtherEvidence("GATE-RESULT", self.binding.subject),)), "ASSURANCE-GAP")

    def test_s23_identity_readback_occurrence_cannot_substitute(self):
        self.assert_blocked(self.gate((OtherEvidence("IDENTITY-READBACK", self.binding.subject),)), "ASSURANCE-GAP")

    def test_s24_adapter_cannot_override_canonical_expected_identity(self):
        obs = self.observation("malicious-expected")
        failed = self.receipt(obs)
        self.assertEqual(failed.reason, "IDENTITY-MISMATCH")
        self.assertFalse(hasattr(obs, "expected_identity"))
        forged = replace(failed, result="PASS", reason="EXACT-INDEPENDENT-MATCH")
        self.assert_blocked(self.gate((forged,)), "ASSURANCE-GAP")

    def test_s25_caller_expected_mismatch_fails_closed(self):
        caller = replace(self.binding, expected_identity="caller:override")
        receipt = self.receipt(self.observation(), binding=caller)
        self.assert_blocked(self.gate((receipt,)), "ASSURANCE-GAP")

    def test_s26_unknown_stale_conflict_affected_scope_block(self):
        for code in ("UNKNOWN", "STALE", "CONFLICT"):
            with self.subTest(code=code):
                result = self.gate((self.receipt(),), corpus_blockers=(CorpusBlocker(code, self.requirement().scope, "state:x"),))
                self.assert_blocked(result, code)

    def test_s27_orphan_affected_scope_blocks(self):
        result = self.gate((self.receipt(),), corpus_blockers=(CorpusBlocker("ORPHAN", self.requirement().scope, "evt:x"),))
        self.assert_blocked(result, "ORPHAN")

    def test_s28_untrustworthy_corpus_never_sanitizes_to_green(self):
        for trust in ("INVALID", "UNKNOWN", "COLLISION", "LAYOUT-VIOLATION"):
            with self.subTest(trust=trust):
                self.assert_blocked(self.gate((self.receipt(),), corpus_trust=trust), "CORPUS-UNTRUSTWORTHY")

    def test_s29_unrelated_scope_blocker_is_isolated(self):
        result = self.gate((self.receipt(),), corpus_blockers=(CorpusBlocker("CONFLICT", "project:other", "x"),))
        self.assertEqual(result.verdict, "PASS")

    def test_s30_slice1_case_s_corruption_cannot_be_washed_green(self):
        state = CorpusBlocker("INVALID", self.requirement().scope, "case-s:baseline")
        self.assert_blocked(self.gate((self.receipt(),), corpus_blockers=(state,)), "INVALID")

    def test_s31_cache_deletion_does_not_change_result(self):
        one = self.gate((self.receipt(),), adapter_cache={"green": True})
        two = self.gate((self.receipt(),), adapter_cache=None)
        self.assertEqual(one, two)

    def test_s32_no_git_fixture_path_works(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "artifact.bin").write_bytes(b"payload")
            binding = replace(
                self.binding,
                expected_identity="sha256:239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5",
                expected_algorithm="sha256",
            )
            obs = FilesystemReadbackAdapter(root, "agent:reviewer").read(binding, "artifact.bin", observed_at=NOW)
            self.assertFalse((root / ".git").exists())
            self.assertTrue(self.receipt(obs, binding=binding).verified_independent)

    def test_s33_slice3_git_sidecar_is_non_authority(self):
        a = self.gate((self.receipt(),), git_sidecar={"status": "green"})
        b = self.gate((self.receipt(),), git_sidecar={"status": "red"})
        self.assertEqual(a, b)

    def test_s34_slice4_dispatch_authority_is_not_independence(self):
        from blackbox_vnext import canonical
        from blackbox_vnext.slice4 import dispatch
        event = {
            "schema_version": "0.3.2", "event_id": "evt-d10000000000000000000000000000a1",
            "type": "TASK", "subtype": "dispatch", "receipt_class": "claim",
            "actor": "human:owner", "subject": "task:evt-d10000000000000000000000000000a1",
            "recorded_at": NOW, "prior_refs": {"parent": [], "supports": []},
            "extensions": {"dispatch": {"assignee": "executor:worker", "surface": "project:reference"}},
            "body": "acceptance fixture",
        }
        event["content_hash"] = canonical.sha256_canonical(event)
        with tempfile.TemporaryDirectory() as td:
            events = Path(td) / "corpus"
            events.mkdir()
            (events / f"{event['event_id']}.json").write_bytes(canonical.stored_bytes(event))
            decision = dispatch.validate_dispatch(event["event_id"], events)
        self.assertTrue(authorization_from_slice4(decision))
        self.assert_blocked(self.gate(()), "ASSURANCE-GAP")

    def test_s35_authorized_dispatch_does_not_imply_acceptance(self):
        self.assert_blocked(self.gate((), self.requirement(prior_authorized=True)), "ASSURANCE-GAP")

    def test_s36_completion_claim_without_independence_is_non_green(self):
        self.assert_blocked(self.gate((), self.requirement(completion_claimed=True)), "ASSURANCE-GAP")

    def test_s37_independence_cannot_retroactively_authorize(self):
        req = self.requirement(effect_class="irreversible", prior_authorized=False)
        self.assert_blocked(self.gate((self.receipt(),), req), "AUTHORIZATION-MISSING")

    def test_s38_after_fact_readback_still_needs_prior_authorization(self):
        req = self.requirement(effect_class="production", prior_authorized=False)
        self.assert_blocked(self.gate((self.receipt(),), req), "AUTHORIZATION-MISSING")

    def test_s39_replay_and_input_order_are_deterministic(self):
        other = self.requirement(effect_id="effect:a", effect_class="local")
        event = make_receipt_event(self.receipt(), event_id="evt-" + "b" * 32, recorded_at=NOW)
        a = pre_release_check((self.requirement(), other), (event,))
        b = pre_release_check((other, self.requirement()), tuple(reversed((event,))))
        self.assertEqual(a, b)

    def test_s40_dependency_drift_blocks_semantic_operation(self):
        original = dependency_pin.FILE_SHA256[1]["blackbox_vnext/canonical.py"]
        with mock.patch.dict(dependency_pin.FILE_SHA256[1], {"blackbox_vnext/canonical.py": "0" * 64}):
            with self.assertRaises(dependency_pin.DependencyDrift):
                dependency_pin.gate_semantic_entrypoint()
        self.assertEqual(dependency_pin.FILE_SHA256[1]["blackbox_vnext/canonical.py"], original)

    def test_s41_strict_parser_rejects_malformed_profiles(self):
        receipt = self.receipt()
        event = make_receipt_event(receipt, event_id="evt-" + "1" * 32, recorded_at=NOW)
        self.assertTrue(validate_receipt_event(event).valid)
        cases = []
        duplicate = json.dumps(event).replace('{"schema_version"', '{"actor":"dup","schema_version"', 1)
        cases.append(duplicate)
        unknown = json.loads(json.dumps(event)); unknown["unknown"] = "x"; cases.append(unknown)
        surrogate = json.loads(json.dumps(event)); surrogate["extensions"]["independent_verification"]["provenance"] = "\ud800"; cases.append(surrogate)
        scalar = json.loads(json.dumps(event)); scalar["extensions"]["independent_verification"]["verifier"] = 7; cases.append(scalar)
        timestamp = json.loads(json.dumps(event)); timestamp["recorded_at"] = "not-time"; cases.append(timestamp)
        for case in cases:
            with self.subTest(case=type(case).__name__):
                self.assertFalse(validate_receipt_event(case).valid)

    def test_s42_adapter_and_cli_are_read_only(self):
        self.assertFalse(hasattr(FilesystemReadbackAdapter, "write"))
        with self.assertRaises(SystemExit):
            _parser().parse_args(["readback-file", "--write", "x"])

    def test_s43_path_and_symlink_escape_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            adapter = FilesystemReadbackAdapter(root, "agent:reviewer")
            with self.assertRaises(ReadbackError):
                adapter.read(self.binding, "../escape")
            outside = Path(td) / "outside"; outside.write_text("x")
            link = root / "link"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(ReadbackError):
                adapter.read(self.binding, "link")

    def test_s44_recorded_timestamp_cannot_refresh_stale_observation(self):
        stale = self.receipt(self.observation(observed_at=OLD), max_age_seconds=60)
        event = make_receipt_event(stale, event_id="evt-" + "2" * 32, recorded_at=NOW)
        parsed = validate_receipt_event(event)
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.receipt.reason, "STALE-READBACK")
        self.assert_blocked(self.gate((event,)), "STALE")

    def test_s45_conflicting_independent_observations_are_non_green(self):
        good = self.receipt()
        bad = self.receipt(self.observation("bad"))
        self.assert_blocked(self.gate((good, bad)), "CONFLICT")

    def test_s46_independent_fail_not_hidden_by_self_pass(self):
        bad = self.receipt(self.observation("bad"))
        self.assert_blocked(
            self.gate((bad, OtherEvidence("self-verification", self.binding.subject))),
            "IDENTITY-MISMATCH",
        )

    def test_s47_gate_reports_exact_blockers_and_identities(self):
        req = self.requirement(prior_authorized=False, lifecycle_ready=False)
        result = self.gate((), req)
        self.assertEqual({b.code for b in result.blockers}, {
            "ASSURANCE-GAP", "AUTHORIZATION-MISSING", "LIFECYCLE-NOT-READY"
        })
        self.assertTrue(all(b.effect_id == req.effect_id and b.identity for b in result.blockers))

    def test_s48_no_hidden_writable_acceptance_ssot(self):
        with tempfile.TemporaryDirectory() as td:
            before = set(Path(td).iterdir())
            result = self.gate((self.receipt(),), adapter_cache={"accepted": False})
            after = set(Path(td).iterdir())
        self.assertEqual(before, after)
        self.assertFalse(result.creates_acceptance_receipt)

    def test_l1_01_canonical_valid_receipt_is_eligible(self):
        event = make_receipt_event(self.receipt(), event_id="evt-" + "c" * 32, recorded_at=NOW)
        self.assertEqual(pre_release_check((self.requirement(),), (event,)).verdict, "PASS")

    def test_l1_02_raw_dataclass_cannot_satisfy_gate(self):
        receipt = self.receipt()
        self.assertTrue(receipt.verified_independent)
        self.assert_blocked(pre_release_check((self.requirement(),), (receipt,)), "ASSURANCE-GAP")

    def test_l1_03_bad_canonical_discriminator_or_hash_is_non_green(self):
        dependency_pin.gate_semantic_entrypoint()
        from blackbox_vnext.canonical import sha256_canonical
        original = make_receipt_event(self.receipt(), event_id="evt-" + "d" * 32, recorded_at=NOW)
        cases = []
        for field, value in (("subtype", "other"), ("receipt_class", "other")):
            event = json.loads(json.dumps(original)); event[field] = value
            event["content_hash"] = sha256_canonical(event)
            cases.append(event)
        event = json.loads(json.dumps(original)); event["content_hash"] = "sha256:" + "0" * 64
        cases.append(event)
        for event in cases:
            with self.subTest(field=event.get("subtype"), receipt_class=event.get("receipt_class")):
                self.assert_blocked(pre_release_check((self.requirement(),), (event,)), "ASSURANCE-GAP")

    def test_l1_04_validated_event_round_trip_is_deterministic(self):
        event = make_receipt_event(self.receipt(), event_id="evt-" + "e" * 32, recorded_at=NOW)
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        first = validate_receipt_event(encoded)
        second = validate_receipt_event(json.dumps(json.loads(encoded), sort_keys=True))
        self.assertTrue(first.valid and second.valid)
        self.assertEqual(first.receipt, second.receipt)

    def test_l2_01_wrong_target_is_non_green(self):
        binding = replace(self.binding, target="other.bin")
        obs = FixtureReadbackAdapter({"other.bin": "text:green"}, "agent:reviewer").read(binding, observed_at=NOW)
        self.assert_blocked(self.gate((self.receipt(obs, binding=binding),)), "ASSURANCE-GAP")

    def test_l2_02_wrong_read_path_is_non_green(self):
        self.assert_blocked(self.gate((self.receipt(self.observation(read_path="fixture://other")),)), "ASSURANCE-GAP")

    def test_l2_03_wrong_source_id_is_non_green(self):
        self.assert_blocked(self.gate((self.receipt(self.observation(source_id="fixture:other")),)), "ASSURANCE-GAP")

    def test_l2_04_wrong_required_verifier_is_non_green(self):
        self.assert_blocked(self.gate((self.receipt(self.observation(verifier="agent:other")),)), "ASSURANCE-GAP")

    def test_l2_05_exact_readback_profile_is_eligible(self):
        self.assertEqual(self.gate((self.receipt(),)).verdict, "PASS")

    def test_l3_01_unknown_evidence_order_is_deterministic(self):
        stale = self.receipt(self.observation(observed_at=OLD))
        unavailable = self.receipt(self.observation(transport_ok=False))
        a = self.gate((stale, unavailable))
        b = self.gate((unavailable, stale))
        self.assertEqual(a, b)
        self.assertEqual({item.code for item in a.blockers}, {"STALE", "ASSURANCE-GAP"})

    def test_l3_02_fail_evidence_order_is_deterministic(self):
        base = self.receipt(self.observation("bad"))
        a_receipt = replace(base, reason="A-FAILURE")
        b_receipt = replace(base, reason="B-FAILURE")
        self.assertEqual(self.gate((a_receipt, b_receipt)), self.gate((b_receipt, a_receipt)))

    def test_l3_03_conflict_is_deterministic(self):
        good = self.receipt()
        bad = self.receipt(self.observation("bad"))
        first = self.gate((good, bad))
        second = self.gate((bad, good))
        self.assertEqual(first, second)
        self.assert_blocked(first, "CONFLICT")

    def test_l3_04_requirement_and_evidence_permutations_are_deterministic(self):
        other = self.requirement(effect_id="effect:release-2")
        good = self.receipt()
        stale = self.receipt(self.observation(observed_at=OLD))
        events = tuple(
            make_receipt_event(item, event_id="evt-" + digit * 32, recorded_at=NOW)
            for item, digit in ((good, "6"), (stale, "7"))
        )
        a = pre_release_check((self.requirement(), other), events)
        b = pre_release_check((other, self.requirement()), tuple(reversed(events)))
        self.assertEqual(a, b)

    def test_l4_01_sha256_expected_cannot_compare_as_text(self):
        identity = "sha256:" + "1" * 64
        binding = replace(self.binding, expected_identity=identity, expected_algorithm="sha256")
        receipt = self.receipt(self.observation(value=identity, algorithm="text"), binding=binding)
        self.assertEqual((receipt.result, receipt.reason), ("UNKNOWN", "UNCOMPARABLE-IDENTITY"))

    def test_l4_02_text_expected_cannot_compare_as_sha256(self):
        receipt = self.receipt(self.observation(algorithm="sha256", observed_identity="text:green"))
        self.assertEqual((receipt.result, receipt.reason), ("UNKNOWN", "UNCOMPARABLE-IDENTITY"))

    def test_l4_03_absent_only_compares_with_absent(self):
        binding = replace(self.binding, expected_identity="absent", expected_algorithm="absent")
        good = FixtureReadbackAdapter({"release.bin": None}, "agent:reviewer").read(binding, observed_at=NOW)
        bad = replace(good, algorithm="text")
        self.assertEqual(self.receipt(good, binding=binding).result, "PASS")
        self.assertEqual(self.receipt(bad, binding=binding).reason, "UNCOMPARABLE-IDENTITY")

    def test_l4_04_correct_algorithm_pairs_work(self):
        self.assertEqual(self.receipt().result, "PASS")
        sha_binding = replace(
            self.binding,
            expected_identity="sha256:239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5",
            expected_algorithm="sha256",
        )
        sha_obs = FixtureReadbackAdapter({"release.bin": b"payload"}, "agent:reviewer").read(sha_binding, observed_at=NOW)
        self.assertEqual(self.receipt(sha_obs, binding=sha_binding).result, "PASS")
        absent_binding = replace(self.binding, expected_identity="absent", expected_algorithm="absent")
        absent_obs = FixtureReadbackAdapter({"release.bin": None}, "agent:reviewer").read(absent_binding, observed_at=NOW)
        self.assertEqual(self.receipt(absent_obs, binding=absent_binding).result, "PASS")


if __name__ == "__main__":
    unittest.main()

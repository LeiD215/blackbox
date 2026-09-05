"""Strict independent-verification receipt profile over the canonical envelope."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from .dependency_pin import gate_semantic_entrypoint



RESULTS = frozenset({"PASS", "FAIL", "UNKNOWN"})
ALGORITHMS = frozenset({"sha256", "text", "absent"})
EXTENSION_FIELDS = frozenset({
    "target", "subject", "artifact_identity", "input_scope",
    "expected_identity", "expected_algorithm", "observed_identity", "algorithm", "read_path",
    "source_id", "claim_source", "verifier", "executor", "independent_source",
    "transport_ok",
    "evidence_digest", "provenance", "observed_at", "result", "reason",
})
TOP_FIELDS = frozenset({
    "schema_version", "event_id", "content_hash", "actor", "type", "subtype",
    "receipt_class", "subject", "recorded_at", "prior_refs", "extensions",
})


def identity_value_valid(algorithm: str, value: str) -> bool:
    """Keep typed identity domains disjoint, including textual lookalikes."""
    if algorithm == "sha256":
        return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))
    if algorithm == "absent":
        return value == "absent"
    if algorithm == "text":
        return value != "absent" and not bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))
    return False


@dataclass(frozen=True)
class IdentityBinding:
    target: str
    subject: str
    artifact_identity: str
    input_scope: str
    expected_identity: str
    expected_algorithm: str
    executor: str
    claim_source: str


@dataclass(frozen=True)
class Observation:
    target: str
    subject: str
    artifact_identity: str
    input_scope: str
    algorithm: str
    observed_identity: str
    read_path: str
    source_id: str
    verifier: str
    executor: str
    independent_source: bool
    transport_ok: bool
    observed_at: str
    provenance: str


@dataclass(frozen=True)
class IndependentReceipt:
    target: str
    subject: str
    artifact_identity: str
    input_scope: str
    expected_identity: str
    expected_algorithm: str
    observed_identity: str
    algorithm: str
    read_path: str
    source_id: str
    claim_source: str
    verifier: str
    executor: str
    independent_source: bool
    transport_ok: bool
    evidence_digest: str
    provenance: str
    observed_at: str
    result: str
    reason: str
    profile_valid: bool = True

    @property
    def verified_independent(self) -> bool:
        return (
            self.profile_valid
            and self.result == "PASS"
            and self.reason == "EXACT-INDEPENDENT-MATCH"
            and self.transport_ok
            and self.observed_identity == self.expected_identity
            and self.algorithm == self.expected_algorithm
            and identity_value_valid(self.algorithm, self.observed_identity)
            and identity_value_valid(self.expected_algorithm, self.expected_identity)
            and self.verifier != self.executor
            and self.independent_source
            and self.source_id != self.claim_source
            and self.read_path != self.claim_source
            and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", self.evidence_digest))
        )

    def extension(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("profile_valid")
        return data


@dataclass(frozen=True)
class ReceiptValidation:
    valid: bool
    reason: str
    receipt: IndependentReceipt | None = None


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be an RFC3339 UTC string ending in Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC")
    return parsed


def _no_surrogates(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ValueError("lone surrogate")
    elif isinstance(value, dict):
        for key, child in value.items():
            _no_surrogates(key)
            _no_surrogates(child)
    elif isinstance(value, list):
        for child in value:
            _no_surrogates(child)


def strict_json_loads(value: str | bytes) -> dict[str, Any]:
    text = value.decode("utf-8", "strict") if isinstance(value, bytes) else value

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, child in pairs:
            if key in out:
                raise ValueError(f"duplicate key: {key}")
            out[key] = child
        return out

    parsed = json.loads(text, object_pairs_hook=pairs_hook)
    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON must be an object")
    _no_surrogates(parsed)
    return parsed


def _canonical_bytes(value: Any) -> bytes:
    from .canonical import canonical_bytes
    return canonical_bytes(value)


def observation_digest(observation: Observation) -> str:
    import hashlib
    material = asdict(observation)
    return "sha256:" + hashlib.sha256(_canonical_bytes(material)).hexdigest()


def verify_independent(
    binding: IdentityBinding,
    observation: Observation,
    *,
    now: str,
    max_age_seconds: int = 300,
) -> IndependentReceipt:
    gate_semantic_entrypoint()
    result = "PASS"
    reason = "EXACT-INDEPENDENT-MATCH"
    try:
        observed_time = _timestamp(observation.observed_at)
        current_time = _timestamp(now)
    except ValueError:
        observed_time = current_time = datetime.min.replace(tzinfo=timezone.utc)
        result, reason = "UNKNOWN", "INVALID-TIMESTAMP"

    bindings_match = (
        observation.target == binding.target
        and observation.subject == binding.subject
        and observation.artifact_identity == binding.artifact_identity
        and observation.input_scope == binding.input_scope
        and observation.executor == binding.executor
    )
    if not observation.transport_ok:
        result, reason = "UNKNOWN", "ADAPTER-UNAVAILABLE"
    elif not bindings_match:
        result, reason = "FAIL", "BINDING-MISMATCH"
    elif not observation.observed_identity:
        result, reason = "UNKNOWN", "RAW-IDENTITY-MISSING"
    elif observation.verifier == binding.executor:
        result, reason = "FAIL", "SELF-VERIFICATION"
    elif (
        not observation.independent_source
        or observation.source_id == binding.claim_source
        or observation.read_path == binding.claim_source
    ):
        result, reason = "FAIL", "COMMON-SOURCE"
    elif observation.algorithm not in ALGORITHMS or binding.expected_algorithm not in ALGORITHMS:
        result, reason = "UNKNOWN", "UNCOMPARABLE-IDENTITY"
    elif observation.algorithm != binding.expected_algorithm:
        result, reason = "UNKNOWN", "UNCOMPARABLE-IDENTITY"
    elif not identity_value_valid(binding.expected_algorithm, binding.expected_identity):
        result, reason = "UNKNOWN", "UNCOMPARABLE-IDENTITY"
    elif not identity_value_valid(observation.algorithm, observation.observed_identity):
        result, reason = "UNKNOWN", "UNCOMPARABLE-IDENTITY"
    elif current_time < observed_time or (current_time - observed_time).total_seconds() > max_age_seconds:
        result, reason = "UNKNOWN", "STALE-READBACK"
    elif observation.observed_identity != binding.expected_identity:
        result, reason = "FAIL", "IDENTITY-MISMATCH"

    return IndependentReceipt(
        target=binding.target,
        subject=observation.subject,
        artifact_identity=observation.artifact_identity,
        input_scope=observation.input_scope,
        expected_identity=binding.expected_identity,
        expected_algorithm=binding.expected_algorithm,
        observed_identity=observation.observed_identity,
        algorithm=observation.algorithm,
        read_path=observation.read_path,
        source_id=observation.source_id,
        claim_source=binding.claim_source,
        verifier=observation.verifier,
        executor=observation.executor,
        independent_source=observation.independent_source,
        transport_ok=observation.transport_ok,
        evidence_digest=observation_digest(observation),
        provenance=observation.provenance,
        observed_at=observation.observed_at,
        result=result,
        reason=reason,
    )


def make_receipt_event(
    receipt: IndependentReceipt,
    *, event_id: str,
    recorded_at: str,
    supports: tuple[str, ...] = (),
) -> dict[str, Any]:
    from .canonical import sha256_canonical
    event: dict[str, Any] = {
        "schema_version": "0.3.2",
        "event_id": event_id,
        "actor": receipt.verifier,
        "type": "VERIFICATION",
        "subtype": "independent-verification",
        "receipt_class": "independent-verification",
        "subject": receipt.subject,
        "recorded_at": recorded_at,
        "prior_refs": {"parent": [], "supports": list(supports)},
        "extensions": {"independent_verification": receipt.extension()},
    }
    event["content_hash"] = sha256_canonical(event)
    return event


def validate_receipt_event(value: str | bytes | dict[str, Any]) -> ReceiptValidation:
    gate_semantic_entrypoint()
    from .canonical import verify_content_hash
    from .validity import is_event_shape_ok, lexical_event_id_ok
    try:
        event = strict_json_loads(value) if isinstance(value, (str, bytes)) else value
        if not isinstance(event, dict):
            raise ValueError("event must be object")
        _no_surrogates(event)
        if set(event) != TOP_FIELDS:
            raise ValueError("unknown or missing top-level field")
        if not is_event_shape_ok(event) or not lexical_event_id_ok(event.get("event_id")):
            raise ValueError("invalid canonical envelope")
        if not verify_content_hash(event):
            raise ValueError("content hash mismatch")
        if (
            event.get("type"), event.get("subtype"), event.get("receipt_class")
        ) != ("VERIFICATION", "independent-verification", "independent-verification"):
            raise ValueError("wrong independent-verification discriminator")
        _timestamp(event.get("recorded_at"))
        extensions = event.get("extensions")
        if not isinstance(extensions, dict) or set(extensions) != {"independent_verification"}:
            raise ValueError("invalid extensions container")
        profile = extensions["independent_verification"]
        if not isinstance(profile, dict) or set(profile) != EXTENSION_FIELDS:
            raise ValueError("unknown or missing profile field")
        for key, child in profile.items():
            if key in ("independent_source", "transport_ok"):
                if type(child) is not bool:
                    raise ValueError(f"{key} must be bool")
            elif not isinstance(child, str) or not child:
                raise ValueError(f"profile field {key} must be non-empty string")
        if profile["result"] not in RESULTS:
            raise ValueError("invalid result")
        if profile["algorithm"] not in ALGORITHMS:
            raise ValueError("invalid algorithm")
        if profile["expected_algorithm"] not in ALGORITHMS:
            raise ValueError("invalid expected algorithm")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", profile["evidence_digest"]):
            raise ValueError("invalid evidence digest")
        _timestamp(profile["observed_at"])
        receipt = IndependentReceipt(**profile)
        if receipt.subject != event["subject"] or receipt.verifier != event["actor"]:
            raise ValueError("envelope/profile identity mismatch")
        return ReceiptValidation(True, "OK", receipt)
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        return ReceiptValidation(False, f"PROFILE-INVALID: {exc}")

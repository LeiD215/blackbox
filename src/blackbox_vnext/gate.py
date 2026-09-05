"""Deterministic non-authority pre-release/acceptance eligibility gate."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .receipt import (
    IndependentReceipt, Observation, identity_value_valid, observation_digest,
    validate_receipt_event,
)
from .dependency_pin import gate_semantic_entrypoint


INDEPENDENT_CLASSES = frozenset({
    "production", "external", "irreversible", "high-risk", "security",
    "authority-boundary", "release", "acceptance",
})
LOCAL_CLASSES = frozenset({"local"})
EFFECT_CLASSES = INDEPENDENT_CLASSES | LOCAL_CLASSES
PRIOR_AUTH_CLASSES = frozenset({
    "production", "external", "irreversible", "high-risk", "security",
    "authority-boundary", "release", "acceptance",
})
REQUIREMENT_STRING_FIELDS = (
    "effect_id", "scope", "target", "subject", "artifact_identity",
    "input_scope", "expected_identity", "expected_algorithm", "executor",
    "claim_source", "required_read_path", "required_source_id",
    "required_verifier", "effect_class",
)
REQUIREMENT_BOOLEAN_FIELDS = ("prior_authorized", "lifecycle_ready", "completion_claimed")
BLOCKING_STATES = frozenset({
    "UNKNOWN", "STALE", "CONFLICT", "ORPHAN", "INVALID", "COLLISION",
    "LAYOUT-VIOLATION",
})


@dataclass(frozen=True)
class CanonicalRequirement:
    effect_id: str
    scope: str
    target: str
    subject: str
    artifact_identity: str
    input_scope: str
    expected_identity: str
    expected_algorithm: str
    executor: str
    claim_source: str
    required_read_path: str
    required_source_id: str
    required_verifier: str
    effect_class: str
    prior_authorized: bool
    lifecycle_ready: bool
    completion_claimed: bool


@dataclass(frozen=True)
class CorpusBlocker:
    code: str
    scope: str
    identity: str


@dataclass(frozen=True)
class OtherEvidence:
    receipt_class: str
    subject: str
    result: str = "PASS"


@dataclass(frozen=True)
class GateBlocker:
    code: str
    effect_id: str
    scope: str
    identity: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    verdict: str
    blockers: tuple[GateBlocker, ...]
    affected_effects: tuple[str, ...]
    derived_non_authority: bool
    creates_acceptance_receipt: bool
    result_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "blockers": [asdict(item) for item in self.blockers],
            "affected_effects": list(self.affected_effects),
            "derived_non_authority": self.derived_non_authority,
            "creates_acceptance_receipt": self.creates_acceptance_receipt,
            "result_id": self.result_id,
        }


def authorization_from_slice4(decision: object) -> bool:
    """Consume only a current, issued Slice 4 dispatch authorization."""
    from .slice4.dispatch import is_gate_authorization
    return is_gate_authorization(decision)


def _requirement_schema_errors(requirement: CanonicalRequirement) -> tuple[str, ...]:
    errors = []
    for field in REQUIREMENT_STRING_FIELDS:
        if type(getattr(requirement, field)) is not str:
            errors.append(f"{field} must be a string")
    for field in REQUIREMENT_BOOLEAN_FIELDS:
        if type(getattr(requirement, field)) is not bool:
            errors.append(f"{field} must be a boolean")
    effect_class = requirement.effect_class
    if type(effect_class) is str and effect_class not in EFFECT_CLASSES:
        errors.append("effect_class is not in the allowed profile")
    return tuple(errors)


def _block(
    code: str, requirement: CanonicalRequirement, detail: str,
    identity: str | None = None,
) -> GateBlocker:
    return GateBlocker(
        code=code, effect_id=requirement.effect_id, scope=requirement.scope,
        identity=identity or requirement.artifact_identity, detail=detail,
    )


def _matches(receipt: IndependentReceipt, req: CanonicalRequirement) -> bool:
    return (
        receipt.target == req.target
        and receipt.subject == req.subject
        and receipt.artifact_identity == req.artifact_identity
        and receipt.input_scope == req.input_scope
        and receipt.expected_identity == req.expected_identity
        and receipt.expected_algorithm == req.expected_algorithm
        and receipt.executor == req.executor
        and receipt.claim_source == req.claim_source
        and receipt.read_path == req.required_read_path
        and receipt.source_id == req.required_source_id
        and receipt.verifier == req.required_verifier
    )


def _profile_valid(receipt: IndependentReceipt) -> bool:
    """Recheck the strict profile; never trust a caller-constructed PASS object."""
    if (
        not receipt.profile_valid
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt.evidence_digest)
        or receipt.verifier == receipt.executor
        or not receipt.independent_source
        or receipt.source_id == receipt.claim_source
        or receipt.read_path == receipt.claim_source
        or receipt.result not in {"PASS", "FAIL", "UNKNOWN"}
    ):
        return False
    if receipt.result == "PASS" and (
        not receipt.transport_ok
        or receipt.observed_identity != receipt.expected_identity
        or receipt.algorithm != receipt.expected_algorithm
        or not identity_value_valid(receipt.algorithm, receipt.observed_identity)
        or not identity_value_valid(receipt.expected_algorithm, receipt.expected_identity)
        or receipt.reason != "EXACT-INDEPENDENT-MATCH"
    ):
        return False
    observation = Observation(
        target=receipt.target,
        subject=receipt.subject,
        artifact_identity=receipt.artifact_identity,
        input_scope=receipt.input_scope,
        algorithm=receipt.algorithm,
        observed_identity=receipt.observed_identity,
        read_path=receipt.read_path,
        source_id=receipt.source_id,
        verifier=receipt.verifier,
        executor=receipt.executor,
        independent_source=receipt.independent_source,
        transport_ok=receipt.transport_ok,
        observed_at=receipt.observed_at,
        provenance=receipt.provenance,
    )
    return observation_digest(observation) == receipt.evidence_digest


def pre_release_check(
    requirements: Iterable[CanonicalRequirement],
    evidence: Iterable[str | bytes | dict[str, Any] | IndependentReceipt | OtherEvidence],
    *,
    corpus_trust: str = "TRUSTED",
    corpus_blockers: Iterable[CorpusBlocker] = (),
    adapter_cache: object = None,
    git_sidecar: object = None,
) -> GateResult:
    """Derive eligibility. Cache/sidecar inputs are intentionally non-authority."""
    gate_semantic_entrypoint()
    del adapter_cache, git_sidecar
    reqs = sorted(tuple(requirements), key=lambda item: item.effect_id)
    records: list[IndependentReceipt | OtherEvidence] = []
    for item in evidence:
        if isinstance(item, OtherEvidence):
            records.append(item)
        elif isinstance(item, (str, bytes, dict)):
            validated = validate_receipt_event(item)
            if validated.valid and validated.receipt is not None and _profile_valid(validated.receipt):
                records.append(validated.receipt)
        # A raw IndependentReceipt is deliberately ineligible: canonical envelope
        # validation is part of the evidence boundary, not a caller assertion.
    records.sort(key=lambda item: (
        type(item).__name__,
        repr(asdict(item)),
    ))
    state_blockers = tuple(corpus_blockers)
    blockers: list[GateBlocker] = []

    for req in reqs:
        schema_errors = _requirement_schema_errors(req)
        if schema_errors:
            blockers.append(GateBlocker(
                code="INVALID-REQUIREMENT",
                effect_id=req.effect_id if type(req.effect_id) is str else "",
                scope=req.scope if type(req.scope) is str else "",
                identity=req.artifact_identity if type(req.artifact_identity) is str else "",
                detail="; ".join(schema_errors),
            ))
            continue
        if corpus_trust != "TRUSTED":
            blockers.append(_block("CORPUS-UNTRUSTWORTHY", req, corpus_trust))
        for state in state_blockers:
            if state.code in BLOCKING_STATES and state.scope in ("*", req.scope):
                blockers.append(_block(state.code, req, "affected canonical scope", state.identity))
        if not req.lifecycle_ready:
            blockers.append(_block("LIFECYCLE-NOT-READY", req, "required parent/lifecycle state absent"))
        if req.effect_class in PRIOR_AUTH_CLASSES and not req.prior_authorized:
            blockers.append(_block("AUTHORIZATION-MISSING", req, "prior authorization required"))
        if not req.completion_claimed:
            blockers.append(_block("COMPLETION-MISSING", req, "claim does not establish completion"))

        if req.effect_class not in INDEPENDENT_CLASSES:
            continue
        independent = [
            item for item in records
            if isinstance(item, IndependentReceipt) and _matches(item, req) and _profile_valid(item)
        ]
        observed = {item.observed_identity for item in independent}
        outcomes = {item.result for item in independent}
        if len(observed) > 1 or ("PASS" in outcomes and "FAIL" in outcomes):
            blockers.append(_block("CONFLICT", req, "conflicting independent observations"))
            continue
        failures = sorted(
            (item for item in independent if item.result == "FAIL"),
            key=lambda item: (item.reason, item.evidence_digest),
        )
        if failures:
            for reason in sorted({item.reason for item in failures}):
                blockers.append(_block("IDENTITY-MISMATCH", req, reason))
            continue
        unknown = sorted(
            (item for item in independent if item.result == "UNKNOWN"),
            key=lambda item: (item.reason, item.evidence_digest),
        )
        if unknown:
            for reason in sorted({item.reason for item in unknown}):
                code = "STALE" if reason == "STALE-READBACK" else "ASSURANCE-GAP"
                blockers.append(_block(code, req, reason))
            continue
        passes = [item for item in independent if item.verified_independent]
        if not passes:
            blockers.append(_block("ASSURANCE-GAP", req, "exact independent evidence missing"))

    ordered = tuple(sorted(
        set(blockers),
        key=lambda item: (item.effect_id, item.code, item.scope, item.identity, item.detail),
    ))
    payload = {
        "verdict": "PASS" if not ordered else "NON-GREEN",
        "blockers": [asdict(item) for item in ordered],
        "affected_effects": [item.effect_id for item in reqs],
        "derived_non_authority": True,
        "creates_acceptance_receipt": False,
    }
    from .canonical import canonical_bytes
    result_id = "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return GateResult(
        verdict=payload["verdict"], blockers=ordered,
        affected_effects=tuple(payload["affected_effects"]),
        derived_non_authority=True, creates_acceptance_receipt=False,
        result_id=result_id,
    )

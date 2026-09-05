"""Blackbox vNext portable reference runtime."""

from .adapters import FilesystemReadbackAdapter, FixtureReadbackAdapter, ReadbackError
from .authority import CaseSAuthorityProfile, DispatchDecision
from .canonical import canonical_bytes, sha256_canonical, verify_content_hash
from .gate import (
    CanonicalRequirement, CorpusBlocker, GateResult, OtherEvidence,
    authorization_from_slice4, pre_release_check,
)
from .receipt import (
    IdentityBinding, IndependentReceipt, Observation, make_receipt_event,
    validate_receipt_event, verify_independent,
)
from .recovery import RecoveryResult, recover

__version__ = "2.0.0.dev0"

__all__ = [
    "CanonicalRequirement", "CaseSAuthorityProfile", "CorpusBlocker",
    "DispatchDecision", "FilesystemReadbackAdapter", "FixtureReadbackAdapter",
    "GateResult", "IdentityBinding", "IndependentReceipt", "Observation",
    "OtherEvidence", "ReadbackError", "RecoveryResult", "canonical_bytes",
    "authorization_from_slice4", "make_receipt_event", "pre_release_check", "recover", "sha256_canonical",
    "validate_receipt_event", "verify_content_hash", "verify_independent",
]

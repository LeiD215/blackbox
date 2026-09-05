"""Two-layer validity model (FORMAT / Design v0.1.4 G3).

This module keeps the explicit separation required by the spec:
  Layer 1: valid authorized receipt (event self-legality)
  Layer 2: governance effect / status-upgrade eligibility

A structurally/causally/authorized valid claim is RETAINED even if assurance is
insufficient. It is NOT relabeled invalid merely because COMPLETE/PASS is not yet
eligible.

Pure functions only; no I/O.
"""
from __future__ import annotations

from typing import Any

# Reserved slot keys in the canonical event object (per FORMAT F.4).
REQUIRED_TOP_FIELDS = (
    "schema_version",
    "event_id",
    "content_hash",
    "actor",
    "type",
    "subtype",
    "receipt_class",
    "subject",
    "recorded_at",
    "prior_refs",
)


def is_event_shape_ok(obj: Any) -> bool:
    """Top-level dict with all FORMAT F.4 required fields present + prior_refs shape."""
    if not isinstance(obj, dict):
        return False
    for k in REQUIRED_TOP_FIELDS:
        if k not in obj:
            return False
    pr = obj.get("prior_refs")
    if not isinstance(pr, dict):
        return False
    if not isinstance(pr.get("parent"), list) or not isinstance(pr.get("supports"), list):
        return False
    if not isinstance(obj.get("prior_refs", {}).get("parent"), list):
        return False
    return True


def lexical_event_id_ok(eid: Any) -> bool:
    """evt- + exactly 32 lowercase hex chars (FORMAT F.1)."""
    if not isinstance(eid, str):
        return False
    if not eid.startswith("evt-"):
        return False
    return len(eid) == 4 + 32 and all(c in "0123456789abcdef" for c in eid[4:])


def lexical_content_hash_ok(ch: Any) -> bool:
    """sha256: + exactly 64 lowercase hex chars (FORMAT F.1)."""
    if not isinstance(ch, str):
        return False
    if not ch.startswith("sha256:"):
        return False
    return len(ch) == 7 + 64 and all(c in "0123456789abcdef" for c in ch[7:])


def event_id_format_ref_ok(eid: str) -> bool:
    """FORMAT F.1: event_id is a reference lexical profile; this only checks
    format, not its uniqueness or semantic correctness."""
    return lexical_event_id_ok(eid)

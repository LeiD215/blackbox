"""S2-6 set-difference staleness.

Compares a candidate (typically a freshly-built checkpoint's
input_event_manifest) against a baseline (typically a previously-materialized
checkpoint's input_event_manifest) on (event_id, content_hash) pair-sets.

States (TASK S2-R5):
  - CURRENT       : every baseline pair appears in candidate with same hash,
                    candidate has no extra pairs.
  - STALE_REMOVED : baseline ⊄ candidate — events lost (no candidate-only adds)
  - STALE_ADDED   : candidate ⊄ baseline — events added (no baseline-only)
  - STALE_BOTH    : both STALE_ADDED and STALE_REMOVED hold (drift in both
                    directions). Both added_present AND removed_present.
  - UNKNOWN       : events appear in both but with different content_hash
                    (corruption / divergence).

The set-difference MUST NOT be used as authority input. It is purely a
recovery-index drift signal: "you have a checkpoint from T0, here is what
changed since then." It MUST NOT prove auth/completion.

TASK S2-R5.1: Reject malformed / duplicate manifest IDs before comparison
(no silent collapse through dict construction).
TASK S2-R5.2: Both-direction drift deterministically represented without
count-based winner. The state STALE_BOTH is used whenever both added and
removed are non-empty (even when counts differ).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import canonical as r1_canonical  # noqa: E402


# State constants
STATE_CURRENT = "CURRENT"
STATE_STALE_ADDED = "STALE_ADDED"
STATE_STALE_REMOVED = "STALE_REMOVED"
STATE_STALE_BOTH = "STALE_BOTH"
STATE_UNKNOWN = "UNKNOWN"

ALL_STATES = (
    STATE_CURRENT, STATE_STALE_ADDED, STATE_STALE_REMOVED,
    STATE_STALE_BOTH, STATE_UNKNOWN,
)


class StalenessInvalid(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class StalenessResult:
    state: str                       # CURRENT | STALE_ADDED | STALE_REMOVED | STALE_BOTH | UNKNOWN
    added: list[dict[str, str]]      # present in candidate, missing from baseline
    removed: list[dict[str, str]]    # present in baseline, missing from candidate
    diverged: list[dict[str, Any]]   # event_id present both, different content_hash
    added_present: bool              # True iff added non-empty
    removed_present: bool            # True iff removed non-empty
    summary: dict[str, int]

    @property
    def is_current(self) -> bool:
        return self.state == STATE_CURRENT


def _normalize_manifest(manifest: list[dict[str, str]]) -> list[dict[str, str]]:
    """Validate manifest shape; return canonical-ordered list (sorted by
    event_id only). Reject malformed / duplicate IDs."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for i, e in enumerate(manifest):
        if not isinstance(e, dict):
            raise StalenessInvalid(f"manifest[{i}] not object")
        eid = e.get("event_id")
        chash = e.get("content_hash")
        if not (isinstance(eid, str) and isinstance(chash, str)):
            raise StalenessInvalid(
                f"manifest[{i}] missing event_id/content_hash"
            )
        if eid in seen:
            raise StalenessInvalid(
                f"duplicate event_id {eid!r} at index {i} in manifest"
            )
        seen.add(eid)
        out.append({"event_id": eid, "content_hash": chash})
    out.sort(key=lambda x: x["event_id"])
    return out


def compute_staleness(baseline: list[dict[str, str]],
                      candidate: list[dict[str, str]]) -> StalenessResult:
    """Compare (event_id, content_hash) pair-sets between baseline and candidate.

    Rejects malformed/duplicate IDs (TASK S2-R5.1). Both-direction drift
    deterministically represented (TASK S2-R5.2): when both added and
    removed are non-empty, state = STALE_BOTH (no count-based winner).
    """
    b = _normalize_manifest(baseline)
    c = _normalize_manifest(candidate)
    b_by_id = {e["event_id"]: e["content_hash"] for e in b}
    c_by_id = {e["event_id"]: e["content_hash"] for e in c}
    b_ids = set(b_by_id)
    c_ids = set(c_by_id)
    added = [e for e in c if e["event_id"] not in b_ids]
    removed = [e for e in b if e["event_id"] not in c_ids]
    diverged: list[dict[str, Any]] = []
    for eid in sorted(b_ids & c_ids):
        if b_by_id[eid] != c_by_id[eid]:
            diverged.append({
                "event_id": eid,
                "baseline_content_hash": b_by_id[eid],
                "candidate_content_hash": c_by_id[eid],
            })

    added_present = bool(added)
    removed_present = bool(removed)
    summary = {
        "baseline_count": len(b),
        "candidate_count": len(c),
        "added_count": len(added),
        "removed_count": len(removed),
        "diverged_count": len(diverged),
    }
    # TASK S2-R5.2: divergence wins (UNKNOWN) over direction-only drift.
    if diverged:
        state = STATE_UNKNOWN
    elif added_present and removed_present:
        state = STATE_STALE_BOTH
    elif added_present:
        state = STATE_STALE_ADDED
    elif removed_present:
        state = STATE_STALE_REMOVED
    else:
        state = STATE_CURRENT
    return StalenessResult(
        state=state,
        added=added,
        removed=removed,
        diverged=diverged,
        added_present=added_present,
        removed_present=removed_present,
        summary=summary,
    )


def staleness_to_dict(result: StalenessResult) -> dict[str, Any]:
    return {
        "state": result.state,
        "added": list(result.added),
        "removed": list(result.removed),
        "diverged": list(result.diverged),
        "added_present": result.added_present,
        "removed_present": result.removed_present,
        "summary": dict(result.summary),
    }


def strict_manifest_from_corpus(manifest: list[dict[str, str]]) -> dict[str, Any]:
    """TASK S2-R5.3 checkpoint-status path: a wrapping helper that builds
    a current strict manifest from a corpus path. Returns either
    {"ok": True, "manifest": [...]} or {"ok": False, "state": "UNKNOWN/BLOCKED",
    "reason": "..."}. The caller uses this in checkpoint-status to refuse
    comparison on untrustworthy corpus."""
    try:
        normalized = _normalize_manifest(manifest)
    except StalenessInvalid as e:
        return {"ok": False, "state": STATE_UNKNOWN, "reason": str(e)}
    return {"ok": True, "manifest": normalized}

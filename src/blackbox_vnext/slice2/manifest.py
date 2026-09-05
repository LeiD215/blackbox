"""S2-2 enumerable manifest.

Manifest enumerates every canonical event in the controlled Slice 1 corpus
that passes strict ingest, as exact {event_id, content_hash} tuples.

Ordering: sort by event_id only — this ordering has zero causal / governance
meaning (per Slice 0 manifest schema + §S2-2).

Fail-closed: malformed/unreadable/unexpected-layout/duplicate-event_id/
collision corpus states block manifest generation rather than silently
omitting evidence.

`manifest_integrity_sha256` = sha256 over RFC8785/JCS canonical bytes of the
manifest array, no trailing LF; lexical form = bare 64 lowercase hex (per
frozen Slice 0 checkpoint schema).

Dependency gating (S2-I1): public semantic entrypoints
(`build_manifest`, `recompute_integrity`) call
`bbx2.dependency_pin.gate_semantic_entrypoint(strict=True)` on entry, so
drifted Slice 1 `reingest` / `canonical` bytes raise `DependencyDrift`
before any manifest/hash output is returned. Pure formatting helpers that
do NOT touch Slice 1 semantics remain ungated.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import reingest as r1_reingest  # noqa: E402
from blackbox_vnext.slice1 import canonical as r1_canonical  # noqa: E402
from blackbox_vnext.slice1 import subtypes as r1_subtypes  # noqa: E402

from . import dependency_pin as bbx2_dep


class ManifestBlocked(Exception):
    """Raised when corpus state prevents a trustworthy complete manifest."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class ManifestResult:
    manifest: list[dict[str, str]]     # [{event_id, content_hash}, ...]
    integrity_sha256: str              # bare 64 hex
    corpus_blocked: bool
    corpus_reason: str
    counts: dict[str, int]


def build_manifest(events_dir, reg: r1_subtypes.SubtypeRegistry | None = None,
                   ) -> ManifestResult:
    """Enumerate every canonical event that passed strict ingest.

    Refuses (raises ManifestBlocked) when corpus contains any INVALID /
    UNKNOWN / layout-violation / duplicate-event_id / collision state —
    these block checkpoint generation rather than silently omitting.

    Dependency drift raises DependencyDrift before any output is returned.
    """
    # S2-I1: fail-closed Slice 1 dependency gate.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    if reg is None:
        reg = r1_subtypes.SubtypeRegistry.load()
    result = r1_reingest.reingest_events_dir(events_dir, reg)
    counts = {r1_reingest.V_VALID: 0, r1_reingest.V_UNCLASSIFIED: 0,
              r1_reingest.V_INVALID: 0, r1_reingest.V_UNKNOWN: 0}
    for v in result.verdicts:
        counts[v.status] += 1
    if (counts[r1_reingest.V_INVALID] > 0
            or counts[r1_reingest.V_UNKNOWN] > 0
            or result.corpus_blocked):
        raise ManifestBlocked(
            f"corpus not trustworthy for manifest: "
            f"INVALID={counts[r1_reingest.V_INVALID]} "
            f"UNKNOWN={counts[r1_reingest.V_UNKNOWN]} "
            f"corpus_blocked={result.corpus_blocked} "
            f"reason={result.corpus_reason}"
        )
    entries: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for v in result.verdicts:
        if v.event is None:
            continue
        if v.status not in (r1_reingest.V_VALID, r1_reingest.V_UNCLASSIFIED):
            continue
        eid = v.event.get("event_id")
        chash = v.event.get("content_hash")
        if not (isinstance(eid, str) and isinstance(chash, str)):
            continue
        if eid in seen_ids:
            # Corpus reingest already blocks duplicates, but defense in depth.
            raise ManifestBlocked(
                f"duplicate event_id {eid} in trustworthy re-ingest set"
            )
        seen_ids.add(eid)
        entries.append({"event_id": eid, "content_hash": chash})
    entries.sort(key=lambda e: e["event_id"])  # deterministic, no meaning
    canonical = r1_canonical.canonical_bytes(entries)
    integrity = hashlib.sha256(canonical).hexdigest()
    return ManifestResult(
        manifest=entries,
        integrity_sha256=integrity,
        corpus_blocked=result.corpus_blocked,
        corpus_reason=result.corpus_reason,
        counts=counts,
    )


def recompute_integrity(manifest: list[dict[str, str]]) -> str:
    """Independent recomputation (no caching): sha256 over canonical bytes,
    no trailing LF. Used by validators and tests.

    Dependency drift raises DependencyDrift before any output is returned
    (consumes Slice 1 canonicalization, so gated per S2-I1)."""
    # S2-I1: fail-closed Slice 1 dependency gate (consumes r1_canonical).
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    canonical = r1_canonical.canonical_bytes(manifest)
    return hashlib.sha256(canonical).hexdigest()
"""S2-4 checkpoint generation.

Builds a Slice 0 frozen checkpoint from the deterministic projector +
manifest + frontier. Frozen Slice 0 schema pins:
  - checkpoint_id = `cp-` + bare 64 lowercase hex sha256
  - source_event_refs sorted by event_id only (no causal semantics)
  - frontier = subject -> single head event_id (FRONTIER_AMBIGUOUS refuses)
  - header.kind = `DERIVED/NON-AUTHORITY_RECOVERY_INDEX`

Self-reference boundary (strict, frozen):
  The checkpoint MUST NOT include its own checkpoint_id in the bytes used
  to compute its own checkpoint_id. Procedure:
    1. Build candidate payload without `header.checkpoint_id`.
    2. JCS-canonicalize the candidate.
    3. Compute sha256 over canonical bytes.
    4. Prefix `cp-`.
    5. Assign to header.checkpoint_id in the final checkpoint.

Checkpoint generation rules (TASK S2-R3):
  - REFUSE on: INVALID/UNKNOWN/untrustworthy corpus, event-id collision
    duplicate physical corpus, FRONTIER_AMBIGUOUS, schema/self-reference
    failure.
  - ALLOW structurally trustworthy governance-non-green corpus
    (UNCLASSIFIED/UNAUTHORIZED retained). The checkpoint preserves the
    non-green nature explicitly via header.input_governance_state +
    non_green_subjects + sensor_state, all under schema_valid
    additionalProperties:true at header level. The checkpoint itself
    remains derived/non-authority and cannot wash live authority green.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import canonical as r1_canonical  # noqa: E402
from blackbox_vnext.slice1 import reingest as r1_reingest  # noqa: E402
from blackbox_vnext.slice1 import subtypes as r1_subtypes  # noqa: E402
from blackbox_vnext.slice1 import authority as r1_authority  # noqa: E402

from . import FRAMEWORK_VERSION, GENERATOR_IDENTITY, SCHEMA_VERSION
from . import manifest as bbx2_manifest
from . import frontier as bbx2_frontier
from . import dependency_pin as bbx2_dep


class CheckpointBlocked(Exception):
    """Raised when checkpoint generation is refused (untrustworthy /
    ambiguous / schema / self-reference failure)."""

    def __init__(self, reason: str, detail: dict[str, Any] | None = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


@dataclass
class Checkpoint:
    checkpoint: dict[str, Any]
    checkpoint_id: str
    manifest_integrity_sha256: str
    # CLI output distinction: separate from governance green (TASK S2-R3.4)
    input_governance_state: str


def _candidate_payload(materialized: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical payload WITHOUT header.checkpoint_id (the
    self-reference boundary)."""
    candidate = copy.deepcopy(materialized)
    hdr = candidate.get("header", {})
    if "checkpoint_id" in hdr:
        hdr.pop("checkpoint_id")
    candidate["header"] = hdr
    return candidate


def _collect_events_for_frontier(events_dir, reg):
    """Re-collect tagged events (with __verdict_status) for frontier
    fold_subject calls."""
    result = r1_reingest.reingest_events_dir(events_dir, reg)
    events = []
    for v in result.verdicts:
        if v.event is None:
            continue
        if v.status not in (r1_reingest.V_VALID, r1_reingest.V_UNCLASSIFIED):
            continue
        ev = dict(v.event)
        ev["__verdict_status"] = v.status
        events.append(ev)
    return events


def _materialize(events_dir, reg, projection: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
    """Assemble the candidate checkpoint payload from projector + manifest +
    frontier (without checkpoint_id).

    When `projection` is supplied (already computed), reuses it instead of
    running the projector again. This lets the caller reuse an existing
    projection (avoids duplicate Slice 1 sensor/T3 work in live mode).
    """
    from . import projector as bbx2_projector
    # S2-H2: gate at every internal semantic entrypoint too.
    bbx2_dep.gate_semantic_entrypoint(strict=True)

    if projection is None:
        proj = bbx2_projector.project(events_dir, reg=reg)
        projection = proj.projection

    # Build manifest (refuses on INVALID/UNKNOWN/corpus_blocked)
    manifest_result = bbx2_manifest.build_manifest(events_dir, reg=reg)
    manifest = manifest_result.manifest
    integrity = manifest_result.integrity_sha256

    # Derive frontier (refuses on FRONTIER_AMBIGUOUS) with same authority
    auth = r1_authority.CaseSAuthorityProfile(registry=reg)
    events = _collect_events_for_frontier(events_dir, reg)
    fr = bbx2_frontier.compute_frontier(events, reg, auth)
    bbx2_frontier.enforce_single_head(fr)

    frontier = dict(fr.frontier)
    source_event_refs = sorted(e["event_id"] for e in manifest)

    # S2-H1: separate lifecycle (corpus-derived governance) from current/live
    # health. lifecycle_state may be "ok" in offline mode when the corpus is
    # trustworthy and governance is clean. current_health reflects whether the
    # full live aggregate (with sensor/T3) was actually evaluated. Checkpoint
    # retains both so a downstream consumer can read whichever they need
    # without inferring the other.
    lifecycle_state = projection["aggregate"].get("governance_state",
                                                  projection["aggregate"].get("state"))
    current_health = projection["aggregate"].get("current_health",
                                                 projection["aggregate"].get("state"))

    candidate = {
        "schema_version": SCHEMA_VERSION,
        "header": {
            "kind": "DERIVED/NON-AUTHORITY_RECOVERY_INDEX",
            "as_of_event_count": len(manifest),
            "generator_identity": GENERATOR_IDENTITY,
            "framework_version": FRAMEWORK_VERSION,
            # Non-authority summary fields (TASK S2-R3.3 + S2-H1): preserved
            # for consumers to inspect without turning checkpoint into
            # authority. The checkpoint MUST NOT claim current/live green
            # when sensor/T3 was not evaluated.
            "input_lifecycle_state": lifecycle_state,
            "current_health": current_health,
            "input_governance_state": lifecycle_state,  # back-compat alias
            "non_green_subjects": list(
                projection["aggregate"].get("non_green_subjects", [])
            ),
            "sensor_state": projection["sensor_health"].get("state"),
            "input_mode": projection["header"].get("input_mode"),
            "input_manifest_hash": projection["input_identity"].get("event_manifest_hash"),
        },
        "input_event_manifest": manifest,
        "frontier": frontier,
        "source_event_refs": source_event_refs,
        "manifest_integrity_sha256": integrity,
    }
    return candidate, lifecycle_state


def generate_checkpoint(events_dir, reg=None, profile=None,
                         profile_identity=None, project_root=None,
                         projection: dict[str, Any] | None = None
                         ) -> Checkpoint:
    """Build a Slice 0 frozen checkpoint from the controlled Slice 1 corpus.

    Args:
      events_dir: path-like, the `.blackbox/events` directory.
      reg: optional Slice 1 SubtypeRegistry.
      profile, profile_identity, project_root: optional live-mode params
        forwarded to the projector (only used when `projection` is None).
      projection: optional precomputed projector projection dict.
    """
    if reg is None:
        reg = r1_subtypes.SubtypeRegistry.load()
    # S2-H2: fail-closed Slice 1 dependency gate.
    bbx2_dep.gate_semantic_entrypoint(strict=True)

    if projection is None:
        from . import projector as bbx2_projector
        proj = bbx2_projector.project(
            events_dir, reg=reg, profile=profile,
            profile_identity=profile_identity,
            project_root=project_root,
        )
        projection = proj.projection

    # Refuse only on structurally untrustworthy corpus / ambiguous frontier.
    # TASK S2-R3: governance-non-green is ALLOWED; the non-green nature is
    # preserved explicitly in header.
    if projection["corpus_health"]["corpus_blocked"]:
        raise CheckpointBlocked(
            f"corpus_blocked: {projection['corpus_health']['corpus_reason']}"
        )
    counts = projection["corpus_health"]["counts"]
    if counts[r1_reingest.V_INVALID] > 0 or counts[r1_reingest.V_UNKNOWN] > 0:
        raise CheckpointBlocked(
            f"untrustworthy corpus: INVALID={counts[r1_reingest.V_INVALID]} "
            f"UNKNOWN={counts[r1_reingest.V_UNKNOWN]}"
        )

    materialized, input_gov_state = _materialize(events_dir, reg, projection)
    candidate = _candidate_payload(materialized)
    canonical = r1_canonical.canonical_bytes(candidate)
    cp_id = "cp-" + hashlib.sha256(canonical).hexdigest()

    final = copy.deepcopy(candidate)
    final["header"]["checkpoint_id"] = cp_id
    return Checkpoint(
        checkpoint=final,
        checkpoint_id=cp_id,
        manifest_integrity_sha256=final["manifest_integrity_sha256"],
        input_governance_state=input_gov_state,
    )


def checkpoint_to_bytes(checkpoint: dict[str, Any]) -> bytes:
    """Deterministic serialization: RFC8785/JCS canonical JSON + 1 LF."""
    return r1_canonical.canonical_bytes(checkpoint) + b"\n"


def verify_self_reference(checkpoint: dict[str, Any]) -> bool:
    """Independent re-verification of the self-reference boundary.

    Builds the candidate WITHOUT header.checkpoint_id, computes sha256, and
    confirms it matches header.checkpoint_id.
    """
    # S2-H2: fail-closed Slice 1 dependency gate.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    if not isinstance(checkpoint, dict):
        return False
    hdr = checkpoint.get("header", {})
    cp_id = hdr.get("checkpoint_id")
    if not (isinstance(cp_id, str) and cp_id.startswith("cp-")):
        return False
    expected_hex = cp_id[3:]
    if len(expected_hex) != 64:
        return False
    candidate = _candidate_payload(checkpoint)
    canonical = r1_canonical.canonical_bytes(candidate)
    actual = hashlib.sha256(canonical).hexdigest()
    return actual == expected_hex

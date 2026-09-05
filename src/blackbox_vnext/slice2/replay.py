"""S2-7 recovery-index / offline replay.

Loads a materialized checkpoint, runs validation (S2-5), then re-derives
its manifest + frontier from the controlled Slice 1 corpus and compares
against the checkpoint's stored values. This is an offline, deterministic
replay: it proves the checkpoint is recoverable from the corpus + Slice 1
semantics alone.

Replay MUST NOT be used to prove authorization / completion / acceptance /
scope. It only proves recovery-index reproducibility.

When the pair-set is not exactly equal, replay returns a structured
differential (added/removed/diverged) — this is the source of truth for
recovery-plan. Replay never silently claims reproducibility when the
underlying bytes differ.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import reingest as r1_reingest  # noqa: E402
from blackbox_vnext.slice1 import authority as r1_authority  # noqa: E402
from blackbox_vnext.slice1 import subtypes as r1_subtypes  # noqa: E402

from . import manifest as bbx2_manifest
from . import frontier as bbx2_frontier
from . import staleness as bbx2_staleness
from . import validate as bbx2_validate
from . import dependency_pin as bbx2_dep


@dataclass
class ReplayResult:
    ok: bool
    reason: str
    detail: dict[str, Any]

    @property
    def is_reproducible(self) -> bool:
        return self.ok


def replay(checkpoint: dict[str, Any], events_dir,
           reg: r1_subtypes.SubtypeRegistry | None = None,
           ) -> ReplayResult:
    """Validate the checkpoint, then re-derive manifest + frontier from
    corpus (using the same Case S authority evaluator as projector / live
    fold) and compare.

    When the pair-set exactly matches AND frontier exactly matches,
    returns ok=True. Otherwise returns ok=False with a structured
    differential in detail.
    """
    # S2-H2: fail-closed Slice 1 dependency gate.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    # 1. validate first — full frozen schema + manifest integrity +
    #    self-reference + as_of count + unique IDs + source refs +
    #    frontier-in-manifest containment
    val = bbx2_validate.validate(checkpoint)
    if not val.ok:
        return ReplayResult(
            ok=False,
            reason=f"validation failed: {val.reason}",
            detail={"validation": val.__dict__},
        )

    # 2. re-derive manifest from corpus (refuses on INVALID/UNKNOWN)
    try:
        mr = bbx2_manifest.build_manifest(events_dir, reg=reg)
    except bbx2_manifest.ManifestBlocked as e:
        return ReplayResult(
            ok=False,
            reason=f"manifest re-derivation blocked: {e.reason}",
            detail={"where": "manifest.build_manifest"},
        )

    derived_manifest = mr.manifest
    derived_integrity = mr.integrity_sha256
    cp_manifest = checkpoint["input_event_manifest"]
    cp_integrity = checkpoint.get("manifest_integrity_sha256")

    if derived_integrity != cp_integrity:
        return ReplayResult(
            ok=False,
            reason=(
                f"manifest integrity mismatch: "
                f"derived={derived_integrity} cp={cp_integrity}"
            ),
            detail={"where": "manifest_integrity_sha256"},
        )

    # pair-set comparison (TASK S2-R5 strict): structured differential on
    # mismatch rather than silent collapse.
    sr = bbx2_staleness.compute_staleness(cp_manifest, derived_manifest)
    if sr.state != bbx2_staleness.STATE_CURRENT:
        return ReplayResult(
            ok=False,
            reason=f"manifest pair-set mismatch: {sr.state}",
            detail={
                "where": "input_event_manifest",
                "staleness": bbx2_staleness.staleness_to_dict(sr),
            },
        )

    # 3. re-derive frontier with same authority evaluator (TASK S2-R2)
    if reg is None:
        reg = r1_subtypes.SubtypeRegistry.load()
    auth = r1_authority.CaseSAuthorityProfile(registry=reg)
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
    fr = bbx2_frontier.compute_frontier(events, reg, auth)
    try:
        bbx2_frontier.enforce_single_head(fr)
    except bbx2_frontier.FrontierAmbiguous as e:
        return ReplayResult(
            ok=False,
            reason=f"frontier re-derivation ambiguous: {e}",
            detail={
                "where": "frontier.enforce_single_head",
                "subject": e.subject,
                "conflicts": e.conflicts,
            },
        )
    derived_frontier = dict(fr.frontier)
    cp_frontier = dict(checkpoint["frontier"])
    if derived_frontier != cp_frontier:
        return ReplayResult(
            ok=False,
            reason=f"frontier mismatch: derived={sorted(derived_frontier.items())} "
                   f"cp={sorted(cp_frontier.items())}",
            detail={"where": "frontier"},
        )

    return ReplayResult(
        ok=True,
        reason="replay matched stored manifest + frontier",
        detail={
            "manifest_count": len(derived_manifest),
            "frontier_subjects": sorted(derived_frontier.keys()),
        },
    )

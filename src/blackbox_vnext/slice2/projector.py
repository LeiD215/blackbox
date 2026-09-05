"""S2-1 deterministic projector.

The projector consumes Slice 1's strict re-ingest, authority evaluator,
lifecycle fold, and **governance-health walker** (imported directly from
`bbx.__main__._subject_governance_health` — TASK explicitly permits private
import for this reference layer to avoid forking Slice 1 semantics). When
given a project_root, it additionally invokes Slice 1 sensor/T3 (baseline
load + workspace recompute + 4-way classification). When given only
events_dir (offline recovery), it reports sensor state as NOT_EVALUATED
and never fabricates `t3_clean=true`.

It emits a deterministic, explicitly NON-AUTHORITY machine-readable derived
projection. The projection MUST NOT be used as authority input to any
validator/authority/fold — it is output-only.

Determinism rules (frozen):
  - Directory enumeration, file mtime, recorded_at, Git order, filename
    lexical order MUST NOT determine governance state.
  - Serialization order may be deterministic for byte reproducibility
    (sorted by event_id, sorted subjects) but has no causal/governance
    meaning.
  - Invalid/unknown/corpus-blocked corpus MUST project explicit
    non-green/UNKNOWN — never green-from-subset.

Input mode:
  - live(project_root=...): real Slice 1 sensor/T3 (baseline + workspace
    recompute + 4-way classification). sensor_health.t3_clean reflects
    actual Slice 1 sensor state.
  - events_dir-only: offline recovery. sensor_health.t3_clean = False,
    sensor_state = NOT_EVALUATED. Aggregate cannot be green purely from
    corpus cleanliness.

input_identity.event_manifest_hash (TASK S2-R1.4): sha256 over enumerable
(event_id, content_hash) pair-set (sorted by event_id), NOT IDs alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import reingest as r1_reingest  # noqa: E402
from blackbox_vnext.slice1 import fold as r1_fold  # noqa: E402
from blackbox_vnext.slice1 import authority as r1_authority  # noqa: E402
from blackbox_vnext.slice1 import observe as r1_observe  # noqa: E402
from blackbox_vnext.slice1 import subtypes as r1_subtypes  # noqa: E402
from blackbox_vnext.slice1 import canonical as r1_canonical  # noqa: E402

# TASK S2-R1.1: import Slice 1 governance walker directly (private import
# permitted for this reference layer to avoid forking).
import blackbox_vnext.slice1.__main__ as r1_main  # noqa: E402
from blackbox_vnext.slice1.__main__ import (  # noqa: E402
    _corpus_health,
    _events_from_verdicts,
    _load_profile,
    _sensor_delta,
    _subject_governance_health,
    CODE_FIXED_EXCLUSIONS,
)

from . import FRAMEWORK_VERSION, GENERATOR_IDENTITY, SCHEMA_VERSION
from . import dependency_pin as bbx2_dep


# ---------------------------------------------------------------------------
# Projector input mode
# ---------------------------------------------------------------------------


def _manifest_pair_set(events: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], str]:
    """Return (sorted pair-set, sha256 over canonical pair-set bytes).

    Computes the enumerable (event_id, content_hash) pair-set and the
    sha256 identity hash over its JCS-canonical bytes (TASK S2-R1.4)."""
    pairs = sorted(
        (ev.get("event_id"), ev.get("content_hash"))
        for ev in events
        if isinstance(ev, dict)
        and isinstance(ev.get("event_id"), str)
        and isinstance(ev.get("content_hash"), str)
    )
    pairs_list = [{"event_id": e, "content_hash": c} for e, c in pairs]
    canonical = r1_canonical.canonical_bytes(pairs_list)
    digest = r1_canonical.sha256_canonical(pairs_list)
    return pairs, digest


def _run_live_sensor(
    project_root: Path,
    profile: dict[str, Any],
    pid: str,
    events: list[dict[str, Any]],
    reg: r1_subtypes.SubtypeRegistry,
) -> tuple[list[dict[str, str]], list[dict[str, str]], str | None]:
    """Run real Slice 1 sensor/T3: load baseline, recompute workspace,
    4-way classify changes. Returns (delta, uncovered, sensor_err)."""
    return _sensor_delta(project_root, profile, pid, events, reg)


# ---------------------------------------------------------------------------
# Projector result
# ---------------------------------------------------------------------------


@dataclass
class ProjectionResult:
    """S2-1 deterministic derived projection."""

    projection: dict[str, Any]

    @property
    def subjects(self) -> list[str]:
        return list(self.projection["per_subject"].keys())

    @property
    def is_green(self) -> bool:
        # S2-H1: only live mode with actually-evaluated Slice 1 T3 and
        # current_health=ok can be considered green. Offline mode is
        # NEVER green even if governance_state is "ok".
        agg = self.projection["aggregate"]
        return (
            agg.get("current_health") == "ok"
            and agg.get("input_mode") == "live"
        )

    @property
    def blocked(self) -> bool:
        return bool(self.projection["corpus_health"]["corpus_blocked"])

    @property
    def input_mode(self) -> str:
        return self.projection["aggregate"].get("input_mode", "offline_recovery")

    @property
    def current_health(self) -> str:
        return self.projection["aggregate"].get("current_health", "UNKNOWN")

    @property
    def governance_state(self) -> str:
        return self.projection["aggregate"].get("governance_state", "UNKNOWN")


# ---------------------------------------------------------------------------
# Projector
# ---------------------------------------------------------------------------


def project(events_dir, reg: r1_subtypes.SubtypeRegistry | None = None,
            profile: dict[str, Any] | None = None,
            profile_identity: str | None = None,
            project_root: str | Path | None = None,
            ) -> ProjectionResult:
    """Run Slice 1 strict re-ingest + (optional) sensor/T3 + governance +
    fold; emit deterministic derived projection.

    Args:
      events_dir: path-like, the `.blackbox/events` directory.
      reg: optional Slice 1 SubtypeRegistry (loaded from frozen Slice 0 schema
           if None).
      profile: optional observation profile dict (Slice 1 schema).
      profile_identity: optional precomputed profile identity sha256 string.
      project_root: optional Slice 1 project root. When provided AND profile
                    is provided, real Slice 1 sensor/T3 runs (live mode).
                    When omitted, sensor is NOT_EVALUATED (offline recovery).
    """
    if reg is None:
        reg = r1_subtypes.SubtypeRegistry.load()
    # S2-H2: fail-closed Slice 1 dependency gate. Drift -> DependencyDrift.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    auth = r1_authority.CaseSAuthorityProfile(registry=reg)

    # 1. strict corpus re-ingest (S1-F4 fail-closed)
    result = r1_reingest.reingest_events_dir(events_dir, reg)
    counts = {r1_reingest.V_VALID: 0, r1_reingest.V_UNCLASSIFIED: 0,
              r1_reingest.V_INVALID: 0, r1_reingest.V_UNKNOWN: 0}
    for v in result.verdicts:
        counts[v.status] += 1

    # 2. corpus health (blocking vs evidence)
    blocking, _evidence, corpus_blocked, corpus_reason = _corpus_health(result)

    # 3. events list (VALID + UNCLASSIFIED only)
    events = _events_from_verdicts(result.verdicts)
    # tag events with verdict status so Slice 1 governance walker can read it
    events_tagged = []
    for ev in events:
        # Find its verdict status from result.verdicts (event_id lookup)
        ev2 = dict(ev)
        ev2["__verdict_status"] = next(
            (v.status for v in result.verdicts
             if v.event is not None and v.event.get("event_id") == ev.get("event_id")),
            r1_reingest.V_UNKNOWN,
        )
        events_tagged.append(ev2)

    # 4. sensor / T3 (live vs offline)
    sensor_state = "NOT_EVALUATED"
    sensor_err: str | None = None  # offline mode is not an error; T3 just not run
    sensor_delta: list[dict[str, str]] = []
    sensor_uncovered: list[dict[str, str]] = []
    sensor_profile_id: str | None = None

    if project_root is not None and profile is not None:
        sensor_state = "EVALUATED"
        sensor_err = None
        sensor_profile_id = r1_observe.compute_profile_identity(profile)
        sensor_delta, sensor_uncovered, sensor_err = _run_live_sensor(
            Path(project_root), profile, sensor_profile_id, events, reg,
        )
        if sensor_err is not None:
            sensor_state = "EVALUATED_WITH_ERROR"
        elif sensor_uncovered:
            sensor_state = "EVALUATED_WITH_UNCOVERED"

    # 5. governance health (S1-F3 + S1-G2) — direct import, no fork
    governance_blockers = _subject_governance_health(result.verdicts, reg, auth)

    # 6. per-subject lifecycle fold + derived COMPLETE
    subjects = sorted({
        ev.get("subject") for ev in events
        if isinstance(ev, dict) and isinstance(ev.get("subject"), str)
    })
    receipt_validity = r1_authority.evaluate_receipt_validity(events, auth)
    receipt_assurance = r1_authority.evaluate_receipt_assurance(events, auth)
    per_subject: dict[str, dict[str, Any]] = {}
    aggregate_non_green = False
    for subj in subjects:
        head, conflicts = r1_fold.fold_subject(events, reg, subj, authority=auth)
        rc = r1_fold.derived_complete(
            events, reg, subj, authority=auth,
            receipt_assurance=receipt_assurance,
        )
        # Apply subject governance blocker (S1-F3 / S1-G2)
        subj_blockers = governance_blockers.get(subj, [])
        if rc.state == "ok" and subj_blockers:
            rc = r1_fold.FoldResult(
                state="non_green",
                head_event_id=rc.head_event_id,
                detail=(
                    f"derived COMPLETE blocked by same-subject governance "
                    f"health: {len(subj_blockers)} blocker(s)"
                ),
            )
        per_subject[subj] = {
            "head": head,
            "conflicts": list(conflicts),
            "derived_complete_state": rc.state,
            "derived_complete_detail": rc.detail,
            "blockers": list(subj_blockers),
        }
        if rc.state != "ok":
            aggregate_non_green = True

    # 7. ambiguous/conflict reclassify states (no silent collapse)
    ambiguous: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("subtype") != "reclassify":
            continue
        ext = ev.get("extensions", {})
        if not isinstance(ext, dict):
            continue
        cs = r1_fold.candidate_set_for_reclassify(
            events, ev.get("event_id"), reg=reg, authority=auth,
        )
        if cs.state in ("ambiguous", "non_green", "unresolved"):
            ambiguous.append({
                "reclassify_event_id": ev.get("event_id"),
                "subject": ev.get("subject"),
                "state": cs.state,
                "candidates": list(cs.candidate_ids),
                "detail": cs.detail,
            })

    # 8. input identity (TASK S2-R1.4: pair-set, not IDs alone)
    _pairs, pair_hash = _manifest_pair_set(events)
    input_event_count = len(_pairs)
    input_identity = {
        "event_count": input_event_count,
        "event_manifest_hash": pair_hash,         # sha256 over pair-set
        "profile_identity_sha256": (
            profile_identity if profile_identity is not None
            else (sensor_profile_id or None)
        ),
    }

    # 9. sensor health + aggregate (TASK S2-R1.2 / S2-R1.3)
    # t3_clean is ONLY true when:
    #   - corpus is trustworthy (no INVALID/UNKNOWN/corpus_blocked), AND
    #   - sensor was actually evaluated (project_root + profile provided), AND
    #   - sensor_err is None, AND
    #   - sensor_uncovered is empty.
    sensor_health: dict[str, Any] = {
        "state": sensor_state,
        "t3_clean": (
            counts[r1_reingest.V_INVALID] == 0
            and counts[r1_reingest.V_UNKNOWN] == 0
            and not result.corpus_blocked
            and sensor_state == "EVALUATED"
            and not sensor_uncovered
        ),
        "delta": list(sensor_delta),
        "uncovered": list(sensor_uncovered),
        "error": sensor_err,
        "governance_blockers_present": any(bool(v) for v in governance_blockers.values()),
    }

    # S2-H1: separate governance/lifecycle result from current/live aggregate.
    # governance_state reflects only the corpus-derived lifecycle + governance
    # outcome (it CAN be "ok" in offline mode when the corpus is trustworthy
    # and lifecycle/governance are clean). current_health is the full live
    # aggregate health — UNKNOWN / NOT_EVALUATED whenever sensor/T3 is not
    # actually evaluated. Offline mode therefore cannot claim full green.
    if (
        counts[r1_reingest.V_INVALID] > 0
        or counts[r1_reingest.V_UNKNOWN] > 0
        or result.corpus_blocked
    ):
        governance_state = "UNKNOWN"
        current_health = "UNKNOWN"
    elif sensor_err is not None:
        # Live sensor error fails closed: governance state is unknowable,
        # and current live health is UNKNOWN.
        governance_state = "UNKNOWN"
        current_health = "UNKNOWN"
    elif sensor_state == "NOT_EVALUATED":
        # Offline recovery: governance_state reflects corpus lifecycle result
        # (allowed to be "ok"), but current_health MUST be NOT_EVALUATED
        # because T3 / sensor was never actually evaluated. The full live
        # project can never be claimed green purely from corpus cleanliness.
        if aggregate_non_green or any(bool(v) for v in governance_blockers.values()):
            governance_state = "non_green"
        else:
            governance_state = "ok"
        current_health = "NOT_EVALUATED"
    elif aggregate_non_green or any(bool(v) for v in governance_blockers.values()) \
            or sensor_uncovered:
        governance_state = "non_green"
        current_health = "non_green"
    else:
        governance_state = "ok"
        current_health = "ok"

    aggregate_state = current_health

    projection = {
        "header": {
            "kind": "DERIVED/NON-AUTHORITY_PROJECTION",
            "schema_version": SCHEMA_VERSION,
            "framework_version": FRAMEWORK_VERSION,
            "generator_identity": GENERATOR_IDENTITY,
            "input_mode": (
                "live" if (project_root is not None and profile is not None)
                else "offline_recovery"
            ),
        },
        "input_identity": input_identity,
        "corpus_health": {
            "counts": counts,
            "corpus_blocked": result.corpus_blocked,
            "corpus_reason": corpus_reason,
        },
        "sensor_health": sensor_health,
        "per_subject": per_subject,
        "ambiguous_reclassifies": ambiguous,
        "aggregate": {
            # S2-H1: governance_state is the corpus-derived lifecycle/governance
            # result (may be "ok" offline). current_health is the full live
            # aggregate health — MUST be UNKNOWN/NOT_EVALUATED in offline
            # mode. state == current_health for backward compat with
            # downstream consumers, but consumers SHOULD read current_health.
            "state": aggregate_state,
            "governance_state": governance_state,
            "current_health": current_health,
            "input_mode": (
                "live" if (project_root is not None and profile is not None)
                else "offline_recovery"
            ),
            "non_green_subjects": [
                s for s, d in per_subject.items()
                if d["derived_complete_state"] != "ok" or d["blockers"]
            ],
            "sensor_uncovered_paths": [u["path"] for u in sensor_uncovered],
        },
    }
    return ProjectionResult(projection=projection)


def projection_to_bytes(projection: dict[str, Any]) -> bytes:
    """Deterministic serialization: RFC8785/JCS canonical JSON bytes + 1 LF."""
    return r1_canonical.canonical_bytes(projection) + b"\n"

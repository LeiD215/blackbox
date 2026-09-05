"""S2-6b recovery-plan (TASK S2-R6).

Generates a recovery assistance report from a stored checkpoint + the
current strict corpus. The plan includes:
  - checkpoint validity (or unavailable/corrupt status)
  - current strict manifest
  - added/removed/diverged pair delta
  - checkpoint frontier + current frontier
  - per-subject causal refs (parent + supports) for events in the
    added delta or current-only delta events, so a downstream fold/
    replay can use them
  - explicit recommendation: `FULL_CANONICAL_REPLAY_REQUIRED` when the
    checkpoint is missing / corrupt / untrusted, OR when the pair-set
    drift is so large that incremental recovery is unsafe. Never trusts
    a bad checkpoint.

The recovery-plan MUST NOT be used as authority input. It is an
operator-visible recovery assistance surface; the underlying canonical
events are still the only authoritative source.

Demonstration invariant (TASK S2-R6): deleting the checkpoint and doing
a full canonical replay must yield the same current live projection /
frontier as a recovery plan that loads the (deleted) checkpoint and
recomputes from canonical events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import reingest as r1_reingest  # noqa: E402
from blackbox_vnext.slice1 import fold as r1_fold  # noqa: E402
from blackbox_vnext.slice1 import authority as r1_authority  # noqa: E402
from blackbox_vnext.slice1 import subtypes as r1_subtypes  # noqa: E402

from . import manifest as bbx2_manifest
from . import frontier as bbx2_frontier
from . import staleness as bbx2_staleness
from . import validate as bbx2_validate
from . import dependency_pin as bbx2_dep


FULL_CANONICAL_REPLAY_REQUIRED = "FULL_CANONICAL_REPLAY_REQUIRED"
PARTIAL_RECOVERY_PLAN = "PARTIAL_RECOVERY_PLAN"


@dataclass
class RecoveryPlan:
    recommendation: str           # FULL_CANONICAL_REPLAY_REQUIRED | PARTIAL_RECOVERY_PLAN
    checkpoint_validity: str      # VALID | INVALID | MISSING | CORRUPT
    checkpoint_status_reason: str = ""
    current_manifest: list[dict[str, str]] = field(default_factory=list)
    current_manifest_unavailable_reason: str = ""
    delta_state: str = ""
    added: list[dict[str, str]] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    diverged: list[dict[str, Any]] = field(default_factory=list)
    checkpoint_frontier: dict[str, str] = field(default_factory=dict)
    current_frontier: dict[str, str] = field(default_factory=dict)
    current_frontier_unavailable_reason: str = ""
    causal_refs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    full_canonical_replay_frontier: dict[str, str] = field(default_factory=dict)
    full_canonical_replay_subjects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation,
            "checkpoint_validity": self.checkpoint_validity,
            "checkpoint_status_reason": self.checkpoint_status_reason,
            "current_manifest": list(self.current_manifest),
            "current_manifest_unavailable_reason":
                self.current_manifest_unavailable_reason,
            "delta_state": self.delta_state,
            "added": list(self.added),
            "removed": list(self.removed),
            "diverged": list(self.diverged),
            "checkpoint_frontier": dict(self.checkpoint_frontier),
            "current_frontier": dict(self.current_frontier),
            "current_frontier_unavailable_reason":
                self.current_frontier_unavailable_reason,
            "causal_refs": {
                k: dict(v) for k, v in self.causal_refs.items()
            },
            "full_canonical_replay_frontier":
                dict(self.full_canonical_replay_frontier),
            "full_canonical_replay_subjects":
                list(self.full_canonical_replay_subjects),
        }


def _validate_checkpoint_or_none(checkpoint: dict[str, Any] | None
                                  ) -> tuple[str, str, dict[str, Any] | None]:
    """Returns (validity, reason, validated_cp). validity ∈
    {VALID, INVALID, MISSING, CORRUPT}.

    MISSING = no checkpoint supplied (None).
    CORRUPT = JSON-shaped but fails validation with structural/schema
              problem.
    INVALID = a specific semantic check failed (e.g. self-reference drift).
    """
    if checkpoint is None:
        return "MISSING", "no checkpoint supplied", None
    val = bbx2_validate.validate(checkpoint)
    if not val.ok:
        # Distinguish structural (CORRUPT) vs semantic (INVALID)
        structural = (
            not val.schema_pins_ok
            or not val.manifest_integrity_ok
            or not val.self_reference_ok
            or not val.as_of_count_ok
            or not val.unique_manifest_ids_ok
        )
        if structural:
            return "CORRUPT", val.reason, None
        return "INVALID", val.reason, None
    return "VALID", "", checkpoint


def _build_current_strict_manifest(events_dir, reg
                                   ) -> tuple[list[dict[str, str]], str]:
    """Returns (manifest, reason_unavailable). When reason_unavailable is
    non-empty, manifest is empty. Reasons include ManifestBlocked (any
    INVALID/UNKNOWN/corpus_blocked)."""
    try:
        mr = bbx2_manifest.build_manifest(events_dir, reg=reg)
    except bbx2_manifest.ManifestBlocked as e:
        return [], str(e)
    return mr.manifest, ""


def _collect_added_causal_refs(added: list[dict[str, str]],
                                events_dir, reg
                                ) -> dict[str, dict[str, list[str]]]:
    """For each added event_id, look up its prior_refs in the on-disk
    canonical event. Returns {event_id: {parent: [...], supports: [...]}}.

    If the event cannot be located or read, that event_id maps to
    {"parent": [], "supports": []}."""
    from pathlib import Path
    from blackbox_vnext.slice1.ingest import parse_strict_with_duplicate_check
    out: dict[str, dict[str, list[str]]] = {}
    for entry in added:
        eid = entry["event_id"]
        path = Path(events_dir) / (eid + ".json")
        if not path.exists():
            out[eid] = {"parent": [], "supports": []}
            continue
        try:
            raw = path.read_bytes()
            obj = parse_strict_with_duplicate_check(raw[:-1].decode("utf-8"))
            pr = obj.get("prior_refs", {}) if isinstance(obj, dict) else {}
            out[eid] = {
                "parent": list(pr.get("parent", [])) if isinstance(pr, dict) else [],
                "supports": list(pr.get("supports", [])) if isinstance(pr, dict) else [],
            }
        except Exception:
            out[eid] = {"parent": [], "supports": []}
    return out


def _derive_frontier_from_events(events: list[dict[str, Any]],
                                  reg: r1_subtypes.SubtypeRegistry,
                                  auth: r1_authority.CaseSAuthorityProfile,
                                  ) -> dict[str, str]:
    fr = bbx2_frontier.compute_frontier(events, reg, auth)
    return dict(fr.frontier)


def build_recovery_plan(events_dir,
                         checkpoint: dict[str, Any] | None = None,
                         reg: r1_subtypes.SubtypeRegistry | None = None,
                         ) -> RecoveryPlan:
    """Build a recovery assistance report.

    Args:
      events_dir: path-like, the controlled Slice 1 corpus.
      checkpoint: optional loaded checkpoint dict (None = no checkpoint).
      reg: optional Slice 1 SubtypeRegistry (loaded if None).

    The plan NEVER trusts a corrupt checkpoint. When the checkpoint is
    missing/corrupt/untrusted, recommendation=FULL_CANONICAL_REPLAY_REQUIRED
    and the plan still includes the full canonical replay frontier from
    the current corpus (so a downstream operator knows what canonical
    replay yields).
    """
    # S2-H2: fail-closed Slice 1 dependency gate.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    if reg is None:
        reg = r1_subtypes.SubtypeRegistry.load()
    auth = r1_authority.CaseSAuthorityProfile(registry=reg)

    plan = RecoveryPlan(
        recommendation=FULL_CANONICAL_REPLAY_REQUIRED,
        checkpoint_validity="MISSING",
        checkpoint_status_reason="(none)",
    )

    # 1. Validate the checkpoint (if supplied)
    validity, reason, validated_cp = _validate_checkpoint_or_none(checkpoint)
    plan.checkpoint_validity = validity
    plan.checkpoint_status_reason = reason or "(ok)"
    if validated_cp is not None:
        plan.checkpoint_frontier = dict(validated_cp.get("frontier", {}))
        baseline_manifest = list(validated_cp.get("input_event_manifest", []))
    else:
        plan.checkpoint_frontier = {}
        baseline_manifest = []

    # 2. Build the current strict manifest (or refuse if untrustworthy)
    current_manifest, cur_unavail = _build_current_strict_manifest(events_dir, reg)
    plan.current_manifest = current_manifest
    plan.current_manifest_unavailable_reason = cur_unavail

    # 3. Compute staleness (pair-set, no count-based winner).
    if cur_unavail:
        # TASK S2-R5: refuse comparison; do not invent from a sanitized subset.
        plan.delta_state = bbx2_staleness.STATE_UNKNOWN
        plan.added = []
        plan.removed = []
        plan.diverged = []
        plan.recommendation = FULL_CANONICAL_REPLAY_REQUIRED
    else:
        sr = bbx2_staleness.compute_staleness(baseline_manifest, current_manifest)
        plan.delta_state = sr.state
        plan.added = list(sr.added)
        plan.removed = list(sr.removed)
        plan.diverged = list(sr.diverged)

    # 4. Derive current frontier from canonical events (same authority as
    # projector). S2-I2: ONLY when the complete current controlled corpus
    # passed strict ingest cleanly. If the corpus is untrustworthy
    # (INVALID/UNKNOWN/corpus_blocked/layout violation), do NOT compute or
    # expose a frontier derived from a sanitized subset — that would turn
    # omitted bad evidence into apparent recovery guidance. Leave the
    # frontier fields empty/unavailable with reason CURRENT_CORPUS_UNTRUSTWORTHY.
    try:
        result = r1_reingest.reingest_events_dir(events_dir, reg)
        bad = sum(
            1 for v in result.verdicts
            if v.status in (r1_reingest.V_INVALID, r1_reingest.V_UNKNOWN)
        )
        corpus_untrustworthy = bool(bad) or bool(result.corpus_blocked)
        if corpus_untrustworthy:
            reason = (
                f"CURRENT_CORPUS_UNTRUSTWORTHY: INVALID+UNKNOWN={bad} "
                f"corpus_blocked={result.corpus_blocked} "
                f"reason={result.corpus_reason}"
            )
            plan.current_frontier = {}
            plan.current_frontier_unavailable_reason = reason
            plan.full_canonical_replay_frontier = {}
            plan.full_canonical_replay_subjects = []
        else:
            events = []
            for v in result.verdicts:
                if v.event is None:
                    continue
                if v.status not in (r1_reingest.V_VALID, r1_reingest.V_UNCLASSIFIED):
                    continue
                ev = dict(v.event)
                ev["__verdict_status"] = v.status
                events.append(ev)
            current_frontier = _derive_frontier_from_events(events, reg, auth)
            plan.current_frontier = current_frontier
            plan.full_canonical_replay_frontier = current_frontier
            plan.full_canonical_replay_subjects = sorted(current_frontier.keys())
    except Exception as e:
        plan.current_frontier = {}
        plan.current_frontier_unavailable_reason = f"reingest failed: {e}"
        plan.full_canonical_replay_frontier = {}
        plan.full_canonical_replay_subjects = []

    # 5. Per-subject causal refs for added events (TASK S2-R6 per-subject)
    if plan.added and not cur_unavail:
        plan.causal_refs = _collect_added_causal_refs(
            plan.added, events_dir, reg,
        )

    # 6. Decide recommendation (S2-H3: only checkpoint_validity=VALID may
    # yield PARTIAL_RECOVERY_PLAN. MISSING / CORRUPT / INVALID all force
    # FULL_CANONICAL_REPLAY_REQUIRED. Never trust an untrusted checkpoint.)
    if plan.checkpoint_validity != "VALID":
        plan.recommendation = FULL_CANONICAL_REPLAY_REQUIRED
    elif plan.delta_state == bbx2_staleness.STATE_UNKNOWN:
        # Divergence on (event_id, content_hash) -> checkpoint stored
        # different bytes than current corpus for at least one event.
        # Trust the corpus, not the checkpoint.
        plan.recommendation = FULL_CANONICAL_REPLAY_REQUIRED
    elif plan.current_manifest_unavailable_reason:
        plan.recommendation = FULL_CANONICAL_REPLAY_REQUIRED
    elif not plan.current_frontier:
        plan.recommendation = FULL_CANONICAL_REPLAY_REQUIRED
    else:
        plan.recommendation = PARTIAL_RECOVERY_PLAN

    # S2-H3: when the checkpoint is non-VALID, do not present the pair
    # delta against a fabricated empty baseline as if it were meaningful
    # incremental checkpoint drift. Expose current manifest/frontier for
    # recovery assistance (they remain useful), but mark checkpoint delta
    # unavailable/untrusted.
    if plan.checkpoint_validity != "VALID":
        plan.added = []
        plan.removed = []
        plan.diverged = []
        plan.causal_refs = {}

    return plan


def recovery_plan_to_dict(plan: RecoveryPlan) -> dict[str, Any]:
    return plan.to_dict()

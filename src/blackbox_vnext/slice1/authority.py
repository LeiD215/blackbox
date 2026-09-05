"""Minimal deterministic Case S authority / assurance evaluator (R3, S1-3).

This module implements the Slice 1 authority scope ONLY — no IAM/RBAC, no
network, no policy engine. It is a pure function layer over the frozen
subtype registry + a small configured bootstrap principal profile.

Two separate verdicts are produced per event (Formal Spec layering):
  - authorized: bool           — actor/subtype/receipt_class may author this
                                 receipt at all (Layer 1 authority)
  - assurance: str             — what assurance this receipt carries
                                 ("verified-self" | "unverified" | ...)

NEVER fabricates VERIFIED-INDEPENDENT.

Bootstrap profile (Case S): the human principal is the configured bootstrap
authority. Executors/AI agents may only author the execution/verification
receipt classes. Sensors (observed receipts) carry no authority.
"""
from __future__ import annotations

from typing import Any

from .subtypes import SubtypeRegistry

# Subtypes a configured human principal may author (Case S bootstrap).
HUMAN_SUBTYPES = frozenset({
    "dispatch",              # formal TASK authorization
    "acceptance",            # formal DECISION
    "authority-change",
    "reconciliation",        # explicit backfill requires human/owner sign-off
    "reclassify",            # corrective record is a human curator action
    "checkpoint-recorded",
    "correction",
    "resolution",
})

# Subtypes an AI/executor principal may author (frozen Slice 1 executor set).
EXECUTOR_SUBTYPES = frozenset({
    "execution-result",
    "task-update",           # registered? checked via registry at runtime
    "completion-claim",
    "self-verification",
})

# Subtypes a passive sensor may author (observed receipts, no authority).
SENSOR_SUBTYPES = frozenset({
    "orphan-observed",
})

# Subtypes a self-verification may support to yield effective VERIFIED-SELF
# (S1-F1): the relevant execution path / completion path — NOT an arbitrary
# dispatch ancestor.
ELIGIBLE_SELF_VERIFICATION_TARGETS = frozenset({
    "execution-result",
    "completion-claim",
})


def effect_entries(event: dict[str, Any]) -> list[tuple[str, str]]:
    """Machine-readable effect bindings of an event: [(path, post_identity)].

    Only well-typed entries (string path + string post_identity) are returned.
    """
    ext = event.get("extensions", {})
    eff = ext.get("effect", []) if isinstance(ext, dict) else []
    out: list[tuple[str, str]] = []
    if isinstance(eff, list):
        for e in eff:
            if isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("post_identity"), str):
                out.append((e["path"], e["post_identity"]))
    return out


class CaseSAuthorityProfile:
    """Configured Case S bootstrap authority profile.

    human_principals: actor strings recognized as the bootstrap human
    authority (e.g. "human:owner"). Everything else that authors a formal
    receipt is treated as an executor/AI principal; "sensor:*" actors may
    only author observed receipts.
    """

    def __init__(
        self,
        human_principals: tuple[str, ...] = ("human:owner",),
        registry: SubtypeRegistry | None = None,
    ) -> None:
        self.human_principals = frozenset(human_principals)
        self.registry = registry

    # ------------------------------------------------------------------
    # actor classification
    # ------------------------------------------------------------------

    def actor_role(self, actor: Any) -> str:
        """Classify an actor string: "human" | "executor" | "sensor" | "unknown"."""
        if not isinstance(actor, str) or not actor:
            return "unknown"
        if actor in self.human_principals:
            return "human"
        if actor.startswith("sensor:"):
            return "sensor"
        return "executor"

    # ------------------------------------------------------------------
    # authority verdicts
    # ------------------------------------------------------------------

    def is_authorized(self, event: dict[str, Any]) -> tuple[bool, str]:
        """May this actor author this type/subtype/receipt_class receipt?

        Returns (authorized, reason). Registry combination legality is
        checked first (INVALID combo), then the actor/subtype role rule.
        """
        subtype = event.get("subtype")
        type_ = event.get("type")
        receipt_class = event.get("receipt_class")
        actor = event.get("actor")
        role = self.actor_role(actor)

        if self.registry is not None:
            classification = self.registry.classify_event(event)
            if classification is not None:
                label, reason = classification
                # INVALID registered combo -> unauthorized outright.
                # UNCLASSIFIED (unregistered subtype) -> not eligible for
                # authority evaluation; caller treats as no-effect evidence.
                if label == "INVALID":
                    return (False, f"invalid combination: {reason}")
                if label == "UNCLASSIFIED":
                    return (False, f"unclassified: {reason}")
        else:
            # no registry: fall back to hardcoded Slice 1 receipt classes
            if receipt_class not in ("claim", "observed", "self-verification"):
                return (False, f"receipt_class {receipt_class!r} not in Slice 1 set")

        if role == "human":
            if subtype in HUMAN_SUBTYPES:
                return (True, "human principal authorized for this subtype")
            return (
                False,
                f"human principal not authorized for subtype {subtype!r}",
            )
        if role == "executor":
            if subtype in EXECUTOR_SUBTYPES:
                return (True, "executor authorized for this subtype")
            return (
                False,
                f"executor/AI not authorized for subtype {subtype!r} "
                "(formal authorization is human-principal-only in Case S)",
            )
        if role == "sensor":
            if subtype in SENSOR_SUBTYPES:
                return (True, "sensor authorized for observed receipt")
            return (
                False,
                f"sensor principal not authorized for subtype {subtype!r}",
            )
        return (False, f"actor {actor!r} not a recognized Case S principal")

    # ------------------------------------------------------------------
    # assurance verdicts
    # ------------------------------------------------------------------

    def assurance_of(self, event: dict[str, Any]) -> str:
        """RECEIPT-CLASS assurance of this event in ISOLATION (S1-F1: this is
        NOT the effective governance assurance).

        - self-verification receipts carry receipt-class "self-verification"
          ONLY when the event is authorized (a malformed/unauthorized
          self-verification carries nothing).
        - everything else carries "unverified".

        Effective assurance (VERIFIED-SELF) additionally requires the
        supported target to exist, be valid/authorized, be in scope, and be
        effect-compatible — see evaluate_receipt_assurance().
        """
        authorized, _ = self.is_authorized(event)
        if not authorized:
            return "unverified"
        if event.get("subtype") == "self-verification" and event.get("receipt_class") == "self-verification":
            return "verified-self-receipt"  # receipt class only; NOT yet effective
        return "unverified"


def _supported_target_ids(event: dict[str, Any]) -> list[str]:
    pr = event.get("prior_refs", {})
    if not isinstance(pr, dict):
        return []
    supports = pr.get("supports", [])
    return [s for s in supports if isinstance(s, str)] if isinstance(supports, list) else []


def evaluate_receipt_assurance(
    events: list[dict[str, Any]],
    profile: CaseSAuthorityProfile | None = None,
) -> dict[str, str]:
    """Map event_id -> EFFECTIVE assurance string for a whole event corpus
    (R3 + S1-F1 + S1-G1).

    Effective assurance layers:
      - "verified-self"          — authorized self-verification whose support
                                   target(s) resolve (exist, are valid/authorized,
                                   same-subject, eligible subtype); when the SV
                                   carries effect entries, EVERY SV effect pair
                                   must be present in the UNION of effect pairs
                                   across ALL eligible supported targets
                                   (S1-G1: per-effect scope — unmatched effect
                                   entries cannot make the receipt effective).
                                   SV with no effect entries may still resolve
                                   to verified-self for task-level derived
                                   COMPLETE but cannot classify a workspace
                                   path (no effect binding).
      - "verified-self-receipt"  — authorized self-verification whose support
                                   does NOT resolve (missing/invalid/
                                   unauthorized/out-of-scope/non-eligible
                                   target), or whose effect entries are NOT all
                                   contained in the eligible-target effect
                                   union. Contributes NO assurance anywhere.
      - "unverified"             — everything else

    VERIFIED-INDEPENDENT is never fabricated.
    """
    profile = profile or CaseSAuthorityProfile()
    by_eid = {ev.get("event_id"): ev for ev in events if isinstance(ev, dict)}

    def _authorized(ev: dict[str, Any] | None) -> bool:
        if ev is None:
            return False
        ok, _ = profile.is_authorized(ev)
        return ok

    out: dict[str, str] = {}
    for ev in events:
        eid = ev.get("event_id")
        if not isinstance(eid, str):
            continue
        effective = "unverified"
        if _authorized(ev) and ev.get("subtype") == "self-verification":
            sv_effects = effect_entries(ev)
            eligible_targets: list[dict[str, Any]] = []
            for tid in _supported_target_ids(ev):
                t = by_eid.get(tid)
                if t is None:
                    continue  # nonexistent target
                if not _authorized(t):
                    continue  # invalid/unauthorized target
                if t.get("subject") != ev.get("subject"):
                    continue  # out of scope
                if t.get("subtype") not in ELIGIBLE_SELF_VERIFICATION_TARGETS:
                    continue  # not an eligible claim/effect target
                eligible_targets.append(t)
            if not eligible_targets:
                # Authorized SV with no resolvable eligible target:
                # receipt class is still "self-verification" but no target
                # resolved — verified-self-receipt (never effective).
                effective = "verified-self-receipt"
            else:
                # S1-G1: build the union of effect pairs across ALL eligible
                # supported targets. If the SV carries effect entries, every
                # SV effect pair MUST be present in that union before the
                # SV may receive receipt-wide verified-self. Otherwise it
                # stays at verified-self-receipt (non-effective — none of
                # its effects may classify COVERED).
                if sv_effects:
                    target_effect_union: set[tuple[str, str]] = set()
                    for t in eligible_targets:
                        for pair in effect_entries(t):
                            target_effect_union.add(pair)
                    if all(pair in target_effect_union for pair in sv_effects):
                        effective = "verified-self"
                    else:
                        effective = "verified-self-receipt"
                else:
                    # SV has no effects: task-level resolution only (cannot
                    # classify a workspace path because no effect binding).
                    effective = "verified-self"
        out[eid] = effective
    return out


def evaluate_receipt_validity(
    events: list[dict[str, Any]],
    profile: CaseSAuthorityProfile | None = None,
) -> dict[str, bool]:
    """Map event_id -> authorized(bool) for a whole event corpus (runtime
    receipt-validity layer for observe/status; R3)."""
    profile = profile or CaseSAuthorityProfile()
    out: dict[str, bool] = {}
    for ev in events:
        eid = ev.get("event_id")
        if not isinstance(eid, str):
            continue
        authorized, _ = profile.is_authorized(ev)
        out[eid] = authorized
    return out

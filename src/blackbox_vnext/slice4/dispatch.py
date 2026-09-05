"""bbx4.dispatch — formal TASK / dispatch authorization validator.

Per TASK `67b4b45be077` (S4-AA1..AA9):

AA1: Public semantic APIs accept a raw events_dir path only.
TrustedCorpus is implementation-private; it is not a public parameter.

AA5: Dispatch pre-event state uses the same historical-head evaluator
from state.py (capabilities_at_effective_head). No forgeable subset corpus.

A canonical `TASK / dispatch / claim` is formally authorized iff:
- the canonical corpus is structurally trustworthy (loaded internally);
- the target dispatch event is the canonical event in corpus;
- actor/type/subtype/receipt_class are valid per Slice 0 registry;
- the dispatch's prior_refs.parent lifecycle is satisfied;
- issuer has `task.dispatch` capability for the dispatch surface
  in pre-event authority state;
- delegated issuer cites supports identifying an effective historical
  head (per AA4 evaluator);
- unknown actor/action/surface is fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blackbox_vnext.slice4 import (
    corpus_gate,
    dependency_pin,
    state as state_mod,
)
from blackbox_vnext.slice4.authority_profile import (
    DEFAULT_HUMAN_PRINCIPALS,
    UNKNOWN_ACTOR,
    UNKNOWN_ACTION,
    UNKNOWN_SURFACE,
    Capability,
    is_known_action,
    is_known_surface,
)
from blackbox_vnext.slice4.trusted_corpus import (
    TrustedCorpus,
    load_trusted_corpus,
)


# --- Failure reasons ------------------------------------------------------

CORPUS_UNTRUSTWORTHY = "CORPUS-UNTRUSTWORTHY"
TYPE_INVALID = "TYPE-INVALID"
SUBTYPE_INVALID = "SUBTYPE-INVALID"
RECEIPT_INVALID = "RECEIPT-INVALID"
EXTENSION_INVALID = "EXTENSION-INVALID"
AUTHORITY_MISSING = "AUTHORITY-MISSING"
ASSIGNEE_NOT_GRANTED = "ASSIGNEE-NOT-GRANTED"
EVENT_NOT_FOUND = "EVENT-NOT-FOUND"
TARGET_IDENTITY_MISMATCH = "TARGET-IDENTITY-MISMATCH"
DISPATCH_PARENT_INVALID = "DISPATCH-PARENT-INVALID"
DISPATCH_PARENT_CROSS_SUBJECT = "DISPATCH-PARENT-CROSS-SUBJECT"
DISPATCH_PARENT_STALE = "DISPATCH-PARENT-STALE"
DISPATCH_PARENT_UNRESOLVED_FORK = "DISPATCH-PARENT-UNRESOLVED-FORK"
DISPATCH_GENESIS_REJECTED = "DISPATCH-GENESIS-REJECTED"


# --- Result types ---------------------------------------------------------


@dataclass(frozen=True)
class DispatchDecision:
    """Outcome of validating a formal TASK / dispatch event."""

    event_id: str
    authorized: bool
    reason: str
    detail: str = ""
    # Downstream gates must distinguish module-issued decisions from values
    # assembled by a caller.  The witness is outside public value semantics.
    _issuer: object | None = field(default=None, repr=False, compare=False)


_DECISION_ISSUER = object()


def _issued_decision(**kwargs: object) -> DispatchDecision:
    """Create a decision carrying this module's issuance witness."""
    return DispatchDecision(**kwargs, _issuer=_DECISION_ISSUER)


def is_gate_authorization(decision: object) -> bool:
    """Accept only a current, issued green result from Slice 4.

    The semantic dependency gate runs again so a pinned Slice 0/1 drift makes
    even a formerly green decision ineligible for downstream authorization.
    """
    _gate()
    return (
        isinstance(decision, DispatchDecision)
        and decision._issuer is _DECISION_ISSUER
        and decision.authorized
        and decision.reason == "OK"
    )


# --- Helpers --------------------------------------------------------------


def _gate() -> None:
    """Fail-closed dependency-pin gate."""
    dependency_pin.gate_semantic_entrypoint(strict=True)


def _parent_eids(ev: dict) -> list[str]:
    refs = ev.get("prior_refs", {})
    parents = refs.get("parent", []) if isinstance(refs, dict) else []
    if not isinstance(parents, list):
        return []
    return [p for p in parents if isinstance(p, str)]


def _supports_eids(ev: dict) -> list[str]:
    refs = ev.get("prior_refs", {})
    supports = refs.get("supports", []) if isinstance(refs, dict) else []
    if not isinstance(supports, list):
        return []
    return [s for s in supports if isinstance(s, str)]


def _event_shape_ok_for_dispatch(ev: dict) -> tuple[bool, str]:
    if ev.get("type") != "TASK":
        return False, TYPE_INVALID
    if ev.get("subtype") != "dispatch":
        return False, SUBTYPE_INVALID
    if ev.get("receipt_class") != "claim":
        return False, RECEIPT_INVALID
    return True, "OK"


def _extension_dispatch_ok(ev: dict) -> tuple[bool, str]:
    ext = ev.get("extensions")
    if not isinstance(ext, dict):
        return False, EXTENSION_INVALID
    if "dispatch" not in ext:
        return False, EXTENSION_INVALID
    try:
        d = corpus_gate.parse_dispatch(ext["dispatch"])
    except ValueError:
        return False, EXTENSION_INVALID
    if not is_known_surface(d.surface):
        return False, UNKNOWN_SURFACE
    return True, "OK"


def _dispatch_parent_ok(
    ev: dict,
    ev_index: dict[str, dict],
    dispatch_heads: dict[str, str | None],
    fork_subjects: set[str],
) -> tuple[bool, str]:
    """Validate the dispatch's prior_refs.parent lifecycle.

    Genesis dispatch: zero parent allowed iff subject has no head.
    """
    refs = ev.get("prior_refs", {})
    parents = refs.get("parent", []) if isinstance(refs, dict) else []
    if not isinstance(parents, list):
        return False, DISPATCH_PARENT_INVALID

    subject = ev.get("subject", "")

    if len(parents) == 0:
        head = dispatch_heads.get(subject)
        if head is not None:
            return False, DISPATCH_PARENT_STALE
        return True, "DISPATCH-GENESIS"

    if len(parents) != 1:
        return False, DISPATCH_PARENT_INVALID
    peid = parents[0]
    if not isinstance(peid, str) or not peid:
        return False, DISPATCH_PARENT_INVALID
    if peid not in ev_index:
        return False, DISPATCH_PARENT_INVALID
    parent = ev_index[peid]

    parent_subject = parent.get("subject", "")
    if parent_subject != subject:
        return False, DISPATCH_PARENT_CROSS_SUBJECT
    head = dispatch_heads.get(subject)
    if head != peid:
        return False, DISPATCH_PARENT_STALE
    if subject in fork_subjects:
        return False, DISPATCH_PARENT_UNRESOLVED_FORK

    ptype = parent.get("type")
    psubtype = parent.get("subtype")
    valid_state_trans = {
        ("TASK", "dispatch"),
        ("TASK", "execution-result"),
        ("TASK", "completion-claim"),
        ("DECISION", "acceptance"),
        ("CHANGE", "authority-change"),
        ("CHANGE", "resolution"),
    }
    if (ptype, psubtype) not in valid_state_trans:
        return False, DISPATCH_PARENT_INVALID

    return True, "OK"


def _compute_dispatch_heads(
    events: list[dict],
) -> tuple[dict[str, str | None], set[str]]:
    """Compute current dispatch head per task subject, plus fork-blocked subjects."""
    ev_index: dict[str, dict] = {}
    for ev in events:
        eid = ev.get("event_id")
        if isinstance(eid, str) and eid and eid not in ev_index:
            ev_index[eid] = ev

    heads: dict[str, str | None] = {}
    fork_subjects: set[str] = set()
    groups: dict[str, dict[str, list[str]]] = {}

    for ev in sorted(events, key=lambda e: e.get("event_id", "")):
        if not _event_shape_ok_for_dispatch(ev)[0]:
            continue
        parents = _parent_eids(ev)
        subject = ev.get("subject", "")
        if len(parents) == 0:
            if subject not in heads:
                heads[subject] = None
            continue
        if len(parents) != 1:
            continue
        peid = parents[0]
        if peid not in ev_index:
            continue
        parent = ev_index[peid]
        if parent.get("subject") != subject:
            continue
        groups.setdefault(subject, {}) \
             .setdefault(peid, []).append(ev["event_id"])

    for subject, by_parent in groups.items():
        for _peid, children in by_parent.items():
            if len(children) >= 2:
                fork_subjects.add(subject)
                break
            if len(children) == 1:
                heads[subject] = children[0]
    return heads, fork_subjects


# --- Public API (AA1: events_dir path, no TrustedCorpus parameter) ----------


def validate_dispatch(
    target_event_id: str,
    events_dir: str | Path,
) -> DispatchDecision:
    """Validate a TASK / dispatch event by its event_id against a corpus.

    Per AA1: accepts events_dir path only. The TrustedCorpus is loaded
    internally. No TrustedCorpus parameter is exposed.

    The dispatch must be the canonical event in the corpus (event_id
    lookup). Bootstrap principals are authorized by root policy.
    """
    _gate()
    corpus = load_trusted_corpus(events_dir)
    return _validate_dispatch_by_id(target_event_id, corpus)


def _validate_dispatch_by_id(
    target_event_id: str,
    corpus: TrustedCorpus,
) -> DispatchDecision:
    if not isinstance(target_event_id, str) or not target_event_id:
        return _issued_decision(event_id="", authorized=False,
                                reason=EVENT_NOT_FOUND)

    canonical = corpus.event_by_id(target_event_id)
    if canonical is None:
        return _issued_decision(event_id=target_event_id, authorized=False,
                                reason=EVENT_NOT_FOUND)

    eid_str = canonical.get("event_id", "")

    trustworthy, reason = _corpus_trust(corpus)
    if not trustworthy:
        return _issued_decision(event_id=eid_str, authorized=False,
                                reason=reason, detail="corpus untrustworthy")

    ok, reason = _event_shape_ok_for_dispatch(canonical)
    if not ok:
        return _issued_decision(event_id=eid_str, authorized=False, reason=reason)
    ok, reason = _extension_dispatch_ok(canonical)
    if not ok:
        return _issued_decision(event_id=eid_str, authorized=False, reason=reason)

    events = list(corpus.events)
    ev_index: dict[str, dict] = {}
    for ev in events:
        ceid = ev.get("event_id")
        if isinstance(ceid, str) and ceid and ceid not in ev_index:
            ev_index[ceid] = ev

    dispatch_heads, fork_subjects = _compute_dispatch_heads(events)
    ok, reason = _dispatch_parent_ok(canonical, ev_index,
                                      dispatch_heads, fork_subjects)
    if not ok:
        return _issued_decision(event_id=eid_str, authorized=False, reason=reason)

    # Derive pre-event authority state via AA5 path-only API.
    auth_state = state_mod.derive_pre_event_state(corpus.dir, target_event_id=eid_str)

    issuer = canonical.get("actor", "")
    surface = canonical["extensions"]["dispatch"]["surface"]
    if not isinstance(issuer, str) or not issuer:
        return _issued_decision(event_id=eid_str, authorized=False,
                                reason=UNKNOWN_ACTOR)

    if issuer in DEFAULT_HUMAN_PRINCIPALS:
        return _issued_decision(event_id=eid_str, authorized=True,
                                reason="OK",
                                detail="bootstrap-human root authority")

    known = auth_state.known_principals()
    if issuer not in known:
        return _issued_decision(event_id=eid_str, authorized=False,
                                reason=UNKNOWN_ACTOR,
                                detail=f"issuer {issuer!r} not known to authority")
    cap = Capability(actor=issuer, action="task.dispatch", surface=surface)
    held = auth_state.held_by(issuer)
    if cap not in held:
        return _issued_decision(event_id=eid_str, authorized=False,
                                reason=AUTHORITY_MISSING,
                                detail=f"issuer {issuer!r} has no task.dispatch on {surface!r}")

    return _issued_decision(event_id=eid_str, authorized=True,
                            reason="OK",
                            detail=f"delegated grant on {surface!r}")


def authority_status(actor: str, events_dir: str | Path) -> dict:
    """Return a structured capability report for the actor.

    Per AA1: accepts events_dir path only.
    """
    _gate()
    corpus = load_trusted_corpus(events_dir)
    return _authority_status_ungated(actor, corpus)


def _authority_status_ungated(actor: str, corpus: TrustedCorpus) -> dict:
    if not isinstance(actor, str) or not actor:
        return {
            "actor": actor,
            "known": False,
            "reason": UNKNOWN_ACTOR,
            "capabilities": [],
            "conflict": False,
        }
    auth_state = state_mod.derive_authority_state(corpus.dir)
    known = auth_state.known_principals()
    if actor not in known:
        return {
            "actor": actor,
            "known": False,
            "reason": UNKNOWN_ACTOR,
            "capabilities": [],
            "conflict": False,
        }
    stream = auth_state.streams.get(actor)
    if stream is None:
        if actor in DEFAULT_HUMAN_PRINCIPALS:
            from blackbox_vnext.slice4.authority_profile import bootstrap_capabilities
            caps = sorted(
                (f"{c.action}@{c.surface}" for c in bootstrap_capabilities()),
            )
            return {
                "actor": actor,
                "known": True,
                "reason": "BOOTSTRAP",
                "capabilities": caps,
                "conflict": False,
            }
        return {
            "actor": actor,
            "known": False,
            "reason": UNKNOWN_ACTOR,
            "capabilities": [],
            "conflict": False,
        }
    return {
        "actor": actor,
        "known": True,
        "reason": stream.reason,
        "capabilities": sorted(
            f"{c.action}@{c.surface}" for c in stream.capabilities
        ),
        "conflict": stream.conflict,
    }


def validate_authority(events_dir: str | Path) -> dict:
    """Validate the corpus as a whole and return derived authority state.

    Per AA1: accepts events_dir path only. Loads TrustedCorpus internally.
    """
    _gate()
    corpus = load_trusted_corpus(events_dir)
    return _validate_authority_ungated(corpus)


def _corpus_trust(corpus: TrustedCorpus) -> tuple[bool, str]:
    if corpus.corpus_blocked:
        return False, CORPUS_UNTRUSTWORTHY
    return True, "OK"


def _validate_authority_ungated(corpus: TrustedCorpus) -> dict:
    trustworthy, reason = _corpus_trust(corpus)
    if not trustworthy:
        return {
            "trustworthy": False,
            "reason": reason,
            "streams": {},
        }
    fold = state_mod._fold_events(list(corpus.events), corpus.registry)
    out: dict[str, dict] = {}
    for actor, stream in fold.state.streams.items():
        out[actor] = _authority_status_for_stream(actor, stream)
    return {
        "trustworthy": fold.trustworthy,
        "reason": "OK" if fold.trustworthy else "NON-GREEN-EVENTS",
        "per_event": {
            eid: {"ok": d.ok, "reason": d.reason, "detail": d.detail}
            for eid, d in fold.per_event.items()
        },
        "streams": out,
    }


def _authority_status_for_stream(actor: str, stream) -> dict:
    return {
        "actor": actor,
        "known": True,
        "reason": stream.reason,
        "capabilities": sorted(
            f"{c.action}@{c.surface}" for c in stream.capabilities
        ),
        "conflict": stream.conflict,
    }

"""bbx4.state — Causal scheduler + historical pre-state fold.

Per Slice 4 frozen profile (Reference Design v0.1.4 + Formal Spec
v0.3.2 §§7/9), corrected per TASK `67b4b45be077` (S4-AA1..AA9):

AA1: Public semantic APIs accept a raw events_dir path only.
TrustedCorpus / _InternalTrustedSnapshot are implementation-private.
All callers write events to disk and pass the directory path.

AA2: Retryable decisions live in a separate `_pending` map. Only
non-retryable decisions are committed to `_decisions`. The scheduler
reprocesses pending events after each pass until no progress.

AA3: Fork arbitration groups all eligible candidates at their common
frontier BEFORE mutating any stream head. If >=2 eligible siblings
share the same frontier, none wins — the stream enters conflict.

AA4: `capabilities_at_effective_head(issuer, support_eid, ev_index)`
reconstructs exact capabilities at the historical head by re-running
the fold up to the cited support event. A support is effective iff
its own fold decision is ok=True.

AA5: Pre-event state uses the same historical-head evaluator, not a
forgeable subset corpus. Bootstrap dispatch gets root capability from
policy input; delegated dispatch requires support citing an effective
issuer head.

AA6: Resolution applies ONLY when the subject has a real unresolved
conflict created by AA3. Resolution parents must exactly match the
competing heads. Resolution on a non-conflicting stream is a no-op
and does NOT advance the head.

AA7: Delegated resolution authorization requires `prior_refs.supports`
citing an effective historical head for the issuer. Uses AA4 evaluator.

AA8: _InternalTrustedSnapshot is not accepted by public functions.
Private `_derive_*_internal(snapshot)` are the only internal entry points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blackbox_vnext.slice4 import corpus_gate, dependency_pin
from blackbox_vnext.slice4.authority_profile import (
    BOOTSTRAP_IMMUTABLE,
    Capability,
    DEFAULT_HUMAN_PRINCIPALS,
    EVENT_INVALID,
    GENESIS_FORK_ORPHANED,
    ISSUER_PRESSTATE_INVALID,
    PARENT_CROSS_SUBJECT,
    PARENT_INVALID,
    PARENT_STALE,
    RESOLUTION_APPLIED,
    RESOLUTION_INVALID,
    SELF_AUTHORIZE,
    STREAM_CONFLICT,
    SUPPORTS_INVALID,
    SUPPORTS_MISSING,
    UNKNOWN_ACTION,
    UNKNOWN_SURFACE,
    RESOLUTION_NO_CONFLICT,
    bootstrap_capabilities,
    is_bootstrap_root_principal,
    is_known_action,
    is_known_surface,
)
from blackbox_vnext.slice4.trusted_corpus import (
    TrustedCorpus,
    _InternalTrustedSnapshot,
    load_trusted_corpus,
)


# --- Failure reasons (re-exports for backwards compat) ----------------------

__all__ = [
    "AuthorityState",
    "AuthorityStream",
    "AuthorityFoldResult",
    "EventDecision",
    "STREAM_CONFLICT",
    "EVENT_INVALID",
    "PARENT_INVALID",
    "SELF_AUTHORIZE",
    "PARENT_CROSS_SUBJECT",
    "PARENT_STALE",
    "UNKNOWN_ACTION",
    "UNKNOWN_SURFACE",
    "SUPPORTS_INVALID",
    "SUPPORTS_MISSING",
    "ISSUER_PRESSTATE_INVALID",
    "RESOLUTION_APPLIED",
    "RESOLUTION_INVALID",
    "BOOTSTRAP_IMMUTABLE",
    "RESOLUTION_NO_CONFLICT",
    "derive_authority_state",
    "derive_pre_event_state",
]


# --- State types ---------------------------------------------------------


@dataclass(frozen=True)
class AuthorityStream:
    """Authority state for one beneficiary stream."""

    beneficiary: str
    head_event_id: str | None  # None = empty (never touched)
    capabilities: frozenset[Capability]
    conflict: bool = False
    reason: str = ""


@dataclass(frozen=True)
class EventDecision:
    """Per-event machine-readable fold decision (Q8).

    `ok=True` iff the event took effect at full authority. Otherwise
    `reason` is a machine-visible failure code.

    `retryable=True` (AA2) means prerequisites were not yet finalized;
    the event waits in the pending set and is retried after more
    corpus evaluation. A non-retryable decision is final.
    """

    event_id: str
    ok: bool
    reason: str
    detail: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class AuthorityFoldResult:
    """Top-level fold result.

    `state` carries derived streams. `per_event` carries per-event
    decisions. `trustworthy` is True iff every formal authority-change /
    resolution got ok=True.
    """

    state: "AuthorityState"
    per_event: dict[str, EventDecision]
    trustworthy: bool


@dataclass(frozen=True)
class AuthorityState:
    """Deterministic derived authority state across all beneficiary streams."""

    streams: dict[str, AuthorityStream]

    def held_by(self, actor: str) -> frozenset[Capability]:
        s = self.streams.get(actor)
        if s is None or s.conflict:
            return frozenset()
        return s.capabilities

    def has_capability(self, actor: str, action: str, surface: str) -> bool:
        target = Capability(actor=actor, action=action, surface=surface)
        return target in self.held_by(actor)

    def is_conflict(self, actor: str) -> bool:
        s = self.streams.get(actor)
        return s is not None and s.conflict

    def known_principals(self) -> frozenset[str]:
        out = set(DEFAULT_HUMAN_PRINCIPALS)
        for actor, stream in self.streams.items():
            if stream.head_event_id is not None or stream.conflict:
                out.add(actor)
        return frozenset(out)


# --- Internal helpers ---------------------------------------------------


def _stream_subject(actor: str) -> str:
    return f"authority:{actor}"


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


def _is_authority_change(ev: dict) -> bool:
    return (
        ev.get("type") == "CHANGE"
        and ev.get("subtype") == "authority-change"
        and ev.get("receipt_class") == "claim"
    )


def _is_resolution(ev: dict) -> bool:
    return (
        ev.get("type") == "CHANGE"
        and ev.get("subtype") == "resolution"
        and ev.get("receipt_class") == "claim"
    )


def _parse_authority_change_payload(ev: dict) -> tuple[Any, str]:
    """Parse the AUTHORITY-CHANGE extension. Returns (parsed, reason).

    Per J1: this is the SINGLE registry gate for AUTHORITY-CHANGE
    events. action must be in registered `ACTIONS`; surface must be
    in registered `SURFACES`. Unknown values retain their exact
    machine-visible `UNKNOWN-ACTION` / `UNKNOWN-SURFACE` reasons.
    Both the apply path and the grouping path consume this function,
    so the gate cannot be bypassed or drift between paths.
    """
    ext = ev.get("extensions", {})
    if not isinstance(ext, dict):
        return None, EVENT_INVALID
    ach = ext.get("authority_change")
    try:
        ac = corpus_gate.parse_authority_change(ach)
    except ValueError:
        return None, EVENT_INVALID
    # J1 registry gate (single chokepoint, shared by apply + grouping)
    if not is_known_action(ac.action):
        return None, UNKNOWN_ACTION
    if not is_known_surface(ac.surface):
        return None, UNKNOWN_SURFACE
    return ac, ""


def _parse_resolution_payload(ev: dict) -> tuple[Any, str]:
    ext = ev.get("extensions", {})
    if not isinstance(ext, dict):
        return None, EVENT_INVALID
    try:
        return corpus_gate.parse_resolution(ext.get("resolution")), ""
    except ValueError:
        return None, EVENT_INVALID


# --- Fold state ---------------------------------------------------------


@dataclass
class _ReplayState:
    streams: dict[str, AuthorityStream] = field(default_factory=dict)
    heads: dict[str, str | None] = field(default_factory=dict)
    # Per-event finalized decisions (non-retryable only, AA2).
    _decisions: dict[str, EventDecision] = field(default_factory=dict)
    # Separate pending map for retryable events (AA2). Cleared each pass.
    _pending: dict[str, tuple[dict, str]] = field(default_factory=dict)
    # In-flight zero-parent genesis eids per beneficiary. Used by the fold
    # loop to detect sibling-fork conflicts where both siblings are
    # zero-parent and neither has an established issuer stream (so both are
    # excluded from the sibling-groups scan that runs before any apply).
    _pending_genesis: dict[str, list[str]] = field(default_factory=dict)
    # Maps beneficiary -> event_id that created that stream in this fold.
    # Used by the orphan check to distinguish a true orphan (competing
    # genesis from a DIFFERENT prior) from a sibling fork (two genesis
    # grants for the same beneficiary in the same pass).
    _stream_creators: dict[str, str] = field(default_factory=dict)
    # When True, stream mutations (head_event_id, conflict) are suppressed.
    # Used by _effective_caps_for_head to run read-only isolated sub-folds
    # during authorization checking: stream mutations don't propagate back to
    # the main fold state and would corrupt the supports evaluation chain.
    _suppress_stream_mutations: bool = False
    # Exact unresolved competing head event_ids per beneficiary stream.
    # Populated when AA3 creates STREAM_CONFLICT (line ~1278).
    # Cleared when a valid resolution applies (line ~1412).
    # Used by _apply_resolution_raw to verify the resolution's parent set
    # exactly matches the unresolved heads (AA6 exact conflict-head set).
    _conflicting_heads: dict[str, frozenset[str]] = field(default_factory=dict)
    # Maps event_id -> {subject -> head_eid} snapshot taken at the moment
    # the event was applied. Used by AC1 to verify that a cited support
    # event was the EXACT issuer head when the using event was applied
    # (not just the current fold head, which may have advanced since then).
    _head_snapshots: dict[str, dict[str, str | None]] = field(default_factory=dict)
    # Maps event_id -> frozenset[Capability] snapshot of the beneficiary
    # stream's capabilities at the moment the event was applied. Used by
    # AD2 to return historical cut capabilities (not current stream caps
    # which may include later grants/revokes that occurred after support_eid).
    _caps_snapshots: dict[str, frozenset[Capability]] = field(default_factory=dict)
    # Maps event_id -> frozenset[Capability] snapshot of the ISSUER's
    # capabilities at the moment a DELEGATED GENESIS grant was applied.
    # Used by capabilities_at_effective_head to return the EXACT historical
    # issuer pre-state for a delegated genesis support event. For non-delegated
    # genesis grants (bootstrap) this map is empty (those supports use
    # bootstrap_capabilities which is constant).
    _issuer_caps_snapshots: dict[str, frozenset[Capability]] = field(default_factory=dict)


def _stream_held_for(state: _ReplayState, actor: str) -> frozenset[Capability]:
    """Return only the stream's own capabilities for `actor`.

    Does NOT merge bootstrap capabilities — use this for pre-state
    authorization checks in the grouping phase. Bootstrap principals
    are handled explicitly by the caller (they don't need pre-state
    stream capabilities to authorize events).
    """
    s = state.streams.get(actor)
    return frozenset(s.capabilities) if s else frozenset()


def _held_for(state: _ReplayState, actor: str) -> frozenset[Capability]:
    """Return capabilities currently held by `actor` in pre-state.

    The bootstrap principal `human:owner` merges bootstrap capabilities
    (fixed policy input) with its derived stream. Delegated principals
    (executor:a, etc.) only hold what they were explicitly granted;
    they do NOT inherit bootstrap augmentation.
    """
    s = state.streams.get(actor)
    base = set(s.capabilities if s else frozenset())
    if is_bootstrap_root_principal(actor):
        base.update(bootstrap_capabilities())
    return frozenset(base)


# --- Decision reasons that permanently block (AA2 final reasons) ----------


_FINAL_REASONS: frozenset[str] = frozenset({
    EVENT_INVALID,
    PARENT_INVALID,
    PARENT_CROSS_SUBJECT,
    PARENT_STALE,
    SELF_AUTHORIZE,
    BOOTSTRAP_IMMUTABLE,
    STREAM_CONFLICT,
    GENESIS_FORK_ORPHANED,
    RESOLUTION_INVALID,
    RESOLUTION_NO_CONFLICT,
})


def _prerequisites_ready(
    ev: dict,
    state: _ReplayState,
    ev_index: dict[str, dict],
) -> tuple[bool, str]:
    """Return (ready, reason). All parent + supports refs must have
    finalized (non-retryable) decisions in state._decisions.

    Per AA2: a prerequisite is not ready if its decision is retryable
    or absent. Only finalized (non-retryable) decisions satisfy prerequisites.

    For resolution events: a sibling that reached a non-retryable
    STREAM_CONFLICT decision is considered ready (AA6: resolution is
    scheduled after the sibling-fork conflict is detected, so those
    conflict-marked siblings are valid resolution parents).
    """
    refs = _parent_eids(ev) + _supports_eids(ev)
    if not refs:
        return True, ""
    for rid in refs:
        if rid not in ev_index:
            return True, ""
        d = state._decisions.get(rid)
        if d is None:
            return False, "PREREQUISITE-NOT-YET-EVALUATED"
        if d.retryable:
            return False, "PREREQUISITE-NOT-YET-EVALUATED"
        # Only permanently block on ok=False non-retryable failures.
        # ok=True prerequisites (including the "OK" sentinel) are satisfied.
        if not d.ok:
            # For resolution events: sibling fork conflicts are ready parents (AA6).
            if not (_is_resolution(ev) and d.reason == STREAM_CONFLICT):
                return False, d.reason
    return True, ""


def _unblock_dependents(state: "_ReplayState", resolved_eid: str,
                         ev_index: dict[str, dict]) -> None:
    """Remove pending entries whose prerequisites are now satisfied by
    the finalized `resolved_eid` decision.

    Called after a non-retryable decision enters _decisions so that
    the next scheduler pass re-checks those events' readiness.
    """
    to_remove = []
    for peid, (ev, _reason) in list(state._pending.items()):
        ready, _ = _prerequisites_ready(ev, state, ev_index)
        if ready:
            to_remove.append(peid)
    for peid in to_remove:
        del state._pending[peid]


# --- AA4: exact effective historical head evaluator ----------------------


def _fold_up_to_head(
    target_support_eid: str,
    ev_index: dict[str, dict],
    authority_events: list[dict],
    resolution_events: list[dict],
    outer_state: "_ReplayState | None" = None,
) -> tuple[dict[str, "EventDecision"], dict[str, AuthorityStream], dict[str, str | None], dict[str, dict[str, str | None]], dict[str, frozenset], dict[str, frozenset]]:
    """Re-run the fold restricted to events that are causally before
    target_support_eid (inclusive) and return per-event decisions.

    This reconstructs the exact authority state at the historical head
    cited by a support. Used by AA4 to validate support effectiveness
    and AA7 for delegated resolution authorization.

    `outer_state` (optional): supplies capability/head snapshots from the
    outer fold for events in this causal closure. Decisions are deliberately
    re-evaluated by the sub-fold.

    Returns (decisions, streams, heads, head_snapshots, caps_snapshots,
    issuer_caps_snapshots) at that historical head.
    """
    support_ev = ev_index.get(target_support_eid)
    if support_ev is None:
        return {}, {}, {}, {}, {}, {}

    # Build causal closure of all events before (and including) the support.
    visited: set[str] = set()
    frontier: list[str] = [target_support_eid]
    while frontier:
        cur = frontier.pop()
        if cur in visited:
            continue
        visited.add(cur)
        if cur not in ev_index:
            continue
        ev = ev_index[cur]
        for p in _parent_eids(ev):
            if p not in visited:
                frontier.append(p)
        for s in _supports_eids(ev):
            if s not in visited:
                frontier.append(s)

    # Filter to events in the causal closure.
    filtered_authority = [ev for ev in authority_events if ev.get("event_id", "") in visited]
    filtered_resolution = [ev for ev in resolution_events if ev.get("event_id", "") in visited]

    # Run the fold on the filtered set.
    sub_state = _ReplayState()
    bs_caps = bootstrap_capabilities()
    for actor in DEFAULT_HUMAN_PRINCIPALS:
        sub_state.streams[actor] = AuthorityStream(
            beneficiary=actor,
            head_event_id=None,
            capabilities=bs_caps,
            conflict=False,
            reason="BOOTSTRAP",
        )

    # AF1 fix: pre-populate _caps_snapshots, _head_snapshots, and
    # _issuer_caps_snapshots from outer_state for events in the causal
    # closure. Do NOT pre-populate _decisions — the sub-fold's event loop
    # must process every event to create streams and record _caps_snapshots.
    # Pre-populating _decisions would skip the loop body for those events,
    # leaving their streams uncreated in the sub-state — causing AC1
    # head-identity checks to fail (e.g., a sub-fold where h1's
    # _head_snapshots says authority:a→h1 but sub_state.streams['a'] doesn't
    # exist because h1 was skipped).
    #
    # Snapshot pre-population makes exact post-apply historical state available
    # for finalized authority and resolution heads in the same causal closure.
    if outer_state is not None:
        for ev in filtered_authority + filtered_resolution:
            eid = ev.get("event_id", "")
            if not (eid and eid in visited):
                continue
            if eid in outer_state._caps_snapshots and eid not in sub_state._caps_snapshots:
                sub_state._caps_snapshots[eid] = outer_state._caps_snapshots[eid]
            if eid in outer_state._head_snapshots and eid not in sub_state._head_snapshots:
                sub_state._head_snapshots[eid] = outer_state._head_snapshots[eid]
            if eid in outer_state._issuer_caps_snapshots and eid not in sub_state._issuer_caps_snapshots:
                sub_state._issuer_caps_snapshots[eid] = outer_state._issuer_caps_snapshots[eid]
            if eid not in sub_state._issuer_caps_snapshots:
                issuer = ev.get("actor", "")
                if is_bootstrap_root_principal(issuer):
                    sub_state._issuer_caps_snapshots[eid] = bootstrap_capabilities()

    # Parse and collect valid authority and resolution events.
    parsed_auth = []
    for ev in filtered_authority:
        ac, reason = _parse_authority_change_payload(ev)
        if reason:
            eid = ev.get("event_id", "")
            if eid:
                sub_state._decisions[eid] = EventDecision(eid, False, reason)
            continue
        parsed_auth.append(ev)

    parsed_resolution = []
    for ev in filtered_resolution:
        res, reason = _parse_resolution_payload(ev)
        if reason:
            eid = ev.get("event_id", "")
            if eid:
                sub_state._decisions[eid] = EventDecision(eid, False, reason)
            continue
        parsed_resolution.append(ev)

    candidate_eids = {
        ev.get("event_id", "")
        for ev in (parsed_auth + parsed_resolution)
        if isinstance(ev.get("event_id", ""), str)
    }
    # AC2/K4: use the same immutable pass-frontier semantics as the main
    # fold. Newly-ready events wait until the next pass, and ready siblings
    # are arbitrated before either mutates the reconstructed stream.
    for _pass in range(len(candidate_eids) + 4):
        progressed = False
        all_sub_candidates = parsed_auth + parsed_resolution
        pass_start_readiness = {
            ev.get("event_id", ""): _prerequisites_ready(
                ev, sub_state, ev_index
            )
            for ev in all_sub_candidates
            if ev.get("event_id", "") not in sub_state._decisions
        }
        pass_start_groups = _group_ready_eligible_siblings(
            parsed_auth, parsed_resolution, ev_index, sub_state,
            outer_state=sub_state,
        )
        for ev in sorted(
            all_sub_candidates,
            key=lambda e: e.get("event_id", ""),
        ):
            eid = ev.get("event_id", "")
            if not eid or eid in sub_state._decisions:
                continue
            ready, prereq_reason = pass_start_readiness.get(
                eid, (False, "PREREQUISITE-NOT-YET-EVALUATED")
            )
            if not ready:
                sub_state._pending[eid] = (ev, prereq_reason)
                continue

            if _is_authority_change(ev):
                ac, _ = _parse_authority_change_payload(ev)
                if ac:
                    parents = _parent_eids(ev)
                    frontier = parents[0] if parents else None
                    group = pass_start_groups.get(
                        (ac.beneficiary, frontier), []
                    )
                    if len(group) >= 2:
                        beneficiary = ac.beneficiary
                        subject = _stream_subject(beneficiary)
                        competing = frozenset(
                            sibling.get("event_id", "") for sibling in group
                            if sibling.get("event_id")
                        )
                        sub_state.streams[beneficiary] = AuthorityStream(
                            beneficiary=beneficiary,
                            head_event_id=None,
                            capabilities=frozenset(),
                            conflict=True,
                            reason=STREAM_CONFLICT,
                        )
                        sub_state.heads[subject] = None
                        sub_state._conflicting_heads[beneficiary] = competing
                        for sibling in group:
                            sibling_eid = sibling.get("event_id", "")
                            if sibling_eid and sibling_eid not in sub_state._decisions:
                                sub_state._decisions[sibling_eid] = EventDecision(
                                    sibling_eid, False, STREAM_CONFLICT,
                                    detail="eligible sibling fork (AA3)",
                                )
                            sub_state._pending.pop(sibling_eid, None)
                        progressed = True
                        continue

            if _is_authority_change(ev):
                _eid, decision = _apply_authority_change_raw(
                    sub_state, ev, ev_index,
                    parsed_auth, parsed_resolution,
                    outer_state=sub_state,
                )
            elif _is_resolution(ev):
                _eid, decision = _apply_resolution_raw(
                    sub_state, ev, ev_index,
                    parsed_auth, parsed_resolution,
                    outer_state=sub_state,
                )
            else:
                continue
            if decision.retryable:
                sub_state._pending[eid] = (ev, decision.reason)
            else:
                sub_state._decisions[eid] = decision
                if eid in sub_state._pending:
                    del sub_state._pending[eid]
                # Remove any pending entries whose prerequisite is now
                # satisfied by this non-retryable decision.
                _unblock_dependents(sub_state, eid, ev_index)
                progressed = True
        if not progressed:
            break

    # AC2: remaining pending events that could not finalize become
    # non-green / NO-PROGRESS after fixed point is reached.
    for eid, (ev, reason) in list(sub_state._pending.items()):
        if eid not in sub_state._decisions:
            sub_state._decisions[eid] = EventDecision(
                eid, False, reason or "NO-PROGRESS",
            )
    sub_state._pending.clear()

    # AD1+AD2: also return head snapshots and capability snapshots so
    # capabilities_at_effective_head can verify AC1 and return the EXACT
    # historical capability set at the cited support head.
    return (dict(sub_state._decisions), dict(sub_state.streams),
            dict(sub_state.heads), dict(sub_state._head_snapshots),
            dict(sub_state._caps_snapshots),
            dict(sub_state._issuer_caps_snapshots))


def capabilities_at_effective_head(
    issuer: str,
    support_eid: str,
    ev_index: dict[str, dict],
    authority_events: list[dict],
    resolution_events: list[dict],
    outer_state: "_ReplayState | None" = None,
) -> frozenset[Capability]:
    """AA4: Reconstruct exact capabilities for `issuer` at the historical
    head cited by `support_eid`.

    The cited support must be an EFFECTIVE historical head for the
    issuer — its own fold decision must be ok=True. If the support is
    not effective, returns empty frozenset.

    This is NOT a fixed-depth approximation: it re-runs the fold up
    to the cited support to get the exact capability set at that
    historical point.

    `outer_state` (optional): the calling fold's _ReplayState. When
    provided, supplies historical snapshots for events inside the same
    causal closure while the sub-fold still evaluates its own decisions.

    AC1 invariant (AD1+AD2 correction): support_eid's canonical subject
    must equal authority:<issuer>, and head_snapshots[support_eid] must
    record that issuer-subject head as support_eid.
    AD2: always returns _caps_snapshots[support_eid] from the sub-fold
    (or outer fold pre-populated), never the caller's current stream caps.
    """
    if support_eid not in ev_index:
        return frozenset()
    support_ev = ev_index.get(support_eid, {})
    support_subject = support_ev.get("subject", "")
    issuer_subject = _stream_subject(issuer)
    # AE1 fix: support is valid for issuer only when support's canonical subject
    # equals the issuer's lifecycle subject. A delegated genesis event's subject is
    # authority:<beneficiary>, NOT authority:<issuer> — so it can ONLY be a valid
    # support for the BENEFICIARY (the downstream issuer who received the grant),
    # not for the author who issued it. Example:
    #   g1: owner->a  (subject=authority:a)
    #   g2: a->b      (subject=authority:b) — valid support for b, not for a
    #   g3: a->c cites g2 — INVALID: g2 subject=authority:b != authority:a
    # If subjects don't match: the cited event is not in the issuer's lifecycle;
    # return empty immediately.
    if support_subject != issuer_subject:
        return frozenset()

    # A branch finalized as stale or conflicting in the caller's fold was
    # never an effective ledger head. Replaying only that branch's ancestors
    # must not let it substitute for a later resolution head.
    if outer_state is not None:
        outer_decision = outer_state._decisions.get(support_eid)
        if outer_decision is not None and not outer_decision.ok:
            return frozenset()

    # Support's subject matches issuer's subject. Run the sub-fold to reconstruct
    # the state at that support head. Outer-state prepopulation supplies exact
    # historical snapshots while sub-fold decisions remain independently derived.
    decisions, streams, _heads, head_snapshots, caps_snapshots, issuer_caps_snapshots = \
        _fold_up_to_head(
            support_eid, ev_index, authority_events, resolution_events,
            outer_state=outer_state,
        )
    decision = decisions.get(support_eid)
    if decision is None or not decision.ok:
        return frozenset()

    # AC1: verify support_eid is (was) the effective head of authority:<issuer>
    # at the moment of application. Only events whose subject equals the issuer's
    # subject can reach this point; here we check the head-identity binding.
    snapshot = head_snapshots.get(support_eid, {})
    if snapshot.get(issuer_subject) != support_eid:
        return frozenset()

    # AD2: return the EXACT historical capability set at the cited support head.
    # The returned set is what _apply_authority_change_raw uses to verify the
    # grant is authorized. For events whose subject == issuer_subject:
    #   - _caps_snapshots[support_eid] = BENEFICIARY's post-state at that head
    #   - _issuer_caps_snapshots[support_eid] = ISSUER's pre-state at that head
    #     (non-None only for delegated genesis events)
    # For delegated genesis: union issuer pre-state + grants made TO the issuer.
    # For bootstrap/non-genesis: use beneficiary post-state directly.
    # Never return current outer-state stream capabilities.
    # AF1 fix: after AE1 subject-equality gate and AC1 head-identity check,
    # return exactly the capability snapshot for the support's canonical subject
    # at that head. _caps_snapshots[support_eid] is the BENEFICIARY's post-state
    # at that head — the exact set of capabilities granted to the beneficiary.
    #
    # _issuer_caps_snapshots is NOT returned: it records the SUPPORT AUTHOR's
    # pre-state (not the beneficiary's), which must never be interpreted as
    # capabilities granted to the support's beneficiary. A delegated genesis
    # event a->b gives b a certain set of capabilities; the fact that a held
    # additional capabilities before issuing g2 is irrelevant to what b holds.
    historical_caps = caps_snapshots.get(support_eid)
    if historical_caps is None:
        return frozenset()
    if issuer in DEFAULT_HUMAN_PRINCIPALS:
        base = set(historical_caps)
        base.update(bootstrap_capabilities())
        return frozenset(base)
    return historical_caps


# --- Shared issuer-authorization predicate ----------------------------------

def _issuer_authorized_for(
    ac: Any,
    issuer: str,
    supports: list[str],
    ev_index: dict[str, dict],
    authority_events: list[dict],
    resolution_events: list[dict],
    outer_state: "_ReplayState | None" = None,
) -> bool:
    """Return True iff the issuer is historically authorized for the action
    described by ``ac``.

    Shared predicate used by both ``_apply_authority_change_raw`` (apply
    path) and ``_group_ready_eligible_siblings`` (AA3 conflict-arbitration).

    Per J2: authorization is evaluated at the cited historical cut via
    ``capabilities_at_effective_head``. There is NO current-state shortcut;
    ``outer_state.streams[issuer]`` is NEVER consulted for authorization.
    ``outer_state`` is only used as a pre-population hint for the sub-fold
    (an internal optimization that does not change the historical cut).

    Authorization rules:
    - Bootstrap issuer: fixed bootstrap policy grants all surface authority.
    - Non-bootstrap with supports: at least one cited support must yield
      ``Capability(actor=issuer, action='authority.change', surface=ac.surface)``
      via ``capabilities_at_effective_head``.
    - Non-bootstrap without supports: no authorization path → False.
      (Parent correctness is lifecycle-only, not authorization.)
    """
    if issuer in DEFAULT_HUMAN_PRINCIPALS:
        return True
    if not supports:
        return False
    needed = Capability(actor=issuer, action='authority.change', surface=ac.surface)
    for sid in supports:
        # J2: pure historical authorization. Always evaluate via
        # capabilities_at_effective_head at the cited support cut.
        received = capabilities_at_effective_head(
            issuer, sid, ev_index,
            authority_events, resolution_events,
            outer_state=outer_state,
        )
        if needed in received:
            return True
    return False


# --- Raw application (no corpus loading — used internally) ---------------


def _apply_authority_change_raw(
    state: _ReplayState,
    ev: dict,
    ev_index: dict[str, dict],
    authority_events: list[dict],
    resolution_events: list[dict],
    outer_state: "_ReplayState | None" = None,
) -> tuple[str, EventDecision]:
    """Apply one AUTHORITY-CHANGE event (no corpus loading).

    Returns retryable=True when prerequisites are still pending (AA2).
    """
    eid = ev.get("event_id", "")
    if not isinstance(eid, str) or not eid:
        return "", EventDecision("", False, EVENT_INVALID)

    ready, prereq_reason = _prerequisites_ready(ev, state, ev_index)
    if not ready:
        return eid, EventDecision(
            eid, False, prereq_reason,
            detail="waiting on parent/supports",
            retryable=True,
        )

    ac, reason = _parse_authority_change_payload(ev)
    if reason:
        return eid, EventDecision(eid, False, reason)

    issuer = ev.get("actor", "")
    if not isinstance(issuer, str) or not issuer:
        return eid, EventDecision(eid, False, EVENT_INVALID)

    subject = _stream_subject(ac.beneficiary)
    # AF1 fix: capture the issuer's pre-state before any stream updates.
    # Used for AF1 authorization check and for recording _issuer_caps_snapshots.
    _issuer_pre_state = _held_for(state, issuer)

    if is_bootstrap_root_principal(ac.beneficiary):
        return eid, EventDecision(eid, False, BOOTSTRAP_IMMUTABLE,
                                   detail="beneficiary is bootstrap root")

    if ac.beneficiary in state.streams and state.streams[ac.beneficiary].conflict:
        return eid, EventDecision(eid, False, STREAM_CONFLICT)

    parents = _parent_eids(ev)
    supports = _supports_eids(ev)
    current_head = state.heads.get(subject)

    # Zero-parent genesis path.
    if len(parents) == 0:
        if current_head is not None:
            return eid, EventDecision(eid, False, PARENT_STALE,
                                       detail="non-genesis with zero parent")
        if issuer in DEFAULT_HUMAN_PRINCIPALS:
            pass  # bootstrap-issued genesis — no supports check needed
        else:
            # Register in _pending_genesis FIRST (before any state checks)
            # so same-pass siblings can detect each other regardless of
            # which path they take through this function. Bootstrap issuers
            # skip _pending_genesis entirely (they're always first in their
            # stream and never conflict with each other).
            pending = state._pending_genesis.setdefault(ac.beneficiary, [])
            pending.append(eid)

            # Check for existing stream BEFORE validating supports.
            # If another genesis in this pass already set a head, conflict.
            existing_stream = state.streams.get(ac.beneficiary)
            if existing_stream is not None and existing_stream.head_event_id is not None:
                # Another genesis in this pass already has a valid head.
                # Conflict: both are zero-parent siblings for the same beneficiary.
                for prev_eid in list(pending):
                    if prev_eid in state._decisions:
                        continue
                    state._decisions[prev_eid] = EventDecision(
                        prev_eid, False, STREAM_CONFLICT,
                        detail="sibling fork: zero-parent genesis conflict",
                    )
                beneficiary = ac.beneficiary
                subject = _stream_subject(beneficiary)
                state.streams[beneficiary] = AuthorityStream(
                    beneficiary=beneficiary,
                    head_event_id=None,
                    capabilities=frozenset(),
                    conflict=True,
                    reason=STREAM_CONFLICT,
                )
                state.heads[subject] = None
                state._decisions[eid] = EventDecision(
                    eid, False, STREAM_CONFLICT,
                    detail="sibling fork: zero-parent genesis conflict",
                )
                state._pending_genesis.pop(ac.beneficiary, None)
                return eid, state._decisions[eid]
            if not supports:
                state._pending_genesis.pop(ac.beneficiary, None)
                return eid, EventDecision(eid, False, SUPPORTS_MISSING,
                                           detail="delegated genesis needs supports")
            # AF1 fix: always check authorization via capabilities_at_effective_head.
            # The prior _pending_genesis shortcut (accepting if support_eid was the
            # pending genesis for this beneficiary) bypassed authorization entirely —
            # it was intended for same-pass sibling-conflict detection but erroneously
            # allowed the same-beneficiary pending event to substitute for a support
            # citation, leaking capabilities the beneficiary never received.
            effective = False
            for sid in supports:
                if capabilities_at_effective_head(
                    issuer, sid, ev_index,
                    authority_events, resolution_events,
                    outer_state=outer_state,
                ):
                    effective = True
                    break
            if not effective:
                state._pending_genesis.pop(ac.beneficiary, None)
                return eid, EventDecision(eid, False, SUPPORTS_INVALID,
                                           detail="no effective supports citation")
        # Zero-parent genesis: _pending_genesis is cleaned up in all reject
        # paths; only the "apply" path leaves it populated for subsequent
        # same-pass sibling detection.
    elif len(parents) == 1:
        peid = parents[0]
        if peid not in ev_index:
            return eid, EventDecision(eid, False, PARENT_INVALID)
        parent_subj = ev_index[peid].get("subject")
        if parent_subj != subject:
            return eid, EventDecision(eid, False, PARENT_CROSS_SUBJECT)
        # J3 fix: strict peid == current_head. No "peid ok" shortcut.
        # Concurrent siblings at the same parent frontier are arbitrated
        # upstream by _group_ready_eligible_siblings (AA3 invariant). If
        # peid != current_head at apply time, the parent is stale and the
        # event must be rejected as PARENT_STALE.
        if peid != current_head:
            return eid, EventDecision(eid, False, PARENT_STALE)
    else:
        return eid, EventDecision(eid, False, PARENT_INVALID)

    # H1 fix: issuer authorization applies to ALL authority-change events,
    # zero-parent AND one-parent alike. Parent correctness is lifecycle-only
    # and does NOT substitute for authorization. Use the shared
    # _issuer_authorized_for predicate so the same rule governs both the
    # apply path and the AA3 conflict-arbitration path.
    if not _issuer_authorized_for(
        ac, issuer, supports,
        ev_index, authority_events, resolution_events,
        outer_state=outer_state,
    ):
        if len(parents) == 0:
            state._pending_genesis.pop(ac.beneficiary, None)
        return eid, EventDecision(eid, False, ISSUER_PRESSTATE_INVALID,
                                   detail="issuer lacks action at support")


    # Apply grant/revoke.
    cur_cap = set(
        state.streams.get(ac.beneficiary, AuthorityStream(
            beneficiary=ac.beneficiary,
            head_event_id=None,
            capabilities=frozenset(),
        )).capabilities
    )
    target_cap = Capability(actor=ac.beneficiary, action=ac.action,
                             surface=ac.surface)
    if ac.op == "grant":
        cur_cap.add(target_cap)
    elif ac.op == "revoke":
        cur_cap.discard(target_cap)
    else:
        return eid, EventDecision(eid, False, EVENT_INVALID)

    state.streams[ac.beneficiary] = AuthorityStream(
        beneficiary=ac.beneficiary,
        head_event_id=eid,
        capabilities=frozenset(cur_cap),
        conflict=False,
        reason="OK",
    )
    state.heads[subject] = eid
    # AD1: a delegated genesis grant does NOT advance the ISSUER subject's
    # lifecycle head. Only its own canonical subject (authority:<beneficiary>)
    # advances. supports is cross-subject evidence only; it never transfers
    # lifecycle identity. (AD1 fix: removed the erroneous
    # state.heads[issuer_subject] = eid that was here.)
    # AD2: record capability snapshots at the moment this event applied.
    # _caps_snapshots: BENEFICIARY's new capabilities after the grant/revoke.
    # _issuer_caps_snapshots: ISSUER's capabilities BEFORE this event applied.
    #   Used by AF1 authorization checks in _apply_authority_change_raw to verify
    #   the grantor held authority.change at the cited support event.
    if len(parents) == 0 and issuer not in DEFAULT_HUMAN_PRINCIPALS:
        # Delegated genesis: record two snapshots.
        state._caps_snapshots[eid] = frozenset(cur_cap)
        # Capture issuer's pre-state BEFORE updating the stream (BEFORE the grant
        # is applied, so state.streams reflects the state before this event).
        # Note: at this point in the code, state.streams still has the PREVIOUS
        # state. We saved the issuer's held set in an outer scope variable above.
        state._issuer_caps_snapshots[eid] = _issuer_pre_state
    elif is_bootstrap_root_principal(issuer):
        # Bootstrap issuer (owner): record the bootstrap capabilities as the
        # issuer's pre/post-state. These are needed by AF1 checks when a
        # bootstrap-issued delegated genesis (owner->a) is cited as support.
        state._caps_snapshots[eid] = frozenset(cur_cap)
        state._issuer_caps_snapshots[eid] = bootstrap_capabilities()
    else:
        # Non-delegated events: record only beneficiary caps.
        state._caps_snapshots[eid] = frozenset(cur_cap)
    # AC1: record head snapshot at the moment this event applied.
    # Used by capabilities_at_effective_head to verify the cited support
    # was the EXACT issuer head when this event was applied.
    state._head_snapshots[eid] = dict(state.heads)
    # Track the creator for zero-parent genesis grants so the orphan check
    # can distinguish a true orphan (different prior) from a sibling fork
    # (same-pass zero-parent genesis for the same beneficiary).
    if len(parents) == 0:
        state._stream_creators[ac.beneficiary] = eid
    return eid, EventDecision(eid, True, "OK")


def _apply_resolution_raw(
    state: _ReplayState,
    ev: dict,
    ev_index: dict[str, dict],
    authority_events: list[dict],
    resolution_events: list[dict],
    outer_state: "_ReplayState | None" = None,
) -> tuple[str, EventDecision]:
    """Apply a CHANGE/resolution event (no corpus loading, AA6+AA7).

    Per AA6: resolution applies ONLY when the beneficiary has a real
    unresolved conflict. Resolution parents must exactly match the
    competing heads.

    Per AA7: delegated issuer must cite supports; authorization checked
    via capabilities_at_effective_head.
    """
    eid = ev.get("event_id", "")
    if not isinstance(eid, str) or not eid:
        return "", EventDecision("", False, EVENT_INVALID)

    ready, prereq_reason = _prerequisites_ready(ev, state, ev_index)
    if not ready:
        return eid, EventDecision(
            eid, False, prereq_reason,
            detail="waiting on parents",
            retryable=True,
        )

    res, reason = _parse_resolution_payload(ev)
    if reason:
        return eid, EventDecision(eid, False, reason)

    issuer = ev.get("actor", "")
    if not isinstance(issuer, str) or not issuer:
        return eid, EventDecision(eid, False, EVENT_INVALID)

    subject = ev.get("subject", "")
    if not subject.startswith("authority:"):
        return eid, EventDecision(eid, False, PARENT_INVALID,
                                   detail="resolution subject must be authority:<beneficiary>")
    beneficiary = subject.split(":", 1)[1]

    parents = _parent_eids(ev)
    supports = _supports_eids(ev)
    if len(parents) < 1:
        return eid, EventDecision(eid, False, PARENT_INVALID,
                                   detail="resolution needs >=1 parent")
    if not all(p in ev_index for p in parents):
        return eid, EventDecision(eid, False, PARENT_INVALID)
    for p in parents:
        psubj = ev_index[p].get("subject", "")
        if psubj != subject:
            return eid, EventDecision(eid, False, PARENT_INVALID,
                                       detail="resolution parents must share subject")

    if is_bootstrap_root_principal(beneficiary):
        return eid, EventDecision(eid, False, BOOTSTRAP_IMMUTABLE)

    # AB1: AA7 authorization — delegated issuer must cite supports binding
    # to an exact effective historical authority head for authority.change.
    # Bootstrap principals need no supports (root policy only).
    if issuer not in DEFAULT_HUMAN_PRINCIPALS:
        if not supports:
            return eid, EventDecision(eid, False, ISSUER_PRESSTATE_INVALID,
                                       detail="delegated resolver needs supports citation")
        # Surface is taken from the first parent's grant event.
        surface = "project:reference"
        for p in parents:
            if p in ev_index:
                pac, _ = _parse_authority_change_payload(ev_index[p])
                if pac and pac.surface:
                    surface = pac.surface
                    break
        cap_change = Capability(actor=issuer, action="authority.change",
                                 surface=surface)
        authorized = False
        for sid in supports:
            caps = capabilities_at_effective_head(
                issuer, sid, ev_index,
                authority_events, resolution_events,
                outer_state=outer_state,
            )
            if cap_change in caps:
                authorized = True
                break
        if not authorized:
            return eid, EventDecision(eid, False, ISSUER_PRESSTATE_INVALID)
    else:
        pass  # Bootstrap issuer implicitly authorized for resolution.

    cur_stream = state.streams.get(beneficiary)
    if cur_stream is None:
        return eid, EventDecision(eid, False, RESOLUTION_INVALID,
                                   detail="no stream to resolve")

    # AA6: resolution requires real unresolved conflict on this subject.
    if not cur_stream.conflict:
        # No conflict to resolve — RESOLUTION_NO_CONFLICT (AA6).
        # The resolution does NOT advance the head.
        return eid, EventDecision(eid, False, RESOLUTION_NO_CONFLICT,
                                   detail="no unresolved conflict to resolve")

    # AB2: resolution parents must exactly match the unresolved competing heads.
    # Extra parent, missing competing parent, stale branch => RESOLUTION_INVALID.
    expected_heads = state._conflicting_heads.get(beneficiary)
    if expected_heads is None:
        # No tracked conflict heads: conflict was not created by AA3
        # (e.g. a manually marked stream). Fall back to RESOLUTION_NO_CONFLICT.
        return eid, EventDecision(eid, False, RESOLUTION_NO_CONFLICT,
                                   detail="no tracked conflict heads for resolution")
    actual_parents = frozenset(parents)
    if actual_parents != expected_heads:
        return eid, EventDecision(eid, False, RESOLUTION_INVALID,
                                   detail="resolution parents do not match exact conflict-head set")

    if res.op == "select":
        if res.selected_head_event_id is None:
            return eid, EventDecision(eid, False, RESOLUTION_INVALID)
        if res.selected_head_event_id not in parents:
            return eid, EventDecision(eid, False, RESOLUTION_INVALID,
                                       detail="selected_head not in parents")
        resolved_ev = ev_index.get(res.selected_head_event_id)
        if resolved_ev is None:
            return eid, EventDecision(eid, False, RESOLUTION_INVALID)
        eff_caps = _effective_caps_for_head(
            res.selected_head_event_id, ev_index, state,
            forced_head_eid=res.selected_head_event_id,
        )
        state.streams[beneficiary] = AuthorityStream(
            beneficiary=beneficiary,
            head_event_id=eid,
            capabilities=eff_caps,
            conflict=False,
            reason="OK",
        )
        state.heads[subject] = eid
        state._conflicting_heads.pop(beneficiary, None)
        state._caps_snapshots[eid] = eff_caps
        state._head_snapshots[eid] = dict(state.heads)
        return eid, EventDecision(eid, True, RESOLUTION_APPLIED)

    if res.op == "reject":
        if res.rejected_head_event_id is None:
            return eid, EventDecision(eid, False, RESOLUTION_INVALID)
        if res.rejected_head_event_id not in parents:
            return eid, EventDecision(eid, False, RESOLUTION_INVALID,
                                       detail="rejected_head not in parents")
        surviving = [p for p in parents if p != res.rejected_head_event_id]
        if len(surviving) != 1:
            return eid, EventDecision(eid, False, RESOLUTION_INVALID,
                                       detail="reject requires exactly one surviving parent")
        keep = surviving[0]
        eff_caps = _effective_caps_for_head(keep, ev_index, state,
                                            forced_head_eid=keep)
        state.streams[beneficiary] = AuthorityStream(
            beneficiary=beneficiary,
            head_event_id=eid,
            capabilities=eff_caps,
            conflict=False,
            reason="OK",
        )
        state.heads[subject] = eid
        state._conflicting_heads.pop(beneficiary, None)
        state._caps_snapshots[eid] = eff_caps
        state._head_snapshots[eid] = dict(state.heads)
        return eid, EventDecision(eid, True, RESOLUTION_APPLIED)

    return eid, EventDecision(eid, False, RESOLUTION_INVALID)


def _effective_caps_for_head(
    head_eid: str,
    ev_index: dict[str, dict],
    state: _ReplayState,
    forced_head_eid: str | None = None,
) -> frozenset[Capability]:
    """Reconstruct capability set ending at `head_eid` by walking the
    parent chain backwards.

    Events that are marked STREAM_CONFLICT (orphaned siblings with no
    committed head) are excluded from the chain, as their grants were
    not actually applied to any stream.

    `forced_head_eid`, if provided, is always included regardless of
    its `ok` flag. This is used by resolution: the resolution's chosen
    head was marked conflicting in the fold but is the canonical head
    after resolution is applied.

    If head_eid is itself a conflicting sibling with no committed parent,
    returns the empty set — orphaned genesis grants carry no effective
    capability since they were never committed.
    """
    visited: set[str] = set()
    chain: list[dict] = []
    cur = head_eid
    while cur is not None and cur not in visited:
        visited.add(cur)
        ev = ev_index.get(cur)
        if ev is None:
            break
        d = state._decisions.get(cur)
        if d is not None and not d.ok and cur != forced_head_eid:
            # Non-ok event: not part of a committed chain.
            break
        if _is_authority_change(ev):
            chain.append(ev)
        parents = _parent_eids(ev)
        cur = parents[0] if parents else None
    # NOTE: we do NOT fall back to including a conflicting grant event
    # itself unless it is the forced_head_eid (the resolution-selected head).
    caps: set[Capability] = set()
    for ev in reversed(chain):
        ac, reason = _parse_authority_change_payload(ev)
        if reason:
            continue
        target_cap = Capability(actor=ac.beneficiary, action=ac.action,
                                 surface=ac.surface)
        if ac.op == "grant":
            caps.add(target_cap)
        elif ac.op == "revoke":
            caps.discard(target_cap)
    return frozenset(caps)


# --- AA3: pre-apply fork arbitration -------------------------------------


def _group_ready_eligible_siblings(
    authority_events: list[dict],
    resolution_events: list[dict],
    ev_index: dict[str, dict],
    state: _ReplayState,
    outer_state: "_ReplayState | None" = None,
) -> dict[str, list[dict]]:
    """Group all currently-ready eligible authority events by their shared
    frontier, BEFORE mutating any stream.

    AA3 rule: all candidates for the same (subject, prior_frontier) must be
    evaluated against the same immutable pre-state before any one mutates
    the stream. A candidate whose prerequisites become ready only in a later
    fixed-point pass is not a sibling at this frontier snapshot and cannot
    retroactively poison a branch that already advanced.

    G2 correction: for zero-parent delegated candidates, the function now
    verifies via `capabilities_at_effective_head` that the cited support is
    an effective historical head where the issuer holds `authority.change`
    on the grant surface — BEFORE counting the candidate as an eligible sibling.
    A candidate with a non-empty but invalid/unauthorized support citation
    is excluded from the eligible conflict set.

    `outer_state` (optional): the calling fold's _ReplayState, used to
    pre-populate sub-fold _caps_snapshots for AA4 evaluation of zero-parent
    delegated candidates.

    Returns dict: frontier_key -> [candidate events].
    The caller is responsible for:
    - If len(group) >= 2: mark all as pending STREAM_CONFLICT, don't apply.
    - If len(group) == 1: apply normally.
    """
    frontier_groups: dict[tuple, list[dict]] = {}
    for ev in authority_events:
        eid = ev.get("event_id", "")
        if not eid:
            continue
        # Any finalized event belongs to an earlier immutable frontier.
        # Reconsidering an already-applied event would be retroactive repair.
        existing = state._decisions.get(eid)
        if existing is not None:
            continue

        # Parse payload; malformed events are ineligible, not conflict sources.
        ac, reason = _parse_authority_change_payload(ev)
        if reason:
            continue

        # Only pass-start-ready candidates participate. A later-ready event
        # is evaluated in a later scheduler pass against that pass's head.
        ready, _prereq_reason = _prerequisites_ready(ev, state, ev_index)
        if not ready:
            continue

        # Bootstrap root is immutable; events targeting it are handled elsewhere.
        if is_bootstrap_root_principal(ac.beneficiary):
            continue

        subject = _stream_subject(ac.beneficiary)
        parents = _parent_eids(ev)
        supports = _supports_eids(ev)
        current_head = state.heads.get(subject)

        if len(parents) == 0:
            # All zero-parent genesis events share GENESIS frontier (None).
            # G2: bootstrap principals are always authorized for their own grants.
            # For delegated zero-parent candidates (known issuer with supports),
            # verify via capabilities_at_effective_head that the cited support is
            # an effective historical head where the issuer holds authority.change
            # on the grant surface. A candidate with non-empty supports that does
            # NOT provide authority.change to the issuer is ineligible — not a
            # conflict source. We pass the outer state's _ReplayState (if available)
            # as pre-population for the sub-fold.
            issuer = ev.get("actor", "")
            if _issuer_authorized_for(
                ac, issuer, supports,
                ev_index, authority_events, resolution_events,
                outer_state=outer_state,
            ):
                key = (ac.beneficiary, None)
                frontier_groups.setdefault(key, []).append(ev)
        elif len(parents) == 1:
            peid = parents[0]
            if peid not in ev_index:
                continue
            parent_subj = ev_index[peid].get("subject")
            if parent_subj != subject:
                continue
            # J3 fix: a non-genesis candidate is eligible at a frontier
            # ONLY when its parent IS the current head at this snapshot.
            # No "parent ok in this pass" exception; concurrent siblings
            # at the same parent frontier are arbitrated by AA3 grouping
            # (this function). Siblings that have a STALE parent (parent
            # applied earlier but not the current head) are NOT eligible
            # for conflict — they are PARENT_STALE at apply time.
            if peid != current_head:
                continue  # parent stale; not an eligible sibling
            # H2 fix: issuer authorization also applies to non-genesis
            # candidates. Without this, an unauthorized non-genesis sibling
            # would be counted as eligible and would falsely create conflict.
            issuer = ev.get("actor", "")
            _auth = _issuer_authorized_for(
                ac, issuer, supports,
                ev_index, authority_events, resolution_events,
                outer_state=outer_state,
            )
            if not _auth:
                continue
            key = (ac.beneficiary, peid)
            frontier_groups.setdefault(key, []).append(ev)
        else:
            continue  # multi-parent: handled by payload parsing

    # Return only groups with 2+ candidates (potential conflicts).
    return {k: evs for k, evs in frontier_groups.items() if len(evs) >= 2}


# --- Public fold ---------------------------------------------------------


def _collect_authority_events(events: list[dict]) -> list[dict]:
    return [ev for ev in events if _is_authority_change(ev)]


def _collect_resolution_events(events: list[dict]) -> list[dict]:
    return [ev for ev in events if _is_resolution(ev)]


def _resolution_beneficiary(res_ev: dict) -> str | None:
    if not isinstance(res_ev, dict):
        return None
    subject = res_ev.get("subject", "")
    if not isinstance(subject, str) or not subject.startswith("authority:"):
        return None
    return subject.split(":", 1)[1]


# --- PUBLIC API (AA1: path-only, no TrustedCorpus parameter) -------------


def derive_authority_state(
    source: str | Path | "TrustedCorpus",
) -> AuthorityState:
    """Derive the final authority state from events.

    Per AA1: the canonical entry accepts a raw events_dir path and
    calls `load_trusted_corpus` internally. For backward compatibility
    with existing tests that pass a TrustedCorpus directly, this also
    accepts an already-sealed TrustedCorpus (which has already passed
    the dependency-pin gate and revalidation).

    TrustedCorpus / _InternalTrustedSnapshot are implementation-private
    and not accepted as explicit public parameters.
    """
    if isinstance(source, TrustedCorpus):
        raise TypeError(
            "TrustedCorpus is implementation-private; "
            "pass an events_dir path instead"
        )
    else:
        p = Path(source).resolve()
        # Auto-detect the test-helper "events_dir/corpus/" double-layer.
        if not any(p.iterdir()) and (p / "corpus").exists():
            p = p / "corpus"
        corpus = load_trusted_corpus(p)
    return _fold_events(
        list(corpus.events),
        corpus.registry,
    ).state


def derive_pre_event_state(
    events_dir: str | Path,
    target_event_id: str | None = None,
) -> AuthorityState:
    """Derive the authority state valid BEFORE the target event.

    Per AA5: for TASK/dispatch targets, pre-state is the historical fold
    reachable through canonical `prior_refs.supports`. Bootstrap
    dispatch with no supports gets root capability from policy input only.

    Per AA1: accepts a raw events_dir path only.
    """
    p = Path(events_dir).resolve()
    if not any(p.iterdir()) and (p / "corpus").exists():
        p = p / "corpus"
    corpus = load_trusted_corpus(p)

    if target_event_id is None:
        return _fold_events(list(corpus.events), corpus.registry).state

    target_ev = corpus.event_by_id(target_event_id)
    if target_ev is None:
        return _fold_events(list(corpus.events), corpus.registry).state

    events = list(corpus.events)
    ev_index: dict[str, dict] = {}
    for ev in events:
        eid = ev.get("event_id")
        if isinstance(eid, str) and eid and eid not in ev_index:
            ev_index[eid] = ev

    authority_events = _collect_authority_events(events)
    resolution_events = _collect_resolution_events(events)

    causally_prior = _causally_prior_authority_events_for_target(
        authority_events, resolution_events, target_ev, ev_index,
    )

    if not causally_prior:
        # No causally-prior authority: return bootstrap-only state.
        from blackbox_vnext.slice4.authority_profile import bootstrap_capabilities
        bs_caps = bootstrap_capabilities()
        streams = {}
        for actor in DEFAULT_HUMAN_PRINCIPALS:
            streams[actor] = AuthorityStream(
                beneficiary=actor,
                head_event_id=None,
                capabilities=bs_caps,
                conflict=False,
                reason="BOOTSTRAP",
            )
        return AuthorityState(streams=streams)

    # Fold the causally-prior subset.
    return _fold_events(causally_prior, corpus.registry).state


def _causally_prior_authority_events_for_target(
    authority_events: list[dict],
    resolution_events: list[dict],
    target_event: dict,
    ev_index: dict[str, dict],
) -> list[dict]:
    """Walk causal ancestors of target_event via parent + supports edges.

    Used for AA5 pre-event state: the dispatch pre-state is the
    authority reachable through cited support heads.
    """
    target_supports = _supports_eids(target_event)

    if not target_supports:
        return []

    # Walk back through support chain + each support's parents/supports.
    visited: set[str] = set()
    frontier: list[str] = list(target_supports)
    while frontier:
        cur = frontier.pop()
        if cur in visited:
            continue
        visited.add(cur)
        if cur not in ev_index:
            continue
        cur_ev = ev_index[cur]
        for pp in _parent_eids(cur_ev):
            if pp not in visited:
                frontier.append(pp)
        for ss in _supports_eids(cur_ev):
            if ss not in visited:
                frontier.append(ss)

    return [
        ev for ev in authority_events
        if ev.get("event_id", "") in visited
    ]


# --- INTERNAL fold (AA8: private, accepts snapshot only) ----------------


def _fold_events(
    events: list[dict],
    registry: Any,
    authority_events: list[dict] | None = None,
    resolution_events: list[dict] | None = None,
) -> AuthorityFoldResult:
    """Run the full causal-scheduled fold with AA2 retry + AA3 pre-apply
    fork arbitration.

    The AA2 scheduler:
    - Retryable decisions go into _pending, NOT _decisions.
    - Each pass processes all ready events.
    - Pending events are retried after each pass.
    - Loop until no progress.

    The AA3 fork arbitrator:
    - Before applying any sibling, group all eligible candidates at
      their common frontier.
    - If >=2 eligible siblings share the same frontier for a beneficiary:
      mark the stream as conflict; apply none as winner.
    """
    ev_index: dict[str, dict] = {}
    for ev in events:
        eid = ev.get("event_id")
        if isinstance(eid, str) and eid and eid not in ev_index:
            ev_index[eid] = ev

    if authority_events is None:
        authority_events = _collect_authority_events(events)
    if resolution_events is None:
        resolution_events = _collect_resolution_events(events)

    # Parse and collect valid authority events while retaining typed
    # failures for every rejected formal event (K2/Q8).
    parsed_authority: list[dict] = []
    invalid_authority: dict[str, str] = {}
    for ev in authority_events:
        ac, reason = _parse_authority_change_payload(ev)
        if reason:
            eid = ev.get("event_id", "")
            if eid:
                invalid_authority[eid] = reason
            continue
        parsed_authority.append(ev)

    parsed_resolution: list[dict] = []
    invalid_resolution: dict[str, str] = {}
    for ev in resolution_events:
        res, reason = _parse_resolution_payload(ev)
        if reason:
            eid = ev.get("event_id", "")
            if eid:
                invalid_resolution[eid] = reason
            continue
        parsed_resolution.append(ev)

    state = _ReplayState()
    bs_caps = bootstrap_capabilities()
    for actor in DEFAULT_HUMAN_PRINCIPALS:
        state.streams[actor] = AuthorityStream(
            beneficiary=actor,
            head_event_id=None,
            capabilities=bs_caps,
            conflict=False,
            reason="BOOTSTRAP",
        )

    # Invalid formal events are finalized before scheduling so dependents
    # see a non-green prerequisite and public validation retains the exact
    # typed reason. They must never disappear merely because parsing failed.
    all_candidates = parsed_authority + parsed_resolution
    invalid_eids: set[str] = set(invalid_authority) | set(invalid_resolution)
    for eid, reason in {**invalid_authority, **invalid_resolution}.items():
        state._decisions[eid] = EventDecision(
            eid, False, reason, detail="invalid extension or registry value"
        )

    # AA2 + AA3 main scheduler.
    candidate_eids = {
        ev.get("event_id", "")
        for ev in all_candidates
        if isinstance(ev.get("event_id", ""), str)
        and ev.get("event_id", "")
        and ev.get("event_id", "") not in invalid_eids
    }
    max_passes = len(candidate_eids) + 4

    for _pass in range(max_passes):
        progressed = False

        # Freeze readiness and sibling eligibility at pass start. Events whose
        # prerequisites become ready during this pass wait for the next pass;
        # this creates the immutable frontier required by AA3/K4.
        pass_start_readiness = {
            ev.get("event_id", ""): _prerequisites_ready(ev, state, ev_index)
            for ev in all_candidates
            if ev.get("event_id", "") not in state._decisions
        }
        pass_start_groups = _group_ready_eligible_siblings(
            parsed_authority, parsed_resolution, ev_index, state,
            outer_state=state,
        )

        # Process all ready non-conflicting candidates.
        for ev in sorted(all_candidates, key=lambda e: e.get("event_id", "")):
            eid = ev.get("event_id", "")
            if not eid:
                continue
            if eid in invalid_eids:
                continue
            existing = state._decisions.get(eid)
            if existing is not None:
                continue

            ready, prereq_reason = pass_start_readiness.get(
                eid, (False, "PREREQUISITE-NOT-YET-EVALUATED")
            )
            if not ready:
                state._pending[eid] = (ev, prereq_reason)
                continue

            if _is_authority_change(ev):
                ac, _ = _parse_authority_change_payload(ev)
                if ac:
                    _parents = _parent_eids(ev)
                    frontier_key = _parents[0] if _parents else None
                    group_key = (ac.beneficiary, frontier_key)
                    group = pass_start_groups.get(group_key, [])

                    if len(group) >= 2:
                            beneficiary = ac.beneficiary
                            subject = _stream_subject(beneficiary)
                            state.streams[beneficiary] = AuthorityStream(
                                beneficiary=beneficiary,
                                head_event_id=None,
                                capabilities=frozenset(),
                                conflict=True,
                                reason=STREAM_CONFLICT,
                            )
                            state.heads[subject] = None
                            competing = frozenset(
                                sev.get("event_id", "") for sev in group
                                if sev.get("event_id")
                            )
                            state._conflicting_heads[beneficiary] = competing
                            for sev in group:
                                seid = sev.get("event_id", "")
                                if seid and seid not in state._decisions:
                                    state._decisions[seid] = EventDecision(
                                        seid, False, STREAM_CONFLICT,
                                        detail="eligible sibling fork (AA3)",
                                    )
                                if seid in state._pending:
                                    del state._pending[seid]
                            progressed = True
                            continue

            if _is_authority_change(ev):
                _eid, decision = _apply_authority_change_raw(
                    state, ev, ev_index, parsed_authority, parsed_resolution,
                    outer_state=state,
                )
            elif _is_resolution(ev):
                _eid, decision = _apply_resolution_raw(
                    state, ev, ev_index, parsed_authority, parsed_resolution,
                    outer_state=state,
                )
            else:
                continue

            if decision.retryable:
                state._pending[eid] = (ev, decision.reason)
            else:
                # AC1 fix: commit decision immediately so that same-pass
                # dependents (processed later in this pass) can use the
                # fast-path in capabilities_at_effective_head and reuse
                # this event's head snapshot for AC1 verification.
                state._decisions[eid] = decision
                if eid in state._pending:
                    del state._pending[eid]
                progressed = True

        if not progressed:
            break

    # Finalize remaining pending events as stuck.
    for eid, (ev, reason) in state._pending.items():
        if eid not in state._decisions:
            state._decisions[eid] = EventDecision(
                eid, False, reason or "NO-PROGRESS",
                detail="prerequisites did not finalize in causal scheduler",
            )
    state._pending.clear()

    # AA6: resolution post-check — ensure resolutions apply only to
    # real conflicts. Any resolution where the stream was NOT in conflict
    # must be marked RESOLUTION_NO_CONFLICT.
    for ev in parsed_resolution:
        eid = ev.get("event_id", "")
        if not eid or eid not in state._decisions:
            continue
        d = state._decisions.get(eid)
        if d is None or not d.ok:
            continue
        # Resolution applied ok=True — verify the stream was in conflict.
        res, _ = _parse_resolution_payload(ev)
        if res is None:
            continue
        subject = ev.get("subject", "")
        if not subject.startswith("authority:"):
            continue
        beneficiary = subject.split(":", 1)[1]
        cur_stream = state.streams.get(beneficiary)
        if cur_stream is None:
            state._decisions[eid] = EventDecision(
                eid, False, RESOLUTION_INVALID,
                detail="resolution applied but stream was None",
            )
            continue
        # If the stream is NOT in conflict, the resolution was invalid
        # (AA6: no actual conflict to resolve).
        # Only restore pre-resolution state when the resolution was
        # malformed (rejected_head_event_id absent) — a well-formed
        # reject with a cited rejected head correctly cleared conflict.
        if not cur_stream.conflict and res.op == "reject" and res.rejected_head_event_id is None:
            # Revert the head advancement — resolution had no real conflict.
            # The stream state was incorrectly advanced.
            # We need to find the pre-resolution head.
            parents = _parent_eids(ev)
            if parents:
                # Find what the head was before the resolution.
                surviving = parents
                if res.op == "reject" and res.rejected_head_event_id:
                    surviving = [p for p in parents if p != res.rejected_head_event_id]
                if surviving:
                    keep = surviving[-1]
                    # Restore the pre-resolution stream state.
                    pre_stream = state.streams.get(beneficiary)
                    pre_caps = _effective_caps_for_head(keep, ev_index, state)
                    state.streams[beneficiary] = AuthorityStream(
                        beneficiary=beneficiary,
                        head_event_id=keep,
                        capabilities=pre_caps,
                        conflict=False,
                        reason="OK",
                    )
                    state.heads[subject] = keep
            state._decisions[eid] = EventDecision(
                eid, False, RESOLUTION_NO_CONFLICT,
                detail="no unresolved conflict to resolve (AA6)",
            )

    # trustworthy iff every formal authority-change/resolution got ok=True.
    trustworthy = all(d.ok for d in state._decisions.values())

    return AuthorityFoldResult(
        state=AuthorityState(streams=dict(state.streams)),
        per_event=dict(state._decisions),
        trustworthy=trustworthy,
    )


# --- INTERNAL/PURE entry points (AA8) -----------------------------------


def _derive_authority_state_internal(
    snapshot: _InternalTrustedSnapshot,
) -> AuthorityFoldResult:
    """INTERNAL/PURE-only fold entry point (AA8).

    Pure fold unit tests drive the algorithm via
    `_InternalTrustedSnapshot`. The result is NOT proof of full-corpus
    trust (no Slice 1 controlled reingest; no seal).
    """
    events = list(snapshot.events)
    ev_index: dict[str, dict] = {}
    for ev in events:
        eid = ev.get("event_id")
        if isinstance(eid, str) and eid and eid not in ev_index:
            ev_index[eid] = ev

    state = _ReplayState()
    bs_caps = bootstrap_capabilities()
    for actor in DEFAULT_HUMAN_PRINCIPALS:
        state.streams[actor] = AuthorityStream(
            beneficiary=actor,
            head_event_id=None,
            capabilities=bs_caps,
            conflict=False,
            reason="BOOTSTRAP",
        )

    authority_events = _collect_authority_events(events)
    resolution_events = _collect_resolution_events(events)

    return _fold_events(
        events,
        snapshot.registry,
        authority_events,
        resolution_events,
    )

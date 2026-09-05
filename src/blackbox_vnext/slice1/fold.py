"""Event-set + per-subject causal DAG fold (Design v0.1.4 D + E6).

Canonical history = event set + per-subject causal DAG. Filename lexical order
and recorded_at wall-clock are NOT authority. State fold per subject follows
parent edges; support edges are evidence, not heads.

B1 candidate-set rule (FORMAT F.8.1) is implemented here for reclassify events.

Pure functions + small state container; no I/O.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .subtypes import SubtypeRegistry


# Semantic classes from subtypes.json.
STATE_TRANSITION = "state_transition"
SUPPORT = "support"
NON_STATE = "non_state"


@dataclass
class FoldResult:
    """Outcome of a per-subject fold or a B1 candidate-set query."""

    state: str  # "ok" | "concurrent_fork" | "ambiguous" | "unresolved" | "non_green"
    detail: str = ""
    candidate_ids: list[str] = field(default_factory=list)
    head_event_id: str | None = None


def _classify_subtype(reg: SubtypeRegistry, subtype: str) -> str:
    spec = reg.lookup(subtype)
    if spec is None:
        return NON_STATE  # UNCLASSIFIED treated as non-state for fold purposes
    return spec.get("semantic", NON_STATE)


def _parents_supports(obj: dict[str, Any]) -> tuple[list[str], list[str]]:
    pr = obj.get("prior_refs", {})
    if not isinstance(pr, dict):
        return ([], [])
    parents = pr.get("parent", []) or []
    supports = pr.get("supports", []) or []
    return (list(parents), list(supports))


def _cardinality_ok(parents: list[str], spec: dict[str, Any]) -> bool:
    """Check prior_refs.parent against the registry parent_cardinality rule."""
    rule = spec.get("parent_cardinality", "exactly_one")
    n = len(parents)
    if rule == "zero":
        return n == 0
    if rule == "exactly_one":
        return n == 1
    if rule == "at_most_one":
        return n <= 1
    if rule == "one_or_more":
        return n >= 1
    return False


def _event_registry_valid(reg: SubtypeRegistry, ev: dict[str, Any]) -> bool:
    """Registry type/subtype/receipt_class combination must be valid (not
    UNCLASSIFIED / not INVALID)."""
    return reg.classify_event(ev) is None


def fold_subject(
    events: list[dict[str, Any]],
    reg: SubtypeRegistry,
    subject: str,
    authority: Any = None,
) -> tuple[str | None, list[str]]:
    """Fold state for one subject.

    Returns (head_event_id, list_of_conflicting_state_transition_event_ids).
    head_event_id is None if the subject has no recorded head.
    conflicting_ids is non-empty iff two state_transition events have the same
    parent AND are both state-transition (per Design v0.1.4 fork rule).

    Rules:
      - only events with `subject == subject` participate
      - only VALID registered state_transition events advance the head:
        registry type/subtype/receipt_class combination must be legal, and
        (when `authority` is provided) the actor must be authorized to author
        this receipt — invalid/unauthorized events never advance the head
      - registry parent_cardinality + genesis_allowed are enforced machine
        rules: a malformed event (wrong parent count, genesis where not
        allowed) cannot participate in the fold
      - per-subject state heads fold via parent edges (no wall-clock)
      - support / non_state events are evidence only (do not become heads)
      - two state_transition events with same parent => concurrent_fork
        (caller must invoke RESOLUTION to resolve)
    """
    candidates: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("subject") != subject:
            continue
        sem = _classify_subtype(reg, ev.get("subtype", ""))
        if sem != STATE_TRANSITION:
            continue
        # R4: only valid registered combinations may participate
        if not _event_registry_valid(reg, ev):
            continue
        # R4: authority eligibility (when an evaluator is supplied)
        if authority is not None:
            authorized, _ = authority.is_authorized(ev)
            if not authorized:
                continue
        spec = reg.lookup(ev.get("subtype", "")) or {}
        parents, _ = _parents_supports(ev)
        # R4: genesis rule — parent==[] only when genesis_allowed
        if not parents and not spec.get("genesis_allowed"):
            continue
        # R4: parent cardinality machine rule (applies to events WITH parents;
        # parent==[] genesis admissibility is governed by genesis_allowed)
        if parents and not _cardinality_ok(parents, spec):
            continue
        candidates.append(ev)

    if not candidates:
        return (None, [])

    # Build parent map (event_id -> parents) within the subject
    by_eid: dict[str, dict[str, Any]] = {ev.get("event_id"): ev for ev in events}

    # Walk from genesis: collect all state_transition events reachable from
    # any genesis event (parent=[]) and follow parent chains. Events whose
    # parent list is non-empty but parent is missing from set are still
    # included (they're part of the fold universe but mark head-gap).
    included: dict[str, dict[str, Any]] = {}

    def try_include(ev: dict[str, Any]) -> None:
        eid = ev.get("event_id")
        if eid in included:
            return
        parents, _ = _parents_supports(ev)
        if not parents:
            # genesis transition: only if subtype registry allows it
            spec = reg.lookup(ev.get("subtype", ""))
            if spec and spec.get("genesis_allowed"):
                included[eid] = ev
            return
        # non-genesis: needs at least one parent in included
        any_parent_included = False
        for p in parents:
            if p in included:
                any_parent_included = True
                break
        if any_parent_included:
            included[eid] = ev
        # else: deferred; we'll resolve below

    # First pass: include all genesis state_transitions + their reachable
    # descendants (chain following parent).
    for ev in candidates:
        parents, _ = _parents_supports(ev)
        if not parents:
            spec = reg.lookup(ev.get("subtype", ""))
            if spec and spec.get("genesis_allowed"):
                included[ev.get("event_id")] = ev
                # walk descendants
                stack = [ev.get("event_id")]
                while stack:
                    cur = stack.pop()
                    for other in candidates:
                        if other.get("event_id") in included:
                            continue
                        op, _ = _parents_supports(other)
                        if cur in op:
                            included[other.get("event_id")] = other
                            stack.append(other.get("event_id"))

    # Iterate to fixed point: any non-genesis whose parent is now included.
    changed = True
    while changed:
        changed = False
        for ev in candidates:
            eid = ev.get("event_id")
            if eid in included:
                continue
            parents, _ = _parents_supports(ev)
            if any(p in included for p in parents):
                included[eid] = ev
                changed = True

    # Determine head by walking to the deepest state_transition reachable.
    # If multiple leaves exist with no successor among candidates, there is
    # no single head (UNRESOLVED FORK).
    child_map: dict[str, list[str]] = defaultdict(list)
    for ev in included.values():
        eid = ev.get("event_id")
        parents, _ = _parents_supports(ev)
        for p in parents:
            if p in included:
                child_map[p].append(eid)

    # Detect conflicts: any parent referenced by >1 state_transition child
    # (with that parent itself in included) => fork.
    conflicts: list[str] = []
    for parent_eid, kids in child_map.items():
        if len(kids) > 1:
            # All those kids are in fork state; mark parent + each kid as conflict.
            conflicts.append(parent_eid)
            for k in kids:
                if k not in conflicts:
                    conflicts.append(k)

    # Head: pick the event with no children in included. If exactly one,
    # that's the head; otherwise unresolved fork.
    leaves = [eid for eid in included if not child_map.get(eid)]
    head: str | None = None
    if len(leaves) == 1:
        head = leaves[0]
    elif len(leaves) == 0 and len(included) == 1:
        head = next(iter(included))
    # else: multiple leaves => unresolved fork, head stays None

    if conflicts and not head:
        return (None, conflicts)
    if conflicts:
        return (head, conflicts)
    return (head, [])


# ----------------------------------------------------------------------
# B1 candidate-set rule for reclassify events (FORMAT F.8.1)
# ----------------------------------------------------------------------


def _original_event_id_from_reclassify(reclassify: dict[str, Any]) -> str | None:
    ext = reclassify.get("extensions", {})
    if not isinstance(ext, dict):
        return None
    return ext.get("x_reclassifies")


def _declared_replacement_id(reclassify: dict[str, Any]) -> str | None:
    ext = reclassify.get("extensions", {})
    if not isinstance(ext, dict):
        return None
    return ext.get("x_replacement_event_id")


def candidate_set_for_reclassify(
    events: list[dict[str, Any]],
    reclassify_event_id: str,
    reg: SubtypeRegistry | None = None,
    authority: Any = None,
) -> FoldResult:
    """Return candidate-set outcome for one reclassify event per FORMAT F.8.1.

    A replacement candidate C must:
      - be a valid registered combination (UNCLASSIFIED excluded) — enforced
        when `reg` is provided;
      - be authorized per the Case S authority evaluator — enforced when
        `authority` is provided; an invalid/unauthorized matching candidate
        does NOT count and cannot create false ambiguity;
      - have prior_refs.supports containing reclassify_event_id
      - have extensions.x_reclassified_from == R.extensions.x_reclassifies
    """
    reclassify: dict[str, Any] | None = None
    for ev in events:
        if ev.get("event_id") == reclassify_event_id:
            reclassify = ev
            break
    if reclassify is None:
        return FoldResult(state="unresolved", detail="reclassify event not found")

    original_id = _original_event_id_from_reclassify(reclassify)
    declared_id = _declared_replacement_id(reclassify)
    if original_id is None:
        return FoldResult(
            state="unresolved",
            detail="reclassify missing extensions.x_reclassifies",
        )

    by_eid = {ev.get("event_id"): ev for ev in events}
    candidates: list[str] = []
    for ev in events:
        if ev.get("event_id") == reclassify_event_id:
            continue
        pr = ev.get("prior_refs", {})
        supports = pr.get("supports", []) if isinstance(pr, dict) else []
        if reclassify_event_id not in supports:
            continue
        ext = ev.get("extensions", {})
        if not isinstance(ext, dict):
            continue
        if ext.get("x_reclassified_from") != original_id:
            continue
        # R4: structural-match candidates must ALSO pass registry validity
        # and authority eligibility before they count.
        if reg is not None and not _event_registry_valid(reg, ev):
            continue
        if authority is not None:
            authorized, _ = authority.is_authorized(ev)
            if not authorized:
                continue
        candidates.append(ev.get("event_id"))

    n = len(candidates)
    if n == 0:
        return FoldResult(
            state="non_green",
            candidate_ids=[],
            detail="no valid/authorized replacement candidates (unresolved/non-green)",
        )
    if n >= 2:
        return FoldResult(
            state="ambiguous",
            candidate_ids=candidates,
            detail=(
                f"{n} valid/authorized candidates -> ambiguous/conflict/non-green "
                f"(no time/file-order winner; declared-id match is irrelevant)"
            ),
        )
    # n == 1
    only = candidates[0]
    if declared_id is None:
        return FoldResult(
            state="non_green",
            candidate_ids=candidates,
            head_event_id=None,
            detail="declared replacement id missing on reclassify (unresolved/non-green)",
        )
    if only == declared_id:
        return FoldResult(
            state="ok",
            candidate_ids=candidates,
            head_event_id=only,
            detail=f"unambiguous replacement: candidate {only} matches declared id",
        )
    return FoldResult(
        state="non_green",
        candidate_ids=candidates,
        detail=(
            f"single candidate {only} != declared id {declared_id} "
            f"(declared missing/wrong; unresolved/non-green)"
        ),
    )


def derived_complete(
    events: list[dict[str, Any]],
    reg: SubtypeRegistry,
    subject: str,
    reclassify_event_id: str | None = None,
    authority: Any = None,
    receipt_assurance: dict[str, str] | None = None,
) -> FoldResult:
    """Compute derived COMPLETE for a subject (S1-5 minimum Case S gate).

    COMPLETE is a derived state (NOT a canonical event). Requirements:
      - subject has a state head (from fold_subject over valid/authorized events)
      - the effective head IS a completion-claim (registered state_transition)
      - at least one EFFECTIVE VERIFIED-SELF self-verification supports the
        completion path. When `receipt_assurance` (corpus effective-assurance
        map from authority.evaluate_receipt_assurance) is supplied, the
        verification must carry "verified-self" there (S1-F1: authorized
        receipt + resolving support target); without the map, the older
        receipt-class check applies (walkthrough unit path).
      - no blocking lifecycle conflict / unresolved reclassify
      - no canonical actor-authored COMPLETE event exists (by construction:
        the registry has no COMPLETE subtype)

    A competing reclassify candidate-set (ambiguous/non-green/unresolved) is the
    most informative blocker and takes priority over a bare "no head" outcome,
    since it names WHY the subject's state cannot fold to a single head.
    """
    if reclassify_event_id is not None:
        cs = candidate_set_for_reclassify(
            events, reclassify_event_id, reg=reg, authority=authority
        )
        if cs.state in ("ambiguous", "non_green", "unresolved"):
            return FoldResult(
                state="non_green",
                detail=f"reclassify candidate set {cs.state}: {cs.detail}",
            )
    head, conflicts = fold_subject(events, reg, subject, authority=authority)
    if head is None:
        return FoldResult(state="non_green", detail="no head (subject empty or unresolved fork)")
    if conflicts:
        return FoldResult(
            state="non_green",
            detail=f"concurrent fork: {len(conflicts)} conflicting transition(s)",
        )
    # R2: the effective head must be a completion-claim.
    by_eid = {ev.get("event_id"): ev for ev in events}
    head_ev = by_eid.get(head, {})
    if head_ev.get("subtype") != "completion-claim":
        return FoldResult(
            state="non_green",
            head_event_id=head,
            detail=(
                f"head {head} is {head_ev.get('subtype')!r}, not completion-claim; "
                "derived COMPLETE requires a completion-claim as effective head"
            ),
        )
    # R2/S1-F1: an EFFECTIVE VERIFIED-SELF self-verification must support the
    # completion path. The normal Case S pattern: completion-claim head ->
    # supports self-verification; the self-verification targets the relevant
    # execution path with agreeing effect identities. S1-F1 forbids unlocking
    # COMPLETE via an arbitrary dispatch ancestor: the verification must
    # support the head itself or an event on the head's parent chain, and (when
    # a corpus effective-assurance map is supplied) must actually resolve to a
    # valid/authorized/in-scope/eligible target with matching effect scope.
    ancestor_ids: set[str] = {head}
    collect = [head_ev]
    i = 0
    while i < len(collect):
        cur = collect[i]
        i += 1
        parents, _ = _parents_supports(cur)
        for p in parents:
            if p not in ancestor_ids and p in by_eid:
                ancestor_ids.add(p)
                collect.append(by_eid[p])
    chain_events = [by_eid[e] for e in ancestor_ids if e in by_eid]
    verified_self: str | None = None
    for ev in events:
        if ev.get("subtype") != "self-verification":
            continue
        if not _event_registry_valid(reg, ev):
            continue
        if authority is not None:
            authorized, _ = authority.is_authorized(ev)
            if not authorized:
                continue
            # S1-F1: when the caller supplies the corpus effective-assurance
            # map, the verification's support must RESOLVE (target exists,
            # authorized, same subject, eligible subtype, effect agreement) —
            # an authorized-but-unresolvable verification contributes nothing.
            if receipt_assurance is not None and receipt_assurance.get(
                ev.get("event_id")
            ) != "verified-self":
                continue
        _, supports = _parents_supports(ev)
        if any(s in ancestor_ids for s in supports):
            verified_self = ev.get("event_id")
            break
    if verified_self is None:
        return FoldResult(
            state="non_green",
            head_event_id=head,
            detail=(
                "completion-claim present but no valid VERIFIED-SELF "
                "self-verification supports the completion path (assurance gap)"
            ),
        )
    if reclassify_event_id is not None:
        return FoldResult(
            state="ok",
            head_event_id=head,
            detail=(
                f"derived COMPLETE; head={head}; VERIFIED-SELF={verified_self}; "
                "reclassify resolved"
            ),
        )
    return FoldResult(
        state="ok",
        head_event_id=head,
        detail=f"derived COMPLETE; head={head}; VERIFIED-SELF={verified_self}",
    )

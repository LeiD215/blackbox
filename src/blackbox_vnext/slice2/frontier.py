"""S2-3 frontier / subject heads.

Derive `frontier` from Slice 1 lifecycle fold using the **same authority
evaluator** as projector/live fold (TASK S2-R2). Frozen Slice 0 checkpoint
schema: subject -> single head event_id.

Rules:
  - Only subjects with one deterministic lifecycle head can be represented
    directly in a checkpoint.
  - Multi-head / ambiguous frontier: checkpoint generation must refuse with
    FRONTIER_AMBIGUOUS rather than silently choose a head. The projector
    still reports all conflicting heads.
  - Support / non_state receipts never become lifecycle heads (handled by
    Slice 1's fold_subject which restricts state_transition subtypes and
    filters unauthorized transitions when authority is supplied).
  - **Authority is mandatory**: without authority, unauthorized state
    transitions could become heads (TASK S2-R2). All callers must supply
    the same Slice 1 CaseS authority used by projector + replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import fold as r1_fold  # noqa: E402
from blackbox_vnext.slice1 import subtypes as r1_subtypes  # noqa: E402
from blackbox_vnext.slice1 import authority as r1_authority  # noqa: E402

from . import dependency_pin as bbx2_dep


class FrontierAmbiguous(Exception):
    """Raised when a subject has multiple conflicting lifecycle heads that
    cannot be represented by the frozen Slice 0 frontier schema (one head
    per subject)."""

    def __init__(self, subject: str, head: str | None, conflicts: list[str]):
        self.subject = subject
        self.head = head
        self.conflicts = list(conflicts)
        super().__init__(
            f"FRONTIER_AMBIGUOUS: subject={subject} head={head} "
            f"conflicts={conflicts}"
        )


@dataclass
class FrontierResult:
    frontier: dict[str, str]              # subject -> head event_id
    conflicts: dict[str, list[str]]       # subject -> list of competing heads
    ambiguous_subjects: list[str]         # subjects that refused checkpoint


def compute_frontier(events: list[dict[str, Any]],
                    reg: r1_subtypes.SubtypeRegistry,
                    auth: r1_authority.CaseSAuthorityProfile) -> FrontierResult:
    """Walk Slice 1 fold_subject per subject using the Slice 1 CaseS
    authority evaluator. Collect each head + conflicts.

    The result is returned unconditionally (for the projector). Use
    `enforce_single_head()` to refuse checkpoint generation when any subject
    is in conflict.
    """
    # S2-H2: fail-closed Slice 1 dependency gate.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    frontier: dict[str, str] = {}
    conflicts_map: dict[str, list[str]] = {}
    seen_subjects: set[str] = set()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        subj = ev.get("subject")
        if not isinstance(subj, str) or not subj:
            continue
        if subj in seen_subjects:
            continue
        seen_subjects.add(subj)
        # TASK S2-R2: pass the authority evaluator so unauthorized
        # state-transition events cannot become heads.
        head, conflicts = r1_fold.fold_subject(
            events, reg, subj, authority=auth,
        )
        if head is not None:
            frontier[subj] = head
        if conflicts:
            conflicts_map[subj] = list(conflicts)
    return FrontierResult(
        frontier=frontier,
        conflicts=conflicts_map,
        ambiguous_subjects=sorted(conflicts_map.keys()),
    )


def enforce_single_head(fr: FrontierResult) -> None:
    """Refuse checkpoint generation when any subject has multiple competing
    heads (frozen Slice 0 schema: subject -> single head event_id)."""
    if fr.ambiguous_subjects:
        subj = fr.ambiguous_subjects[0]
        conflicts = fr.conflicts.get(subj, [])
        head = fr.frontier.get(subj)
        raise FrontierAmbiguous(subj, head, conflicts)

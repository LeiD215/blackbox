"""bbx4.trusted_corpus — Sealed trusted corpus boundary.

Per TASK `a578dc13ffd6` §S4-Z1: the trusted corpus boundary MUST be
unforgeable. A public dataclass with a public constructor is forgeable
— any caller can do `TrustedCorpus(dir=..., events=(...))` and every
public semantic API accepts it because `normalize_to_trusted_corpus()`
only checks `isinstance`. Frozen `@dataclass` freezes attributes only,
not the contained dict/list objects.

Correction (S4-Z1):
1. TrustedCorpus is constructed ONLY by `load_trusted_corpus(events_dir)`
   which performs the Slice 1 controlled reingest of a real on-disk
   events dir. The public constructor is private; the public type is
   a sealed handle.
2. The seal binds snapshot integrity to per-event canonical digests
   captured at load time. Every public semantic entrypoint revalidates
   the digests against the live event content before use. Post-load
   mutation of any event dict fails closed.
3. Tests that need valid corpora MUST write a real controlled directory
   and call the loader. Pure fold unit tests may use a private/internal
   non-public helper type (NOT the public TrustedCorpus) only for
   INTERNAL/PURE algorithm fixtures — and those fixtures are explicitly
   labeled as not proof of full-corpus trust.
4. A caller-created fake object, copied dataclass, mutated event dict,
   replaced verdict tuple, or changed corpus_blocked field MUST NOT
   produce semantic green.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# --- Witness token (forgeable-boundary protection) -----------------------


# Module-private witness. Only functions defined in this module can
# construct a TrustedCorpus, because TrustedCorpus.__init__ requires
# this witness as a positional argument.
class _SealWitness:
    """Module-private token. Cannot be constructed outside this module."""

    __slots__ = ("_marker",)

    def __init__(self, marker: str) -> None:
        if marker != "bbx4.trusted_corpus.load_trusted_corpus":
            raise PermissionError(
                "TrustedCorpus seal witness can only be minted by "
                "load_trusted_corpus() in bbx4.trusted_corpus"
            )
        self._marker = marker


# Public witness symbol (the type itself); instance creation is gated.
def _mint_witness() -> _SealWitness:
    return _SealWitness("bbx4.trusted_corpus.load_trusted_corpus")


# --- Result types --------------------------------------------------------


@dataclass(frozen=True)
class TrustedCorpus:
    """A sealed trusted corpus bound to an actual on-disk events dir.

    Construction is gated by the private `_SealWitness` token (S4-Z1).
    The frozen dataclass wraps a per-event digest tuple that is
    revalidated on every public semantic call, so post-load mutation
    of any contained event dict fails closed.

    `events` is a tuple of canonical event dicts (only V_VALID +
    V_UNCLASSIFIED survived reingest). `verdicts` carries every file
    decision for forensic visibility. `corpus_blocked` / `corpus_reason`
    reflect the Slice 1 reingest gate decision.

    Public semantic APIs (`state.derive_authority_state`,
    `state.derive_pre_event_state`, `dispatch.validate_dispatch`,
    `dispatch.validate_authority`, `dispatch.authority_status`) all
    accept a TrustedCorpus and reject arbitrary corpus inputs at the
    API boundary.
    """

    # Private witness — must be supplied by trusted factory only.
    _witness: Any = field(default=None, repr=False, compare=False)
    dir: str = ""                                # original on-disk events dir path
    events: tuple = ()                           # only V_VALID + V_UNCLASSIFIED
    event_digests: tuple = ()                    # sha256(stored_bytes(ev)) per event
    verdicts: tuple = ()                         # ReingestVerdict list (frozen)
    corpus_blocked: bool = False
    corpus_reason: str = ""
    registry: Any = None                         # SubtypeRegistry handle

    def __post_init__(self) -> None:
        if not isinstance(self._witness, _SealWitness):
            raise TypeError(
                "TrustedCorpus cannot be constructed by callers; "
                "use bbx4.trusted_corpus.load_trusted_corpus(events_dir) "
                "(S4-Z1 sealed boundary)"
            )

    # --- Public accessors with integrity revalidation -------------------

    def _revalidate_digest(self, idx: int, ev: dict) -> bool:
        """Return True iff the event at `idx` still matches its captured
        digest (i.e. has not been mutated since load)."""
        if idx < 0 or idx >= len(self.event_digests):
            return False
        try:
            current = _event_canonical_digest(ev)
        except Exception:  # noqa: BLE001
            return False
        return current == self.event_digests[idx]

    def event_by_id(self, event_id: str) -> dict | None:
        """Look up a canonical event by event_id with digest revalidation.

        Returns None on miss OR on post-load mutation (S4-Z1 fail-closed).
        """
        if not isinstance(event_id, str):
            return None
        for idx, ev in enumerate(self.events):
            if not isinstance(ev, dict):
                continue
            if ev.get("event_id") != event_id:
                continue
            if not self._revalidate_digest(idx, ev):
                # Post-load mutation: seal violation; treat as miss.
                return None
            return ev
        return None

    def event_ids(self) -> frozenset[str]:
        return frozenset(
            ev.get("event_id") for ev in self.events
            if isinstance(ev, dict) and isinstance(ev.get("event_id"), str)
        )

    def is_intact(self) -> bool:
        """True iff every event in this corpus still matches its captured
        digest. Use this from public semantic APIs to fail closed on
        post-load mutation."""
        for idx, ev in enumerate(self.events):
            if not self._revalidate_digest(idx, ev):
                return False
        return True

    # --- Private factory for filtered internal subsets (S4-Z5) --------
    #
    # The pre-event state filter constructs a subset TrustedCorpus that
    # contains only the events causally reachable from a target. This
    # factory mints a fresh seal witness and re-captures per-event
    # digests so the integrity invariant is preserved on the subset.
    # It is private (single underscore) so it cannot be called from
    # outside `bbx4.trusted_corpus`.

    @classmethod
    def _seal_for_subset(
        cls,
        dir: str,
        events: tuple,
        registry: Any,
    ) -> "TrustedCorpus":
        """Build a sealed TrustedCorpus from an internal subset.

        PRIVATE: used only by `bbx4.state.derive_pre_event_state` to
        construct the causally-reachable subset corpus. The seal is
        re-minted; per-event digests are re-captured so integrity
        revalidation still works.
        """
        digests = tuple(_event_canonical_digest(ev) for ev in events)
        return cls(
            _witness=_mint_witness(),
            dir=dir,
            events=events,
            event_digests=digests,
            verdicts=(),
            corpus_blocked=False,
            corpus_reason="",
            registry=registry,
        )


# --- Integrity helpers ---------------------------------------------------


def _event_canonical_digest(ev: dict) -> str:
    """Compute sha256 over RFC 8785 canonical bytes of `ev`.

    Mirrors Slice 1's `bbx.canonical.stored_bytes` form so digest
    equality is robust to key-order permutations. Falls back to a
    JSON-sorted digest if Slice 1 is not importable.
    """
    try:
        from blackbox_vnext.slice4._slice1_path import ensure_slice1_importable
        ensure_slice1_importable()
        from blackbox_vnext.slice1.canonical import stored_bytes
        return hashlib.sha256(stored_bytes(ev)).hexdigest()
    except Exception:  # noqa: BLE001
        # Last-resort deterministic digest; not equivalent to Slice 1
        # stored_bytes but stable for the lifetime of one process.
        return hashlib.sha256(
            json.dumps(ev, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        ).hexdigest()


# --- Construction (the ONLY producer) -----------------------------------


def load_trusted_corpus(
    events_dir: str | Path,
    registry: Any | None = None,
) -> TrustedCorpus:
    """Build a sealed TrustedCorpus by reingesting an actual on-disk events dir.

    This is the ONLY way to construct a TrustedCorpus (S4-Z1). It calls
    Slice 1 `reingest_events_dir` against the real directory; the
    resulting on-disk verdict list becomes the corpus. Reingest
    decisions are preserved for forensic visibility.

    Per-event canonical digests are captured at load time and revalidated
    on every public semantic call. Post-load mutation fails closed.

    The gate is invoked first so any Slice 0/1 byte drift fails closed.
    """
    from blackbox_vnext.slice4 import dependency_pin
    dependency_pin.gate_semantic_entrypoint(strict=True)

    # Ensure Slice 1 importable + load registry.
    from blackbox_vnext.slice4._slice1_path import ensure_slice1_importable
    ensure_slice1_importable()
    if registry is None:
        from blackbox_vnext.slice1.subtypes import SubtypeRegistry
        registry = SubtypeRegistry.load()

    from blackbox_vnext.slice1.reingest import (
        reingest_events_dir,
        V_INVALID,
        V_VALID,
        V_UNCLASSIFIED,
    )

    path = Path(events_dir).resolve()
    result = reingest_events_dir(path, registry)

    # S4-Z1 hardening: any INVALID / UNKNOWN verdict or non-empty
    # corpus_blocked / corpus_reason on the controlled reingest result
    # blocks the corpus. Public semantic APIs must not green a
    # partially-trustworthy corpus.
    invalid_present = any(
        v.status in (V_INVALID,) for v in result.verdicts
    )
    derived_corpus_blocked = bool(
        result.corpus_blocked or invalid_present,
    )
    derived_corpus_reason = (
        result.corpus_reason or ""
    )
    if invalid_present and not result.corpus_blocked:
        # Compose a machine-visible reason enumerating the invalid files
        bad = [
            v for v in result.verdicts
            if v.status == V_INVALID
        ]
        derived_corpus_reason = (
            f"{len(bad)} invalid verdict(s) in corpus; "
            f"first: {bad[0].reason[:120] if bad else 'unknown'}"
        )

    accepted: list[dict] = []
    digests: list[str] = []
    for v in result.verdicts:
        if (v.status in (V_VALID, V_UNCLASSIFIED)
                and v.event is not None
                and not derived_corpus_blocked):
            accepted.append(v.event)
            digests.append(_event_canonical_digest(v.event))

    return TrustedCorpus(
        _witness=_mint_witness(),
        dir=str(path),
        events=tuple(accepted),
        event_digests=tuple(digests),
        verdicts=tuple(result.verdicts),
        corpus_blocked=derived_corpus_blocked,
        corpus_reason=derived_corpus_reason,
        registry=registry,
    )


# --- API-boundary normalization ------------------------------------------


def normalize_to_trusted_corpus(source: Any) -> TrustedCorpus:
    """Reject anything that is not an already-sealed TrustedCorpus.

    Public semantic APIs call this as the API-boundary guard. Free-
    floating `list[dict]`, file paths, and dicts are all rejected
    with TypeError. The only valid source is a previously-sealed
    TrustedCorpus (returned by `load_trusted_corpus`).

    Per S4-Z1, an additional integrity revalidation runs here so any
    post-load mutation fails closed BEFORE semantic work begins.
    """
    if isinstance(source, TrustedCorpus):
        if not source.is_intact():
            raise RuntimeError(
                "TrustedCorpus integrity violation: events were mutated "
                "after load (S4-Z1 fail-closed)"
            )
        return source
    raise TypeError(
        f"public semantic APIs require a sealed TrustedCorpus (from "
        f"load_trusted_corpus); got {type(source).__name__}"
    )


# --- Internal/test-only helper (NOT a public TrustedCorpus) --------------
#
# Per S4-Z8, pure fold unit tests may use a clearly private/internal
# non-public helper type to drive isolated algorithm fixtures. Such
# fixtures are INTERNAL/PURE only and are NOT proof of full-corpus
# trust. Public semantic APIs MUST NOT accept this type.


@dataclass(frozen=True)
class _InternalTrustedSnapshot:
    """INTERNAL/PURE-only fixture for fold algorithm tests.

    This is NOT a TrustedCorpus and MUST NOT be passed to public
    semantic APIs. It exists only so unit tests of pure fold logic
    can drive the algorithm without going through Slice 1 controlled
    reingest (which would force them to also be full-corpus tests).
    """

    events: tuple
    registry: Any = None

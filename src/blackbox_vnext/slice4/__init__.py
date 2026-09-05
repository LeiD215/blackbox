"""bbx4 — Blackbox vNext Reference Implementation Slice 4.

multi-agent dispatch / authority (corrected per TASK `5cf6cfc763ec`
S4-Q1..Q10, TASK `a578dc13ffd6` S4-Z1..Z8, and TASK `67b4b45be077`
S4-AA1..AA9).

This package is intentionally small: it adds a fourth slice-prefix
on top of Slice 0/1. It does NOT modify prior slices, the Formal
Spec, the Reference Design, the HANDOFF, or production artifacts.

Authority philosophy (per Formal Spec v0.3.2 §§7/9 + Reference
Design v0.1.4 Slice 4 frozen boundary):

- Bootstrap root (`human:owner`) is the explicit trusted principal.
  Root trust is policy input, never self-proved by any authority
  event (Q1 + Q9).
- Capability is an exact tuple `(actor, action, surface)`.
  Unknown actor/action/surface fails closed with a machine-visible
  reason.
- AUTHORITY-CHANGE uses pre-state authority of the issuer and
  MUST NOT authorize itself using the capability it grants.
- Public semantic APIs accept a RAW EVENTS_DIR PATH ONLY (AA1).
  TrustedCorpus is constructed internally. Arbitrary `list[dict]` /
  TrustedCorpus / _InternalTrustedSnapshot are NOT accepted as
  public parameters. Tests use write_corpus() + load_test_corpus()
  (real on-disk reingest) to build valid paths.
- Genesis events use zero parent per Design v0.1.4 D3 (Q1).
- `parent` is strictly per-subject lifecycle; cross-subject authority
  binding uses canonical `prior_refs.supports` ONLY (Q2 + Z4).
- The causal scheduler uses a separate pending set for retryable
  events; only non-retryable decisions are finalized (AA2).
- Fork arbitration groups ALL eligible siblings at their common
  frontier BEFORE mutating any stream head (AA3). If >=2 eligible
  siblings share a frontier, none wins — stream enters conflict.
- A cited support is effective only if it is an EXACT effective
  historical head for the issuer, reconstructed by re-running the
  fold to that cited event (AA4). Fixed-depth / payload-only checks
  are rejected.
- Pre-event state for dispatch/authority uses the same historical-head
  evaluator, not a forgeable subset corpus (AA5).
- Resolution applies ONLY to an actual unresolved conflict (AA6).
  Resolution on a non-conflicting stream is RESOLUTION_NO_CONFLICT
  and does NOT advance the head.
- Delegated resolution authorization requires `prior_refs.supports`
  citing an effective historical head (AA7).
- `_InternalTrustedSnapshot` is NOT accepted by public functions;
  private `_derive_*_internal(snapshot)` are INTERNAL/PURE-only (AA8).
- No IAM/RBAC, no Git authority, no mutable second SSOT.
- Every public semantic entrypoint calls `dependency_pin.
  gate_semantic_entrypoint()` automatically (Q7 closure).
"""

from blackbox_vnext.slice4 import (
    authority_profile,
    corpus_gate,
    dependency_pin,
    dispatch,
    state,
    trusted_corpus,
)

__version__ = "0.4.2"
GENERATOR_IDENTITY = "bbx4-0.4.2"
FRAMEWORK_VERSION = "0.3.2"
SCHEMA_VERSION = "0.3.2"

__all__ = [
    "FRAMEWORK_VERSION",
    "GENERATOR_IDENTITY",
    "SCHEMA_VERSION",
    "__version__",
    "authority_profile",
    "corpus_gate",
    "dependency_pin",
    "dispatch",
    "state",
    "trusted_corpus",
]

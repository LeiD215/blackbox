"""bbx4.authority_profile — minimal exact capability tuple registry.

Per Slice 4 frozen profile (Reference Design v0.1.4 + Formal Spec
v0.3.2 §§7/9), corrected per TASK `5cf6cfc763ec` (S4-Q1..Q10):

- Authority decision is over the exact tuple `(actor, action, surface)`.
- Reference profile defines a small exact action/surface registry.
- Unknown actor/action/surface fails closed with a machine-visible
  reason (UNKNOWN-ACTOR / UNKNOWN-ACTION / UNKNOWN-SURFACE).
- No wildcard/prefix inheritance, no groups, no roles, no conditional
  policy language, no deny/allow precedence engine.

Bootstrap root (`human:owner`) is fixed policy input; it is not
self-proved by any authority event and CANNOT be mutated by canonical
authority events (BOOTSTRAP-IMMUTABLE — Q9). Bootstrap holds the
reference capabilities needed to create formal TASK/dispatch and
AUTHORITY-CHANGE for any registered surface.

Actor-knownness: an actor is "known" iff it is one of the fixed
bootstrap principals OR it is a canonical beneficiary/principal
introduced by valid authority history.

Per Q2: parent is strictly per-subject lifecycle; cross-subject
authority dependency uses `prior_refs.supports` and supports must
fold to a canonical cited receipt (never directly creates authority).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    from blackbox_vnext.slice4.state import AuthorityState


# --- Failure reasons (machine-visible) ------------------------------------

UNKNOWN_ACTOR = "UNKNOWN-ACTOR"
UNKNOWN_ACTION = "UNKNOWN-ACTION"
UNKNOWN_SURFACE = "UNKNOWN-SURFACE"
UNKNOWN_OP = "UNKNOWN-OP"

# Parent lifecycle reasons (Q1, Q4)
PARENT_CROSS_SUBJECT = "PARENT-CROSS-SUBJECT"
PARENT_STALE = "PARENT-STALE"
PARENT_UNRESOLVED_FORK = "PARENT-UNRESOLVED-FORK"
DISPATCH_GENESIS = "DISPATCH-GENESIS"

# Supports binding reasons (Q2)
SUPPORTS_INVALID = "SUPPORTS-INVALID"
SUPPORTS_MISSING = "SUPPORTS-MISSING"
ISSUER_PRESSTATE_INVALID = "ISSUER-PRESSTATE-INVALID"

# Conflict / resolution reasons (Q5, Q6, AA6)
STREAM_CONFLICT = "STREAM-CONFLICT"
GENESIS_FORK_ORPHANED = "GENESIS-FORK-ORPHANED"
RESOLUTION_APPLIED = "RESOLUTION-APPLIED"
RESOLUTION_INVALID = "RESOLUTION-INVALID"
RESOLUTION_NO_CONFLICT = "RESOLUTION-NO-CONFLICT"

# Event-level reasons (Q8)
EVENT_INVALID = "EVENT-INVALID"
PARENT_INVALID = "PARENT-INVALID"
SELF_AUTHORIZE = "SELF-AUTHORIZE"

# Bootstrap root reasons (Q9)
BOOTSTRAP_IMMUTABLE = "BOOTSTRAP-IMMUTABLE"


# --- Action registry ------------------------------------------------------

ACTIONS: frozenset[str] = frozenset({
    "task.dispatch",
    "authority.change",
})


# --- Surface registry ------------------------------------------------------

SURFACES: frozenset[str] = frozenset({
    "project:reference",
})


# --- Bootstrap policy input ----------------------------------------------

DEFAULT_HUMAN_PRINCIPALS: tuple[str, ...] = ("human:owner",)


# --- Result types ----------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    """An exact capability tuple. Never partially populated."""

    actor: str
    action: str
    surface: str


@dataclass(frozen=True)
class AuthorityCheckResult:
    """Outcome of `resolve_capability`."""

    actor: str
    action: str
    surface: str
    granted: bool
    reason: str  # "OK" | UNKNOWN_ACTOR | UNKNOWN_ACTION | UNKNOWN_SURFACE | NOT-HELD


# --- Helpers --------------------------------------------------------------


def is_known_actor(actor: Any) -> bool:
    """Bootstrap human principals are always known. Other actors'
    knownness depends on derived authority history; use
    `is_known_actor_with_state()` for that variant."""
    return isinstance(actor, str) and actor in DEFAULT_HUMAN_PRINCIPALS


def is_known_actor_with_state(actor: Any, auth_state: "AuthorityState | None") -> bool:
    """True iff actor is a known principal under the bootstrap
    policy or has been canonically introduced by valid authority
    history. With `auth_state=None`, only bootstrap principals
    pass."""
    if not isinstance(actor, str):
        return False
    if actor in DEFAULT_HUMAN_PRINCIPALS:
        return True
    if auth_state is None:
        return False
    return actor in auth_state.known_principals()


def is_known_action(action: Any) -> bool:
    return isinstance(action, str) and action in ACTIONS


def is_known_surface(surface: Any) -> bool:
    return isinstance(surface, str) and surface in SURFACES


def is_bootstrap_root_principal(actor: Any) -> bool:
    """True iff actor is a bootstrap root principal (whose authority
    is fixed policy input and cannot be mutated by canonical
    events)."""
    return isinstance(actor, str) and actor in DEFAULT_HUMAN_PRINCIPALS


# --- Public API -----------------------------------------------------------


def bootstrap_capabilities() -> frozenset[Capability]:
    """Return the fixed bootstrap capability set."""
    out: set[Capability] = set()
    for action in ACTIONS:
        for surface in SURFACES:
            out.add(Capability(actor=DEFAULT_HUMAN_PRINCIPALS[0],
                                action=action, surface=surface))
    return frozenset(out)


def resolve_capability(
    actor: Any,
    action: Any,
    surface: Any,
    held: frozenset[Capability],
    auth_state: "AuthorityState | None" = None,
) -> AuthorityCheckResult:
    """Resolve a capability decision against a derived authority state.

    `held` is the *derived* capability set the issuer actually
    possesses in pre-event authority state.

    `auth_state` (optional) is the derived `AuthorityState` used to
    determine actor-knownness. If omitted, only bootstrap principals
    are considered known (preserves backwards-compatible fail-closed
    semantics for tests that don't construct a full AuthorityState).

    Performs fail-closed field validation AND exact-tuple membership
    against `held`. Returns AuthorityCheckResult.
    """
    if not is_known_actor_with_state(actor, auth_state):
        return AuthorityCheckResult(
            actor=str(actor), action=str(action), surface=str(surface),
            granted=False, reason=UNKNOWN_ACTOR,
        )
    if not is_known_action(action):
        return AuthorityCheckResult(
            actor=str(actor), action=str(action), surface=str(surface),
            granted=False, reason=UNKNOWN_ACTION,
        )
    if not is_known_surface(surface):
        return AuthorityCheckResult(
            actor=str(actor), action=str(action), surface=str(surface),
            granted=False, reason=UNKNOWN_SURFACE,
        )
    target = Capability(actor=actor, action=action, surface=surface)
    if target in held:
        return AuthorityCheckResult(
            actor=actor, action=action, surface=surface,
            granted=True, reason="OK",
        )
    return AuthorityCheckResult(
        actor=actor, action=action, surface=surface,
        granted=False, reason="NOT-HELD",
    )

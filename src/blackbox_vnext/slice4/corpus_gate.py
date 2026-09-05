"""bbx4.corpus_gate — strict local extension parser + duplicate-key + surrogate rejection.

Per Slice 4 frozen profile (Reference Design v0.1.4 + Formal Spec
v0.3.2), corrected per TASK `5cf6cfc763ec` (S4-Q1..Q10):

- Use existing canonical event envelope and the open `extensions` object.
  Do NOT change Slice 0 schema.
- AUTHORITY-CHANGE uses a strict local `authority_change` extension
  with `{op: grant|revoke, beneficiary, action, surface}` plus optional
  `supports` (cross-subject issuer pre-state binding per Q2).
- TASK / dispatch uses a strict local `dispatch` extension with
  `{assignee, surface}`.
- CHANGE / resolution uses a strict local `resolution` extension with
  `{op: select|reject, resolved_head_event_id?}` (Q6).
- Reject duplicate JSON keys at every depth.
- Reject lone surrogates and malformed UTF-8.
- Reject unknown fields and unknown operation values.

All Slice 4 semantic entrypoints must reject malformed payloads
without partial effect; this module is the choke point.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# --- Failure reasons (machine-visible) ------------------------------------

MALFORMED_EXTENSION = "MALFORMED-EXTENSION"
DUPLICATE_KEY = "DUPLICATE-KEY"
LONE_SURROGATE = "LONE-SURROGATE"
UNKNOWN_FIELD = "UNKNOWN-FIELD"
UNKNOWN_OP = "UNKNOWN-OP"


# --- AUTHORITY-CHANGE extension -------------------------------------------

# Per S4-Z4: `prior_refs.supports` is the SOLE support/evidence reference
# channel for authority-change (canonical, frozen). The extension-level
# `supports` field is removed to eliminate split-brain support semantics.
AUTHORITY_CHANGE_FIELDS: frozenset[str] = frozenset({
    "op", "beneficiary", "action", "surface",
})
AUTHORITY_CHANGE_OPS: frozenset[str] = frozenset({"grant", "revoke"})


# --- TASK / dispatch extension --------------------------------------------

DISPATCH_FIELDS: frozenset[str] = frozenset({"assignee", "surface"})


# --- CHANGE / resolution extension ----------------------------------------

# Per S4-Z6 + S4-Z7: resolution is a same-subject state-transition
# lifecycle event. Both `op=select` and `op=reject` require explicit
# identification of the target (selected_head_event_id for select,
# rejected_head_event_id for reject). Permuting the prior_refs.parent
# array order MUST NOT change outcome — branch choice is by explicit
# extension field, not array position.
RESOLUTION_FIELDS: frozenset[str] = frozenset({
    "op", "selected_head_event_id", "rejected_head_event_id",
})
RESOLUTION_OPS: frozenset[str] = frozenset({"select", "reject"})


# --- Result types ---------------------------------------------------------


@dataclass(frozen=True)
class AuthorityChange:
    """A parsed AUTHORITY-CHANGE extension payload.

    Strict local profile: `{op, beneficiary, action, surface}`. There
    is NO extension-level `supports` field — cross-subject pre-state
    binding uses canonical `prior_refs.supports` (S4-Z4). Bootstrap
    issuer does not need supports.
    """

    op: str            # "grant" | "revoke"
    beneficiary: str   # actor identity string
    action: str        # exact registered action
    surface: str       # exact registered surface


@dataclass(frozen=True)
class Dispatch:
    """A parsed TASK / dispatch extension payload."""

    assignee: str
    surface: str


@dataclass(frozen=True)
class Resolution:
    """A parsed CHANGE / resolution extension payload.

    Per S4-Z6 + S4-Z7: resolution is a same-subject lifecycle event.
    Both ops require explicit head identification:
    - `op=select` requires `selected_head_event_id` (one of the parents)
    - `op=reject` requires `rejected_head_event_id` (one of the parents)

    Branch choice is by explicit extension field — parent array order
    MUST NOT decide outcome.
    """

    op: str                              # "select" | "reject"
    selected_head_event_id: str | None   # required for "select"
    rejected_head_event_id: str | None   # required for "reject"


@dataclass(frozen=True)
class ExtensionError:
    """A typed extension rejection."""

    reason: str
    detail: str


# --- Strict parser primitives --------------------------------------------


def _check_no_lone_surrogate(text: str, where: str) -> None:
    for idx, ch in enumerate(text):
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise ValueError(
                f"lone surrogate U+{ord(ch):04X} at {where}[{idx}]"
            )


def _make_pairs_hook() -> Any:
    def hook(pairs: list[tuple[str, Any]]) -> dict:
        keys = [k for k, _ in pairs]
        seen: set[str] = set()
        for k in keys:
            if k in seen:
                raise ValueError(f"duplicate JSON object key: {k!r}")
            seen.add(k)
        out: dict[str, Any] = {}
        for k, v in pairs:
            if isinstance(v, dict):
                out[k] = hook(list(v.items()))
            elif isinstance(v, list):
                out[k] = [_recurse(x) for x in v]
            elif isinstance(v, str):
                _check_no_lone_surrogate(v, f"key {k!r}")
                out[k] = v
            else:
                out[k] = v
        return out

    def _recurse(obj: Any) -> Any:
        if isinstance(obj, dict):
            return hook(list(obj.items()))
        if isinstance(obj, list):
            return [_recurse(x) for x in obj]
        if isinstance(obj, str):
            _check_no_lone_surrogate(obj, "list element")
            return obj
        return obj

    return hook


def strict_json_loads(line: bytes | str) -> dict:
    """Parse a JSON object line, rejecting duplicate keys and lone
    surrogates. Returns a plain dict. Raises ValueError otherwise."""
    if isinstance(line, bytes):
        try:
            text = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            raise ValueError(f"utf-8 decode error: {e}") from e
    else:
        text = line
    obj = json.loads(text, object_pairs_hook=_make_pairs_hook())
    if not isinstance(obj, dict):
        raise ValueError(f"not a JSON object, got {type(obj).__name__}")
    return obj


# --- Extension schema enforcement ----------------------------------------


def _require_only_fields(obj: dict, allowed: frozenset[str], label: str) -> None:
    extra = set(obj.keys()) - allowed
    if extra:
        raise ValueError(f"{label} unknown field(s): {sorted(extra)!r}")


def _require_field(obj: dict, name: str, label: str) -> Any:
    if name not in obj:
        raise ValueError(f"{label} missing required field: {name!r}")
    return obj[name]


def _require_str(value: Any, name: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} field {name!r} must be a non-empty string")
    return value


def _parse_supports_field(value: Any, label: str) -> tuple[str, ...]:
    """REMOVED in S4-Z4: support/evidence references use canonical
    `prior_refs.supports` only. This helper is retained as a stub so
    that any legacy import does not silently produce a different
    result; it always raises.
    """
    raise ValueError(
        f"{label}: extension-level `supports` is forbidden (S4-Z4); "
        f"use canonical `prior_refs.supports` instead"
    )


# --- Public API -----------------------------------------------------------


def parse_authority_change(extension: Any) -> AuthorityChange:
    """Parse an AUTHORITY-CHANGE `extensions.authority_change` payload.

    Strict local profile per S4-Z4: `{op, beneficiary, action, surface}`.
    Reject duplicate-key / lone-surrogate / unknown-field (including
    extension-level `supports` — use canonical `prior_refs.supports`)
    / missing-field / wrong-typed / unknown-op.

    Cross-subject pre-state binding lives in `prior_refs.supports` of
    the canonical event envelope, NOT in this extension.
    """
    if not isinstance(extension, dict):
        raise ValueError("authority_change extension must be an object")
    _require_only_fields(extension, AUTHORITY_CHANGE_FIELDS, "authority_change")
    op = _require_str(_require_field(extension, "op", "authority_change"),
                       "op", "authority_change")
    if op not in AUTHORITY_CHANGE_OPS:
        raise ValueError(f"authority_change op {op!r} not in {sorted(AUTHORITY_CHANGE_OPS)}")
    beneficiary = _require_str(
        _require_field(extension, "beneficiary", "authority_change"),
        "beneficiary", "authority_change",
    )
    action = _require_str(
        _require_field(extension, "action", "authority_change"),
        "action", "authority_change",
    )
    surface = _require_str(
        _require_field(extension, "surface", "authority_change"),
        "surface", "authority_change",
    )
    return AuthorityChange(op=op, beneficiary=beneficiary,
                            action=action, surface=surface)


def parse_dispatch(extension: Any) -> Dispatch:
    """Parse a TASK / dispatch `extensions.dispatch` payload.

    Reject duplicate-key / lone-surrogate / unknown-field /
    missing-field / wrong-typed.
    """
    if not isinstance(extension, dict):
        raise ValueError("dispatch extension must be an object")
    _require_only_fields(extension, DISPATCH_FIELDS, "dispatch")
    assignee = _require_str(
        _require_field(extension, "assignee", "dispatch"),
        "assignee", "dispatch",
    )
    surface = _require_str(
        _require_field(extension, "surface", "dispatch"),
        "surface", "dispatch",
    )
    return Dispatch(assignee=assignee, surface=surface)


def parse_resolution(extension: Any) -> Resolution:
    """Parse a CHANGE / resolution `extensions.resolution` payload.

    Reject duplicate-key / lone-surrogate / unknown-field /
    missing-field / wrong-typed / unknown-op.

    Per S4-Z7: branch choice is BY EXPLICIT FIELD, not parent array
    order. Both ops require explicit head identification:
    - `op=select` requires `selected_head_event_id` (one of the parents)
    - `op=reject` requires `rejected_head_event_id` (one of the parents)
    """
    if not isinstance(extension, dict):
        raise ValueError("resolution extension must be an object")
    _require_only_fields(extension, RESOLUTION_FIELDS, "resolution")
    op = _require_str(_require_field(extension, "op", "resolution"),
                       "op", "resolution")
    if op not in RESOLUTION_OPS:
        raise ValueError(f"resolution op {op!r} not in {sorted(RESOLUTION_OPS)}")
    if op == "select":
        sel = _require_str(
            _require_field(extension, "selected_head_event_id", "resolution"),
            "selected_head_event_id", "resolution",
        )
        return Resolution(op=op, selected_head_event_id=sel,
                          rejected_head_event_id=None)
    # op == "reject" — rejected_head_event_id required.
    rej = _require_str(
        _require_field(extension, "rejected_head_event_id", "resolution"),
        "rejected_head_event_id", "resolution",
    )
    return Resolution(op=op, selected_head_event_id=None,
                      rejected_head_event_id=rej)


def is_extensions_ok(extensions: Any) -> bool:
    """Cheap shape check used by validation paths: extensions must
    be a dict (or absent) and not contain a lone-surrogate / dup-key
    string. Detailed semantic checks live in parse_*."""
    if extensions is None:
        return True
    if not isinstance(extensions, dict):
        return False
    try:
        for k, v in extensions.items():
            if not isinstance(k, str):
                return False
            _check_no_lone_surrogate(k, "extension key")
            if isinstance(v, str):
                _check_no_lone_surrogate(v, f"key {k!r}")
        return True
    except ValueError:
        return False
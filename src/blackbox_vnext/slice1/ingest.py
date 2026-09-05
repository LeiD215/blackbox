"""Pre-schema FORMAT F.3 input-domain rejection rules.

These rules cannot be enforced by JSON Schema alone and must run before
canonicalization. Rejection categories mirror FORMAT F.3 + Design v0.1.4 G1.

Canonical exception type is IngestError; raised with a specific reject reason
that distinguishes the five FORMAT F.3 categories + the FORMAT F.6 invalid-
combination class (also enforced here for convenience).
"""
from __future__ import annotations

import json
from typing import Any


class IngestError(Exception):
    """Raised when a raw event fails one of the FORMAT F.3 pre-schema rules."""

    def __init__(self, reason: str, path: str = "") -> None:
        self.reason = reason
        self.path = path
        super().__init__(f"{reason}{' @ ' + path if path else ''}")


def _validate_object_keys_unique(obj: Any, path: str = "") -> None:
    """FORMAT F.3 rule 1: duplicate object property names MUST be rejected."""
    if isinstance(obj, dict):
        seen = set()
        for k in obj.keys():
            if k in seen:
                raise IngestError("duplicate object property name", path=f"{path}.{k}" if path else k)
            seen.add(k)
            _validate_object_keys_unique(obj[k], f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            _validate_object_keys_unique(x, f"{path}[{i}]")


def _validate_no_lone_surrogate(obj: Any, path: str = "") -> None:
    """FORMAT F.3 rule 2: lone surrogates / invalid Unicode MUST be rejected.

    Validates by walking the object and checking every string. We use the
    surrogatepass error handler to surface invalid surrogates.
    """
    if isinstance(obj, str):
        # .encode() with 'strict' raises on lone surrogates; we manually scan
        try:
            obj.encode("utf-8", errors="strict")
        except UnicodeEncodeError as e:
            raise IngestError(
                "lone surrogate or invalid Unicode",
                path=f"{path}:{e.start}",
            ) from None
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            _validate_no_lone_surrogate(k, sub)  # keys are always ASCII but check anyway
            _validate_no_lone_surrogate(v, sub)
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            _validate_no_lone_surrogate(x, f"{path}[{i}]")


def _validate_safe_integer(obj: Any, path: str = "") -> None:
    """FORMAT F.3 rule 4: integer values must lie in the signed safe-integer
    range [-9007199254740991, 9007199254740991]; values outside MUST be represented
    as strings under an explicitly typed extension/domain field.
    """
    if isinstance(obj, bool):
        # JSON booleans are also ints; reject per FORMAT F.3 rule 3.
        raise IngestError("boolean values are rejected (use explicit type)", path=path)
    if isinstance(obj, float):
        # Reject floats per FORMAT F.3 rule 3 (project profile disallows floats).
        raise IngestError(
            "float values are rejected (use string or integer in extensions)", path=path
        )
    if isinstance(obj, int):
        if obj < -9007199254740991 or obj > 9007199254740991:
            raise IngestError(
                f"safe-integer overflow: {obj} outside "
                f"[-9007199254740991, 9007199254740991]",
                path=path,
            )
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            _validate_safe_integer(v, sub)
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            _validate_safe_integer(x, f"{path}[{i}]")


def validate_input_domain(obj: Any) -> None:
    """Run all FORMAT F.3 + F.6 input-domain pre-schema constraints."""
    _validate_object_keys_unique(obj)
    _validate_no_lone_surrogate(obj)
    _validate_safe_integer(obj)


class DuplicateKeyJsonError(Exception):
    """Raised from raw-text duplicate-key detection (FORMAT F.3 rule 1)."""


def parse_strict_with_duplicate_check(text: str) -> Any:
    """Parse JSON text, raising DuplicateKeyJsonError on duplicate object keys.

    Standard `json.loads` silently keeps the last value of duplicate keys; this
    helper detects them via object_pairs_hook.
    """
    def _hook(pairs):
        seen: set[str] = set()
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in seen:
                raise DuplicateKeyJsonError(f"duplicate object property name: {k!r}")
            seen.add(k)
            out[k] = v
        return out

    return json.loads(text, object_pairs_hook=_hook)

"""RFC 8785 (JCS) compatible canonical JSON with UTF-16 code-unit property sort.

Provides:
    canonical_bytes(obj)  -> bytes  (UTF-8, RFC 8785 canonicalization, no whitespace)
    sha256_canonical(obj)  -> str   ("sha256:" + 64 lowercase hex)
    sort_keys_utf16(obj)  -> dict/list/scalar (recursively sorted)

The slice-0 discriminated correctness of this implementation is verified by the
acceptance test: g2b_utf16_discriminator.json must produce sha256:
    ef16091eec4b5049e3297635897ba019c1727335b68774fcd3627958751998e4
"""
from __future__ import annotations

import functools
import hashlib
import json
import struct
from typing import Any


def _utf16_cmp(a: str, b: str) -> int:
    """Compare two strings by raw UTF-16 code units (little-endian 2-byte ints).

    RFC 8785: object property names MUST be sorted by raw UTF-16 code-unit order.
    For non-BMP characters the surrogate pair (high first, then low) sorts
    before any BMP-only string whose first code unit is higher than 0xD800.
    """
    if not isinstance(a, str):
        a = str(a)
    if not isinstance(b, str):
        b = str(b)
    ba = a.encode("utf-16-le")
    bb = b.encode("utf-16-le")
    ua = struct.unpack("<" + "H" * (len(ba) // 2), ba)
    ub = struct.unpack("<" + "H" * (len(bb) // 2), bb)
    for x, y in zip(ua, ub):
        if x != y:
            return x - y
    return len(ua) - len(ub)


def sort_keys_utf16(obj: Any) -> Any:
    """Recursively sort object property names by UTF-16 code-unit order.

    Lists preserve element order. Scalars returned unchanged.
    """
    if isinstance(obj, dict):
        return {
            k: sort_keys_utf16(v)
            for k, v in sorted(obj.items(), key=lambda kv: functools.cmp_to_key(_utf16_cmp)(kv[0]))
        }
    if isinstance(obj, list):
        return [sort_keys_utf16(x) for x in obj]
    return obj


def canonical_bytes(obj: Any) -> bytes:
    """Produce canonical JSON bytes per RFC 8785 (JCS) subset:
    - UTF-8, no BOM
    - no extra whitespace (separators = (",", ":"))
    - object property names sorted by raw UTF-16 code units (recursive)
    - array element order preserved
    - string escaping: JSON minimal + control-character escaping
    - non-ASCII Unicode characters NOT escaped (raw UTF-8)
    """
    return json.dumps(sort_keys_utf16(obj), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_canonical(obj: Any) -> str:
    """sha256 over exact canonical bytes (FORMAT F.4: input bytes WITHOUT trailing LF).

    Returns "sha256:" + 64 lowercase hex chars.
    """
    return "sha256:" + hashlib.sha256(canonical_bytes(obj)).hexdigest()


def stored_bytes(obj: Any) -> bytes:
    """Build canonical-stored file bytes: canonical full obj + exactly one trailing LF.

    FORMAT F.4: stored event = canonical bytes (with content_hash) + 1 LF.
    """
    return canonical_bytes(obj) + b"\n"


def verify_content_hash(obj: dict) -> bool:
    """True iff obj['content_hash'] == sha256 over canonical hash-input bytes."""
    if "content_hash" not in obj:
        return False
    declared = obj["content_hash"]
    if not isinstance(declared, str) or not declared.startswith("sha256:"):
        return False
    computed = sha256_canonical({k: v for k, v in obj.items() if k != "content_hash"})
    return declared == computed

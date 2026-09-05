"""Atomic canonical event write path (FORMAT F.6, S1-6).

Required behavior:
  1. accept raw event JSON string OR already-parsed dict
  2. validate input domain (FORMAT F.3)
  3. validate event shape + event_id + content_hash format
  4. validate taxonomy compatibility (UNCLASSIFIED vs invalid)
  5. compute canonical bytes + verify content_hash
  6. atomic write to .blackbox/events/<event_id>.json
  7. immediately run synchronous post-write validation (T3)
  8. return non-green if any step fails; never silently claim formal effect

FAILURE SEMANTICS: any rejected step raises WriteError; the write does NOT
happen (or is rolled back). UNCLASSIFIED events are retained for evidence
but cannot participate in effect eligibility / fold / derived COMPLETE.

The store does NOT enforce any global lock; for Slice 1 single-writer Case S
this is acceptable. A future slice may add file-locking or a process pid-file
advisory lock.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes, sha256_canonical, verify_content_hash
from .ingest import IngestError, validate_input_domain
from .subtypes import SubtypeRegistry, RECEIPT_CLASSES
from .validity import (
    event_id_format_ref_ok,
    is_event_shape_ok,
    lexical_content_hash_ok,
    lexical_event_id_ok,
)


class WriteError(Exception):
    """Raised when a canonical event write fails any required step."""


class NullSubtypeRegistry:
    """Trivial default registry used when subtypes.json is unavailable.

    Loads only from the default path; if that fails, the registry is empty
    and any registered check returns False.
    """

    def __init__(self) -> None:
        self._reg: SubtypeRegistry | None = None
        try:
            from .subtypes import DEFAULT_REGISTRY_PATH
            self._reg = SubtypeRegistry.load(DEFAULT_REGISTRY_PATH)
        except Exception:
            self._reg = None

    def lookup(self, subtype: str):
        return self._reg.lookup(subtype) if self._reg else None

    def is_registered(self, subtype: str) -> bool:
        return self._reg.is_registered(subtype) if self._reg else False

    def is_valid_combination(self, type_, subtype, receipt_class) -> bool:
        return self._reg.is_valid_combination(type_, subtype, receipt_class) if self._reg else False

    def classify_event(self, obj):
        return self._reg.classify_event(obj) if self._reg else ("UNCLASSIFIED", "no registry available")


# What constitutes the "events dir" for Slice 1 tests (use the repo slice1 dir).
DEFAULT_EVENTS_DIR = (
    Path(__file__).parent.parent.parent / ".blackbox" / "events"
).resolve()


def write_event(
    raw: str | bytes | dict,
    event_id: str | None = None,
    registry: SubtypeRegistry | None = None,
    events_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write a canonical event with synchronous post-write validation.

    Args:
      raw: raw JSON bytes/string OR already-parsed dict.
      event_id: optional explicit event_id (must match raw's event_id if both
        present); used as the file name.
      registry: optional SubtypeRegistry; loads default if absent.
      events_dir: optional override; defaults to DEFAULT_EVENTS_DIR.

    Returns the canonical event dict (with computed content_hash).

    Raises WriteError on any validation failure. Does NOT silently claim
    formal effect on UNCLASSIFIED events (which are still written for
    evidence but the caller must not use them in fold / derived COMPLETE).
    """
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode("utf-8", errors="strict")
    elif isinstance(raw, str):
        text = raw
    elif isinstance(raw, dict):
        # already parsed; convert back to text via canonical path later
        text = None
    else:
        raise WriteError(f"unsupported raw type: {type(raw).__name__}")

    if registry is None:
        registry = SubtypeRegistry.load()

    # 1. parse JSON — use object_pairs_hook to detect duplicate keys before
    #    Python's silent last-wins dedup. The hook raises WriteError (not
    #    IngestError) so the store's "any reject is a WriteError" contract holds.
    def _dup_hook(pairs):
        seen: set[str] = set()
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in seen:
                raise WriteError(f"duplicate object property name @ {k}")
            seen.add(k)
            out[k] = v
        return out

    try:
        if text is None:
            obj = _dup_hook(list(raw.items()))
        else:
            obj = json.loads(text, object_pairs_hook=_dup_hook)
    except IngestError:
        raise
    except json.JSONDecodeError as e:
        raise WriteError(f"JSON parse error: {e.msg} at col {e.colno}") from e

    # 2. input-domain pre-schema validation (FORMAT F.3)
    try:
        validate_input_domain(obj)
    except IngestError as e:
        raise WriteError(f"input-domain reject: {e.reason} ({e.path})") from e

    # 3. event shape + lexical checks
    if not is_event_shape_ok(obj):
        raise WriteError("event shape invalid (missing required fields or prior_refs shape)")
    if not lexical_event_id_ok(obj.get("event_id")):
        raise WriteError(f"event_id lexical invalid: {obj.get('event_id')!r}")
    if not lexical_content_hash_ok(obj.get("content_hash")):
        raise WriteError(f"content_hash lexical invalid: {obj.get('content_hash')!r}")
    if event_id is not None and obj.get("event_id") != event_id:
        raise WriteError(
            f"explicit event_id {event_id!r} != payload event_id {obj.get('event_id')!r}"
        )

    # 4. taxonomy compatibility check
    classification = registry.classify_event(obj)
    if classification is not None:
        label, reason = classification
        if label == "INVALID":
            # invalid registered combo -> reject outright
            raise WriteError(f"invalid type/subtype/receipt_class combination: {reason}")
        # UNCLASSIFIED: allow writing for evidence, but mark the caller-side
        # restriction by raising WriteError with specific reason so callers
        # know this event is no-effect. We still write the file so the audit
        # trail exists; downstream fold MUST skip it.
        classified = ("UNCLASSIFIED", reason)
    else:
        classified = None  # recognized, valid combination

    # 5. compute canonical hash and verify content_hash field
    if not verify_content_hash(obj):
        # Try to repair (recompute) the content_hash from canonical bytes
        # only if user explicitly asks? For Slice 1, we accept that
        # the caller supplied a wrong content_hash and reject.
        raise WriteError(
            "content_hash mismatch: stored content_hash != sha256(canonical(obj - content_hash))"
        )

    # 6. atomic write to events_dir / event_id.json (FORMAT D2: filename = event_id + ".json")
    target_dir = Path(events_dir) if events_dir is not None else DEFAULT_EVENTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (obj["event_id"] + ".json")
    bytes_to_write = canonical_bytes(obj) + b"\n"

    # S1-6 no-silent-overwrite: an existing target must be reconciled BEFORE
    # any write. Identical bytes => idempotent replay (no rewrite). Different
    # bytes/hash => EVENT_ID_COLLISION reject, original preserved. Corrupt
    # existing target => fail closed (never overwritten as "repair").
    if target.exists():
        try:
            with open(target, "rb") as f:
                existing = f.read()
        except Exception as e:
            raise WriteError(f"EVENT_ID_COLLISION: existing target unreadable: {e}") from e
        try:
            existing_obj = json.loads(existing.decode("utf-8"))
            existing_canonical = canonical_bytes(existing_obj) + b"\n"
        except Exception:
            existing_canonical = None  # corrupt / unparseable existing file
        if existing_canonical is None:
            raise WriteError(
                "EVENT_ID_COLLISION: existing target is corrupt (fails canonical "
                "re-parse); fail-closed, original preserved unmodified"
            )
        if existing == bytes_to_write and existing_canonical == bytes_to_write:
            # idempotent replay: same event_id, same canonical bytes
            if classified is not None:
                return {"_bbx_unclassified": classified[0],
                        "_bbx_unclassified_reason": classified[1], **obj}
            return obj
        raise WriteError(
            f"EVENT_ID_COLLISION: event_id {obj['event_id']} already exists with "
            "different canonical bytes/content_hash; original preserved unmodified"
        )

    # Write atomically: write to temp file in same dir, fsync, rename.
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(target_dir))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bytes_to_write)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # 7. synchronous post-write validation: re-read written file and verify
    # that it round-trips to the same canonical bytes (whole-file integrity)
    try:
        with open(target, "rb") as f:
            data = f.read()
    except Exception as e:
        raise WriteError(f"post-write re-read failed: {e}") from e

    if not data.endswith(b"\n"):
        # we just wrote it; this should not happen
        raise WriteError("post-write: written file missing trailing LF")
    if data != bytes_to_write:
        # on-disk content differs from computed canonical bytes -> write
        # didn't round-trip exactly. Remove the file and raise.
        try:
            os.unlink(target)
        except Exception:
            pass
        raise WriteError("post-write: on-disk content != computed canonical bytes (write failed)")

    # Final: return the canonical event dict (with content_hash).
    if classified is not None:
        # attach the classification metadata for downstream caller
        return {"_bbx_unclassified": classified[0], "_bbx_unclassified_reason": classified[1], **obj}
    return obj

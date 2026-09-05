"""Strict disk re-ingest (R6, S1-7/S1-10): read path uses write-path rules.

Every canonical event file on disk must re-validate through the same full
chain used at write time:
  raw bytes -> canonical storage form (compact JSON + exactly one trailing LF)
  -> strict duplicate-key parse -> FORMAT F.3 input domain -> shape/lexical
  -> content_hash -> canonical round-trip -> registry taxonomy.

No silent skip: every file yields an explicit verdict:
  VALID          — fully re-validated, registered combination; fold-eligible
  UNCLASSIFIED   — valid canonical bytes, unregistered subtype; retained as
                   evidence, no effect (derivable from canonical fields +
                   registry on every read; no _bbx_* metadata required)
  INVALID        — structurally parseable but fails any canonical rule
                   (hash mismatch, shape, non-canonical bytes, INVALID combo)
  UNKNOWN        — unreadable / corrupt (not even parseable)

Fail-closed: a corpus containing INVALID/UNKNOWN files can never yield a
green derived projection from the remaining subset.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes, verify_content_hash
from .ingest import (
    DuplicateKeyJsonError,
    IngestError,
    parse_strict_with_duplicate_check,
    validate_input_domain,
)
from .subtypes import SubtypeRegistry
from .validity import (
    is_event_shape_ok,
    lexical_content_hash_ok,
    lexical_event_id_ok,
)

V_VALID = "VALID"
V_UNCLASSIFIED = "UNCLASSIFIED"
V_INVALID = "INVALID"
V_UNKNOWN = "UNKNOWN"


@dataclass
class ReingestVerdict:
    path: str
    status: str  # V_VALID | V_UNCLASSIFIED | V_INVALID | V_UNKNOWN
    event: dict[str, Any] | None
    reason: str


@dataclass
class CorpusReingestResult:
    """Full corpus re-ingest outcome (S1-F4: layout + identity fail-closed)."""

    verdicts: list[ReingestVerdict]
    corpus_blocked: bool
    corpus_reason: str


def reingest_event_file(
    path: str | Path,
    registry: SubtypeRegistry,
) -> ReingestVerdict:
    """Re-validate one on-disk event file through the full write-path chain."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except Exception as e:
        return ReingestVerdict(str(p), V_UNKNOWN, None, f"unreadable: {e}")

    # 1. canonical storage form: compact canonical JSON + exactly one LF
    if not raw.endswith(b"\n"):
        return ReingestVerdict(
            str(p), V_INVALID, None,
            "missing trailing LF (non-canonical storage form)",
        )
    if raw.count(b"\n") != 1:
        return ReingestVerdict(
            str(p), V_INVALID, None,
            "embedded newline in event file (non-canonical storage form)",
        )

    # 2. strict parse (duplicate-key detection; no silent last-wins)
    try:
        obj = parse_strict_with_duplicate_check(raw[:-1].decode("utf-8", errors="strict"))
    except UnicodeDecodeError as e:
        return ReingestVerdict(str(p), V_INVALID, None, f"utf-8 decode error: {e}")
    except DuplicateKeyJsonError as e:
        return ReingestVerdict(str(p), V_INVALID, None, f"duplicate key: {e}")
    except Exception as e:
        return ReingestVerdict(str(p), V_UNKNOWN, None, f"unparseable JSON: {e}")
    if not isinstance(obj, dict):
        return ReingestVerdict(str(p), V_INVALID, None, "event file is not a JSON object")

    # 3. FORMAT F.3 input domain (lone surrogate, safe integers, no floats/bools)
    try:
        validate_input_domain(obj)
    except IngestError as e:
        return ReingestVerdict(str(p), V_INVALID, None, f"input-domain reject: {e}")

    # 4. shape + lexical checks
    if not is_event_shape_ok(obj):
        return ReingestVerdict(str(p), V_INVALID, None, "event shape invalid")
    if not lexical_event_id_ok(obj.get("event_id")):
        return ReingestVerdict(
            str(p), V_INVALID, None,
            f"event_id lexical invalid: {obj.get('event_id')!r}",
        )
    if not lexical_content_hash_ok(obj.get("content_hash")):
        return ReingestVerdict(
            str(p), V_INVALID, None,
            f"content_hash lexical invalid: {obj.get('content_hash')!r}",
        )

    # 5. content_hash integrity
    if not verify_content_hash(obj):
        return ReingestVerdict(
            str(p), V_INVALID, None,
            "content_hash mismatch (stored != sha256(canonical(obj - content_hash)))",
        )

    # 6. canonical round-trip: on-disk bytes must equal canonical bytes + LF
    if canonical_bytes(obj) + b"\n" != raw:
        return ReingestVerdict(
            str(p), V_INVALID, None,
            "on-disk bytes are not the canonical serialization of the parsed event",
        )

    # 6b. S1-F4: FORMAT D2 exact filename identity — a canonical event file's
    # name MUST be <event_id>.json. A file named evt-AAAA....json holding a
    # different canonical event_id is INVALID even though its bytes are valid.
    if p.name != obj["event_id"] + ".json":
        return ReingestVerdict(
            str(p), V_INVALID, None,
            f"filename identity violation: file {p.name!r} holds event_id "
            f"{obj['event_id']!r} (FORMAT D2 requires <event_id>.json)",
        )

    # 7. taxonomy (derivable from canonical fields + registry on every read)
    classification = registry.classify_event(obj)
    if classification is None:
        return ReingestVerdict(str(p), V_VALID, obj, "valid registered combination")
    label, reason = classification
    if label == "INVALID":
        return ReingestVerdict(str(p), V_INVALID, obj, f"invalid combination: {reason}")
    return ReingestVerdict(str(p), V_UNCLASSIFIED, obj, f"unclassified: {reason}")


def reingest_events_dir(
    events_dir: str | Path,
    registry: SubtypeRegistry,
) -> CorpusReingestResult:
    """Re-validate the events directory as a CONTROLLED corpus (S1-F4).

    Every regular directory entry is accounted for — no silent skip:
      - `<event_id>.json` canonical names are re-ingested through the full
        write-path chain;
      - any other regular file (wrong name, stale temp files) is INVALID
        (layout violation);
      - symlinks and subdirectories are UNKNOWN (unresolved corpus surface);
      - exact filename identity: a canonical file's name MUST equal
        `<parsed event_id>.json` (FORMAT D2) — mismatched name is INVALID;
      - corpus-wide event_id uniqueness: the same event_id under two files is
        a violation — identical canonical bytes = layout violation (INVALID),
        different bytes/hash = explicit collision (INVALID); both block the
        corpus.

    Missing directory is not an error (empty corpus).
    """
    d = Path(events_dir)
    if not d.exists():
        return CorpusReingestResult([], False, "")

    verdicts: list[ReingestVerdict] = []
    by_event_id: dict[str, list[ReingestVerdict]] = {}
    for p in sorted(d.iterdir()):
        if p.is_symlink():
            verdicts.append(ReingestVerdict(
                str(p), V_UNKNOWN, None,
                "symlink in controlled events corpus (unresolved surface)",
            ))
            continue
        if p.is_dir():
            verdicts.append(ReingestVerdict(
                str(p), V_UNKNOWN, None,
                "subdirectory in controlled events corpus (unresolved surface)",
            ))
            continue
        if not p.is_file():
            verdicts.append(ReingestVerdict(
                str(p), V_UNKNOWN, None, "non-regular corpus entry",
            ))
            continue
        v = reingest_event_file(p, registry)
        if p.name.startswith("evt-") and p.name.endswith(".json"):
            # FORMAT D2 filename identity is verified inside reingest_event_file
            # for parseable events; bookkeeping here for corpus uniqueness.
            if v.event is not None and v.status in (V_VALID, V_UNCLASSIFIED):
                by_event_id.setdefault(v.event["event_id"], []).append(v)
        else:
            # S1-F4 rule 1: every unexpected regular entry is a layout
            # violation — never a silent skip.
            verdicts.append(ReingestVerdict(
                str(p), V_INVALID, None,
                "unexpected filename in controlled events corpus "
                "(expected <event_id>.json); layout violation, not silently skipped",
            ))
            continue
        verdicts.append(v)

    # Corpus-wide event_id uniqueness (S1-F4 rule 3).
    corpus_blocked = False
    corpus_reasons: list[str] = []
    for eid, group in by_event_id.items():
        if len(group) < 2:
            continue
        corpus_blocked = True
        byte_sets = {v.path: None for v in group}
        raws = {}
        for v in group:
            try:
                raws[v.path] = Path(v.path).read_bytes()
            except Exception:
                raws[v.path] = None
        raw_values = list(raws.values())
        if any(r is None for r in raw_values) or len(set(raw_values)) > 1:
            reason = (
                f"EVENT_ID_COLLISION in corpus: event_id {eid} exists in "
                f"{len(group)} files with different bytes/hash: "
                f"{sorted(raws)}"
            )
        else:
            reason = (
                f"corpus layout violation: event_id {eid} duplicated in "
                f"{len(group)} files with identical canonical bytes "
                f"(no duplicate physical canonical entries): {sorted(raws)}"
            )
        corpus_reasons.append(reason)
        # every file involved is corpus-blocked evidence
        for v in group:
            v.status = V_INVALID
            v.reason = reason

    return CorpusReingestResult(verdicts, corpus_blocked, "; ".join(corpus_reasons))

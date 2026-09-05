"""S2-5 checkpoint validation.

Independent validator that takes an opaque checkpoint dict and verifies
the full frozen Slice 0 checkpoint schema (TASK S2-R4):

  Schema-conformance (frozen Slice 0, additionalProperties:false top-level)
      * top-level keys exactly: schema_version, header, input_event_manifest,
        frontier, source_event_refs [, manifest_integrity_sha256]
      * schema_version = "0.3.2"
      * header.kind = "DERIVED/NON-AUTHORITY_RECOVERY_INDEX"
      * header.as_of_event_count == len(input_event_manifest) (TASK S2-R4)
      * event_id patterns: ^evt-[0-9a-f]{32}$
      * content_hash patterns: ^sha256:[0-9a-f]{64}$
      * checkpoint_id pattern: ^cp-[0-9a-f]{64}$
      * manifest_integrity_sha256 pattern: ^[0-9a-f]{64}$
  No duplicates:
      * manifest event_ids unique (TASK S2-R4)
      * source_event_refs unique and exactly equals manifest event_id set
        (TASK S2-R4)
  Containment:
      * every frontier head event_id appears in the manifest (TASK S2-R4)
  Integrity:
      * manifest integrity: sha256 over JCS-canonical manifest array equals
        header.manifest_integrity_sha256
  Self-reference:
      * header.checkpoint_id == cp- + sha256(candidate_without_checkpoint_id)

Fail-closed: any check that cannot be performed or fails raises
CheckpointInvalid rather than silently accepting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()

from blackbox_vnext.slice1 import canonical as r1_canonical  # noqa: E402

from . import manifest as bbx2_manifest
from . import checkpoint as bbx2_checkpoint
from . import dependency_pin as bbx2_dep


# --- pinned patterns (frozen Slice 0) ---

_EVENT_ID_RE = re.compile(r"^evt-[0-9a-f]{32}$")
_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CHECKPOINT_ID_RE = re.compile(r"^cp-[0-9a-f]{64}$")
_MANIFEST_INTEGRITY_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_VERSION = "0.3.2"
_HEADER_KIND = "DERIVED/NON-AUTHORITY_RECOVERY_INDEX"

# Top-level frozen keys (additionalProperties:false on the schema).
_ALLOWED_TOP_KEYS = frozenset({
    "schema_version", "header", "input_event_manifest", "frontier",
    "source_event_refs", "manifest_integrity_sha256",
})


class CheckpointInvalid(Exception):
    def __init__(self, reason: str, where: str | None = None):
        self.reason = reason
        self.where = where
        prefix = f"{where}: " if where else ""
        super().__init__(prefix + reason)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    where: str = ""
    schema_pins_ok: bool = False
    as_of_count_ok: bool = False
    unique_manifest_ids_ok: bool = False
    frontier_in_manifest_ok: bool = False
    source_refs_ok: bool = False
    manifest_integrity_ok: bool = False
    self_reference_ok: bool = False


def _check_schema_pins(cp: dict[str, Any]) -> None:
    if not isinstance(cp, dict):
        raise CheckpointInvalid("checkpoint must be object", where="root")

    # TASK S2-R4: additionalProperties:false at top-level.
    extra_top = set(cp.keys()) - _ALLOWED_TOP_KEYS
    if extra_top:
        raise CheckpointInvalid(
            f"unexpected top-level keys (additionalProperties:false): "
            f"{sorted(extra_top)}",
            where="root",
        )

    required = ("schema_version", "header", "input_event_manifest",
                "frontier", "source_event_refs")
    missing = [k for k in required if k not in cp]
    if missing:
        raise CheckpointInvalid(
            f"missing required top-level keys: {missing}", where="root"
        )
    if cp["schema_version"] != _SCHEMA_VERSION:
        raise CheckpointInvalid(
            f"schema_version={cp['schema_version']!r} != {_SCHEMA_VERSION!r}",
            where="schema_version",
        )
    hdr = cp.get("header")
    if not isinstance(hdr, dict):
        raise CheckpointInvalid("header must be object", where="header")
    if hdr.get("kind") != _HEADER_KIND:
        raise CheckpointInvalid(
            f"header.kind={hdr.get('kind')!r} != {_HEADER_KIND!r}",
            where="header.kind",
        )
    cp_id = hdr.get("checkpoint_id")
    if not (isinstance(cp_id, str) and _CHECKPOINT_ID_RE.match(cp_id)):
        raise CheckpointInvalid(
            f"header.checkpoint_id={cp_id!r} does not match "
            f"^cp-[0-9a-f]{{64}}$",
            where="header.checkpoint_id",
        )
    manifest = cp.get("input_event_manifest")
    if not isinstance(manifest, list):
        raise CheckpointInvalid(
            "input_event_manifest must be array", where="input_event_manifest"
        )
    for i, entry in enumerate(manifest):
        if not isinstance(entry, dict):
            raise CheckpointInvalid(
                f"manifest[{i}] not object", where=f"input_event_manifest[{i}]"
            )
        eid = entry.get("event_id")
        chash = entry.get("content_hash")
        if not (isinstance(eid, str) and _EVENT_ID_RE.match(eid)):
            raise CheckpointInvalid(
                f"manifest[{i}].event_id={eid!r} does not match pattern",
                where=f"input_event_manifest[{i}].event_id",
            )
        if not (isinstance(chash, str) and _CONTENT_HASH_RE.match(chash)):
            raise CheckpointInvalid(
                f"manifest[{i}].content_hash={chash!r} does not match pattern",
                where=f"input_event_manifest[{i}].content_hash",
            )
    frontier = cp.get("frontier")
    if not isinstance(frontier, dict):
        raise CheckpointInvalid("frontier must be object", where="frontier")
    for subj, head in frontier.items():
        if not (isinstance(head, str) and _EVENT_ID_RE.match(head)):
            raise CheckpointInvalid(
                f"frontier[{subj!r}]={head!r} does not match pattern",
                where=f"frontier.{subj}",
            )
    src = cp.get("source_event_refs")
    if not isinstance(src, list):
        raise CheckpointInvalid(
            "source_event_refs must be array", where="source_event_refs"
        )
    for i, ref in enumerate(src):
        if not (isinstance(ref, str) and _EVENT_ID_RE.match(ref)):
            raise CheckpointInvalid(
                f"source_event_refs[{i}]={ref!r} does not match pattern",
                where=f"source_event_refs[{i}]",
            )
    integrity = cp.get("manifest_integrity_sha256")
    if integrity is not None and not (
        isinstance(integrity, str) and _MANIFEST_INTEGRITY_RE.match(integrity)
    ):
        raise CheckpointInvalid(
            f"manifest_integrity_sha256={integrity!r} does not match pattern",
            where="manifest_integrity_sha256",
        )


def _check_as_of_count(cp: dict[str, Any]) -> None:
    hdr = cp["header"]
    declared = hdr.get("as_of_event_count")
    actual = len(cp["input_event_manifest"])
    if declared != actual:
        raise CheckpointInvalid(
            f"as_of_event_count={declared} != len(input_event_manifest)={actual}",
            where="header.as_of_event_count",
        )


def _check_unique_manifest_ids(cp: dict[str, Any]) -> None:
    manifest = cp["input_event_manifest"]
    seen: set[str] = set()
    for i, entry in enumerate(manifest):
        eid = entry["event_id"]
        if eid in seen:
            raise CheckpointInvalid(
                f"duplicate manifest event_id {eid!r} at index {i}",
                where=f"input_event_manifest[{i}].event_id",
            )
        seen.add(eid)


def _check_frontier_in_manifest(cp: dict[str, Any]) -> None:
    manifest_ids = {e["event_id"] for e in cp["input_event_manifest"]}
    for subj, head in cp["frontier"].items():
        if head not in manifest_ids:
            raise CheckpointInvalid(
                f"frontier[{subj!r}]={head!r} not in input_event_manifest",
                where=f"frontier.{subj}",
            )


def _check_source_refs(cp: dict[str, Any]) -> None:
    manifest_eids = [e["event_id"] for e in cp["input_event_manifest"]]
    src = cp["source_event_refs"]
    # Duplicate-free set equality
    if len(src) != len(set(src)):
        dups = sorted({e for e in src if src.count(e) > 1})
        raise CheckpointInvalid(
            f"source_event_refs contains duplicates: {dups}",
            where="source_event_refs",
        )
    if sorted(src) != sorted(manifest_eids):
        raise CheckpointInvalid(
            f"source_event_refs != sorted(manifest.event_id): "
            f"src={sorted(src)} manifest={sorted(manifest_eids)}",
            where="source_event_refs",
        )


def _check_manifest_integrity(cp: dict[str, Any]) -> None:
    manifest = cp["input_event_manifest"]
    expected = cp.get("manifest_integrity_sha256")
    actual = bbx2_manifest.recompute_integrity(manifest)
    if expected is None:
        raise CheckpointInvalid(
            "manifest_integrity_sha256 missing", where="manifest_integrity_sha256"
        )
    if actual != expected:
        raise CheckpointInvalid(
            f"manifest_integrity_sha256 mismatch: actual={actual} expected={expected}",
            where="manifest_integrity_sha256",
        )


def _check_self_reference(cp: dict[str, Any]) -> None:
    if not bbx2_checkpoint.verify_self_reference(cp):
        raise CheckpointInvalid(
            "self-reference boundary failed: "
            "checkpoint_id != cp- + sha256(canonical_without_checkpoint_id)",
            where="header.checkpoint_id",
        )


def validate(checkpoint: dict[str, Any]) -> ValidationResult:
    """Run all validations. On any failure raises CheckpointInvalid; on full
    success returns ValidationResult(ok=True, ...)."""
    # S2-H2: fail-closed Slice 1 dependency gate. Validation uses Slice 1
    # canonical_bytes + sha256 + checkpoint identity; drift -> DependencyDrift.
    bbx2_dep.gate_semantic_entrypoint(strict=True)
    schema_pins_ok = False
    as_of_count_ok = False
    unique_manifest_ids_ok = False
    frontier_in_manifest_ok = False
    source_refs_ok = False
    manifest_integrity_ok = False
    self_reference_ok = False
    try:
        _check_schema_pins(checkpoint)
        schema_pins_ok = True
        _check_as_of_count(checkpoint)
        as_of_count_ok = True
        _check_unique_manifest_ids(checkpoint)
        unique_manifest_ids_ok = True
        _check_frontier_in_manifest(checkpoint)
        frontier_in_manifest_ok = True
        _check_source_refs(checkpoint)
        source_refs_ok = True
        _check_manifest_integrity(checkpoint)
        manifest_integrity_ok = True
        _check_self_reference(checkpoint)
        self_reference_ok = True
    except CheckpointInvalid as e:
        return ValidationResult(
            ok=False, reason=e.reason, where=e.where or "",
            schema_pins_ok=schema_pins_ok,
            as_of_count_ok=as_of_count_ok,
            unique_manifest_ids_ok=unique_manifest_ids_ok,
            frontier_in_manifest_ok=frontier_in_manifest_ok,
            source_refs_ok=source_refs_ok,
            manifest_integrity_ok=manifest_integrity_ok,
            self_reference_ok=self_reference_ok,
        )
    return ValidationResult(
        ok=True,
        schema_pins_ok=schema_pins_ok,
        as_of_count_ok=as_of_count_ok,
        unique_manifest_ids_ok=unique_manifest_ids_ok,
        frontier_in_manifest_ok=frontier_in_manifest_ok,
        source_refs_ok=source_refs_ok,
        manifest_integrity_ok=manifest_integrity_ok,
        self_reference_ok=self_reference_ok,
    )

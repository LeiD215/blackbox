"""Observation profile + workspace observation baseline + 4-way ORPHAN classifier.

Slice 1 minimum observability (Case S, no Git/CI/daemon):
  - load/validate observation profile (FORMAT F.10 / observation-profile.schema.json)
  - compute workspace baseline = governed path identities/hashes
  - 4-way classification of observed changes vs receipt effect binding

Pure functions + small I/O for reading profiles.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .canonical import canonical_bytes


# Classification categories for observed governed-path changes.
COVERED = "COVERED"                # matching valid authorized eligible receipt set
ASSURANCE_GAP = "ASSURANCE-GAP"      # matching valid authorized receipt but assurance incomplete
UNAUTHORIZED = "UNAUTHORIZED"      # matching binding exists but receipt invalid/unauthorized
ORPHAN = "ORPHAN"                  # no matching ingested machine-readable effect binding


@dataclass
class ObservedChange:
    """A change in a governed path observed in the workspace."""

    path: str
    observed_post_identity: str  # sha256 of post-state content, or "absent" for deletion


@dataclass
class Baseline:
    """Persisted sensor state. NOT canonical governance authority."""

    profile_identity_sha256: str  # pins the profile observed against
    as_of_event_id: str
    governed_paths: list[str]
    path_to_post_identity: dict[str, str] = field(default_factory=dict)


def load_observation_profile(path: str | Path) -> dict[str, Any]:
    """Load + lightly validate observation profile from disk."""
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError("observation profile must be a JSON object")
    return obj


def compute_profile_identity(profile: dict[str, Any]) -> str:
    """sha256 over RFC 8785 canonical bytes of complete profile object with
    profile_identity_sha256 omitted (incl. schema_version), no trailing LF.

    Per B2 contract (FORMAT observation profile identity): lexical form
    'sha256:' + 64 lowercase hex chars.
    """
    canonical = canonical_bytes({k: v for k, v in profile.items() if k != "profile_identity_sha256"})
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def compute_workspace_baseline(
    project_root: str | Path,
    profile: dict[str, Any],
    extra_exclusions: Iterable[str] = (),
) -> dict[str, str]:
    """Walk governed paths (excluding .blackbox/**), compute current identity of each.

    extra_exclusions: code-enforced exclusions applied IN ADDITION to the
        profile's own fixed_exclusions/ignored_paths (R5: .blackbox/** must be
        enforced by code, not trusted solely from mutable profile content).

    Returns dict path -> sha256:<64hex> | "absent" for missing files.

    Implementation uses simple glob matching against governed paths (very
    small reference profile; not a full glob implementation).
    """
    root = Path(project_root)
    fixed_exclusions = list(profile.get("fixed_exclusions", [])) + list(extra_exclusions)
    governed = profile.get("governed_paths", [])
    ignored = list(profile.get("ignored_paths", [])) + list(extra_exclusions)
    out: dict[str, str] = {}

    def is_excluded(rel: str) -> bool:
        if any(_glob_match(rel, pat) for pat in fixed_exclusions):
            return True
        if any(_glob_match(rel, pat) for pat in ignored):
            return True
        return False

    for pat in governed:
        for p in _glob_paths(root, pat):
            rel = p.relative_to(root).as_posix()
            if is_excluded(rel):
                continue
            if p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                out[rel] = "sha256:" + h
            elif p.exists():
                out[rel] = "absent"  # directories not hashed in this minimal impl
            else:
                out[rel] = "absent"
    return out


def _glob_to_regex(pattern: str) -> str:
    """Convert the small reference glob subset to an anchored regex.

    Supports:
      **/  -> zero-or-more directories (matches top-level files too)
      **   -> any depth (across segments, includes '/')
      *    -> within a single segment
      ?    -> any single char (non-'/')
    Other characters are escaped literally.
    """
    import re

    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    # '**/' spans zero or more path segments
                    out.append("(?:[^/]+/)*")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    return "".join(out)


def _glob_match(path: str, pattern: str) -> bool:
    """Tiny recursive-glob matcher for * and **. Sufficient for reference profile."""
    import re

    p = _glob_to_regex(pattern)
    # anchor at start; allow exact match or full path match
    return bool(re.fullmatch(p, path)) or bool(re.fullmatch(p + "/.*", path))


def _glob_paths(root: Path, pattern: str) -> list[Path]:
    """Walk root, return paths matching pattern. Minimal impl."""
    import re

    out: list[Path] = []
    rx = re.compile(_glob_to_regex(pattern))
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if rx.fullmatch(rel):
            out.append(p)
    return out


@dataclass
class Classification:
    """4-way classification of an observed governed-path change."""

    path: str
    observed_post_identity: str
    category: str  # one of COVERED, ASSURANCE-GAP, UNAUTHORIZED, ORPHAN
    matching_receipt_event_id: str | None = None
    detail: str = ""


def classify_change(
    change: ObservedChange,
    baseline: dict[str, str],
    receipt_effect_bindings: dict[str, list[dict[str, Any]]],
    receipt_validity: dict[str, bool],
    receipt_assurance: dict[str, str],
) -> Classification:
    """Classify one observed change per Design v0.1.4 F3 four-way rule.

    receipt_effect_bindings: path -> list of receipt event dicts that bind
        that path's effect (each receipt has extensions.effect path/post_identity).
    receipt_validity: event_id -> bool (valid authorized per Layer 1)
    receipt_assurance: event_id -> "verified-self"|"verified-independent"|...
    """
    path = change.path
    bindings = receipt_effect_bindings.get(path, [])
    matching = [b for b in bindings if _matches_post_identity(b, change.observed_post_identity)]
    if not matching:
        return Classification(
            path=path,
            observed_post_identity=change.observed_post_identity,
            category=ORPHAN,
            detail="no matching ingested machine-readable effect binding",
        )
    # has matching binding(s): classify by validity × assurance
    valid_auth = [b for b in matching if receipt_validity.get(b.get("event_id"), False)]
    if not valid_auth:
        return Classification(
            path=path,
            observed_post_identity=change.observed_post_identity,
            category=UNAUTHORIZED,
            matching_receipt_event_id=matching[0].get("event_id"),
            detail="matching binding exists but receipt invalid/unauthorized",
        )
    # at least one valid authorized receipt
    has_eligible = any(
        receipt_assurance.get(b.get("event_id"), "") == "verified-self"
        or receipt_assurance.get(b.get("event_id"), "") == "verified-independent"
        for b in valid_auth
    )
    if not has_eligible:
        return Classification(
            path=path,
            observed_post_identity=change.observed_post_identity,
            category=ASSURANCE_GAP,
            matching_receipt_event_id=valid_auth[0].get("event_id"),
            detail="valid authorized receipt but assurance incomplete",
        )
    return Classification(
        path=path,
        observed_post_identity=change.observed_post_identity,
        category=COVERED,
        matching_receipt_event_id=valid_auth[0].get("event_id"),
        detail="valid authorized eligible receipt set",
    )


def _matches_post_identity(receipt: dict[str, Any], observed_post_identity: str) -> bool:
    """Match receipt's extensions.effect entries against observed post-state."""
    ext = receipt.get("extensions", {})
    eff = ext.get("effect", []) if isinstance(ext, dict) else []
    if not isinstance(eff, list):
        return False
    for entry in eff:
        if not isinstance(entry, dict):
            continue
        if entry.get("post_identity") == observed_post_identity:
            return True
    return False


def index_receipt_effect_bindings(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build path -> [receipt_events_with_binding_for_path]."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        ext = ev.get("extensions", {})
        if not isinstance(ext, dict):
            continue
        eff = ext.get("effect", [])
        if not isinstance(eff, list):
            continue
        for entry in eff:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if path:
                out.setdefault(path, []).append(ev)
    return out

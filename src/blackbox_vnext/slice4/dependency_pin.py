"""bbx4.dependency_pin — Slice 0/1 byte-pin guard.

Per TASK `5cf6cfc763ec` §S4-Q7: "pin every actually executed transitive
semantic dependency needed by the trusted corpus path, at minimum
bbx/ingest.py, bbx/validity.py, and Slice 0 schema/subtypes.json".

Slice 4 actually consumes Slice 1 APIs for:
- canonical SHA-256 (`bbx.canonical.sha256_canonical`, `canonical_bytes`,
  `stored_bytes`, `verify_content_hash`)
- controlled-corpus re-ingest (`bbx.reingest.reingest_events_dir`)
- subtype registry classification (`bbx.subtypes.SubtypeRegistry`)
- transitively consumed by `bbx.reingest`:
  - `bbx.ingest.parse_strict_with_duplicate_check`, `validate_input_domain`
  - `bbx.validity.is_event_shape_ok`, `lexical_event_id_ok`,
    `lexical_content_hash_ok`
- transitively consumed by `bbx.subtypes.SubtypeRegistry.load()`:
  - Slice 0 `schema/subtypes.json`

Slice 3 was originally pinned but Slice 4 does NOT import any
`bbx3.*` API at runtime; the previous pin was an artificial
dependency. Slice 3 PASS commit/tree remains as provenance-only
documentation (see README.md) but is not runtime-pinned here.

The pin gate is the single automatic fail-closed choke point
invoked by every public semantic entrypoint in `bbx4.dispatch`
and `bbx4.state`. Drift fails closed (raises DependencyDrift).
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blackbox_vnext.slice4 import _slice1_path


# --- Frozen upstream provenance (Slice 4 TASK) ---------------------------

SLICE1_PINNED_COMMIT = "87482a53b74f28b499bdba5d9a22719302a69d7d"
SLICE1_TREE_SHA = "f4ee87676e309418211adffd8110dcdc32b72ede"


# Slice 3 PASS commit/tree retained as provenance only — see README.md.
# It is NOT runtime-pinned because Slice 4 does not import bbx3.* APIs.
SLICE3_PINNED_COMMIT = "20b24e9dcc6603c8cbd826ccba1fb95b476e8216"
SLICE3_TREE_SHA = "694320566912cb3672aa5728ddc40c34caec0634"


# --- File SHA-256 (Slice 0/1 files Slice 4 actually consumes) ------------

# Per Q7: every transitively consumed Slice 0/1 file/data must be
# runtime byte-pinned. These are the Slice 0/1 files that Slice 4
# imports or transitively semantically consumes.
SLICE0_FILE_SHA256: dict[str, str] = {
    # Slice 0 schema (transitively consumed via bbx.subtypes.SubtypeRegistry.load()).
    # Path is computed relative to the Slice 1 root because that's
    # what SubtypeRegistry.DEFAULT_REGISTRY_PATH resolves to.
    # The pinned byte domain is the committed/release LF image.
    "subtypes.json": (
        "c191fd69cebab1d35c2bdc84764640fd85eefc0005d30b1454d3d1ac0c89ba09"
    ),
}

SLICE1_FILE_SHA256: dict[str, str] = {
    "slice1/__init__.py":     "4d5208649d1ef52e42c8dd323189bbfaa76163b8730e227dafe7d73a1575ddc7",
    "slice1/canonical.py":    "4c75f9978bac607fe10816f04e504e2d11a997d1834ac027fa1b17e44d3827ef",
    "slice1/reingest.py":     "106b827ab9d1e243b0bdab8d020c837e7f8e3d0ea500dff2a082ee54d568994e",
    "slice1/subtypes.py":     "bd18904af451f8b27369509227b7461ea6803612f7762b61ce56318340529fa5",
    # Transitively consumed by bbx.reingest (Q7 closure).
    "slice1/ingest.py":       "5c0a832e1a4739464b21c566452c4928fe7222fdf6db4bfe962be2870457b928",
    "slice1/validity.py":     "7a1859d9eabdb5d1c4b73fb0049c6abea2af87e807e7a3dc58848407f6a7f04a",
}


# --- API profile (presence check) -----------------------------------------

# Only APIs Slice 4 actually imports / calls (directly or transitively).
SLICE1_API_PROFILE: dict[str, list[str]] = {
    "slice1/__init__.py": ["__version__"],
    "slice1/canonical.py": ["canonical_bytes", "sha256_canonical",
                          "stored_bytes", "verify_content_hash"],
    "slice1/reingest.py": ["CorpusReingestResult", "ReingestVerdict",
                         "V_INVALID", "V_UNCLASSIFIED", "V_UNKNOWN",
                         "V_VALID", "reingest_event_file",
                         "reingest_events_dir"],
    "slice1/subtypes.py": ["DEFAULT_REGISTRY_PATH", "SubtypeRegistry",
                         "CORE_TYPES", "RECEIPT_CLASSES"],
    # Transitively consumed by bbx.reingest.
    "slice1/ingest.py": ["DuplicateKeyJsonError", "IngestError",
                       "parse_strict_with_duplicate_check",
                       "validate_input_domain"],
    "slice1/validity.py": ["is_event_shape_ok", "lexical_event_id_ok",
                         "lexical_content_hash_ok"],
}


# --- Result types -------------------------------------------------------


@dataclass
class DependencyCheckResult:
    ok: bool
    mismatches: list[dict[str, str]] = field(default_factory=list)
    api_mismatches: dict[str, list[str]] = field(default_factory=dict)
    slice1_root: str = ""
    slice0_root: str = ""
    pinned_slice1_commit: str = SLICE1_PINNED_COMMIT
    pinned_slice1_tree_sha: str = SLICE1_TREE_SHA
    slice_files_checked: int = 0
    api_symbols_checked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mismatches": self.mismatches,
            "api_mismatches": self.api_mismatches,
            "slice1_root": self.slice1_root,
            "slice0_root": self.slice0_root,
            "pinned_slice1_commit": self.pinned_slice1_commit,
            "pinned_slice1_tree_sha": self.pinned_slice1_tree_sha,
            "slice_files_checked": self.slice_files_checked,
            "api_symbols_checked": self.api_symbols_checked,
        }


class DependencyDrift(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(
            f"Slice 4 dependency drift: "
            f"{detail.get('mismatches', []) or detail.get('api_mismatches', {})}"
        )


# --- Hashing helpers ----------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_one_slice(
    pinned_files: dict[str, str],
    api_profile: dict[str, list[str]],
    root: Path,
    label: str,
) -> tuple[list[dict[str, str]], dict[str, list[str]], int, int]:
    mismatches: list[dict[str, str]] = []
    api_mismatches: dict[str, list[str]] = {}
    n_files = 0
    n_symbols = 0

    # File SHA-256
    for rel, expected in pinned_files.items():
        n_files += 1
        path = root / rel
        if not path.exists():
            mismatches.append({"label": label, "rel": rel,
                                "kind": "MISSING", "expected": expected,
                                "actual": ""})
            continue
        actual = _sha256_file(path)
        if actual != expected:
            mismatches.append({"label": label, "rel": rel,
                                "kind": "HASH", "expected": expected,
                                "actual": actual})

    # API presence — only for Slice 1 (it has importable modules).
    if label == "product-slice1":
        sys.path.insert(0, str(root))
        try:
            for rel, symbols in api_profile.items():
                modname = "blackbox_vnext." + rel.replace("/", ".").removesuffix(".py")
                if modname.endswith(".__init__"):
                    modname = modname[: -len(".__init__")]
                elif modname == "__init__":
                    modname = label  # package root
                try:
                    mod = importlib.import_module(modname)
                except Exception as e:  # noqa: BLE001
                    api_mismatches[rel] = [f"IMPORT-ERROR: {e}"]
                    continue
                missing = []
                for sym in symbols:
                    n_symbols += 1
                    if not hasattr(mod, sym):
                        missing.append(sym)
                if missing:
                    api_mismatches[rel] = missing
        finally:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass

    return mismatches, api_mismatches, n_files, n_symbols


# --- Public API --------------------------------------------------------


def verify_dependency(strict: bool = True) -> DependencyCheckResult:
    _slice1_path.ensure_slice1_importable()
    s1_root = _slice1_path.slice1_root()
    s0_root = _slice1_path.slice0_root()

    m0, _, f0, sy0 = _verify_one_slice(
        SLICE0_FILE_SHA256, {}, s0_root, "slice0",
    )
    m1, a1, f1, sy1 = _verify_one_slice(
        SLICE1_FILE_SHA256, SLICE1_API_PROFILE, s1_root, "product-slice1",
    )

    mismatches: list[dict[str, str]] = []
    mismatches.extend(m0)
    mismatches.extend(m1)

    api_mismatches: dict[str, list[str]] = {}
    api_mismatches.update({f"slice1/{k}": v for k, v in a1.items()})

    ok = (not mismatches) and (not api_mismatches)
    if strict and not ok:
        return DependencyCheckResult(
            ok=False, mismatches=mismatches,
            api_mismatches=api_mismatches,
            slice1_root=str(s1_root),
            slice0_root=str(s0_root),
            slice_files_checked=f0 + f1,
            api_symbols_checked=sy1,
        )
    return DependencyCheckResult(
        ok=ok, mismatches=mismatches,
        api_mismatches=api_mismatches,
        slice1_root=str(s1_root),
        slice0_root=str(s0_root),
        slice_files_checked=f0 + f1,
        api_symbols_checked=sy1,
    )


def require_pinned_dependency(strict: bool = True) -> DependencyCheckResult:
    res = verify_dependency(strict=strict)
    if not res.ok:
        raise DependencyDrift(res.to_dict())
    return res


def gate_semantic_entrypoint(strict: bool = True) -> DependencyCheckResult:
    """Single automatic fail-closed choke point invoked by every public
    semantic entrypoint in `bbx4.dispatch` and `bbx4.state`.

    Per Slice 4 TASK acceptance criteria #8: "Direct semantic APIs
    automatically enforce dependency drift."
    """
    return require_pinned_dependency(strict=strict)

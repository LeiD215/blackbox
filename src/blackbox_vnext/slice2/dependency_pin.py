"""S2-H2 Slice 1 dependency identity pin (no-Git, fail-closed).

Local, no-Git dependency identity manifest for the Slice 1 files /
interfaces actually consumed by Slice 2. On every live/reference execution,
the dependency identity is verified; mismatch raises DependencyDrift and
the entrypoint refuses to produce green/current results (no operator need
to remember a separate `verify-dep` command).

Pinned to the actual final Slice 1 PASS delivery (provenance-correct):
  commit `87482a53b74f28b499bdba5d9a22719302a69d7d`
  tree   `f4ee87676e309418211adffd8110dcdc32b72ede`

The pinned SHA-256 list below is computed at delivery time from the
on-disk Slice 1 file bytes. If those bytes drift later, dependency_pin
raises DependencyDrift and all semantic Slice 2 paths refuse to run;
the operator must update both the pin AND the file SHA-256 list (and
re-run the Slice 2 suite to retain the same deliverable identity).

This module never invokes `git`, never reads `.git`, never spawns
subprocesses. It only verifies on-disk bytes + public API identity.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from ._slice1_path import ensure_slice1_importable

ensure_slice1_importable()


# --- pinned identity (TASK S2-H2) ---

#: Slice 1 PASS baseline commit pin (the actual final Slice 1 PASS delivery
#: reviewed by coordinator — NOT a Slice 2 commit). Slice 2 trusts the
#: canonical events produced under that commit; later commits must update
#: this pin AND the file SHA-256s below.
SLICE1_PINNED_COMMIT = "87482a53b74f28b499bdba5d9a22719302a69d7d"
SLICE1_TREE_SHA = "f4ee87676e309418211adffd8110dcdc32b72ede"

#: SHA-256 of each Slice 1 file consumed by Slice 2, computed at delivery.
SLICE1_FILE_SHA256: dict[str, str] = {
    "slice1/__init__.py":
        "4d5208649d1ef52e42c8dd323189bbfaa76163b8730e227dafe7d73a1575ddc7",
    "slice1/__main__.py":
        "0bc86f7eb2e1c35dfac05de9ed6552afdaa4b794e09a2e1ee9f81b8d420511f2",
    "slice1/authority.py":
        "ab50fbe25d5cdc24f3a85fb51dfbcb9c4e4c25305fd8b957abbcef5ab84c2422",
    "slice1/baseline.py":
        "b504e652adf6f8f706eab8d8ec02cd7cd9a364c1e567566485e6c95912b157a1",
    "slice1/canonical.py":
        "4c75f9978bac607fe10816f04e504e2d11a997d1834ac027fa1b17e44d3827ef",
    "slice1/fold.py":
        "07f8442b9d8d4c5b86b47e82d2961793a4e216c648fcf490f24aad1a3cdb37e6",
    "slice1/ingest.py":
        "5c0a832e1a4739464b21c566452c4928fe7222fdf6db4bfe962be2870457b928",
    "slice1/observe.py":
        "c1d66d7e3b9765c9c9ee432bc67f882bb01b1221b7a20b05518e4b47f9e92e62",
    "slice1/reingest.py":
        "106b827ab9d1e243b0bdab8d020c837e7f8e3d0ea500dff2a082ee54d568994e",
    "slice1/store.py":
        "f0fc400bd13ed29cec96af786d75caf2691e27e390608c3a1e62fdce2730ab1c",
    "slice1/subtypes.py":
        "bd18904af451f8b27369509227b7461ea6803612f7762b61ce56318340529fa5",
    "slice1/validity.py":
        "7a1859d9eabdb5d1c4b73fb0049c6abea2af87e807e7a3dc58848407f6a7f04a",
}

#: Public symbols consumed by Slice 2 from each module.
SLICE1_API_PROFILE: dict[str, list[str]] = {
    "slice1/__main__.py": [
        "_corpus_health", "_events_from_verdicts", "_load_profile",
        "_sensor_delta", "_subject_governance_health", "CODE_FIXED_EXCLUSIONS",
    ],
    "slice1/authority.py": [
        "CaseSAuthorityProfile", "evaluate_receipt_validity",
        "evaluate_receipt_assurance",
    ],
    "slice1/baseline.py": [
        "B_CORRUPT", "B_LOST", "B_MISSING", "B_OK", "B_STALE",
        "BaselineRecoveryRequired", "load_baseline", "persist_baseline",
    ],
    "slice1/canonical.py": [
        "canonical_bytes", "sha256_canonical", "verify_content_hash",
    ],
    "slice1/fold.py": [
        "FoldResult", "candidate_set_for_reclassify",
        "derived_complete", "fold_subject",
    ],
    "slice1/observe.py": [
        "COVERED", "classify_change", "compute_profile_identity",
        "compute_workspace_baseline", "index_receipt_effect_bindings",
        "load_observation_profile", "ObservedChange",
    ],
    "slice1/reingest.py": [
        "V_INVALID", "V_UNCLASSIFIED", "V_UNKNOWN", "V_VALID",
        "reingest_events_dir",
    ],
    "slice1/store.py": ["write_event"],
    "slice1/subtypes.py": ["SubtypeRegistry", "DEFAULT_REGISTRY_PATH"],
    "slice1/validity.py": [],
    "slice1/ingest.py": ["parse_strict_with_duplicate_check"],
    "slice1/__init__.py": [],
}


@dataclass
class DependencyCheckResult:
    ok: bool
    mismatches: list[dict[str, str]]
    api_mismatches: dict[str, list[str]]
    slice1_root: str
    pinned_commit: str
    pinned_tree_sha: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mismatches": list(self.mismatches),
            "api_mismatches": {k: list(v) for k, v in self.api_mismatches.items()},
            "slice1_root": self.slice1_root,
            "pinned_commit": self.pinned_commit,
            "pinned_tree_sha": self.pinned_tree_sha,
        }


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_slice1_dependency(strict: bool = True
                             ) -> DependencyCheckResult:
    """Verify the on-disk Slice 1 dependency identity matches the pinned
    SHA-256 list. Also verifies that the API profile (consumed public
    symbols) is still present on the resolved module objects.

    Args:
      strict: when True, returns ok=False on any mismatch. When False,
        mismatches are still collected but ok reflects only file identity.
    """
    ensure_slice1_importable()
    from ._slice1_path import slice1_root
    root = str(slice1_root())
    mismatches: list[dict[str, str]] = []
    api_mismatches: dict[str, list[str]] = {}
    for rel, expected in SLICE1_FILE_SHA256.items():
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            mismatches.append({
                "file": rel, "expected": expected, "actual": "MISSING",
            })
            continue
        actual = _sha256_of_file(path)
        if actual != expected:
            mismatches.append({
                "file": rel, "expected": expected, "actual": actual,
            })

    # API symbol presence (light-touch: import each module + hasattr check)
    for rel, syms in SLICE1_API_PROFILE.items():
        if not syms:
            continue
        mod_name = "blackbox_vnext." + rel.replace("/", ".").removesuffix(".py").removesuffix(".__init__")
        try:
            import importlib
            mod = importlib.import_module(mod_name)
        except Exception as e:
            api_mismatches[mod_name] = [f"import failed: {e}"]
            continue
        missing = [s for s in syms if not hasattr(mod, s)]
        if missing:
            api_mismatches[mod_name] = missing

    ok = not mismatches and (
        not strict or not api_mismatches
    )
    return DependencyCheckResult(
        ok=ok,
        mismatches=mismatches,
        api_mismatches=api_mismatches,
        slice1_root=root,
        pinned_commit=SLICE1_PINNED_COMMIT,
        pinned_tree_sha=SLICE1_TREE_SHA,
    )


class DependencyDrift(Exception):
    def __init__(self, detail: dict[str, Any]):
        self.detail = detail
        super().__init__(f"DEPENDENCY_DRIFT: {detail}")


def require_pinned_slice1(strict: bool = True) -> DependencyCheckResult:
    """Same as verify but raises DependencyDrift on mismatch (used by CLI
    paths that refuse to produce green/current on drift)."""
    res = verify_slice1_dependency(strict=strict)
    if not res.ok:
        raise DependencyDrift(res.to_dict())
    return res


def gate_semantic_entrypoint(strict: bool = True) -> DependencyCheckResult:
    """S2-H2 + S2-I1: fail-closed gate called by every semantic Slice 2
    entrypoint that can produce green/current/recovery results using Slice 1
    semantics.

    Wired call sites (must match this list; verified by adversarial tests):
      - bbx2.projector.project()                 (live + offline)
      - bbx2.manifest.build_manifest()          (S2-I1)
      - bbx2.manifest.recompute_integrity()      (S2-I1)
      - bbx2.checkpoint.generate_checkpoint()
      - bbx2.checkpoint._materialize()
      - bbx2.checkpoint.verify_self_reference()
      - bbx2.frontier.compute_frontier()
      - bbx2.replay.replay()
      - bbx2.recovery.build_recovery_plan()
      - bbx2.validate.validate()

    Drift → DependencyDrift (no operator need to remember a separate
    verify-dep command). Pure formatting helpers that do not consume Slice 1
    semantics are intentionally ungated.

    Pure on-disk SHA-256 + API identity check — no Git invocation.
    """
    return require_pinned_slice1(strict=strict)

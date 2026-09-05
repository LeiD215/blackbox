"""Product-local locations used by the consolidated Slice 4 runtime."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def slice1_root() -> Path:
    return _PACKAGE_ROOT


def slice0_root() -> Path:
    # Product installs ship Slice0 resources as package data, not as a
    # repository-adjacent schemas directory.
    return _PACKAGE_ROOT / "data"


def slice0_registry_path() -> Path:
    """Return the canonical path to the Slice 0 subtypes.json registry.

    This is the registry file transitively consumed by Slice 4 via
    Slice 1 `SubtypeRegistry.load()` (which computes the same path
    internally). Pinning this file in `dependency_pin.SLICE0_PINNED_FILES`
    requires the absolute path so the gate can byte-check it.
    """
    return slice0_root() / "subtypes.json"


def ensure_slice1_importable() -> Path:
    """Retained for API compatibility; product imports do not use sys.path."""
    return slice1_root()

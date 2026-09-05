"""Small fail-closed integrity check for security-critical package data."""

from __future__ import annotations

import hashlib
from pathlib import Path


class DependencyDrift(RuntimeError):
    pass


FILE_SHA256 = {
    1: {
        "blackbox_vnext/canonical.py": "4c75f9978bac607fe10816f04e504e2d11a997d1834ac027fa1b17e44d3827ef",
    }
}


def gate_semantic_entrypoint() -> None:
    root = Path(__file__).parent.parent
    for relative, expected in FILE_SHA256[1].items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise DependencyDrift(f"dependency drift: {relative}")


def verify() -> dict[str, object]:
    gate_semantic_entrypoint()
    return {"ok": True, "files_checked": 1}

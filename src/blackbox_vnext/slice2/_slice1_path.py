"""Product-local compatibility locator for consolidated Slice 1."""
from __future__ import annotations

from pathlib import Path

def ensure_slice1_importable() -> None:
    """Retained for Slice 2 API compatibility; imports are product-local."""


def slice1_root() -> Path:
    return Path(__file__).resolve().parents[1]

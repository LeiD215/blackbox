"""Subtype registry loader + UNCLASSIFIED vs invalid-combination detection.

Loads FORMAT F.6 / Design v0.1.4 G2 subtype registry from a JSON file and provides
machine predicates for:
    - registry_lookup(subtype) -> dict (or None if not registered)
    - is_registered(subtype) -> bool
    - is_valid_combination(type, subtype, receipt_class) -> bool
    - propagate_unclassified_or_invalid(obj) -> ("UNCLASSIFIED", reason) | ("INVALID", reason) | None
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Default registry location is the packaged Slice 0 registry.
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "subtypes.json"
).resolve()

# Core types accepted (FORMAT F.6 / Design v0.1.4).
CORE_TYPES = ("DECISION", "TASK", "VERIFICATION", "CHANGE")

# Receipt classes (actual receipt_class only; 'derived' is projection).
RECEIPT_CLASSES = ("claim", "observed", "self-verification", "independent-verification")


class SubtypeRegistry:
    """Load the subtypes.json registry and provide machine predicates."""

    def __init__(self, registry_doc: dict[str, Any]) -> None:
        self.core_types = tuple(registry_doc.get("core_types", CORE_TYPES))
        self.receipt_classes = tuple(registry_doc.get("receipt_classes", RECEIPT_CLASSES))
        self.registry: dict[str, dict[str, Any]] = registry_doc.get("registry", {})

    @classmethod
    def load(cls, path: str | Path = DEFAULT_REGISTRY_PATH) -> "SubtypeRegistry":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def lookup(self, subtype: str) -> dict[str, Any] | None:
        return self.registry.get(subtype)

    def is_registered(self, subtype: str) -> bool:
        return subtype in self.registry

    def is_valid_combination(
        self,
        type_: str,
        subtype: str,
        receipt_class: str,
    ) -> bool:
        """A registered subtype may declare allowed_type and allowed_receipt_class.

        The combination is valid iff subtype is registered AND type_ is in
        allowed_type AND receipt_class is in allowed_receipt_class.
        """
        spec = self.lookup(subtype)
        if spec is None:
            return False  # not registered -> caller distinguishes UNCLASSIFIED
        return type_ in spec.get("allowed_type", []) and receipt_class in spec.get(
            "allowed_receipt_class", []
        )

    def classify_event(self, obj: dict[str, Any]) -> tuple[str, str] | None:
        """Classify event per FORMAT F.6 semantics.

        Returns:
            None                              -> recognized, valid combination
            ("UNCLASSIFIED", reason)          -> unregistered subtype (no formal effect)
            ("INVALID", reason)               -> registered subtype, invalid type/receipt_class combo

        Caller distinguishes "registered but invalid combo" from "unregistered"
        because UNCLASSIFIED events are retained for historical evidence only,
        while invalid-combo events are rejected outright.
        """
        subtype = obj.get("subtype")
        type_ = obj.get("type")
        receipt_class = obj.get("receipt_class")
        if not self.is_registered(subtype):
            return (
                "UNCLASSIFIED",
                f"subtype={subtype!r} not in registry (no formal effect; retained for evidence)",
            )
        if not self.is_valid_combination(type_, subtype, receipt_class):
            return (
                "INVALID",
                f"type={type_!r} or receipt_class={receipt_class!r} not in "
                f"subtype={subtype!r} allowed_* set",
            )
        return None

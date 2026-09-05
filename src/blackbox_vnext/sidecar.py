"""Optional derived presentation helpers; never canonical authority."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SidecarStatus:
    text: str
    derived_non_authority: bool = True


def render_status(state: str, blockers: tuple[str, ...] = ()) -> SidecarStatus:
    detail = ",".join(sorted(set(blockers))) if blockers else "none"
    return SidecarStatus(f"state={state}; blockers={detail}")

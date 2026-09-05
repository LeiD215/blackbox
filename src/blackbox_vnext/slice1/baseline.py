"""Persisted derived baseline artifact (R5, S1-7/S1-10).

The baseline is a DERIVED, non-authority sensor artifact persisted under the
project .blackbox sandbox. It records what the sensor last acknowledged:
  - pinned observation-profile identity (sha256:...)
  - governed path -> post identity at last acknowledgement
  - as-of marker (identity of the last ingested event_id corpus input)

NOT canonical governance authority: the baseline never creates receipts and
never legalizes an orphan. It is acknowledged state that observe compares
against a fresh workspace recompute; explicit acknowledge is the only way it
advances (no silent legalization of orphans).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes

BASELINE_FILENAME = "baseline.json"
BASELINE_SCHEMA_VERSION = "0.3.2"
# S1-F2: init-persisted sensor marker recording whether an acknowledged
# baseline has EVER existed for this project root. Loss/corruption of
# baseline.json is then distinguishable from first-time initialization.
SENSOR_MARKER_FILENAME = "baseline-exists.marker"

B_OK = "OK"
B_MISSING = "MISSING"
B_CORRUPT = "CORRUPT"
B_STALE = "STALE"  # profile identity mismatch
B_LOST = "LOST"    # S1-F2: marker says a baseline existed, but the file is gone


@dataclass
class PersistedBaseline:
    profile_identity_sha256: str
    as_of_input_identity: str
    path_to_post_identity: dict[str, str] = field(default_factory=dict)


@dataclass
class BaselineLoadResult:
    status: str  # B_OK | B_MISSING | B_CORRUPT | B_STALE
    baseline: PersistedBaseline | None
    reason: str


def baseline_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".blackbox" / "baseline.json"


def sensor_marker_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".blackbox" / SENSOR_MARKER_FILENAME


def has_baseline_ever_existed(project_root: str | Path) -> bool:
    """S1-F2: init-persisted marker — true iff an acknowledged baseline has
    ever been established for this project root."""
    return sensor_marker_path(project_root).exists()


def _persist_sensor_marker(project_root: str | Path) -> None:
    p = sensor_marker_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(
        json.dumps({
            "marker": "acknowledged-baseline-existed",
            "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        }, sort_keys=True).encode("utf-8") + b"\n"
    )


def load_baseline(
    project_root: str | Path,
    current_profile_identity: str | None = None,
) -> BaselineLoadResult:
    """Load + validate the persisted baseline artifact.

    - absent file + no prior-baseline marker => MISSING (first-time init)
    - absent file + marker present => LOST (S1-F2: a baseline existed but the
      artifact is gone; UNKNOWN until explicit recovery — ordinary ack must
      NOT silently re-bless current bytes)
    - unparseable / wrong shape => CORRUPT (fail-closed: blocks green)
    - profile identity mismatch vs freshly computed profile => STALE
    """
    p = baseline_path(project_root)
    if not p.exists():
        if has_baseline_ever_existed(project_root):
            return BaselineLoadResult(
                B_LOST, None,
                "acknowledged baseline artifact is MISSING but a baseline "
                "previously existed (sensor marker present); explicit safe "
                "recovery required (observe --rebaseline) after reconciling "
                "current workspace state",
            )
        return BaselineLoadResult(B_MISSING, None, "no persisted baseline yet")
    try:
        raw = p.read_bytes()
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return BaselineLoadResult(B_CORRUPT, None, f"baseline unreadable/corrupt: {e}")
    if not isinstance(obj, dict):
        return BaselineLoadResult(B_CORRUPT, None, "baseline is not a JSON object")
    for k in ("baseline_schema_version", "profile_identity_sha256", "as_of_input_identity", "governed"):
        if k not in obj:
            return BaselineLoadResult(B_CORRUPT, None, f"baseline missing field {k!r}")
    if obj.get("baseline_schema_version") != BASELINE_SCHEMA_VERSION:
        return BaselineLoadResult(
            B_CORRUPT, None,
            f"baseline schema version mismatch: {obj.get('baseline_schema_version')!r}",
        )
    gov = obj.get("governed")
    if not isinstance(gov, dict):
        return BaselineLoadResult(B_CORRUPT, None, "baseline 'governed' is not an object")
    for k, v in gov.items():
        if not isinstance(v, str):
            return BaselineLoadResult(B_CORRUPT, None, f"baseline governed[{k!r}] is not a string")
    b = PersistedBaseline(
        profile_identity_sha256=obj["profile_identity_sha256"],
        as_of_input_identity=obj["as_of_input_identity"],
        path_to_post_identity=dict(gov),
    )
    if (
        current_profile_identity is not None
        and b.profile_identity_sha256 != current_profile_identity
    ):
        return BaselineLoadResult(
            B_STALE, b,
            f"profile identity mismatch: baseline pinned {b.profile_identity_sha256}, "
            f"current profile computes {current_profile_identity}",
        )
    return BaselineLoadResult(B_OK, b, "baseline ok")


def persist_baseline(
    project_root: str | Path,
    profile_identity_sha256: str,
    as_of_input_identity: str,
    path_to_post_identity: dict[str, str],
    allow_recovery: bool = False,
) -> Path:
    """Persist the acknowledged baseline artifact (S1-F2 gating).

    - normal acknowledge: allowed only when NO baseline file exists, or when
      an OK baseline exists (its routine advance). Refuses to overwrite a
      corrupt/stale artifact.
    - allow_recovery=True: explicit recovery intent (observe --rebaseline);
      overwrites corrupt/lost artifacts after the caller has reconciled the
      workspace. Audited via the marker + caller-reported reason.
    """
    p = baseline_path(project_root)
    if p.exists() and not allow_recovery:
        existing = load_baseline(project_root)
        if existing.status in (B_CORRUPT, B_STALE, B_LOST):
            raise BaselineRecoveryRequired(
                f"refusing ordinary ack over {existing.status} baseline: "
                f"{existing.reason} (explicit observe --rebaseline required)"
            )
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "profile_identity_sha256": profile_identity_sha256,
        "as_of_input_identity": as_of_input_identity,
        "governed": dict(path_to_post_identity),
    }
    data = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_bytes(data.encode("utf-8"))
    tmp.replace(p)
    _persist_sensor_marker(project_root)
    return p


class BaselineRecoveryRequired(Exception):
    """S1-F2: ordinary acknowledge attempted over corrupt/stale/lost baseline."""

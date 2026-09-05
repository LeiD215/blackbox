"""S2-8 CLI / local API surface.

Thin CLI over bbx2.* modules. Subcommands:
  project          Run the deterministic projector and write projection JSON.
  checkpoint       Generate a Slice 0 frozen checkpoint from a corpus.
  validate         Validate a stored checkpoint.
  replay           Re-derive manifest + frontier and compare against checkpoint.
  staleness        Set-difference two checkpoint manifests.
  recovery-plan    Build a recovery assistance report from corpus + checkpoint.
  version          Print generator identity + framework + schema versions.

No Git adapter, no multi-agent dispatch, no production mutation. Pure
local reference implementation. CLI is non-interactive: every action
requires --events-dir / --checkpoint / --baseline / etc. on argv. stdout is
the artifact (or a small JSON envelope); exit codes carry pass/fail.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import GENERATOR_IDENTITY, SCHEMA_VERSION, FRAMEWORK_VERSION

from . import projector as bbx2_projector
from . import manifest as bbx2_manifest
from . import frontier as bbx2_frontier
from . import checkpoint as bbx2_checkpoint
from . import validate as bbx2_validate
from . import replay as bbx2_replay
from . import staleness as bbx2_staleness
from . import recovery as bbx2_recovery
from . import dependency_pin as bbx2_dep


def _emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def _load_checkpoint(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"checkpoint at {path} is not a JSON object")
    return data


def _load_manifest(path: str) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"manifest at {path} is not a JSON array")
    return data


def cmd_project(args: argparse.Namespace) -> int:
    # S2-H1: offline projection (no project_root/profile supplied) cannot
    # claim green; CLI returns rc=2 and clearly says sensor/T3 not evaluated.
    proj = bbx2_projector.project(args.events_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bbx2_projector.projection_to_bytes(proj.projection))
    summary = {
        "ok": proj.is_green,
        "blocked": proj.blocked,
        "input_mode": proj.input_mode,
        "current_health": proj.current_health,
        "governance_state": proj.governance_state,
        "aggregate_state": proj.projection["aggregate"]["state"],
        "subjects": proj.subjects,
        "out": str(out_path),
    }
    if proj.input_mode == "offline_recovery":
        # Offline mode never yields a green/current claim: full live health
        # is NOT_EVALUATED even when the corpus is trustworthy.
        summary["non_green_reason"] = (
            "offline_recovery: sensor/T3 not evaluated; "
            "cannot claim full live green"
        )
    _emit(summary)
    return 0 if proj.is_green else 2


def cmd_checkpoint(args: argparse.Namespace) -> int:
    try:
        cp = bbx2_checkpoint.generate_checkpoint(args.events_dir)
    except bbx2_checkpoint.CheckpointBlocked as e:
        _emit({"ok": False, "reason": e.reason, "detail": e.detail})
        return 2
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bbx2_checkpoint.checkpoint_to_bytes(cp.checkpoint))
    _emit({
        "ok": True,
        "checkpoint_id": cp.checkpoint_id,
        "manifest_integrity_sha256": cp.manifest_integrity_sha256,
        "out": str(out_path),
    })
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cp = _load_checkpoint(args.checkpoint)
    val = bbx2_validate.validate(cp)
    _emit({
        "ok": val.ok,
        "reason": val.reason,
        "where": val.where,
        "checks": {
            "schema_pins": val.schema_pins_ok,
            "as_of_count": val.as_of_count_ok,
            "unique_manifest_ids": val.unique_manifest_ids_ok,
            "frontier_in_manifest": val.frontier_in_manifest_ok,
            "source_refs": val.source_refs_ok,
            "manifest_integrity": val.manifest_integrity_ok,
            "self_reference": val.self_reference_ok,
        },
    })
    return 0 if val.ok else 2


def cmd_replay(args: argparse.Namespace) -> int:
    cp = _load_checkpoint(args.checkpoint)
    rr = bbx2_replay.replay(cp, args.events_dir)
    _emit({
        "ok": rr.ok,
        "reason": rr.reason,
        "detail": rr.detail,
    })
    return 0 if rr.ok else 2


def cmd_staleness(args: argparse.Namespace) -> int:
    baseline = _load_manifest(args.baseline)
    candidate = _load_manifest(args.candidate)
    sr = bbx2_staleness.compute_staleness(baseline, candidate)
    _emit(bbx2_staleness.staleness_to_dict(sr))
    return 0 if sr.is_current else 2


def cmd_recovery_plan(args: argparse.Namespace) -> int:
    cp = _load_checkpoint(args.checkpoint) if args.checkpoint else None
    plan = bbx2_recovery.build_recovery_plan(args.events_dir, cp)
    _emit(bbx2_recovery.recovery_plan_to_dict(plan))
    # PARTIAL_RECOVERY_PLAN = 0 (operator-friendly); FULL_CANONICAL_REPLAY_REQUIRED = 2
    return (0 if plan.recommendation == bbx2_recovery.PARTIAL_RECOVERY_PLAN
            else 2)


def cmd_version(args: argparse.Namespace) -> int:
    _emit({
        "generator_identity": GENERATOR_IDENTITY,
        "framework_version": FRAMEWORK_VERSION,
        "schema_version": SCHEMA_VERSION,
    })
    return 0


def cmd_verify_dep(args: argparse.Namespace) -> int:
    res = bbx2_dep.verify_slice1_dependency(strict=True)
    _emit(res.to_dict())
    return 0 if res.ok else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bbx2",
        description=(
            "Blackbox vNext Slice 2 reference implementation: "
            "deterministic projector + checkpoint + replay + staleness + "
            "recovery-plan. NON-AUTHORITY, NO production mutation, NO Git adapter."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("project", help="Run deterministic projector")
    pp.add_argument("--events-dir", required=True)
    pp.add_argument("--out", required=True, help="output JSON file path")
    pp.set_defaults(func=cmd_project)

    pc = sub.add_parser("checkpoint", help="Generate Slice 0 frozen checkpoint")
    pc.add_argument("--events-dir", required=True)
    pc.add_argument("--out", required=True)
    pc.set_defaults(func=cmd_checkpoint)

    pv = sub.add_parser("validate", help="Validate a stored checkpoint")
    pv.add_argument("--checkpoint", required=True)
    pv.set_defaults(func=cmd_validate)

    pr = sub.add_parser("replay", help="Replay a checkpoint against corpus")
    pr.add_argument("--checkpoint", required=True)
    pr.add_argument("--events-dir", required=True)
    pr.set_defaults(func=cmd_replay)

    ps = sub.add_parser("staleness", help="Set-difference two manifests")
    ps.add_argument("--baseline", required=True)
    ps.add_argument("--candidate", required=True)
    ps.set_defaults(func=cmd_staleness)

    prp = sub.add_parser("recovery-plan",
                         help="Build a recovery assistance report")
    prp.add_argument("--events-dir", required=True)
    prp.add_argument("--checkpoint", required=False, default=None,
                     help="optional checkpoint JSON path; omit to simulate "
                          "missing checkpoint")
    prp.set_defaults(func=cmd_recovery_plan)

    pver = sub.add_parser("version", help="Print versions")
    pver.set_defaults(func=cmd_version)

    pvd = sub.add_parser("verify-dep",
                         help="Verify the pinned Slice 1 dependency identity")
    pvd.set_defaults(func=cmd_verify_dep)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
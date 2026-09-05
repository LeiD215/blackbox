"""bbx4.cli — small CLI dispatcher for Slice 4.

Per Slice 4 TASK + TASK `5cf6cfc763ec` (S4-Q1..Q10):

- `authority-status --actor <actor>` / capability inspection
- `validate-authority` / corpus authority fold
- `validate-dispatch --event <id>` / formal dispatch authorization check

Each subcommand loads the canonical event corpus via
`bbx4.trusted_corpus.load_trusted_corpus(events_dir)` (the trusted
raw controlled-corpus boundary per Q3). The trusted corpus becomes
the input to `dispatch.validate_dispatch`/`validate_authority`/
`authority_status`; arbitrary `list[dict]` cannot substitute.

Non-green/unknown/conflict/unauthorized validation returns nonzero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from blackbox_vnext.slice4 import dependency_pin, dispatch as dispatch_mod
from blackbox_vnext.slice4.trusted_corpus import load_trusted_corpus


def _gate() -> None:
    """Every CLI subcommand must call this first."""
    dependency_pin.gate_semantic_entrypoint(strict=True)


# --- Subcommands --------------------------------------------------------


def cmd_authority_status(args: argparse.Namespace) -> int:
    _gate()
    corpus = load_trusted_corpus(Path(args.corpus).resolve())
    report = dispatch_mod.authority_status(args.actor, corpus)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("known") else 1


def cmd_validate_authority(args: argparse.Namespace) -> int:
    _gate()
    corpus = load_trusted_corpus(Path(args.corpus).resolve())
    result = dispatch_mod.validate_authority(corpus)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("trustworthy") else 2


def cmd_validate_dispatch(args: argparse.Namespace) -> int:
    _gate()
    corpus = load_trusted_corpus(Path(args.corpus).resolve())
    decision = dispatch_mod.validate_dispatch(args.event, corpus)
    print(json.dumps({
        "event_id": decision.event_id,
        "authorized": decision.authorized,
        "reason": decision.reason,
        "detail": decision.detail,
    }, ensure_ascii=False, indent=2))
    return 0 if decision.authorized else 2


# --- main --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bbx4",
        description="Blackbox vNext Slice 4 — multi-agent dispatch / authority",
    )
    p.add_argument("--corpus", required=True,
                    help="Path to canonical event corpus directory "
                         "(each <event_id>.json file is one canonical event).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("authority-status",
                         help="Report held capabilities for an actor.")
    sp.add_argument("--actor", required=True,
                     help="Actor identity to inspect.")
    sp.set_defaults(func=cmd_authority_status)

    sp = sub.add_parser("validate-authority",
                         help="Fold corpus into authority state.")
    sp.set_defaults(func=cmd_validate_authority)

    sp = sub.add_parser("validate-dispatch",
                         help="Validate a TASK / dispatch event by event_id.")
    sp.add_argument("--event", required=True,
                     help="Canonical event_id of the TASK / dispatch event.")
    sp.set_defaults(func=cmd_validate_dispatch)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except dependency_pin.DependencyDrift as e:
        print(json.dumps({"ok": False, "reason": "DEPENDENCY-DRIFT",
                          "detail": e.detail}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        return 2
    except ValueError as e:
        print(json.dumps({"ok": False, "reason": "INVALID-INPUT",
                          "detail": str(e)}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
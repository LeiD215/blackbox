"""Project-root product CLI; canonical state never defaults to package files."""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .adapters import FilesystemReadbackAdapter, ReadbackError
from .gate import CanonicalRequirement, authorization_from_slice4, pre_release_check
from .receipt import IdentityBinding, make_receipt_event, validate_receipt_event, verify_independent


def _root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _seed(root: Path) -> None:
    """Materialize governed immutable package resources below `.blackbox`."""
    package = Path(__file__).resolve().parent / "data"
    bb = root / ".blackbox"
    governed = [(package / "FORMAT.md", bb / "FORMAT"), (package / "bootstrap.json", bb / "authority" / "bootstrap.json")]
    governed += [(item, bb / "schema" / item.name) for item in package.glob("*.json") if item.name != "bootstrap.json"]
    for source, target in governed:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise RuntimeError(f"GOVERNED-RESOURCE-DRIFT: {target}")
        if not target.exists(): shutil.copy2(source, target)
    for name in ("events", "checkpoints", "projections", "recovery"):
        (bb / name).mkdir(parents=True, exist_ok=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="blackbox-vnext")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "validate", "status", "resume"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", default=".", help="project root (default: current directory)")
    observe = sub.add_parser("observe")
    observe.add_argument("--root", default=".", help="project root (default: current directory)")
    observe.add_argument("--ack", action="store_true", help="acknowledge a clean fresh baseline")
    observe.add_argument("--rebaseline", action="store_true", help="explicit baseline recovery after reconciliation")
    self_verify = sub.add_parser("self-verify", help="ingest an explicit canonical self-verification support record")
    self_verify.add_argument("--root", default=".")
    self_verify.add_argument("--event", required=True, help="canonical VERIFICATION/self-verification event JSON")
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--root", default=".")
    checkpoint.add_argument("--out", default=None, help="checkpoint path; defaults below project .blackbox")
    independent = sub.add_parser("independent-verify")
    independent.add_argument("--root", default=".")
    independent.add_argument("--read-root", required=True)
    independent.add_argument("--read-path", required=True)
    independent.add_argument("--target", required=True)
    independent.add_argument("--subject", required=True)
    independent.add_argument("--artifact-identity", required=True)
    independent.add_argument("--input-scope", required=True)
    independent.add_argument("--expected-identity", required=True)
    independent.add_argument("--expected-algorithm", required=True)
    independent.add_argument("--executor", required=True)
    independent.add_argument("--claim-source", required=True)
    independent.add_argument("--verifier", required=True)
    independent.add_argument("--source-id", required=True)
    independent.add_argument("--event-id", required=True)
    independent.add_argument("--observed-at", required=True, help="RFC3339 UTC observation time")
    independent.add_argument("--now", required=True, help="RFC3339 UTC verification time")
    independent.add_argument("--recorded-at", required=True, help="RFC3339 UTC receipt record time")
    independent.add_argument("--max-age-seconds", type=int, default=300)
    preflight = sub.add_parser("pre-release-check")
    preflight.add_argument("--root", default=".")
    preflight.add_argument("--requirements", required=True, help="canonical requirement JSON array")
    preflight.add_argument("--evidence", action="append", default=[], help="canonical receipt event file; repeatable")
    preflight.add_argument("--dispatch-event-id", action="append", default=[], help="Slice4 dispatch id required for prior authorization; repeatable")
    write = sub.add_parser("write")
    write.add_argument("event")
    write.add_argument("--root", default=".")
    receipt = sub.add_parser("validate-receipt")
    receipt.add_argument("event")
    return p


def _slice1(argv: list[str]) -> int:
    from .slice1.__main__ import main as slice1_main
    return slice1_main(argv)


def _events(root: Path) -> Path:
    return root / ".blackbox" / "events"


def _valid_utc_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00") if value.endswith("Z") else None
        return parsed is not None and parsed.tzinfo == timezone.utc
    except ValueError:
        return False


def _requirements(path: str) -> tuple[CanonicalRequirement, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("requirements must be a JSON array")
    requirements = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise TypeError(f"requirements[{index}] must be a JSON object")
        requirements.append(CanonicalRequirement(**item))
    return tuple(requirements)


def _prior_authorized(requirement: CanonicalRequirement, dispatch_ids: list[str], events_dir: Path) -> bool:
    """Bind one high-risk requirement to exactly one current green dispatch."""
    from .slice4.dispatch import validate_dispatch
    from .slice4.trusted_corpus import load_trusted_corpus
    corpus = load_trusted_corpus(events_dir)
    matches = []
    for event_id in sorted(set(dispatch_ids)):
        event = corpus.event_by_id(event_id)
        if not isinstance(event, dict):
            continue
        extension = event.get("extensions", {}).get("dispatch", {})
        if (
            event.get("subject") == requirement.subject
            and extension.get("surface") == requirement.scope
            and extension.get("assignee") == requirement.executor
            and authorization_from_slice4(validate_dispatch(event_id, events_dir))
        ):
            matches.append(event_id)
    return len(matches) == 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "validate-receipt":
        result = validate_receipt_event(Path(args.event).read_bytes())
        print(json.dumps({"valid": result.valid, "reason": result.reason}, sort_keys=True))
        return 0 if result.valid else 2
    root = _root(args.root)
    if args.command == "init":
        rc = _slice1(["init", str(root)])
        if rc == 0:
            try:
                _seed(root)
            except RuntimeError as error:
                print(str(error))
                return 2
        return rc
    if args.command == "write":
        return _slice1(["write", args.event, "--root", str(root)])
    if args.command in {"validate", "status", "resume"}:
        return _slice1([args.command, str(root)])
    if args.command == "observe":
        forwarded = ["observe", str(root)]
        if args.ack:
            forwarded.append("--ack")
        if args.rebaseline:
            forwarded.append("--rebaseline")
        return _slice1(forwarded)
    if args.command == "self-verify":
        try:
            event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(json.dumps({"ok": False, "reason": str(error)}, sort_keys=True))
            return 2
        if not isinstance(event, dict) or (
            event.get("type"), event.get("subtype"), event.get("receipt_class")
        ) != ("VERIFICATION", "self-verification", "self-verification"):
            print(json.dumps({"ok": False, "reason": "SELF-VERIFICATION-PROFILE-REQUIRED"}, sort_keys=True))
            return 2
        # Normal canonical write/reingest is the sole storage path.  This
        # operation deliberately emits no independent receipt or green gate.
        return _slice1(["write", args.event, "--root", str(root)])
    if args.command == "checkpoint":
        from .slice2.checkpoint import CheckpointBlocked, checkpoint_to_bytes, generate_checkpoint
        try:
            result = generate_checkpoint(_events(root))
        except CheckpointBlocked as error:
            print(json.dumps({"ok": False, "reason": error.reason, "detail": error.detail}, sort_keys=True))
            return 2
        out = Path(args.out) if args.out else root / ".blackbox" / "checkpoint.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(checkpoint_to_bytes(result.checkpoint))
        print(json.dumps({"ok": True, "checkpoint_id": result.checkpoint_id, "out": str(out)}, sort_keys=True))
        return 0
    if args.command == "independent-verify":
        timestamps = (args.observed_at, args.now, args.recorded_at)
        if not all(_valid_utc_timestamp(value) for value in timestamps) or args.max_age_seconds < 0:
            print(json.dumps({"ok": False, "reason": "INVALID-INDEPENDENT-VERIFY-TIMESTAMP"}, sort_keys=True))
            return 2
        binding = IdentityBinding(args.target, args.subject, args.artifact_identity, args.input_scope,
                                  args.expected_identity, args.expected_algorithm, args.executor, args.claim_source)
        try:
            observation = FilesystemReadbackAdapter(Path(args.read_root), args.verifier, args.source_id).read(
                binding, args.read_path, observed_at=args.observed_at)
        except (ReadbackError, OSError) as error:
            print(json.dumps({"ok": False, "reason": str(error)}, sort_keys=True))
            return 2
        receipt = verify_independent(binding, observation, now=args.now, max_age_seconds=args.max_age_seconds)
        event = make_receipt_event(receipt, event_id=args.event_id, recorded_at=args.recorded_at)
        print(json.dumps(event, sort_keys=True))
        return 0 if receipt.verified_independent else 2
    if args.command == "pre-release-check":
        try:
            requirements = _requirements(args.requirements)
            high_risk = {"production", "external", "irreversible", "high-risk", "security", "authority-boundary", "release", "acceptance"}
            requirements = tuple(replace(req, prior_authorized=_prior_authorized(req, args.dispatch_event_id, _events(root))) if req.effect_class in high_risk else req for req in requirements)
            evidence = [Path(path).read_bytes() for path in args.evidence]
        except (OSError, ValueError, TypeError) as error:
            print(json.dumps({"ok": False, "reason": str(error)}, sort_keys=True))
            return 2
        result = pre_release_check(requirements, evidence)
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0 if result.verdict == "PASS" else 2
    raise AssertionError(args.command)

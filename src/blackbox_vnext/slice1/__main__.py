"""Slice 1 CLI entrypoint.

Subcommands (Case S, no Git/CI/daemon):
    bbx init <project_root>      Initialize .blackbox/ directory
    bbx write <event.json>       Canonical event write with synchronous T3
    bbx validate <project_root>  Strict re-ingest of all events + per-file verdict
    bbx status <project_root>    T3 (strict re-ingest + sensor delta + per-subject
                                 governance health) then fold + derived COMPLETE;
                                 fail-closed on any INVALID/UNKNOWN corpus file,
                                 layout/collision violation, sensor UNKNOWN/STALE,
                                 or same-subject UNCLASSIFIED/UNAUTHORIZED receipt
    bbx observe <project_root>   Compare persisted acknowledged baseline vs fresh
                                 workspace recompute; real 4-way classification via
                                 the Case S authority evaluator
    bbx observe --ack            Acknowledge the fresh baseline (rejected when any
                                 non-COVERED change, INVALID/UNKNOWN corpus file,
                                 or corrupt/stale/lost baseline is present — never
                                 silently legalizes an orphan)
    bbx observe --rebaseline     S1-F2 explicit recovery: overwrite a corrupt/
                                 stale/lost baseline after workspace
                                 reconciliation; refuses when non-COVERED changes
                                 remain unresolved
    bbx resume <project_root>    Alias for status (runs the same T3 before reporting)

Read-only when applicable. NO production/external behavior. NO multi-agent
formal dispatch implementation (only the data model + minimal commands).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .authority import (
    CaseSAuthorityProfile,
    evaluate_receipt_assurance,
    evaluate_receipt_validity,
)
from .baseline import (
    B_CORRUPT,
    B_LOST,
    B_MISSING,
    B_OK,
    B_STALE,
    BaselineRecoveryRequired,
    load_baseline,
    persist_baseline,
)
from .canonical import sha256_canonical
from .fold import FoldResult, candidate_set_for_reclassify, derived_complete, fold_subject
from .observe import (
    COVERED,
    classify_change,
    compute_profile_identity,
    compute_workspace_baseline,
    index_receipt_effect_bindings,
    load_observation_profile,
    ObservedChange,
)
from .reingest import (
    V_INVALID,
    V_UNCLASSIFIED,
    V_UNKNOWN,
    V_VALID,
    reingest_events_dir,
)
from .store import DEFAULT_EVENTS_DIR, write_event
from .subtypes import SubtypeRegistry, DEFAULT_REGISTRY_PATH

# R5: .blackbox/** fixed exclusion is enforced BY CODE, in addition to the
# mutable profile's fixed_exclusions content.
CODE_FIXED_EXCLUSIONS = (".blackbox/**",)


def _strict_reingest(events_dir: Path, reg: SubtypeRegistry):
    """Strict re-ingest of the controlled events corpus (no silent skip)."""
    return reingest_events_dir(events_dir, reg)


def _events_from_verdicts(verdicts) -> list[dict[str, Any]]:
    """Extract event dicts from VALID + UNCLASSIFIED verdicts (INVALID/UNKNOWN
    files are NOT returned — caller must treat corpus as fail-closed)."""
    return [v.event for v in verdicts if v.status in (V_VALID, V_UNCLASSIFIED) and v.event is not None]


def _corpus_health(result) -> tuple[list, list, bool, str]:
    """Split verdicts into (blocking, evidence-only) + corpus-layout block."""
    blocking = [
        v for v in result.verdicts
        if v.status in (V_INVALID, V_UNKNOWN)
    ]
    evidence = [
        v for v in result.verdicts
        if v.status in (V_VALID, V_UNCLASSIFIED)
    ]
    return blocking, evidence, result.corpus_blocked, result.corpus_reason


def _corpus_summary(verdicts) -> str:
    counts = {V_VALID: 0, V_UNCLASSIFIED: 0, V_INVALID: 0, V_UNKNOWN: 0}
    for v in verdicts:
        counts[v.status] += 1
    return (
        f"corpus: VALID={counts[V_VALID]}  UNCLASSIFIED={counts[V_UNCLASSIFIED]}  "
        f"INVALID={counts[V_INVALID]}  UNKNOWN={counts[V_UNKNOWN]}"
    )


def _load_profile(project_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Load the observation profile + verify its pinned self-identity.

    Returns (profile, None) or (None, sensor_error_reason)."""
    profile_path = project_root / ".blackbox" / "schema" / "observation-profile.json"
    if not profile_path.exists():
        return None, "sensor UNKNOWN: observation profile missing"
    try:
        profile = load_observation_profile(profile_path)
    except Exception as e:
        return None, f"sensor UNKNOWN: observation profile unreadable/corrupt: {e}"
    pid = compute_profile_identity(profile)
    stored = profile.get("profile_identity_sha256")
    if not stored or stored != pid:
        return (
            None,
            f"sensor STALE: observation profile self-identity mismatch "
            f"(stored={stored} computed={pid})",
        )
    return profile, None


def _sensor_delta(
    project_root: Path,
    profile: dict[str, Any],
    pid: str,
    events: list[dict[str, Any]],
    reg: SubtypeRegistry,
) -> tuple[list[dict[str, str]], list[dict[str, str]], str | None]:
    """Compare the persisted acknowledged baseline against a fresh workspace
    recompute, classifying every changed path via the REAL authority evaluator.

    Returns (delta_entries, uncovered_entries, sensor_error_reason) where each
    entry is {path, category, detail} and uncovered = non-COVERED changes.
    """
    bl = load_baseline(project_root, current_profile_identity=pid)
    if bl.status == B_CORRUPT:
        return ([], [], f"sensor UNKNOWN: {bl.reason}")
    if bl.status == B_STALE:
        return ([], [], f"sensor STALE: {bl.reason}")
    if bl.status == B_LOST:
        return ([], [], f"sensor UNKNOWN: {bl.reason}")
    if bl.status == B_MISSING:
        return ([], [], "sensor UNKNOWN: no acknowledged baseline (run observe --ack first)")

    authority = CaseSAuthorityProfile(registry=reg)
    receipt_validity = evaluate_receipt_validity(events, authority)
    # S1-F1: classification consumes the EFFECTIVE assurance map (support
    # targets resolved), not per-receipt isolated classes.
    receipt_assurance = evaluate_receipt_assurance(events, authority)
    bindings = index_receipt_effect_bindings(events)

    fresh = compute_workspace_baseline(project_root, profile, extra_exclusions=CODE_FIXED_EXCLUSIONS)
    baseline_paths = bl.baseline.path_to_post_identity
    delta: list[dict[str, str]] = []
    uncovered: list[dict[str, str]] = []
    for path in sorted(set(baseline_paths) | set(fresh)):
        ack = baseline_paths.get(path)
        cur = fresh.get(path, "absent")
        if ack == cur:
            continue
        result = classify_change(
            ObservedChange(path=path, observed_post_identity=cur),
            fresh, bindings, receipt_validity, receipt_assurance,
        )
        entry = {"path": path, "category": result.category, "detail": result.detail}
        delta.append(entry)
        if result.category != COVERED:
            uncovered.append(entry)
    return (delta, uncovered, None)


def _subject_governance_health(
    verdicts,
    reg: SubtypeRegistry,
    authority: CaseSAuthorityProfile,
) -> dict[str, list[str]]:
    """S1-F3 + S1-G2: per-subject governance health over the evidence corpus.

    UNCLASSIFIED event on a subject => explicit blocker, until a valid
    append-only reclassify/replacement RESOLVES it (S1-G2: real resolution,
    not absence-of-output-by-accident):
      U is resolved iff there exists a VALID+authorized reclassify R such
      that R.extensions.x_reclassifies == U.event_id AND
      candidate_set_for_reclassify(R) returns state == 'ok' (single valid/
      authorized replacement candidate matching R's declared id).

    Registered but UNAUTHORIZED receipt on a subject => explicit UNAUTHORIZED
    blocker (no reclassify can clear an unauthorized receipt — it's not an
    authority-on-record problem but a real receipt that fails authorization).
    Resolution is subject/scope-local; unrelated subjects not poisoned.
    Ambiguous/unresolved/invalid/unauthorized reclassify or candidate does
    NOT clear U; the original UNCLASSIFIED event remains immutable
    historical evidence.
    """
    # Index reclassify resolutions up front (S1-G2).
    by_eid = {v.event.get("event_id"): v.event for v in verdicts
              if v.event is not None and v.status in (V_VALID, V_UNCLASSIFIED)}
    events_list = list(by_eid.values())

    def _unclass_resolved(u_eid: str) -> bool:
        """True iff U has a valid+authorized reclassify R that resolves
        state==ok with a single declared-valid replacement candidate."""
        u = by_eid.get(u_eid)
        if u is None:
            return False
        u_ext = u.get("extensions", {}) if isinstance(u, dict) else {}
        u_subj = u.get("subject")
        if not isinstance(u_ext, dict) or not isinstance(u_subj, str):
            return False
        for ev in events_list:
            if ev.get("subtype") != "reclassify":
                continue
            if ev.get("subject") != u_subj:
                continue
            ext = ev.get("extensions", {})
            if not isinstance(ext, dict):
                continue
            if ext.get("x_reclassifies") != u_eid:
                continue
            # R must be valid+authorized (it's in by_eid if VALID; check
            # authority too).
            authorized, _ = authority.is_authorized(ev)
            if not authorized:
                continue
            cs = candidate_set_for_reclassify(events_list, ev.get("event_id"),
                                              reg=reg, authority=authority)
            if cs.state == "ok":
                return True
        return False

    blockers: dict[str, list[str]] = {}
    for v in verdicts:
        ev = v.event
        if ev is None:
            continue
        subj = ev.get("subject")
        if not isinstance(subj, str) or not subj:
            continue
        if v.status == V_UNCLASSIFIED:
            u_eid = ev.get("event_id")
            if isinstance(u_eid, str) and _unclass_resolved(u_eid):
                # Append-only reclassify resolved U: blocker is cleared for
                # this subject. The original UNCLASSIFIED event remains
                # immutable historical evidence.
                continue
            blockers.setdefault(subj, []).append(
                f"UNCLASSIFIED receipt {ev.get('event_id')} "
                f"(subtype={ev.get('subtype')!r}) on this subject: explicit "
                "non-green until a valid append-only reclassify/replacement "
                "resolves it"
            )
        elif v.status == V_VALID:
            authorized, reason = authority.is_authorized(ev)
            if not authorized:
                blockers.setdefault(subj, []).append(
                    f"UNAUTHORIZED receipt {ev.get('event_id')} "
                    f"(actor={ev.get('actor')!r} subtype={ev.get('subtype')!r}): "
                    f"{reason}"
                )
    return blockers


def cmd_write(args: argparse.Namespace) -> int:
    with open(args.event_file, "rb") as f:
        raw = f.read()
    events_dir = Path(args.root) / ".blackbox" / "events"
    try:
        ev = write_event(raw, events_dir=events_dir)
    except Exception as e:
        print(f"WRITE_REJECT: {e}", file=sys.stderr)
        return 2
    if ev.get("_bbx_unclassified") == "UNCLASSIFIED":
        print(
            f"UNCLASSIFIED_ACCEPTED: event_id={ev['event_id']} reason={ev['_bbx_unclassified_reason']}",
            file=sys.stderr,
        )
    else:
        print(f"WRITE_OK: event_id={ev['event_id']} content_hash={ev['content_hash']}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    events_dir = Path(args.project_root) / ".blackbox" / "events"
    try:
        reg = SubtypeRegistry.load()
    except Exception as e:
        print(f"REGISTRY_LOAD_ERROR: {e}", file=sys.stderr)
        return 1
    result = _strict_reingest(events_dir, reg)
    if not result.verdicts:
        print("NO_EVENTS")
        return 0
    for v in result.verdicts:
        if v.status == V_VALID:
            print(f"VALID: {v.path}  {v.event.get('event_id')}")
        else:
            print(f"{v.status}: {v.path}  {v.reason}")
    print(_corpus_summary(result.verdicts))
    if result.corpus_blocked:
        print(f"CORPUS_BLOCKED: {result.corpus_reason}")
    blocking, _, corpus_blocked, _ = _corpus_health(result)
    return 2 if (blocking or corpus_blocked) else 0


def _fail_closed_report(blocking, corpus_blocked, corpus_reason, sensor_err, verdicts) -> int:
    print("T3_BLOCKED: fail-closed; no green state can be derived")
    for v in blocking:
        print(f"  {v.status}: {v.path}  {v.reason}")
    if corpus_blocked:
        print(f"  CORPUS_BLOCKED: {corpus_reason}")
    if sensor_err:
        print(f"  {sensor_err}")
    print(_corpus_summary(verdicts))
    print("derived_complete=UNKNOWN (T3 blocked; fail-closed)")
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    try:
        reg = SubtypeRegistry.load()
    except Exception as e:
        print(f"REGISTRY_LOAD_ERROR: {e}", file=sys.stderr)
        return 1

    # ---- T3 (mandatory before reporting any derived state) ----
    # Part 1: strict corpus re-ingest (fail-closed on INVALID/UNKNOWN +
    # layout/collision violations).
    events_dir = project_root / ".blackbox" / "events"
    result = _strict_reingest(events_dir, reg)
    blocking, _evidence, corpus_blocked, corpus_reason = _corpus_health(result)

    # Part 2: sensor health + baseline-vs-workspace delta.
    profile, sensor_err = _load_profile(project_root)
    delta: list[dict[str, str]] = []
    uncovered: list[dict[str, str]] = []
    if sensor_err is None and not blocking and not corpus_blocked:
        events = _events_from_verdicts(result.verdicts)
        pid = compute_profile_identity(profile)
        delta, uncovered, sensor_err = _sensor_delta(project_root, profile, pid, events, reg)

    if blocking or corpus_blocked or sensor_err:
        return _fail_closed_report(blocking, corpus_blocked, corpus_reason, sensor_err, result.verdicts)

    # ---- T3 passed: report sensor delta + per-subject derived state ----
    print(_corpus_summary(result.verdicts))
    events = _events_from_verdicts(result.verdicts)
    authority = CaseSAuthorityProfile(registry=reg)
    receipt_assurance = evaluate_receipt_assurance(events, authority)

    exit_code = 0
    for entry in delta:
        print(f"sensor: {entry['path']}  category={entry['category']}  detail={entry['detail']}")
    if uncovered:
        exit_code = 2

    # S1-F3: per-subject governance health BEFORE derived COMPLETE.
    blockers = _subject_governance_health(result.verdicts, reg, authority)

    # paths bound to which subjects (for relevance of sensor blocks)
    bindings = index_receipt_effect_bindings(events)
    path_subjects: dict[str, set[str]] = {}
    for path, evs in bindings.items():
        for ev in evs:
            path_subjects.setdefault(path, set()).add(ev.get("subject"))
    uncovered_paths = {e["path"]: e["category"] for e in uncovered}

    subjects = sorted(
        {ev.get("subject") for ev in events if isinstance(ev, dict) and ev.get("subject")}
    )
    # S1-G3: track every in-scope subject's governance result so the
    # aggregate exit status can be derived from the full reported corpus.
    subject_results: list[tuple[str, str, FoldResult, list[str]]] = []
    for subj in subjects:
        head, conflicts = fold_subject(events, reg, subj, authority=authority)
        rc = derived_complete(
            events, reg, subj, authority=authority, receipt_assurance=receipt_assurance
        )
        subject_blockers_for_subj: list[str] = []
        # S1-F3: relevant adverse receipts block this subject's COMPLETE.
        if rc.state == "ok" and subj in blockers:
            for b in blockers[subj]:
                print(f"subject-blocker: {subj}  {b}")
                subject_blockers_for_subj.append(b)
            rc = FoldResult(
                state="non_green",
                head_event_id=rc.head_event_id,
                detail=(
                    f"derived COMPLETE blocked by same-subject governance "
                    f"health: {len(blockers[subj])} blocker(s) (see subject-blocker lines)"
                ),
            )
            exit_code = max(exit_code, 2)
        # S1-G3: subject-blocker lines should be reported for relevant subject
        # blockers regardless of whether derived_complete() was independently
        # already non_green; do not hide evidence merely because another gate
        # failed first. If blockers exist for this subject, print them now and
        # ensure the subject's printed state is non_green with the correct
        # detail.
        if subj in blockers and rc.state == "ok":
            # (above branch handled it; defensive no-op here)
            pass
        elif subj in blockers:
            # Subject is already non_green for some other reason; still print
            # the subject-blocker lines so evidence is not hidden.
            for b in blockers[subj]:
                if b not in subject_blockers_for_subj:
                    print(f"subject-blocker: {subj}  {b}")
                    subject_blockers_for_subj.append(b)
        # R2: sensor non-COVERED state on a governed path bound to this task
        # blocks its derived COMPLETE.
        relevant = sorted(p for p in uncovered_paths if subj in path_subjects.get(p, ()))
        if rc.state == "ok" and relevant:
            cats = sorted({uncovered_paths[p] for p in relevant})
            rc = FoldResult(
                state="non_green",
                head_event_id=rc.head_event_id,
                detail=(
                    f"derived COMPLETE blocked by sensor state on path(s) bound to "
                    f"this task: {relevant} categories={cats}"
                ),
            )
            exit_code = max(exit_code, 2)
        print(
            f"subject={subj}  head={head}  conflicts={len(conflicts)}  derived_complete={rc.state} ({rc.detail})"
        )
        subject_results.append((subj, head or "", rc, subject_blockers_for_subj))

    # S1-G3: aggregate exit code MUST be non-zero (rc=2) when ANY in-scope
    # subject reported by status is governance-non-green / derived COMPLETE
    # non-green (or has reported subject-blockers). Per-subject output remains
    # isolated/accurate; this only adjusts the aggregate process status.
    any_non_green = any(rc.state != "ok" or bool(blk)
                        for _, _, rc, blk in subject_results)
    if any_non_green:
        exit_code = max(exit_code, 2)
    for ev in events:
        ext = ev.get("extensions", {})
        if isinstance(ext, dict) and ext.get("x_reclassifies"):
            cs = candidate_set_for_reclassify(events, ev.get("event_id"), reg=reg, authority=authority)
            print(
                f"reclassify={ev.get('event_id')}  state={cs.state}  candidates={cs.candidate_ids}  detail={cs.detail}"
            )
    return exit_code


def _as_of_input_identity(verdicts) -> str:
    """Identity of the ingested event corpus: sha256 over sorted event_ids."""
    eids = sorted(
        v.event.get("event_id") for v in verdicts if v.status in (V_VALID, V_UNCLASSIFIED) and v.event
    )
    payload = ",".join(eids)
    return sha256_canonical(payload)


def _fresh_paths(project_root: Path, profile: dict[str, Any]) -> dict[str, str]:
    return compute_workspace_baseline(project_root, profile, extra_exclusions=CODE_FIXED_EXCLUSIONS)


def _uncovered_without_baseline(
    project_root: Path,
    profile: dict[str, Any],
    events: list[dict[str, Any]],
    reg: SubtypeRegistry,
) -> list[dict[str, str]]:
    """S1-F2: classify every governed path DIRECTLY against the receipt/effect
    bindings (no baseline diff — used when no trustworthy baseline exists).

    Every governed path must be COVERED by a valid authorized receipt with an
    effective-assurance binding matching the current post_identity; anything
    else is uncovered evidence that blocks first-ack/recovery.
    """
    fresh = _fresh_paths(project_root, profile)
    authority = CaseSAuthorityProfile(registry=reg)
    validity = evaluate_receipt_validity(events, authority)
    assurance = evaluate_receipt_assurance(events, authority)
    bindings = index_receipt_effect_bindings(events)
    uncovered: list[dict[str, str]] = []
    for path in sorted(fresh):
        result = classify_change(
            ObservedChange(path=path, observed_post_identity=fresh[path]),
            fresh, bindings, validity, assurance,
        )
        if result.category != COVERED:
            uncovered.append({
                "path": path, "category": result.category, "detail": result.detail,
            })
    return uncovered


def cmd_observe(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    profile, sensor_err = _load_profile(project_root)
    if profile is None:
        print(sensor_err, file=sys.stderr)
        return 2

    try:
        reg = SubtypeRegistry.load()
    except Exception as e:
        print(f"REGISTRY_LOAD_ERROR: {e}", file=sys.stderr)
        return 1

    # strict re-ingest (fail-closed corpus)
    events_dir = project_root / ".blackbox" / "events"
    result = _strict_reingest(events_dir, reg)
    blocking, _evidence, corpus_blocked, corpus_reason = _corpus_health(result)

    pid = compute_profile_identity(profile)

    # S1-F2: baseline health gates the whole observe run.
    bl = load_baseline(project_root, current_profile_identity=pid)
    sensor_err: str | None = None
    if bl.status == B_CORRUPT:
        sensor_err = f"sensor UNKNOWN: {bl.reason}"
    elif bl.status == B_STALE:
        sensor_err = f"sensor STALE: {bl.reason}"
    elif bl.status == B_LOST:
        sensor_err = f"sensor UNKNOWN: {bl.reason}"

    events = _events_from_verdicts(result.verdicts)
    delta: list[dict[str, str]] = []
    uncovered: list[dict[str, str]] = []
    if sensor_err is None:
        if bl.status == B_MISSING:
            sensor_err = "sensor UNKNOWN: no acknowledged baseline (run observe --ack first)"
        else:
            delta, uncovered, sensor_err = _sensor_delta(project_root, profile, pid, events, reg)

    # S1-F2: when no trustworthy baseline exists (LOST/CORRUPT/STALE), ack/
    # recovery decisions must still classify current bytes against receipt
    # bindings — recovery cannot silently bless unresolved mutations.
    binding_uncovered: list[dict[str, str]] = []
    if bl.status in (B_LOST, B_CORRUPT, B_STALE) and (args.ack or args.rebaseline) and not blocking and not corpus_blocked:
        binding_uncovered = _uncovered_without_baseline(project_root, profile, events, reg)

    for entry in delta:
        tag = "NEW" if entry["path"] not in {e["path"] for e in uncovered} else entry["category"]
        print(f"{tag}: {entry['path']}  category={entry['category']}  detail={entry['detail']}")
    for v in blocking:
        print(f"{v.status}: {v.path}  {v.reason}")
    if corpus_blocked:
        print(f"CORPUS_BLOCKED: {corpus_reason}")
    if sensor_err:
        print(f"{sensor_err}")
    print(_corpus_summary(result.verdicts))

    exit_code = 0
    if uncovered or blocking or corpus_blocked or sensor_err:
        exit_code = 2
    if args.ack:
        if blocking or corpus_blocked:
            print("ACK_REJECTED: corpus contains INVALID/UNKNOWN/layout-violation events; fix first", file=sys.stderr)
            exit_code = max(exit_code, 2)
        elif sensor_err is not None and bl.status != B_MISSING:
            # S1-F2: ordinary ack must not overwrite/recover corrupt/stale/lost
            print(
                f"ACK_REJECTED: baseline is {bl.status}; ordinary acknowledge must "
                "not overwrite/recover it — use explicit recovery if the workspace "
                "has been reconciled",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
        elif uncovered or binding_uncovered:
            print(
                "ACK_REJECTED: non-COVERED workspace changes present; reconcile them first "
                "(acknowledging would silently legalize an orphan)",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
        else:
            try:
                persist_baseline(project_root, pid, _as_of_input_identity(result.verdicts), _fresh_paths(project_root, profile))
            except BaselineRecoveryRequired as e:
                print(f"ACK_REJECTED: {e}", file=sys.stderr)
                exit_code = max(exit_code, 2)
            else:
                print(f"BASELINE_ACKNOWLEDGED: profile_identity={pid} paths={len(_fresh_paths(project_root, profile))}")
                exit_code = 0  # successful first ack resolves the no-baseline UNKNOWN
    elif args.rebaseline:
        # S1-F2 explicit recovery: requires reconciled workspace (no
        # non-COVERED changes vs receipts) and a clean corpus; it overwrites
        # the corrupt/stale/lost artifact with an audited fresh acknowledgement
        # — current bytes are NOT blessed as previously-covered: every path is
        # re-derived fresh, and any path whose state has no matching eligible
        # receipt binding still shows as uncovered (recovery refuses in that
        # case).
        if blocking or corpus_blocked:
            print("REBASELINE_REJECTED: corpus contains INVALID/UNKNOWN/layout-violation events; fix first", file=sys.stderr)
            exit_code = max(exit_code, 2)
        elif uncovered or binding_uncovered:
            print(
                "REBASELINE_REJECTED: non-COVERED workspace changes present; "
                "recovery cannot silently bless unresolved mutations — reconcile "
                "(receipts/backfill) first",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
        else:
            try:
                persist_baseline(
                    project_root, pid, _as_of_input_identity(result.verdicts),
                    _fresh_paths(project_root, profile), allow_recovery=True,
                )
            except BaselineRecoveryRequired as e:
                print(f"REBASELINE_REJECTED: {e}", file=sys.stderr)
                exit_code = max(exit_code, 2)
            else:
                print(f"BASELINE_RECOVERED: profile_identity={pid} paths={len(_fresh_paths(project_root, profile))}")
                exit_code = 0  # successful recovery resolves the sensor UNKNOWN/STALE/LOST
    return exit_code


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    bb = root / ".blackbox"
    (bb / "events").mkdir(parents=True, exist_ok=True)
    schema = bb / "schema"
    schema.mkdir(parents=True, exist_ok=True)
    # Product CLI materializes governed resources from package data.  This
    # lower-level directory initializer must never reset their bytes.
    print(f"INIT_OK: {bb}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bbx", description="Blackbox vNext Slice 1 reference")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init")
    sp.add_argument("project_root")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("write")
    sp.add_argument("event_file")
    sp.add_argument("--root", default=".", help="blackbox project root (its .blackbox/events is the target)")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("validate")
    sp.add_argument("project_root")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("status")
    sp.add_argument("project_root")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("observe")
    sp.add_argument("project_root")
    sp.add_argument("--ack", action="store_true",
                    help="acknowledge the freshly recomputed workspace baseline")
    sp.add_argument("--rebaseline", action="store_true",
                    help="S1-F2 explicit recovery: rebuild baseline after "
                         "loss/corruption once the workspace is reconciled")
    sp.set_defaults(func=cmd_observe)

    sp = sub.add_parser("resume")
    sp.add_argument("project_root")
    sp.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

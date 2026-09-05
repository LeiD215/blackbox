# Blackbox vNext operator contract

Blackbox is an opt-in project recording and provenance mechanism, not generic
IAM or workflow policy. Do not activate it merely because work is long or
multi-agent: the user or project must explicitly enable Blackbox vNext.

## Canonical operation

Use `blackbox-vnext init --root <project>` once, then operate on that project
root. Formal records belong in `.blackbox/`; Markdown, STATUS, handoffs and
tool narration are projection, context or legacy material, never formal
authority. `write` validates before accepting a record; `validate`, `status`,
`observe`, `resume`, checkpoint and verification operations fail closed on
unknown, corrupt, stale, conflicted or unauthorized input. Core operation does
not require Git.

Formal acceptance is part of completion, not a later narrative clean-up:

- **T1 — before effect:** record a formal mutation or dispatch canonically
  before relying on its governed effect.
- **T2 — before upgrade:** verification, completion, acceptance and release
  claims require their canonical receipt/gate conditions before any green
  upgrade.
- **T3 — freshness:** reconcile/re-ingest and refresh observations before
  deriving current status.

Claim Is Not Fact: an executor claim, including self-verification, cannot
self-upgrade to independent fact. Receipt or Orphan: a claimed governed effect
without an eligible canonical receipt remains an orphan/non-green condition.
UNKNOWN, CONFLICT, STALE and non-independent evidence never inherit green.

## Migration and governance

`v1.0.0` and `legacy/v1` preserve v1 exactly. Cut over only through an explicit
vNext record; do not parse legacy prose into invented typed history and do not
maintain long-term dual-write current state. Major shared Blackbox rule changes
require user/project governance approval; an agent must not silently rewrite
them. See README and the CLI help for explicit required inputs rather than
fabricating defaults.

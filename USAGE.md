# Blackbox v2.0.0 — Usage Guide

**Scope.** This guide explains how to *use* the released Blackbox v2.0.0 reference
runtime end to end: install it, initialize a project, record canonical events,
understand derived state, verify work, checkpoint, and run the pre-release gate.
It is written for a technically competent user, not for someone implementing the
protocol. For the exact wire format and canonicalization rules, see
`.blackbox/FORMAT` (frozen with the product) and the schemas in
`schemas/`. For the operator contract and design rationale, see [`SKILL.md`](./SKILL.md)
and [`PRODUCT-RUNTIME.md`](./PRODUCT-RUNTIME.md).

> Read this first if you are new: sections 1–5 give the model and a complete
> walkthrough. Sections 6–12 explain each concept and the failure modes.
> Section 15 is a compact command index.

---

## 1. What Blackbox is — and is not

Blackbox v2.0.0 (vNext) is a **portable project recording and provenance
mechanism**. It keeps a canonical, append-only history of formal records
("events") for a project, derives current state from that history by folding,
and fails closed when the history is unknown, corrupt, stale, conflicted, or
unauthorized.

The two invariants that define the product:

- **Claim Is Not Fact.** An executor's claim — including a self-verification —
  cannot upgrade itself into independent fact. A claimed governed effect
  without an eligible canonical receipt is an orphan / non-green condition.
- **Unknown Never Inherits Green.** `UNKNOWN`, `CONFLICT`, `STALE` and
  non-independent evidence never produce a green derived state.

Blackbox is **not**:

- an IAM / authorization system (it records and derives state; it does not
  enforce organizational authority);
- a task scheduler or workflow engine (it records dispatches and completions);
- a Git replacement (core operation does not require Git);
- a "second source of truth" for prose (Markdown, STATUS, handoffs are context
  or projection, never a second writable canonical source).

---

## 2. Installation

The package is **not published to PyPI**. The supported install path is from the
repository. Requires **Python ≥ 3.11**; the runtime has no third-party
dependencies.

From a clone of the repository:

```text
git clone https://github.com/LeiD215/blackbox.git
cd blackbox            # default branch vnext (v2.0.0)
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install .
blackbox-vnext --help
```

macOS / Linux:

```bash
source .venv/bin/activate
python -m pip install .
blackbox-vnext --help
```

The console entry point is `blackbox-vnext`. You can also run the same CLI with
`python -m blackbox_vnext` from an environment where the package is installed.

Verify the install:

```powershell
blackbox-vnext --help        # prints the top-level command list
python -m pip show blackbox-vnext   # Version: 2.0.0
```

> If you installed from a source checkout, the event/observation schemas, the
> subtype registry and the observation profile are governed frozen bytes; they
> are copied into every project at `init` time (section 3) and any drift is
> detected and blocks green state.

---

## 3. Initialize a project

Pick a project directory (it can be empty, and it does not need to be a Git
repository).

```powershell
mkdir C:\work\myproject
blackbox-vnext init --root C:\work\myproject
```

Output:

```text
INIT_OK: C:\work\myproject\.blackbox
```

`init` creates a portable project-local record area `.blackbox/` containing:

| Path | What it is |
|---|---|
| `.blackbox/events/` | canonical event files, one per accepted event (`evt-<id>.json`) — durable record |
| `.blackbox/schema/` | governed schemas / subtype registry / observation profile (frozen, machine-checked) |
| `.blackbox/authority/bootstrap.json` | bootstrap authority profile (which principal may author what) |
| `.blackbox/FORMAT` | frozen wire/canonicalization spec (copy of `src/blackbox_vnext/data/FORMAT.md`) |
| `.blackbox/checkpoints/`, `.blackbox/projections/`, `.blackbox/recovery/` | runtime areas (created at init) |

`init` is idempotent (running it again succeeds). If the governed resources in
an existing project differ from the package bytes, `init` refuses with
`GOVERNED-RESOURCE-DRIFT` instead of silently rewriting them.

> Run `init` before using `status`/`observe`: `write` will create the events
> directory if it is missing, but without `init` the observation profile and
> schemas are absent, and `status`/`observe` fail closed with
> `sensor UNKNOWN: observation profile missing`.

---

## 4. Core command quick start

All commands take `--root <project>` (default: current directory), except
`validate-receipt`, which takes a receipt file path.

| When you want to… | Use |
|---|---|
| Record a canonical event (dispatch, result, completion, …) | `blackbox-vnext write <event.json> --root <project>` |
| Record an executor self-verification as a canonical support record | `blackbox-vnext self-verify --root <project> --event <event.json>` |
| Re-ingest the whole event corpus and check every file | `blackbox-vnext validate --root <project>` |
| Compare the workspace against the acknowledged baseline | `blackbox-vnext observe --root <project>` |
| Acknowledge a clean fresh baseline / recover a lost baseline | `observe --ack` / `observe --rebaseline` |
| Derive current per-subject state (T3 + fold) | `blackbox-vnext status --root <project>` |
| Write a derived, non-authority recovery index | `blackbox-vnext checkpoint --root <project>` |
| Re-derive status after a break / cross-session handoff | `blackbox-vnext resume --root <project>` |
| Produce an independent-verification receipt for an artifact | `blackbox-vnext independent-verify …` |
| Validate an independent-verification receipt file | `blackbox-vnext validate-receipt <receipt.json>` |
| Run the pre-release/acceptance gate | `blackbox-vnext pre-release-check --root <project> --requirements <reqs.json> …` |

Details and exact syntax for every command are in the sections below.

---

## 5. First end-to-end example

This example is intentionally small but complete: it authorizes a task, records
execution and completion, self-verifies, acknowledges the observation baseline,
and derives `COMPLETE`. All JSON below was validated against the v2.0.0
implementation.

### 5.1 Prepare the project and a workspace artifact

```powershell
mkdir C:\work\demo ; cd C:\work\demo
blackbox-vnext init --root .
Set-Content -Path .\docs\release-notes.md -Value "# v2.0.0 release notes" -Encoding UTF8
```

### 5.2 Dispatch — the human authorizes a task

`dispatch.json` (a `TASK / dispatch / claim`, actor `human:owner`):

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-dddddddddddddddddddddddddddddddd",
  "content_hash": "sha256:efb47f0713b3b43a8237b4e28272739c40a138241d8848af9fc0c662e7944a83",
  "actor": "human:owner",
  "type": "TASK",
  "subtype": "dispatch",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:00:00Z",
  "prior_refs": { "parent": [], "supports": [] },
  "extensions": { "dispatch": { "assignee": "executor:worker", "surface": "project:reference" } },
  "body": "Authorize writing v2.0.0 release notes"
}
```

How was `content_hash` computed? It is the SHA-256 (RFC 8785 canonical JSON, for
the event object **without** the `content_hash` key) — see `.blackbox/FORMAT`,
section F.4. From Python:

```python
from blackbox_vnext.canonical import sha256_canonical
event = {
  "schema_version": "0.3.2",
  "event_id": "evt-dddddddddddddddddddddddddddddddd",
  "actor": "human:owner",
  "type": "TASK",
  "subtype": "dispatch",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:00:00Z",
  "prior_refs": {"parent": [], "supports": []},
  "extensions": {"dispatch": {"assignee": "executor:worker", "surface": "project:reference"}},
  "body": "Authorize writing v2.0.0 release notes",
}
print(sha256_canonical(event))   # sha256:efb47f0713b3b43a8237b4e28272739c40a138241d8848af9fc0c662e7944a83
```

Accept it:

```powershell
blackbox-vnext write .\dispatch.json --root .
# WRITE_OK: event_id=evt-dddddddddddddddddddddddddddddddd content_hash=sha256:efb47f07...
```

### 5.3 Execution result — the executor records the effect

`execution-result.json` (`TASK / execution-result / claim`, actor `executor:worker`,
parent = the dispatch):

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "content_hash": "sha256:f4fd67458633084b36a34dbaf10e8994bbf64cc58833fa76a0200f9b15e3070a",
  "actor": "executor:worker",
  "type": "TASK",
  "subtype": "execution-result",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:05:00Z",
  "prior_refs": { "parent": ["evt-dddddddddddddddddddddddddddddddd"], "supports": [] },
  "extensions": { "effect": [ { "change": "added", "path": "docs/release-notes.md", "post_identity": "sha256:0000000000000000000000000000000000000000000000000000000000000000" } ] },
  "body": "Drafted v2.0.0 release notes"
}
```

```powershell
blackbox-vnext write .\execution-result.json --root .
```

### 5.4 Completion claim

`completion-claim.json` (`TASK / completion-claim / claim`, same subject,
parent = the execution result):

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-ffffffffffffffffffffffffffffffff",
  "content_hash": "sha256:c84868bf1add73cf77552fd6d43437a20efff84c692d047c58fb970ab7aa4d83",
  "actor": "executor:worker",
  "type": "TASK",
  "subtype": "completion-claim",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:10:00Z",
  "prior_refs": { "parent": ["evt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"], "supports": [] },
  "extensions": { "effect": [ { "change": "added", "path": "docs/release-notes.md", "post_identity": "sha256:0000000000000000000000000000000000000000000000000000000000000000" } ] },
  "body": "v2.0.0 release notes complete"
}
```

```powershell
blackbox-vnext write .\completion-claim.json --root .
```

### 5.5 Self-verification

`self-verify.json` (`VERIFICATION / self-verification / self-verification`,
actor `executor:worker`, `supports` = the completion claim):

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-99999999999999999999999999999999",
  "content_hash": "sha256:1ef7b4f559798acf402b63b96da9d210752dbedb46162ec5960497e4aaaa6b0c",
  "actor": "executor:worker",
  "type": "VERIFICATION",
  "subtype": "self-verification",
  "receipt_class": "self-verification",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:12:00Z",
  "prior_refs": { "parent": [], "supports": ["evt-ffffffffffffffffffffffffffffffff"] },
  "extensions": {},
  "body": "Self-verified release notes content"
}
```

```powershell
blackbox-vnext self-verify --root . --event .\self-verify.json
# WRITE_OK: event_id=evt-99999999999999999999999999999999 content_hash=sha256:1ef7b4f5...
```

### 5.6 Validate, acknowledge the baseline, and read status

```powershell
blackbox-vnext validate --root .
# VALID: ...\events\evt-9999...  evt-99999999999999999999999999999999
# VALID: ...\events\evt-dddd...  evt-dddddddddddddddddddddddddddddddd
# VALID: ...\events\evt-eeee...  evt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
# VALID: ...\events\evt-ffff...  evt-ffffffffffffffffffffffffffffffff
# corpus: VALID=4  UNCLASSIFIED=0  INVALID=0  UNKNOWN=0

blackbox-vnext observe --root . --ack
# sensor UNKNOWN: no acknowledged baseline (run observe --ack first)
# corpus: VALID=4  UNCLASSIFIED=0  INVALID=0  UNKNOWN=0
# BASELINE_ACKNOWLEDGED: profile_identity=sha256:ffcc14e20126c2ccd1e2f846a606fc20e6aadf3a25f3c64d7170ae4fb4282d7f paths=0

blackbox-vnext status --root .
# corpus: VALID=4  UNCLASSIFIED=0  INVALID=0  UNKNOWN=0
# subject=task:release-notes  head=evt-ffffffffffffffffffffffffffffffff  conflicts=0
#   derived_complete=ok (derived COMPLETE; head=evt-ffff...; VERIFIED-SELF=evt-9999...)
```

`derived_complete=ok` is the green state: the subject's history folds (no
conflicts), the head is a completion claim, and an effective self-verification
supports it. Note that this is **derived**: no event ever claims "COMPLETE" —
the CLI derives it from the canonical history.

### 5.7 Checkpoint and resume

```powershell
blackbox-vnext checkpoint --root .
# {"checkpoint_id": "cp-e3f73547d2805a0282a964b1b6197ae4d46bba08fb7bde5c2286c2afaedc60ab", "ok": true, "out": "...\.blackbox\checkpoint.json"}

blackbox-vnext resume --root .
# same output as status (resume is an alias for the status command)
```

---

## 6. Claims, evidence, and verification

- **Claim.** Any canonical event authored by a principal. Claims are how
  dispatches, execution results, completions, acceptances, changes, and
  observations enter the record. Claims are *assertions*, not facts.
- **Evidence.** Machine-readable effect bindings live in
  `extensions.effect` (`path` + `post_identity`), and supporting references
  live in `prior_refs.supports`. Receipts (independent verification events)
  bind an observed artifact identity to the requirement they evidence.
- **Self-verification** (`VERIFICATION / self-verification`) is an executor's
  own support record. It can contribute to derived `COMPLETE` for the task it
  supports, but it can never claim independence.
- **Independent verification** is a readback performed by a verifier who is
  not the executor, over a separate source, within a bounded age; it produces
  an `independent-verification` receipt (section 11). Only that can satisfy the
  pre-release gate (section 10).

Why an executor reporting PASS is not enough: the system treats the report as a
claim. `pre-release-check` and `status` only trust what the canonical history
and an eligible receipt establish — never the wording of a report.

---

## 7. Status / derived-state interpretation

`status` first performs T3 (strict corpus re-ingest + sensor freshness) and
fails closed if anything is unknown/corrupt, then folds each subject.

Observable outcomes (all verified against v2.0.0):

| Output | Meaning | Typical cause / next step |
|---|---|---|
| `corpus: VALID=N ...` | the strict re-ingest summary | healthy corpus: VALID count, zero INVALID/UNKNOWN |
| `derived_complete=ok (derived COMPLETE; head=…; VERIFIED-SELF=…)` | green state for a subject | keep going / hand off |
| `T3_BLOCKED: fail-closed; no green state can be derived` | a gate blocked the whole project | read the listed blockers below it |
| `sensor UNKNOWN: no acknowledged baseline (run observe --ack first)` | observation profile exists but no baseline | run `observe --ack` on a reconciled workspace |
| `sensor UNKNOWN: observation profile missing` | project was not `init`ed (or profile deleted) | run `init` |
| `INVALID: <file> content_hash mismatch …` | a stored event was tampered with or corrupted | restore from checkpoint/backup; do not hand-edit canonical files |
| `ORPHAN: <path> category=ORPHAN` | a workspace file changed with no matching receipt binding | reconcile (record the change) or revert the file |
| `subject-blocker: <subject> <reason>` | per-subject governance blocker (e.g. `UNCLASSIFIED receipt …`, `UNAUTHORIZED receipt …`) | resolve per the reason, or record a corrective event |

Rules to remember:

- `UNKNOWN`, `STALE`, `CONFLICT`, `ORPHAN`, and non-independent evidence never
  inherit green.
- A registered but unauthorized receipt is an explicit blocker (it is not an
  unclassified event).
- An unregistered subtype is `UNCLASSIFIED` / no-effect historical evidence
  until an append-only `reclassify` + replacement path resolves it.

---

## 8. Observation / drift use

`observe` compares the fresh workspace recompute against the persisted
acknowledged baseline, using the **observation profile**
(`.blackbox/schema/observation-profile.json`, seeded at `init`; `.blackbox/**`
is a fixed exclusion and cannot be overridden).

```powershell
blackbox-vnext observe --root <project>            # report current delta
blackbox-vnext observe --root <project> --ack      # acknowledge a clean fresh baseline
blackbox-vnext observe --root <project> --rebaseline  # explicit recovery of corrupt/stale/lost baseline
```

- On a fresh project the first step is `observe --ack`; until then status is
  `sensor UNKNOWN`.
- Any workspace change not covered by a matching receipt binding is reported as
  an `ORPHAN` (non-covered change). `status` then exits non-zero, and `--ack`
  is **rejected** — acknowledging would silently legalize an orphan. Reconcile
  (record a receipt/effect binding for the change) or revert the file, then
  ack.
- `--rebaseline` overwrites a corrupt/stale/lost baseline only after the
  workspace is reconciled; it refuses while non-covered changes remain.
- If the observation profile itself changes, the baseline becomes
  `STALE`/`INVALID` and must be recovered explicitly.

---

## 9. Checkpoint and resume

- **Checkpoint.** Run `blackbox-vnext checkpoint --root <project>` to write a
  derived, non-authority recovery index (default
  `.blackbox/checkpoint.json`): an enumerable manifest of the accepted events
  (event id + content hash), the per-subject frontier, and a content-derived
  `checkpoint_id`. It is a recovery aid, **not** a source of authorization or a
  second record.
- **Resume.** `resume` is an alias for `status` — it re-runs T3 and re-derives
  current state. Use it when returning to a project after a break or from
  another session. It does not restore files or re-apply work; it re-derives
  state from the canonical history and the acknowledged baseline
  (recover the baseline with `observe --rebaseline` if it was lost while you
  were away).
- Checkpoint frequently as a cheap insurance policy; `resume` (or `status`)
  is the cross-session "where are we" command.

---

## 10. Pre-release check

`pre-release-check` is a **gate**, not a publisher: it derives an eligibility
verdict from canonical requirements + evidence receipts and never creates an
acceptance receipt.

```text
blackbox-vnext pre-release-check --root <project> --requirements <reqs.json>
    [--evidence <receipt.json> ...] [--dispatch-event-id <evt-id> ...]
```

- `--requirements` is a JSON **array** of requirement objects (fields:
  `effect_id`, `scope`, `target`, `subject`, `artifact_identity`, `input_scope`,
  `expected_identity`, `expected_algorithm`, `executor`, `claim_source`,
  `required_read_path`, `required_source_id`, `required_verifier`,
  `effect_class`, `prior_authorized`, `lifecycle_ready`, `completion_claimed`).
- High-risk effect classes (`production`, `external`, `irreversible`,
  `high-risk`, `security`, `authority-boundary`, `release`, `acceptance`)
  require **prior authorization**: pass `--dispatch-event-id` for a green
  `TASK/dispatch` event already recorded in the project whose subject/scope/
  assignee match the requirement.
- `--evidence` takes canonical **independent-verification receipt event files**
  (produced by `independent-verify`, section 11). A requirement passes only if
  a receipt matches its full identity profile.

Concrete example (continuing the demo project, gating a release artifact):

```powershell
# artifact to be released
Set-Content -Path .\release.bin -Value "green" -NoNewline -Encoding Ascii
$hash = (Get-FileHash -Algorithm SHA256 .\release.bin).Hash.ToLower()
$now  = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$obs  = [DateTime]::UtcNow.AddSeconds(-30).ToString("yyyy-MM-ddTHH:mm:ssZ")

blackbox-vnext independent-verify --root . `
  --read-root . --read-path release.bin `
  --target release.bin --subject task:release-notes `
  --artifact-identity artifact:release-notes --input-scope project:reference `
  --expected-identity "sha256:$hash" --expected-algorithm sha256 `
  --executor executor:worker --claim-source workspace:executor `
  --verifier agent:reviewer --source-id filesystem:reviewer `
  --event-id evt-c1000000000000000000000000000001 `
  --observed-at $obs --now $now --recorded-at $now --max-age-seconds 300 `
  | Set-Content .\receipt.json
```

`requirements.json`:

```json
[
  {
    "effect_id": "effect:release-notes",
    "scope": "project:reference",
    "target": "release.bin",
    "subject": "task:release-notes",
    "artifact_identity": "artifact:release-notes",
    "input_scope": "project:reference",
    "expected_identity": "sha256:<hash of release.bin>",
    "expected_algorithm": "sha256",
    "executor": "executor:worker",
    "claim_source": "workspace:executor",
    "required_read_path": "file://fixture-root/release.bin",
    "required_source_id": "filesystem:reviewer",
    "required_verifier": "agent:reviewer",
    "effect_class": "release",
    "prior_authorized": true,
    "lifecycle_ready": true,
    "completion_claimed": true
  }
]
```

```powershell
blackbox-vnext pre-release-check --root . `
  --requirements .\requirements.json `
  --evidence .\receipt.json `
  --dispatch-event-id evt-dddddddddddddddddddddddddddddddd
```

Passing result (verified):

```json
{"affected_effects": ["effect:release-notes"], "blockers": [],
 "creates_acceptance_receipt": false, "derived_non_authority": true,
 "result_id": "sha256:315d07ae5cd0a651280b043961ef808bf4e981ff73fc5110e293aba6670111f4",
 "verdict": "PASS"}
```

Notes:

- The receipt's normalized read path in the evidence is
  `file://fixture-root/<relative-path>`; the requirement's
  `required_read_path` must match it exactly (see section 11 and
  `validate-receipt`).
- A missing dispatch authorization yields `AUTHORIZATION-MISSING`; a receipt
  that does not match yields `ASSURANCE-GAP` (both verified).
- `derived_non_authority: true` and `creates_acceptance_receipt: false` remind
  you that the gate derives eligibility only — it does not perform or record
  the release.

---

## 11. Receipt validation

`validate-receipt` checks a canonical `independent-verification` event file
(envelope, content hash, discriminator, extension profile) and reports validity:

```powershell
blackbox-vnext validate-receipt .\receipt.json
# {"reason": "OK", "valid": true}
```

Use it whenever you receive or build an independent-verification receipt: it
confirms the envelope is genuine before you feed it to `pre-release-check`.
The receipt event itself is produced by `independent-verify` with this shape
(trimmed):

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-…",
  "actor": "agent:reviewer",
  "type": "VERIFICATION",
  "subtype": "independent-verification",
  "receipt_class": "independent-verification",
  "subject": "task:release-notes",
  "recorded_at": "…",
  "prior_refs": { "parent": [], "supports": [] },
  "extensions": {
    "independent_verification": {
      "algorithm": "sha256",
      "observed_identity": "sha256:…",
      "expected_identity": "sha256:…",
      "result": "PASS",
      "reason": "EXACT-INDEPENDENT-MATCH",
      "verifier": "agent:reviewer",
      "executor": "executor:worker",
      "independent_source": true,
      "transport_ok": true,
      "read_path": "file://fixture-root/release.bin",
      "source_id": "filesystem:reviewer",
      "observed_at": "…",
      "provenance": "local-read-only-filesystem",
      "evidence_digest": "sha256:…"
    }
  }
}
```

Receipts are also accepted back into the corpus as canonical events
(`VERIFICATION / independent-verification`), so they participate in the
record while never being able to fabricate independence.

---

## 12. Recovery / common failure modes

| Symptom (verified) | Cause | What to do |
|---|---|---|
| `WRITE_REJECT: JSON parse error: Expecting value at col 1` | event file is not valid JSON | fix the file, write again |
| `WRITE_REJECT: event shape invalid (missing required fields or prior_refs shape)` | event missing required envelope fields | use the schema (`schemas/event.schema.json`) |
| `WRITE_REJECT: content_hash mismatch: stored content_hash != sha256(canonical(obj - content_hash))` | `content_hash` wrong or missing | recompute with `blackbox_vnext.canonical.sha256_canonical` |
| `validate` lists `INVALID` / `status` prints `T3_BLOCKED` with `INVALID: … content_hash mismatch` | a stored event file was edited/tampered | restore the file from checkpoint/backup; canonical event files must not be hand-edited |
| `sensor UNKNOWN: no acknowledged baseline (run observe --ack first)` | baseline not yet acknowledged | run `observe --ack` |
| `sensor UNKNOWN: observation profile missing` | project not initialized | run `blackbox-vnext init --root .` |
| `ORPHAN: <path> category=ORPHAN`; `ACK_REJECTED: non-COVERED workspace changes present` | workspace change has no receipt binding | record the change (or revert it), then ack |
| `ACK_REJECTED: baseline is STALE/LOST…` | baseline corrupt/stale/lost | reconcile the workspace, then `observe --rebaseline` |
| `AUTHORIZATION-MISSING` in pre-release-check | high-risk requirement without matching green dispatch | record the dispatch and pass `--dispatch-event-id` |
| `ASSURANCE-GAP: exact independent evidence missing` | no matching independent receipt | produce/verify the receipt and pass it via `--evidence` |
| No Git repository, no network | core commands do not require Git | nothing to do; Blackbox operates on the project directory |

General recovery rules:

- Never hand-edit a canonical event file under `.blackbox/events/`; correct
  history with append-only records (e.g. `CHANGE/correction` or the
  reclassify/replacement path), never by rewriting the past.
- Back up `.blackbox/` (or rely on `checkpoint` + your normal backup) so a
  corrupt file can be restored.
- If a subject's chain is conflicted (e.g. two children of one head), resolve
  with an explicit `CHANGE/resolution` naming all conflicting heads — the
  system never picks a silent time/file-order winner.

---

## 13. Directory / file anatomy

Canonical (durable, must not be hand-edited):

```text
<project>/
└── .blackbox/
    ├── events/                       # canonical events: evt-<id>.json (append-only)
    ├── authority/bootstrap.json      # bootstrap authority profile (governed)
    ├── schema/                       # governed schema/subtype/profile bytes
    │   ├── event.schema.json
    │   ├── checkpoint.schema.json
    │   ├── manifest.schema.json
    │   ├── observation-profile.json
    │   ├── observation-profile.schema.json
    │   └── subtypes.json
    └── FORMAT                        # frozen format spec
```

Derived / runtime (rebuildable, non-authority):

```text
<project>/.blackbox/
    ├── checkpoint.json               # after checkpoint: derived recovery index
    ├── checkpoints/                  # checkpoint runtime area
    ├── projections/                  # projection runtime area
    └── recovery/                     # recovery runtime area
```

Everything under `.blackbox/**` is excluded from the observation sensor by the
fixed exclusion rule; it is the implementation's record area, not a governed
workspace path.

---

## 14. Recommended operating pattern

Blackbox records and proves; it does not decide organizational authority by
itself. A minimal orchestrator integration:

```text
dispatch ──► execution claim ──► evidence ──► verification ──► status / gate
owner        executor            effect        self-verify /   derive COMPLETE /
TASK/        TASK/               bindings      independent-    pre-release-
dispatch     execution-result                 verify          check
```

Concretely:

1. The owner records a `TASK/dispatch` event authorizing the assignee.
2. The executor records `TASK/execution-result` events with
   `extensions.effect` bindings as it changes governed paths.
3. The executor records a `TASK/completion-claim` when finished, and a
   `VERIFICATION/self-verification` support record.
4. For anything that must be proven independently (release-class effects), an
   independent verifier runs `independent-verify` to produce a receipt.
5. `status` derives per-subject state; `pre-release-check` derives the gate.
6. Only green derived state (with eligible evidence) is treated as completed —
   an executor's or verifier's report alone is never the authority.

---

## 15. Command reference table

| Command | Purpose | Typical input | Typical output / effect |
|---|---|---|---|
| `init --root <dir>` | create portable record area | project directory | `INIT_OK`, seeds `.blackbox/`, governed resources |
| `write <event.json> --root <dir>` | accept a canonical event | path to canonical event JSON | `WRITE_OK` (+ `event_id`, `content_hash`), stores `events/evt-<id>.json`; rejects otherwise |
| `self-verify --root <dir> --event <f>` | record an executor self-verification | canonical `VERIFICATION/self-verification` JSON | `WRITE_OK`; no independent receipt / green gate emitted |
| `validate --root <dir>` | strict re-ingest of the corpus | project root | per-file `VALID/UNCLASSIFIED/INVALID/UNKNOWN` + `corpus:` summary; exit 2 on blockers |
| `status --root <dir>` | T3 + fold derived state | project root | per-subject head/conflicts/`derived_complete`; exit 2 when fail-closed |
| `resume --root <dir>` | alias for status | project root | same as `status` |
| `observe --root <dir> [--ack|--rebaseline]` | workspace-vs-baseline delta | project root | `ORPHAN`/coverage lines; `BASELINE_ACKNOWLEDGED` / `BASELINE_RECOVERED`; ACK/RECOVER rejected while uncovered |
| `checkpoint --root <dir> [--out <f>]` | derived recovery index | project root | `{"ok": true, "checkpoint_id": "cp-…", "out": …}` |
| `independent-verify --root <dir> …` | independent readback receipt | binding + read root/path + identities + RFC3339 times | canonical receipt event on stdout; exit 2 when not independently verified |
| `validate-receipt <f>` | validate a receipt event | receipt JSON file | `{"valid": true, "reason": "OK"}` else exit 2 |
| `pre-release-check --root <dir> --requirements <f> [--evidence <f>…] [--dispatch-event-id <id>…]` | eligibility gate | requirements JSON array + receipt files | `{"verdict": "PASS"}` or `NON-GREEN` + blockers; exit 2 on NON-GREEN |

Run `blackbox-vnext <command> --help` for the authoritative option list; this
table is a convenience index, not a substitute.

---

## 16. Safety / honesty boundaries

- **Claim Is Not Fact**: a claim (including self-verification) does not become
  trusted merely because an executor reports PASS.
- **Unknown Never Inherits Green**: UNKNOWN / STALE / CONFLICT / ORPHAN and
  non-independent evidence never yield green derived state.
- Blackbox does not claim to prove external facts it did not independently
  verify; `independent-verification` is the only path that may contribute
  independent evidence, and it can never be fabricated by the corpus.
- Blackbox provides no legal, security, or IAM authority beyond the implemented
  provenance semantics: it records authorizations and derives eligibility; it
  does not enforce them.
- This guide documents the released implementation only. If documentation and
  implementation disagree, the implementation and its tests win; report such
  discrepancies rather than guessing.


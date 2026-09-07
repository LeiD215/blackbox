# Blackbox v1 to vNext compatibility and cutover matrix

## Scope

This document states the cutover matrix between Blackbox v1 (preserved at `legacy/v1` and tag `v1.0.0`) and Blackbox vNext. It records already-frozen, already-reviewed facts only. It does not promise additional migration guarantees beyond what is already verified.

## v1 preservation

- v1 lives only as preserved history. Tag `v1.0.0` (annotated) and branch `legacy/v1` both resolve to commit `011680e9cfda68e65010a4e402a269fa871ccf20`.
- v1 bytes are not silently rewritten. The vNext migration candidate preserves 16 historical product paths byte-for-byte from the v1 baseline; any evolution after the initial migration commit must be recorded explicitly in the current product manifest/evidence rather than pretending the old blob identity is still in force.

## Cutover rule

- Cut over only through an explicit vNext record (e.g. `blackbox-vnext init`, `write`, `validate`, `checkpoint`).
- There is no long-term dual-write current state. A vNext record is the canonical current state once accepted.
- vNext does not parse legacy prose (STATUS.md, CHANGELOG.md, ADR text, free-form Markdown) into invented typed verified history. Legacy text may be retained as historical context but is never treated as canonical record input.

## v1 legacy mode (does not provide vNext guarantees)

- A retained v1-style workflow, used after cutover or in parallel to vNext, is **legacy v1 mode** only.
- Legacy v1 mode does not provide vNext governance, assurance, validator, projector or independent-readback guarantees. It is suitable only as a frozen reference and as preserved historical material.

## No automatic upgrade path

- There is no automatic translation of v1 records into vNext records. v1 records are not vNext records and vice versa.
- An agent must not silently rewrite shared Blackbox rules; any major shared-rule change requires user/project governance approval.

## Branch / default-branch boundary

- `main` and `legacy/v1` and `v1.0.0` remain preserved.
- `vnext` exists as a remote branch containing the reviewed migration candidate (`e344ef1f913112d661365a2ad3cb9ddb5dec1bf6`, tree `35f8fed246008a7009dae25b4bf0c094ee5a81e3`). Its presence does not by itself change the default branch.
- Switching the default branch from `main` to `vnext`, if later approved, is a distinct adoption gate and is NOT performed by this cutover matrix.

## Release / license boundary

- `pyproject.toml` version is `2.0.0` (local v2.0.0 release-prep successor; no Git tag or GitHub Release created yet).
- The `vnext` successor tree carries BCL 1.0 at root `LICENSE`; license policy is positioned as Source Available / Community License. Qualified legal review is deferred and non-blocking per USER decision.
- A formal v2.0.0 release/tag and any default-branch switch remain separate USER gates and are NOT performed by this cutover matrix.


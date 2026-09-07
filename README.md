# Blackbox vNext

Blackbox is a project recording and provenance mechanism.  vNext makes formal governance records reliable through canonical event and receipt history; it is not a generic IAM or workflow product.

## vNext operation

Run `blackbox-vnext init --root <project>` to create the portable project-local `.blackbox/` record area.  Formal governance mutations MUST use its canonical records and validator/projector semantics.  `write` synchronously validates a record. `validate`, `status`, `checkpoint`, `observe`, `resume`, `self-verify`, `independent-verify`, and `pre-release-check` operate against the same selected project root. Core operation has no Git requirement.

Markdown is human context, projection, or frozen legacy material; it is never a second writable canonical source of truth.  There is no long-term dual write of current state. Existing projects cut over explicitly: vNext does not parse legacy prose into verified typed history.

## Compatibility and cutover

The exact v1 product remains at `v1.0.0` and `legacy/v1`. A retained v1-style workflow is **legacy v1 mode** only and does not provide vNext governance or assurance guarantees. The full cutover matrix lives at `V1-TO-VNEXT-COMPATIBILITY.md` in this tree.

## Branch state

- `main` and `legacy/v1` and `v1.0.0` are preserved as the legacy baseline (commit `011680e9cfda68e65010a4e402a269fa871ccf20`).
- `vnext` is the repository default branch (currently `1f8e3e37119a70c343781db413311b20cdda4668`, tree `7a664628eeeeea2be8803486e2832e98c7c4b01d`). The default-branch switch from `main` to `vnext` has already been performed.
- No future `main` -> `vnext` default-branch switch gate remains. Any subsequent default-branch change would be a separate USER gate.

## Release, version, license status

- `pyproject.toml` version is `2.0.0` (release candidate / prepared tree; this is a local v2.0.0 release-prep successor — no Git tag or GitHub Release exists yet).
- Root `LICENSE` now supplies Blackbox Community License 1.0 (BCL 1.0) in the local successor tree. Licensing is positioned as Source Available / Community License, not OSI Open Source.
- Qualified legal review of BCL 1.0 has not occurred; it is deferred and is non-blocking per USER decision.
- A final v2 release/tag and any remaining release-license actions remain distinct USER gates and are not performed by this README. The default-branch switch from `main` to `vnext` has already been performed and is not a remaining USER gate.

## What this `vnext` branch is and is not

This branch is the reviewed local successor branch on top of the reviewed migration candidate. It is the repository default branch. It is NOT itself a published release, a v2 release tag, or a license-decided artifact.


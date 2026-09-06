# Blackbox vNext

Blackbox is a project recording and provenance mechanism.  vNext makes formal governance records reliable through canonical event and receipt history; it is not a generic IAM or workflow product.

## vNext operation

Run `blackbox-vnext init --root <project>` to create the portable project-local `.blackbox/` record area.  Formal governance mutations MUST use its canonical records and validator/projector semantics.  `write` synchronously validates a record. `validate`, `status`, `checkpoint`, `observe`, `resume`, `self-verify`, `independent-verify`, and `pre-release-check` operate against the same selected project root. Core operation has no Git requirement.

Markdown is human context, projection, or frozen legacy material; it is never a second writable canonical source of truth.  There is no long-term dual write of current state. Existing projects cut over explicitly: vNext does not parse legacy prose into verified typed history.

## Compatibility and cutover

The exact v1 product remains at `v1.0.0` and `legacy/v1`. A retained v1-style workflow is **legacy v1 mode** only and does not provide vNext governance or assurance guarantees. The full cutover matrix lives at `V1-TO-VNEXT-COMPATIBILITY.md` in this tree.

## Branch state

- `main` and `legacy/v1` and `v1.0.0` are preserved.
- `vnext` exists as a remote branch containing the reviewed migration candidate (currently `e344ef1f913112d661365a2ad3cb9ddb5dec1bf6`, tree `35f8fed246008a7009dae25b4bf0c094ee5a81e3`). The presence of the `vnext` branch does not by itself switch the default branch.
- Switching the default branch from `main` to `vnext`, if later approved, is a distinct USER gate. It is not performed by this README.

## Release, version, license status

- `pyproject.toml` version is `2.0.0.dev0` (pre-release).
- Root `LICENSE` now supplies Blackbox Community License 1.0 (BCL 1.0) in the local successor tree. Licensing is positioned as Source Available / Community License, not OSI Open Source.
- Qualified legal review of BCL 1.0 has not occurred; it is deferred and is non-blocking per USER decision.
- A final v2 release/tag, default-branch switch, and any remaining release-license actions remain distinct USER gates and are not performed by this README.

## What this `vnext` branch is and is not

This branch is the reviewed local successor branch on top of the reviewed migration candidate. It exists so that a possible later default-branch switch can be evaluated against truthful, validated material. It is NOT itself a published release, a v2 release tag, or a license-decided artifact.

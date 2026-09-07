# Blackbox v2.0.0

[**中文版**](./README.md) · **Usage guide: [USAGE.md](./USAGE.md)**

Blackbox is a project recording and provenance mechanism. v2.0.0 (vNext) makes
formal governance records reliable through canonical event and receipt history;
it is not a generic IAM or workflow product.

## Quick start

See **[USAGE.md](./USAGE.md)** for the full end-to-end usage guide
(installation, project initialization, record/claim/verification workflow,
checkpoint/resume, receipt validation, and the pre-release gate). The rest of
this README is the product-positioning and repository-state summary.

## vNext operation

Run `blackbox-vnext init --root <project>` to create the portable
project-local `.blackbox/` record area. Formal governance mutations MUST use
its canonical records and validator/projector semantics. `write` synchronously
validates a record. `validate`, `status`, `checkpoint`, `observe`, `resume`,
`self-verify`, `independent-verify`, and `pre-release-check` operate against
the same selected project root. Core operation has no Git requirement.

Markdown is human context, projection, or frozen legacy material; it is never a
second writable canonical source of truth. There is no long-term dual write of
current state. Existing projects cut over explicitly: vNext does not parse
legacy prose into verified typed history.

## Compatibility and cutover

The exact v1 product remains at `v1.0.0` and `legacy/v1`. A retained v1-style
workflow is **legacy v1 mode** only and does not provide vNext governance or
assurance guarantees. The full cutover matrix lives at
`V1-TO-VNEXT-COMPATIBILITY.md` in this tree.

## Branch, release, version, and license status

- `main` and `legacy/v1` and `v1.0.0` are preserved as the legacy baseline
  (commit `011680e9cfda68e65010a4e402a269fa871ccf20`).
- `vnext` is the repository default branch. Its tip is
  `f26db56d0cabdfae900a3befc222547a23c3d909`
  (tree `69b582239920c71f7d0588a38d6b0d1658842abf`).
- The Git tag `v2.0.0` exists and peels to the same commit
  (`f26db56d0cabdfae900a3befc222547a23c3d909`); the GitHub Release
  `v2.0.0` is published at
  <https://github.com/LeiD215/blackbox/releases/tag/v2.0.0>.
- `pyproject.toml` version is `2.0.0`. The package is **not** published to
  PyPI; install from the repository (see [USAGE.md](./USAGE.md#installation)).
- Root `LICENSE` supplies Blackbox Community License 1.0 (BCL 1.0). Licensing
  is positioned as Source Available / Community License, not OSI Open Source.
  Qualified legal review of BCL 1.0 has not occurred; it is deferred and is
  non-blocking per USER decision.

## What this `vnext` branch is and is not

This branch is the repository default branch and the v2.0.0 released state. It
is the reviewed successor to the v1/migration baseline and is NOT a legacy
v1 artifact. The default-branch switch from `main` to `vnext` has already been
performed; no future `main` -> `vnext` switch gate remains. Any subsequent
default-branch change would be a separate USER gate.

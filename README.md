# Blackbox vNext

Blackbox is a project recording and provenance mechanism.  vNext makes formal
governance records reliable through canonical event and receipt history; it is
not a generic IAM or workflow product.

## vNext operation

Run `blackbox-vnext init --root <project>` to create the portable project-local
`.blackbox/` record area.  Formal governance mutations MUST use its canonical
records and validator/projector semantics.  `write` synchronously validates a
record. `validate`, `status`, `checkpoint`, `observe`, `resume`,
`self-verify`, `independent-verify`, and `pre-release-check` operate against
the same selected project root. Core operation has no Git requirement.

Markdown is human context, projection, or frozen legacy material; it is never a
second writable canonical source of truth.  There is no long-term dual write of
current state. Existing projects cut over explicitly: vNext does not parse
legacy prose into verified typed history.

## Compatibility

The exact v1 product remains at `v1.0.0` and `legacy/v1`. A retained v1-style
workflow is **legacy v1 mode** only and does not provide vNext governance or
assurance guarantees. See `V1-TO-VNEXT-COMPATIBILITY.md` in the migration
package for the cutover matrix.

No license is supplied with this staging tree. Public release remains blocked
until license status is explicitly resolved.

# Blackbox vNext runtime

The optional Python runtime is self-contained under `src/blackbox_vnext` and
has no third-party runtime dependency. It implements canonical records,
validation/storage/folding, offline recovery, authority separation, optional
non-authority presentation, and independent readback/acceptance eligibility.

It does not connect to production services, grant authority, or require Git.
`SKILL.md` and `assets/**` remain at their v1-compatible fixed paths.

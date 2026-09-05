"""Slice 1 reference implementation: minimal local Blackbox vNext package.

Modules (import-able; CLI entrypoint via python -m bbx):
    canonical  — RFC 8785 UTF-16 code-unit property sort + canonical bytes + sha256
    ingest     — pre-schema FORMAT F.3 input-domain constraints
    subtypes   — registry loading + UNCLASSIFIED vs invalid-combination detection
    validity   — two-layer model: valid authorized receipt vs effect eligibility
    fold       — event-set + per-subject causal DAG; B1 candidate-set rule
    project    — derived COMPLETE (non-canonical)
    observe    — observation profile + baseline + 4-way ORPHAN classification
    store      — atomic canonical write path (synchronous T3)
    cli        — entrypoint (subcommand dispatch)

Zero external dependencies; Python stdlib only.
"""
__version__ = "slice1-v0.1"

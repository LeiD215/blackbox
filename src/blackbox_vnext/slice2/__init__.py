"""bbx2 — Slice 2 (deterministic projector + checkpoint + staleness).

Slice 2 wraps Slice 1's strict corpus / authority / fold / governance semantics
as read-only dependencies and emits an explicitly non-authoritative derived
projection, enumerable manifest, derived checkpoint, and set-difference
staleness — never reimplementing or forking Slice 1 canonicalization/authority/
fold semantics.

No Git adapter, no multi-agent dispatch, no production mutation. Pure
stdlib-only local reference implementation; the Slice 2 subcommand runner is
a thin CLI over the local bbx2.* modules.
"""
__version__ = "0.2.0"
GENERATOR_IDENTITY = "bbx2-0.2.0"
FRAMEWORK_VERSION = "0.3.2"
SCHEMA_VERSION = "0.3.2"